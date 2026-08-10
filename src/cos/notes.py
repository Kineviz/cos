"""Your notes — the only thing in Kiran that is not derived.

Everything else here is computed from mail headers or the vault, and is
therefore disposable: delete the database, run the pipeline, get it back. Notes
are different. "Morgan is the real decision maker", "Martin said on the call he
wants to increase the investment" — that exists nowhere else. Losing it is
unrecoverable.

So the authoring surface is markdown in the vault (synced, backed up, readable
without Kiran), and this SQLite database is a derived index over it. Rebuild it
any time with `cos notes --reindex`; nothing is lost because nothing
originates here.

What the index adds over the markdown:

  * **identity across runs** — the same note keeps one row as the dashboard is
    regenerated around it, so `first_seen` means the day you wrote it.
  * **deletions** — a note you remove is not dropped, it gets `removed_at`.
    Changing your mind is signal.
  * **the state at the time** — what the deal looked like when you wrote it.
    "Wei flagged Morgan when Northwind had been quiet 45 days" is a usable
    observation; "Wei flagged Morgan" is not.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS note (
    fingerprint  TEXT PRIMARY KEY,   -- sha256(entity || normalized text)
    entity       TEXT NOT NULL,      -- 'deal:northwind' | 'journal' | free-form
    text         TEXT NOT NULL,
    source       TEXT NOT NULL,      -- 'dashboard' | 'cli'
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    removed_at   TEXT,
    context      TEXT                -- JSON: computed state when first written
);
CREATE INDEX IF NOT EXISTS idx_note_entity ON note(entity);
CREATE INDEX IF NOT EXISTS idx_note_first  ON note(first_seen);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_NOTE_LINE = re.compile(r"^\s*[-*]\s+(?P<text>\S.*)$")
_HEADING = re.compile(r"^#{2,4}\s+(?P<title>.+?)\s*$")
_MANAGED = re.compile(r"<!--\s*cos:(begin|end).*?-->", re.S)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unknown"


def fingerprint(entity: str, text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(f"{entity}\x00{norm}".encode()).hexdigest()[:32]


@dataclass
class Note:
    entity: str
    text: str
    source: str = "dashboard"

    @property
    def fp(self) -> str:
        return fingerprint(self.entity, self.text)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def extract_from_dashboard(markdown: str) -> list[Note]:
    """Every bullet the user wrote, keyed to the section it sits under.

    Only text OUTSIDE managed blocks counts — inside them is Kiran's own
    output, and treating that as a user note would re-import the computed
    tables on every run.
    """
    # Blank out managed regions so their content cannot be mistaken for notes.
    masked = re.sub(
        r"<!--\s*cos:begin\s+([^\s>]+)\s*-->.*?<!--\s*cos:end\s+\1\s*-->",
        lambda m: "\n" * m.group(0).count("\n"),
        markdown,
        flags=re.S,
    )
    notes: list[Note] = []
    entity = "journal"
    # Only bullets under a **Notes** marker are notes. The rest of the file is
    # the user's own task system — swallowing it captured placeholders like
    # "_None yet — see [[Personal]]._" as if they were judgement.
    in_notes = False
    for line in masked.splitlines():
        h = _HEADING.match(line)
        if h:
            title = h.group("title")
            entity = (
                "journal"
                if title.lower() in {"at a glance", "waiting on you", "deals", "notes"}
                else f"deal:{_slug(title)}"
            )
            in_notes = False
            continue
        if re.match(r"^\s*\*\*Notes\*\*\s*$", line):
            in_notes = True
            continue
        if not in_notes:
            continue
        m = _NOTE_LINE.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        if text.startswith(("[ ]", "[x]", "[X]")):   # tasks belong elsewhere
            continue
        if text.startswith(">") or "cos:" in text or text.startswith("_"):
            continue
        notes.append(Note(entity=entity, text=text))
    return notes


_LOG_HEADING = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}.*?(?:about\s+\*\*(?P<about>[^*]+)\*\*)?\s*$")


def extract_from_log(markdown: str) -> list[Note]:
    """Notes captured by `cos note`, from the append-only Kiran-Log.md.

    This is what makes the index genuinely derived: the log is the home, and
    the database can be thrown away and rebuilt from it.
    """
    notes: list[Note] = []
    entity = "journal"
    for line in markdown.splitlines():
        h = _LOG_HEADING.match(line)
        if h:
            about = (h.group("about") or "").strip()
            entity = f"deal:{_slug(about)}" if about else "journal"
            continue
        m = _NOTE_LINE.match(line)
        if m:
            text = m.group("text").strip()
            if text and not text.startswith(">"):
                notes.append(Note(entity=entity, text=text, source="cli"))
    return notes


def reindex(conn: sqlite3.Connection, dashboard: str, log: str, now: datetime) -> dict[str, int]:
    """Rebuild the index from the markdown.

    The note TEXT is recoverable from the files, but `context` and `first_seen`
    are not — they record the state of the world when the note was written, and
    that moment is gone. So they are carried across the rebuild by fingerprint
    rather than discarded. A rebuild that silently dropped them would destroy
    the most valuable column in the table.
    """
    keep = {
        r["fingerprint"]: (r["first_seen"], r["context"], r["removed_at"])
        for r in conn.execute(
            "SELECT fingerprint, first_seen, context, removed_at FROM note"
        ).fetchall()
    }
    conn.execute("DELETE FROM note")
    conn.commit()
    a = sync(conn, extract_from_dashboard(dashboard), now, None, "dashboard")
    b = sync(conn, extract_from_log(log), now, None, "cli")
    restored = 0
    for fp, (first_seen, context, removed_at) in keep.items():
        cur = conn.execute(
            "SELECT 1 FROM note WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if cur:
            conn.execute(
                "UPDATE note SET first_seen = ?, context = ?, removed_at = ? "
                "WHERE fingerprint = ?",
                (first_seen, context, removed_at, fp),
            )
            restored += 1
    conn.commit()
    return {"dashboard": a["new"], "log": b["new"], "history_kept": restored}


def sync(
    conn: sqlite3.Connection,
    notes: list[Note],
    now: datetime,
    context: dict | None = None,
    source_filter: str = "dashboard",
) -> dict[str, int]:
    """Reconcile the index against what the file says right now."""
    ts = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    seen = {n.fp for n in notes}
    stats = {"new": 0, "still_there": 0, "removed": 0, "restored": 0}

    for n in notes:
        row = conn.execute(
            "SELECT fingerprint, removed_at FROM note WHERE fingerprint = ?", (n.fp,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO note (fingerprint, entity, text, source, first_seen, "
                "last_seen, context) VALUES (?,?,?,?,?,?,?)",
                (n.fp, n.entity, n.text, n.source, ts, ts,
                 json.dumps(context.get(n.entity)) if context else None),
            )
            stats["new"] += 1
        else:
            if row["removed_at"]:
                stats["restored"] += 1
            conn.execute(
                "UPDATE note SET last_seen = ?, removed_at = NULL WHERE fingerprint = ?",
                (ts, n.fp),
            )
            stats["still_there"] += 1

    # Anything previously present in this source and now absent was deleted.
    gone = conn.execute(
        "SELECT fingerprint FROM note WHERE removed_at IS NULL AND source = ?",
        (source_filter,),
    ).fetchall()
    for row in gone:
        if row["fingerprint"] not in seen:
            conn.execute(
                "UPDATE note SET removed_at = ? WHERE fingerprint = ?",
                (ts, row["fingerprint"]),
            )
            stats["removed"] += 1

    conn.commit()
    return stats


def add(conn: sqlite3.Connection, note: Note, now: datetime, context: dict | None = None) -> bool:
    """Record a note captured outside the dashboard. Returns False if duplicate."""
    ts = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO note (fingerprint, entity, text, source, first_seen, "
            "last_seen, context) VALUES (?,?,?,?,?,?,?)",
            (note.fp, note.entity, note.text, note.source, ts, ts,
             json.dumps(context) if context else None),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE note SET last_seen = ?, removed_at = NULL WHERE fingerprint = ?",
            (ts, note.fp),
        )
        conn.commit()
        return False


def query(
    conn: sqlite3.Connection,
    entity: str | None = None,
    include_removed: bool = False,
    limit: int = 100,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM note"
    where, args = [], []
    if entity:
        where.append("entity LIKE ?")
        args.append(f"%{entity}%")
    if not include_removed:
        where.append("removed_at IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY first_seen DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) total, "
        "SUM(removed_at IS NULL) live, "
        "SUM(removed_at IS NOT NULL) removed, "
        "COUNT(DISTINCT entity) entities, "
        "MIN(first_seen) earliest FROM note"
    ).fetchone()
    return dict(row) if row else {}


# ── export to the brain ─────────────────────────────────────────────────────

# Retrieval EXCERPTS a page and truncates from the end, so anything below the
# fold is invisible however the page is chunked. This bit twice before — the
# date anchor in today.md, and the first version of this page, which put the
# notes under a long preamble and was cited as "contains no note entries".
# Content first, always. Explanation goes underneath.
_PAGE_HEADER = """---
type: note
title: {title}
entity: {entity}
authored_by: wei
knowledge_status: quoted
source: cos-notes
generated_by: cos
---

