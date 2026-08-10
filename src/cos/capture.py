"""Save what Wei told Kiran in chat, before the conversation is discarded.

A Telegram session freezes its date at creation, so a thread left running for
days answers "who owes me a reply" against the wrong day and says so with
complete confidence. Resetting fixes that — but a reset throws away the chat,
and the chat is where the most valuable information lives: verbal commitments,
who really decides, why something stalled. None of it is derivable from email or
the vault, which is exactly why it matters.

So capture first, then reset. Afterwards the conversation is disposable, because
everything durable in it has become a note in `Kiran-Log.md` — searchable, dated,
and citable long after the thread is gone.

── What may be remembered ───────────────────────────────────────────────────

**Only what Wei actually said.** Assistant turns are passed as context but never
mined for facts. Kiran's own output is inference over retrieved documents, and
promoting inference to memory is how a system starts citing its own guesses back
to itself as though they were told to it. Measured on this corpus, commitment
extraction scored 1/7 with two errors inverting who owed what — that is the
quality of claim this would be laundering into permanent memory.

**Only what is not already known.** A fact restated from an email Kiran just
read is not new knowledge; it is an echo. The extractor is told to skip anything
that reads like a summary of retrieved material.

Runs on the local model, so a day of private conversation is never sent
anywhere to decide what is worth keeping.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STATE_DB = Path.home() / ".hermes" / "state.db"
MARK_FILE = Path.home() / ".cos" / "capture-mark"

# The local model. Free, private, and already proven on 25,782 emails.
OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = "qwen3.5:9b"

PROMPT = """You are reading one side of a chat between Wei (a CEO) and his \
assistant. Extract ONLY durable facts that Wei stated and that could not be \
found in his email or notes — verbal commitments, who really makes a decision, \
why something stalled, prices or dates agreed by phone, personal context.

Rules:
- Only what WEI said. Never anything the assistant claimed or inferred.
- Skip questions, instructions, greetings, and requests to do something.
- Skip anything that reads like a restatement of a document.
- If nothing qualifies, return an empty list. That is a normal outcome.

Return JSON only: {"facts": [{"about": "<short topic slug or empty>", \
"text": "<the fact in one sentence, in Wei's terms>"}]}"""


@dataclass
class Captured:
    about: str
    text: str


def _messages_since(session_id: str | None, after_rowid: int) -> tuple[list[dict], int]:
    """User+assistant turns newer than the mark, and the new high-water mark."""
    if not STATE_DB.exists():
        return [], after_rowid
    conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    sql = (
        "SELECT id, session_id, role, content FROM messages "
        "WHERE id > ? AND content IS NOT NULL AND content != '' "
    )
    params: list = [after_rowid]
    if session_id:
        sql += "AND session_id = ? "
        params.append(session_id)
    sql += "ORDER BY id"
    rows = list(conn.execute(sql, params))
    conn.close()
    if not rows:
        return [], after_rowid
    return (
        [{"role": r[2], "content": r[3]} for r in rows],
        max(r[0] for r in rows),
    )


def _read_mark() -> int:
    try:
        return int(MARK_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def _write_mark(value: int) -> None:
    MARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARK_FILE.write_text(str(value))


def _extract(messages: list[dict]) -> list[Captured]:
    """Ask the local model which of Wei's statements are worth keeping."""
    # Assistant turns are included as context so a bare "yes, go ahead" can be
    # understood — but the prompt forbids taking facts from them.
    turns = [
        f"{'WEI' if m['role'] == 'user' else 'ASSISTANT'}: {m['content'][:1500]}"
        for m in messages
        if m["role"] in ("user", "assistant")
    ]
    if not turns:
        return []

    # Chunk instead of truncate. The first version sent transcript[:60000] and
    # kept the OLDEST 60k of a 95k conversation — three days of setup questions,
    # with everything that actually mattered cut off the end. Same mistake as
    # the 600-char retrieval excerpt, in the opposite direction. Chunking reads
    # all of it, and the model is local so the extra passes are free.
    chunks, current, size = [], [], 0
    for t in turns:
        if size + len(t) > _CHUNK_CHARS and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(t)
        size += len(t)
    if current:
        chunks.append("\n\n".join(current))

    facts: list[Captured] = []
    seen: set[str] = set()
    for chunk in chunks:
        for f in _extract_one(chunk):
            key = f.text.lower().strip()
            if key not in seen:
                seen.add(key)
                facts.append(f)
    return facts


_CHUNK_CHARS = 40000


def _extract_one(transcript: str) -> list[Captured]:
    """One model pass over one chunk."""

    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": transcript[:60000]},
        ],
        "max_tokens": 1200,
        "reasoning_effort": "none",
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL, body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.load(resp)
    content = payload["choices"][0]["message"].get("content") or ""

    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        parsed = json.loads(content[start : end + 1])
    except ValueError:
        return []

    out = []
    for f in parsed.get("facts", []):
        text = (f.get("text") or "").strip()
        if len(text) > 8:
            out.append(Captured(about=(f.get("about") or "").strip(), text=text))
    return out


def capture(cfg, now: datetime, session_id: str | None = None,
            dry_run: bool = False) -> list[Captured]:
    """Turn new chat into notes. Returns what was written."""
    from . import dashboard as dashboard_page
    from . import notes as notes_store

    mark = _read_mark()
    messages, new_mark = _messages_since(session_id, mark)
    if not messages:
        return []

    facts = _extract(messages)
    if dry_run:
        return facts
    if not facts:
        _write_mark(new_mark)
        return []

    journal = cfg.vault_root / "05_workspace" / "Task_management" / "Kiran-Log.md"
    journal.parent.mkdir(parents=True, exist_ok=True)
    conn = notes_store.connect(cfg.notes_db)

    written: list[Captured] = []
    for f in facts:
        entity = (
            f"deal:{dashboard_page._slug(f.about)}" if f.about else "journal"
        )
        # The DB is an index over the markdown, so append first: a database
        # failure must never lose something Wei said.
        fresh = notes_store.add(
            conn, notes_store.Note(entity, f.text, source="chat"), now
        )
        if not fresh:
            continue  # already knew it
        with journal.open("a", encoding="utf-8") as fh:
            tag = f" · about **{f.about}**" if f.about else ""
            fh.write(
                f"\n## {now.astimezone():%Y-%m-%d %H:%M}{tag} · from chat\n\n"
                f"- {f.text}\n"
            )
        written.append(f)

    notes_store.export_pages(conn, cfg.vault_root / "90_agent" / "Notes")
    _write_mark(new_mark)
    return written
