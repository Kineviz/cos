"""Conversations with the assistant, kept as sessions.

The first version put every question on the rail as its own row, which made a
list of questions rather than a record of thinking. Wei asked for claude.ai's
shape instead: a session holds a sequence of turns, you go back to it, you
rename it, you delete it, you reorder it.

**Sessions are not only a way of grouping the sidebar.** `hermes -z` starts
cold every time — there is no server-side memory to resume — so without this,
"and what about the proposal?" would be answered by something that had never
heard the previous question. A session's earlier turns are prepended to the
prompt, which is what makes a follow-up mean anything. That is the real reason
this module exists; the sidebar is the visible half.

**Stored in `~/.cos/chats.json`, not in the vault.** The rule set by notes.py
is that content which exists *only* here is content git cannot protect and the
agent cannot see — so notes Wei writes get mirrored into the vault. Answers
fail that test's premise: they are derived from the brain and reproducible from
it, and writing them back would feed the brain its own output on the next sync.
What he types is content; what he was told is not.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

STORE = Path.home() / ".cos" / "chats.json"
MAX_SESSIONS = 100
MAX_TURNS = 200
# How much earlier conversation to replay into a new question. Enough for a
# follow-up to make sense, bounded because every turn is tokens on every
# subsequent question in the session.
CONTEXT_TURNS = 4
TITLE_MAX = 60

_lock = threading.RLock()


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sessions": []}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp name. A shared one lets the server and a CLI process
    # interleave bytes into the same file and rename the result into place.
    tmp = STORE.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, STORE)
    finally:
        tmp.unlink(missing_ok=True)


def _title_from(question: str) -> str:
    q = " ".join((question or "").split())
    return (q[:TITLE_MAX].rstrip() + "…") if len(q) > TITLE_MAX else (q or "New chat")


def sessions() -> list[dict]:
    """Newest activity first, unless Wei has dragged them into an order.

    `rank` is only set by an explicit move, so an untouched list stays sorted
    by recency — which is what you want before you have opinions about it, and
    stops being what you want the moment you do.
    """
    with _lock:
        rows = _load().get("sessions", [])
    pinned = [s for s in rows if s.get("rank") is not None]
    rest = [s for s in rows if s.get("rank") is None]
    pinned.sort(key=lambda s: s["rank"])
    rest.sort(key=lambda s: s.get("updated", 0), reverse=True)
    return pinned + rest


def summaries() -> list[dict]:
    """What the sidebar needs. Turns are not sent — a hundred sessions of full
    answers would be megabytes on every page load."""
    return [
        {"id": s["id"], "title": s.get("title") or "New chat",
         "updated": s.get("updated", 0), "turns": len(s.get("turns", []))}
        for s in sessions()
    ]


def get(session_id: str) -> dict | None:
    with _lock:
        for s in _load().get("sessions", []):
            if s["id"] == session_id:
                return s
    return None


def create(title: str = "") -> dict:
    session = {
        "id": uuid.uuid4().hex[:12],
        "title": title or "New chat",
        "created": time.time(),
        "updated": time.time(),
        "rank": None,
        "turns": [],
    }
    with _lock:
        data = _load()
        data.setdefault("sessions", []).insert(0, session)
        del data["sessions"][MAX_SESSIONS:]
        _save(data)
    return session


def add_turn(session_id: str, turn: dict) -> dict | None:
    """Record or UPDATE an exchange, and name the session from its first
    question if it has not been named.

    Updating in place matters: a turn is written the moment it is asked, with
    status "running", so leaving the conversation and coming back finds the
    question still there and still working. Before this a running turn existed
    only in the browser's memory — clicking another chat during a 69-second
    answer destroyed it, the server finished, wrote 2,439 characters, and
    nobody ever saw them.
    """
    with _lock:
        data = _load()
        for s in data.get("sessions", []):
            if s["id"] != session_id:
                continue
            turns = s.setdefault("turns", [])
            for n, existing in enumerate(turns):
                if existing.get("id") and existing["id"] == turn.get("id"):
                    turns[n] = {**existing, **turn}
                    s["updated"] = time.time()
                    _save(data)
                    return s
            turns.append(turn)
            del s["turns"][:-MAX_TURNS]
            s["updated"] = time.time()
            if s.get("title") in (None, "", "New chat"):
                s["title"] = _title_from(turn.get("question", ""))
            _save(data)
            return s
    return None


def rename(session_id: str, title: str) -> bool:
    title = " ".join((title or "").split())[:TITLE_MAX]
    if not title:
        return False
    with _lock:
        data = _load()
        for s in data.get("sessions", []):
            if s["id"] == session_id:
                s["title"] = title
                _save(data)
                return True
    return False


def delete(session_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data.get("sessions", []))
        data["sessions"] = [s for s in data.get("sessions", []) if s["id"] != session_id]
        if len(data["sessions"]) == before:
            return False
        _save(data)
        return True


def move(session_id: str, above: str | None = None, below: str | None = None) -> bool:
    """Reorder by dropping between two others.

    The first drag has to freeze the whole list, not just the item moved: until
    then everything is ordered by recency, so pinning one row and leaving the
    rest floating would shuffle around it the next time any other session was
    used.
    """
    order = sessions()
    ids = [s["id"] for s in order]
    if session_id not in ids:
        return False
    with _lock:
        data = _load()
        by_id = {s["id"]: s for s in data.get("sessions", [])}
        for n, sid in enumerate(ids):
            if sid in by_id:
                by_id[sid]["rank"] = float(n) * 1000
        me, up, down = by_id.get(session_id), by_id.get(above or ""), by_id.get(below or "")
        if up is not None and down is not None:
            me["rank"] = (up["rank"] + down["rank"]) / 2
        elif down is not None:
            me["rank"] = down["rank"] - 1000
        elif up is not None:
            me["rank"] = up["rank"] + 1000
        else:
            me["rank"] = -1000.0
        _save(data)
    return True


def context(session_id: str) -> list[dict]:
    """The last few exchanges, oldest first, for threading into a prompt."""
    s = get(session_id)
    if not s:
        return []
    return [t for t in s.get("turns", [])[-CONTEXT_TURNS:] if t.get("answer")]


def running(session_id: str) -> list[dict]:
    """Turns still in flight, so a reopened conversation resumes polling."""
    s = get(session_id)
    return [t for t in (s or {}).get("turns", []) if t.get("status") == "running"]


def search(needle: str, limit: int = 40) -> list[dict]:
    """Find a conversation by something said in it.

    Matches the title AND the text of both sides, because what you remember is
    usually a phrase from the answer rather than how you phrased the question.
    """
    needle = (needle or "").strip().lower()
    if not needle:
        return []
    out = []
    for s in sessions():
        hay_title = (s.get("title") or "").lower()
        hit_line = ""
        for t in s.get("turns", []):
            for field in (t.get("question", ""), t.get("answer", "")):
                if needle in (field or "").lower():
                    idx = field.lower().index(needle)
                    hit_line = field[max(0, idx - 40): idx + 80].strip()
                    break
            if hit_line:
                break
        if needle in hay_title or hit_line:
            out.append({
                "id": s["id"], "title": s.get("title") or "New chat",
                "updated": s.get("updated", 0), "turns": len(s.get("turns", [])),
                "excerpt": hit_line,
            })
        if len(out) >= limit:
            break
    return out