# {title}

**What Wei personally knows about {label} that is written down nowhere else** —
verbal commitments, who really decides, why something stalled, off-the-record
context from calls and meetings about {label}. Not derived from email or the
vault. Primary evidence; quote it directly.

"""

_PAGE_FOOTER = """
---

These statements about {label} were recorded by Wei through `cos note` or the
dashboard Notes section. They are the only record of what was said verbally
about {label} — no email or meeting note contains them.

_Derived from `Kiran-Log.md` and the Dashboard notes; regenerated on each run._
"""


def export_pages(
    conn: sqlite3.Connection,
    out_dir: Path,
    entity_titles: dict[str, str] | None = None,
    link_for: dict[str, str] | None = None,
) -> int:
    """One page per entity, aggregating everything Wei has said about it.

    Aggregated rather than one page per note: retrieval then returns the whole
    history for an entity in a single hit, which is what makes "you flagged
    this three weeks ago" answerable.

    Written into the vault (not the brain repo) so `[[wikilinks]]` resolve —
    gbrain scopes basename resolution per source.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    titles = entity_titles or {}
    links = link_for or {}

    rows = conn.execute(
        "SELECT * FROM note ORDER BY entity, first_seen DESC"
    ).fetchall()
    by_entity: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_entity.setdefault(r["entity"], []).append(r)

    written = 0
    for entity, items in by_entity.items():
        label = titles.get(entity) or entity.replace("deal:", "").replace("-", " ").title()
        body = [_PAGE_HEADER.format(title=f"Notes — {label}", entity=entity, label=label)]

        related = links.get(entity) or []
        if isinstance(related, str):
            related = [related]
        if related:
            body.append(
                "Related: " + " · ".join(f"[[{t}]]" for t in related) + "\n"
            )

        live = [r for r in items if not r["removed_at"]]
        gone = [r for r in items if r["removed_at"]]

        for r in live:
            ctx = ""
            if r["context"]:
                try:
                    d = json.loads(r["context"]) or {}
                    parts = []
                    if d.get("quiet_days") is not None:
                        parts.append(f"quiet {d['quiet_days']}d")
                    if d.get("ball_with"):
                        parts.append(f"ball with {d['ball_with']}")
                    if d.get("stage"):
                        parts.append(str(d["stage"]))
                    if parts:
                        ctx = f" _(at the time: {', '.join(parts)})_"
                except Exception:
                    pass
            body.append(f"- **{r['first_seen'][:10]}** — {r['text']}{ctx}")

        if gone:
            body.append("\n## Retracted\n")
            body.append(
                "_Wei wrote these and later removed them. Kept because changing "
                "one's mind is itself information._\n"
            )
            for r in gone:
                body.append(
                    f"- **{r['first_seen'][:10]}** → removed "
                    f"{r['removed_at'][:10]} — {r['text']}"
                )

        body.append(_PAGE_FOOTER.format(label=label))
        (out_dir / f"notes-{_slug(entity)}.md").write_text(
            "\n".join(body), encoding="utf-8"
        )
        written += 1
    return written
