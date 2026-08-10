"""The panel store. One SQLite database, master copy for editable panels.

Wei: *"For each panel (Tasks, Prospects), we should have a table in DB. With
name, state. In GUI, we can drag them around (like in Tasks panel). We can
edit name, note. And the chat in the panel allow us to modify the item,
change state, etc."* And on where the truth lives: *"Database take over as
the master copy."*

So Pipeline.md and Prospects.md stop being the master for deals. They are
seeded from once, backed up first, and from then on their "At a glance"
tables are a generated view of this database — still readable in Obsidian,
no longer the place to edit.

Two kinds of field, and the split matters:

- **Owned** — name, state, note, order. Stored here, edited in the GUI or by
  the assistant through the panel tools.
- **Computed** — days quiet, whose ball, the last inbound email. Derived from
  the mail ledger on every refresh and overlaid at read time. Not stored,
  not editable: "58 days quiet" is a fact about the mail, not an opinion.

Tasks is not in this database yet. Its store (`agenda.json`) already has
exactly this shape — name, bucket, rank, comments — and moving a working
panel is risk without a feature. The assistant's panel tools write to both
stores through one interface, which is the part that had to be unified.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB_FILE = Path.home() / ".cos" / "panels.db"

PROSPECTS = "prospects"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript("""
                CREATE TABLE IF NOT EXISTS panels(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    states TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS items(
                    id TEXT PRIMARY KEY,
                    panel TEXT NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    pos REAL NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    extra TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS items_panel
                    ON items(panel, archived, state, pos);
            """)
            _migrate_notes(_conn)
        return _conn


def _migrate_notes(db: sqlite3.Connection) -> None:
    """Notes became a dated history. Wei: "show latest note, but keep earlier
    notes, collapse them though. each note is dated."

    The `notes` column is a JSON list of {ts, text}, newest last. The old
    single `note` stays as a denormalised copy of the newest text, so
    everything that reads it — the export, the overlay — keeps working. An
    existing note migrates in as the first history entry, dated by when the
    row was created.
    """
    cols = {r["name"] for r in db.execute("PRAGMA table_info(items)")}
    if "notes" in cols:
        return
    db.execute("ALTER TABLE items ADD COLUMN notes TEXT NOT NULL DEFAULT '[]'")
    for r in db.execute("SELECT id, note, created_at FROM items").fetchall():
        if r["note"]:
            day = time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))
            db.execute("UPDATE items SET notes=? WHERE id=?",
                       (json.dumps([{"ts": day, "text": r["note"]}]),
                        r["id"]))
    db.commit()


def reset_for_tests(path: Path) -> None:
    global _conn, DB_FILE
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        DB_FILE = path


# --------------------------------------------------------------------------
# Panels


def ensure_panel(panel: str, title: str, states: list[str]) -> None:
    with _lock:
        db = _db()
        db.execute(
            "INSERT INTO panels(id, title, states) VALUES(?,?,?) "
            "ON CONFLICT(id) DO NOTHING",
            (panel, title, json.dumps(states)))
        db.commit()


def states(panel: str) -> list[str]:
    row = _db().execute("SELECT states FROM panels WHERE id=?",
                        (panel,)).fetchone()
    return json.loads(row["states"]) if row else []


def _learn_state(panel: str, state: str) -> None:
    """A state seen on an item joins the panel's list. The list is data, not
    schema — Wei's stages are his own words and must not be normalised."""
    if not state:
        return
    known = states(panel)
    if state not in known:
        known.append(state)
        _db().execute("UPDATE panels SET states=? WHERE id=?",
                      (json.dumps(known), panel))


# --------------------------------------------------------------------------
# Items


def list_items(panel: str, archived: bool = False) -> list[dict]:
    rows = _db().execute(
        "SELECT * FROM items WHERE panel=? AND archived=? "
        "ORDER BY state, pos, name",
        (panel, int(archived))).fetchall()
    return [_row(r) for r in rows]


def _row(r) -> dict:
    d = dict(r)
    d["extra"] = json.loads(d.get("extra") or "{}")
    d["notes"] = json.loads(d.get("notes") or "[]")
    d["archived"] = bool(d["archived"])
    return d


def _next_pos(db: sqlite3.Connection, panel: str, state: str) -> float:
    row = db.execute("SELECT MAX(pos) m FROM items WHERE panel=? AND state=?",
                     (panel, state)).fetchone()
    return (row["m"] or 0.0) + 100.0


def add_item(panel: str, name: str, state: str = "", note: str = "",
             extra: dict | None = None, item_id: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("An item needs a name.")
    with _lock:
        db = _db()
        iid = item_id or uuid.uuid4().hex[:12]
        now = time.time()
        notes = [{"ts": _today(), "text": note}] if note.strip() else []
        db.execute(
            "INSERT INTO items(id,panel,name,state,note,notes,pos,extra,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (iid, panel, name, state, note, json.dumps(notes),
             _next_pos(db, panel, state),
             json.dumps(extra or {}), now, now))
        _learn_state(panel, state)
        db.commit()
        return get_item(iid)


def get_item(item_id: str) -> dict:
    row = _db().execute("SELECT * FROM items WHERE id=?",
                        (item_id,)).fetchone()
    if not row:
        raise KeyError(f"No item {item_id!r}.")
    return _row(row)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def find_item(panel: str, needle: str) -> dict | None:
    """The item a person means by a name. Exact first, then substring —
    and a substring that matches two items matches nothing, because the
    assistant renaming the wrong deal is worse than it asking."""
    needle = (needle or "").strip().lower()
    if not needle:
        return None
    rows = list_items(panel) + list_items(panel, archived=True)
    exact = [r for r in rows if r["name"].lower() == needle]
    if len(exact) == 1:
        return exact[0]
    part = [r for r in rows if needle in r["name"].lower()]
    return part[0] if len(part) == 1 else None


def update_item(item_id: str, name: str | None = None,
                state: str | None = None, note: str | None = None,
                archived: bool | None = None) -> dict:
    with _lock:
        db = _db()
        cur = get_item(item_id)
        sets, vals = [], []
        if name is not None and name.strip():
            sets.append("name=?"); vals.append(name.strip())
        if state is not None:
            sets.append("state=?"); vals.append(state.strip())
            if state.strip() != cur["state"]:
                sets.append("pos=?")
                vals.append(_next_pos(db, cur["panel"], state.strip()))
            _learn_state(cur["panel"], state.strip())
        if note is not None and note.strip():
            # Notes are a dated history, not a field. The new one goes on
            # top and the old ones stay — Wei: "show latest note, but keep
            # earlier notes". The flat `note` column mirrors the newest text
            # so the export and the overlay keep reading one string.
            history = cur["notes"] + [{"ts": _today(), "text": note.strip()}]
            sets.append("notes=?"); vals.append(json.dumps(history))
            sets.append("note=?"); vals.append(note.strip())
        if archived is not None:
            sets.append("archived=?"); vals.append(int(archived))
        if not sets:
            return cur
        sets.append("updated_at=?"); vals.append(time.time())
        vals.append(item_id)
        db.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", vals)
        db.commit()
        return get_item(item_id)


def _focus_rows(panel: str) -> list[dict]:
    rows = [r for r in list_items(panel) if r["extra"].get("focus")]
    rows.sort(key=lambda r: (r["extra"].get("focus_pos", 0.0), r["name"]))
    return rows


def move_focus(item_id: str, above_id: str | None = None) -> dict:
    """Order within the attention list.

    The list had no order of its own — rows fell back to stage order, so
    dragging inside it did nothing. Focus carries its own position for the
    same reason stages do: the top of "needs attention now" is a statement
    about what comes first.

    Midpoint insertion, so a drag rewrites one row rather than the list.
    """
    with _lock:
        db = _db()
        cur = get_item(item_id)
        rows = [r for r in _focus_rows(cur["panel"]) if r["id"] != item_id]
        idx = next((i for i, r in enumerate(rows) if r["id"] == above_id),
                   None) if above_id else None
        if idx is None:
            pos = (rows[-1]["extra"].get("focus_pos", 0.0) + 100.0
                   if rows else 100.0)
        elif idx == 0:
            pos = rows[0]["extra"].get("focus_pos", 0.0) - 100.0
        else:
            pos = (rows[idx - 1]["extra"].get("focus_pos", 0.0)
                   + rows[idx]["extra"].get("focus_pos", 0.0)) / 2
        extra = dict(cur["extra"])
        extra["focus"] = True
        extra["focus_pos"] = pos
        db.execute("UPDATE items SET extra=?, updated_at=? WHERE id=?",
                   (json.dumps(extra), time.time(), item_id))
        db.commit()
        return get_item(item_id)


def set_focus(item_id: str, on: bool) -> dict:
    """Put an item in the attention list, or take it out.

    Wei: *"I need a top view, those I need to pay attention right now.
    Something I can assign a prospect there."* Focus is deliberately NOT a
    stage. A deal being urgent this week says nothing about whether it is
    Qualified or Engaged, and folding the two would lose the stage the
    moment you flagged something.
    """
    with _lock:
        db = _db()
        cur = get_item(item_id)
        extra = dict(cur["extra"])
        if on:
            extra["focus"] = True
            if "focus_pos" not in extra:
                rows = [r for r in _focus_rows(cur["panel"])
                        if r["id"] != item_id]
                extra["focus_pos"] = (
                    rows[-1]["extra"].get("focus_pos", 0.0) + 100.0
                    if rows else 100.0)
        else:
            extra.pop("focus", None)
            extra.pop("focus_pos", None)
        db.execute("UPDATE items SET extra=?, updated_at=? WHERE id=?",
                   (json.dumps(extra), time.time(), item_id))
        db.commit()
        return get_item(item_id)


def move_item(item_id: str, state: str, above_id: str | None = None) -> dict:
    """Drop an item into a state, optionally above another item.

    Midpoint insertion, same trick as the Tasks panel: a drag touches one
    row instead of renumbering the list.
    """
    with _lock:
        db = _db()
        cur = get_item(item_id)
        state = (state or "").strip()
        rows = [r for r in list_items(cur["panel"]) if r["state"] == state
                and r["id"] != item_id]
        if above_id:
            idx = next((i for i, r in enumerate(rows)
                        if r["id"] == above_id), None)
        else:
            idx = None
        if idx is None:
            pos = _next_pos(db, cur["panel"], state)
        elif idx == 0:
            pos = rows[0]["pos"] - 100.0
        else:
            pos = (rows[idx - 1]["pos"] + rows[idx]["pos"]) / 2
        now = time.time()
        db.execute("UPDATE items SET state=?, pos=?, updated_at=? WHERE id=?",
                   (state, pos, now, item_id))
        _learn_state(cur["panel"], state)
        db.commit()
        return get_item(item_id)


# --------------------------------------------------------------------------
# Seeding: the one-time takeover from the markdown files


_FILES = (("Pipeline.md", "deal"), ("Prospects.md", "prospect"))

# The cells the database owns. Everything else in a row — Campaign, Value,
# whatever Wei adds a column for — is his, carried through export untouched.
_OWNED_COLS = {"stage", "next step", "owner", "paper?"}


def _vault_rows(vault_root: Path):
    """Every deal row from the two markdown files, with all its columns."""
    from .vault import _load_table_after_heading

    tm = vault_root / "05_workspace" / "Task_management"
    for filename, name_col in _FILES:
        for row in _load_table_after_heading(tm / filename, "## At a glance"):
            name = row.get(name_col, "").strip()
            if name:
                yield filename, name_col, name, row


def seed_prospects(vault_root: Path) -> int:
    """Import deals from Pipeline.md and Prospects.md, once.

    Idempotent by name: a deal already in the database is never touched, so
    running this twice cannot undo an edit. The stage strings are imported
    exactly as written — they are Wei's words, not a schema. The whole
    original row is kept in `extra`, because the table has columns the panel
    does not edit — Campaign, Value — and a generated view that loses them
    is not a view, it is a downgrade.
    """
    ensure_panel(PROSPECTS, "Prospects", [])
    have = {r["name"].lower() for r in
            list_items(PROSPECTS) + list_items(PROSPECTS, archived=True)}
    added = 0
    for filename, _name_col, name, row in _vault_rows(vault_root):
        if name.lower() in have:
            continue
        extra = {"owner": row.get("owner", ""),
                 "paper": row.get("paper?", ""),
                 "source_file": filename, "row": row}
        add_item(PROSPECTS, name, state=row.get("stage", ""),
                 note=row.get("next step", ""), extra=extra)
        have.add(name.lower())
        added += 1
    return added


def enrich_from_vault(vault_root: Path) -> int:
    """Attach the full original row to items seeded before rows were kept."""
    with _lock:
        db = _db()
        touched = 0
        by_name = {r["name"].lower(): r for r in
                   list_items(PROSPECTS) + list_items(PROSPECTS, archived=True)}
        for filename, _name_col, name, row in _vault_rows(vault_root):
            item = by_name.get(name.lower())
            if item is None or item["extra"].get("row"):
                continue
            extra = dict(item["extra"])
            extra["row"] = row
            extra.setdefault("source_file", filename)
            db.execute("UPDATE items SET extra=? WHERE id=?",
                       (json.dumps(extra), item["id"]))
            touched += 1
        db.commit()
        return touched


# --------------------------------------------------------------------------
# The generated markdown view


_GENERATED_NOTE = ("<!-- This table is generated from the panel database "
                   "(cos). Edit in the dashboard, not here. -->")


def export_markdown(vault_root: Path) -> list[Path]:
    """Rewrite the "At a glance" tables in Pipeline.md and Prospects.md.

    The database is the master; these files are the view. Only the table
    under "## At a glance" is replaced — everything else in the file is
    someone's writing and is left exactly as found.
    """
    tm = vault_root / "05_workspace" / "Task_management"
    rows = list_items(PROSPECTS)
    written = []
    for filename, name_col in _FILES:
        path = tm / filename
        if not path.is_file():
            continue
        mine = [r for r in rows
                if r["extra"].get("source_file", "Pipeline.md") == filename]
        if not mine:
            continue
        text = path.read_text(encoding="utf-8")
        # The columns are the file's own, in the file's own order, so the
        # ones the panel does not edit — Campaign, Value — survive the
        # rewrite. Only the cells the database owns are replaced.
        cols: list[str] = [name_col]
        for r in mine:
            for key in (r["extra"].get("row") or {}):
                if key not in cols:
                    cols.append(key)
        for key in ("stage", "owner", "next step", "paper?"):
            if key not in cols:
                cols.append(key)
        table = [_GENERATED_NOTE,
                 "| " + " | ".join(c[:1].upper() + c[1:] for c in cols) + " |",
                 "|" + "---|" * len(cols)]
        for r in mine:
            orig = r["extra"].get("row") or {}
            cells = []
            for c in cols:
                if c == name_col:
                    # A deal with its own section keeps its wikilink.
                    cells.append(f"[[#{r['name']}]]"
                                 if f"## {r['name']}" in text else r["name"])
                elif c == "stage":
                    cells.append(r["state"])
                elif c == "next step":
                    cells.append(r["note"])
                elif c == "owner":
                    cells.append(r["extra"].get("owner", ""))
                elif c == "paper?":
                    cells.append(r["extra"].get("paper", ""))
                else:
                    cells.append(orig.get(c, ""))
            table.append("| " + " | ".join(
                c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
        new = _replace_glance(text, "\n".join(table))
        if new != text:
            path.write_text(new, encoding="utf-8")
        written.append(path)
    return written


def _replace_glance(text: str, table: str) -> str:
    """Swap ONLY the table under "## At a glance" — not the section.

    The first version replaced everything up to the next heading, which ate
    the hand-written note sitting below the table. Prose around the table is
    Wei's; the table alone is the generated view.
    """
    m = re.search(r"^## At a glance\s*\n", text, re.M | re.I)
    if not m:
        return text
    rest = text[m.end():]
    block = re.search(
        r"(?:<!--[^>]*-->\s*\n)?(?:^\|.*\n)+", rest, re.M)
    if not block:
        return text
    return (text[:m.end()] + rest[:block.start()]
            + table + "\n" + rest[block.end():])
