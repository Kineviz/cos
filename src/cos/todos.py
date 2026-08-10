"""Read the to-do list Kiran actually maintains.

This module exists because the first version of the dashboard got the source
wrong. It built a list from mail headers — who wrote last and got no reply —
and called it "Today". Meanwhile Kiran had been keeping the *real* list all
along, written from what Wei told it in conversation: reconnect with Northwind,
follow up with Brad at BigBank, the CDL talk, the dynamic-ontology blog series.

Two lists. Which is the exact failure this project is supposed to prevent, and
worse than having no list at all, because the wrong one looks authoritative.

**Kiran owns this file; nothing here writes to it.** Kiran edits it through the
brain (`mcp:put_page`) — renumbering items, marking things DONE, carrying the
list forward day to day. A second writer would fight it. So this parses, and
Wei's ticks and comments live beside it in the dashboard's own state, keyed by
the item's text.

The format is whatever Kiran happens to write, which is markdown with numbered
items under `##` headings, sometimes bold, sometimes with **DONE** in the body.
That is worth parsing loosely rather than demanding a schema: the value of the
list is that Kiran can write it naturally.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NOTES_DIR = Path.home() / "brain" / "90_agent" / "notes"

# "1. **Reconnect with Northwind** — Wei to re-establish contact"
_ITEM = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_HEADING = re.compile(r"^##\s+(.*?)\s*$")
# Kiran marks completion inline rather than with a checkbox.
_DONE = re.compile(r"\*\*DONE\*\*|\bDONE\b(?!\s*\?)", re.I)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_TAIL = re.compile(r"_[^_]*_\s*$")


@dataclass
class Todo:
    id: str
    number: int
    section: str
    title: str
    detail: str
    done: bool
    source: str

    def as_dict(self) -> dict:
        return {
            "id": self.id, "number": self.number, "section": self.section,
            "title": self.title, "detail": self.detail, "done": self.done,
        }


def latest_file() -> Path | None:
    """The most recent list. Kiran carries one forward rather than starting a
    new file each day, but it names them by date, so take the newest."""
    if not NOTES_DIR.is_dir():
        return None
    files = sorted(NOTES_DIR.glob("*-todos.md"))
    return files[-1] if files else None


def _clean(text: str) -> tuple[str, str]:
    """Split an item into a short title and the rest.

    Kiran writes the subject in bold and the context after a dash, which is a
    convention worth honouring: it means the dashboard can show something
    scannable without truncating mid-word.
    """
    text = text.strip()
    bold = _BOLD.search(text)
    if bold:
        title = bold.group(1).strip(" .—-")
        rest = (text[: bold.start()] + text[bold.end():]).strip(" .—-")
    else:
        parts = re.split(r"\s+[—–-]\s+", text, maxsplit=1)
        title = parts[0].strip()
        rest = parts[1].strip() if len(parts) > 1 else ""
    rest = _BOLD.sub(r"\1", rest)
    rest = _ITALIC_TAIL.sub("", rest).strip(" .—-")
    return title, rest


def parse(text: str, source: str = "") -> list[Todo]:
    out: list[Todo] = []
    section = ""
    pending: list[str] = []
    number = 0

    def flush() -> None:
        nonlocal pending, number
        if not pending:
            return
        body = " ".join(p.strip() for p in pending if p.strip())
        title, detail = _clean(body)
        if title:
            out.append(Todo(
                id="todo:" + hashlib.sha1(title.lower().encode()).hexdigest()[:12],
                number=number, section=section, title=title, detail=detail,
                done=bool(_DONE.search(body)), source=source,
            ))
        pending = []

    for line in text.splitlines():
        head = _HEADING.match(line)
        if head:
            flush()
            section = head.group(1)
            continue
        item = _ITEM.match(line)
        if item:
            flush()
            number = int(item.group(1))
            pending = [item.group(2)]
            continue
        # A continuation line belongs to the item above it, but a blank line or
        # a new block ends it — otherwise the trailing "_Source for 5 and 6…_"
        # paragraph gets glued onto item 7.
        if pending and line.strip() and not line.startswith(("_", "-", "#", ">")):
            pending.append(line)
        elif not line.strip():
            flush()
    flush()
    return out


def load() -> list[Todo]:
    path = latest_file()
    if not path:
        return []
    try:
        return parse(path.read_text(encoding="utf-8"), source=str(path))
    except OSError:
        return []


def age_days() -> int | None:
    """How stale the list is. A to-do list nobody has touched for a week is
    worth saying so about rather than presenting as today's."""
    path = latest_file()
    if not path:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    if not m:
        return None
    try:
        when = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now().date() - when).days
