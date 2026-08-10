"""Today's short list — the one page that is meant to be worked, not read.

Everything else this tool produces is derived and disposable. This is the first
surface where Wei's own judgement is the input: what matters today, what he has
dealt with, what he wants pushed down.

**Two kinds of item, deliberately in one list.** Derived items come from the
mail — who is waiting, which deals have gone cold — and appear on their own.
Manual items are the things no mailbox knows about. Keeping them apart would
mean two lists to check, which is the failure this whole project exists to
avoid.

**Where things live follows the rule already set by notes.py.** Content — the
items Wei writes and the comments he leaves — is markdown in the vault:
readable in Obsidian, backed up, in git, and therefore visible to the agent.
State — done, snoozed, priority — is bookkeeping and lives in a small JSON
file. Losing the JSON costs you a day's checkboxes. Losing the markdown would
lose something that exists nowhere else, so it does not live here.

**"Done" means something different for a derived item**, and pretending
otherwise would make the list lie. Marking a manual item done ends it. Marking
"Pat Fisher, waiting 73 days" done does NOT mean Bob stopped waiting — no reply
was sent. It means *I have dealt with this*, so it is hidden until that person
writes again. New inbound mail brings it back, because the situation changed.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_FILE = Path.home() / ".cos" / "agenda.json"
PAGE_NAME = "Today-list.md"

TODO = "todo"          # Kiran's own list — the real one
DERIVED_OWED = "owed"
DERIVED_QUIET = "quiet"
MANUAL = "manual"

# Wei's three buckets. Fixed, in this order, because the point of a top list is
# that "what am I doing today" is answerable at a glance — and that stops being
# true the moment the number of buckets is itself a decision.
SECTIONS = ("today", "soon", "backlog")
SECTION_LABELS = {"today": "Today", "soon": "Soon", "backlog": "Back list"}

# Where an item lands before Wei moves it. Kiran writes its own headings and
# they map cleanly enough; anything unrecognised goes to the back rather than
# the front, so a mis-parse can never shout at him from the top of Today.
_SECTION_FROM_KIRAN = {
    "today": "today",
    "next 30 days": "soon",
    "follow-ups": "soon",
    "reminders": "soon",
}


def default_section(kind: str, kiran_section: str) -> str:
    text = (kiran_section or "").strip().lower()
    for prefix, bucket in _SECTION_FROM_KIRAN.items():
        if text.startswith(prefix):
            return bucket
    if kind == MANUAL:
        return "today"
    return "backlog"


def _sid(kind: str, key: str) -> str:
    """A stable id so state survives the list being rebuilt every 15 minutes.

    Keyed on WHO rather than on the subject line: a person writing a second
    email must not become a second item, or every reply from a waiting
    counterparty would silently resurrect something already dealt with.
    """
    return f"{kind}:" + hashlib.sha1(key.lower().encode()).hexdigest()[:12]


@dataclass
class Item:
    id: str
    kind: str
    title: str
    detail: str = ""
    days: int | None = None
    priority: int = 0          # legacy; ordering is now rank within a bucket
    done: bool = False
    done_at: str | None = None
    comments: list[dict] = field(default_factory=list)
    # For derived items: the newest inbound we had seen when it was dismissed.
    # If a newer one arrives, the situation changed and it comes back.
    dismissed_at_signal: str | None = None
    # The Gmail message a reply would be threaded to, for owed items. It is
    # what decides the recipient of a draft, and it comes from the ledger —
    # never from anything a model wrote. See draft_broker.
    msg: str | None = None
    thread: str | None = None
    # Kiran's own heading, kept for the default placement and nothing else.
    section: str = ""
    number: int = 0
    # Wei's bucket, and his order within it. `rank` is a plain float so an item
    # can always be dropped between two others without renumbering the list —
    # midpoint insertion, so a drag touches one row instead of all of them.
    bucket: str = ""
    rank: float = 0.0
    # True when the SOURCE says it is done — Kiran wrote **DONE** in its list.
    # Kept separate from Wei's tick so neither can silently undo the other.
    from_source: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "detail": self.detail, "days": self.days, "priority": self.priority,
            "msg": self.msg, "thread": self.thread,
            "done": self.done, "done_at": self.done_at, "comments": self.comments,
            "section": self.section, "number": self.number,
            "from_source": self.from_source,
            "bucket": self.bucket, "rank": self.rank,
        }


# --------------------------------------------------------------------------


# Every mutation is a read-modify-write called from a request thread, and the
# server is threaded. Two overlapping clicks — a tick during a drag — both
# loaded, both mutated, and the last write won. Worse, both wrote to the SAME
# tmp path before renaming, which interleaves two writers into one file.
_state_lock = threading.RLock()


def _serialised(fn):
    """Hold the lock across the whole read-modify-write, not just the write."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _state_lock:
            return fn(*a, **kw)
    return wrapper


class StateCorrupt(RuntimeError):
    pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"items": {}, "manual": []}
    except json.JSONDecodeError as e:
        # NOT an empty state. Manual items and comments exist ONLY in this file
        # — nothing parses them back out of the rendered markdown — so
        # returning {} on a torn read silently erases every to-do Wei typed and
        # every note he left, and the page renders that emptiness as though it
        # were correct. settings.py already refuses in exactly this situation.
        # This did not, and its docstring claimed the cost was "a day of
        # checkboxes".
        broken = STATE_FILE.with_suffix(".corrupt")
        try:
            STATE_FILE.replace(broken)
        except OSError:
            pass
        raise StateCorrupt(
            f"{STATE_FILE} is not valid JSON ({e}). Moved to {broken}; "
            f"the previous good copy should be at {STATE_FILE.with_suffix('.bak')}."
        ) from e
    except OSError as e:
        raise StateCorrupt(f"Cannot read {STATE_FILE}: {e}") from e


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            STATE_FILE.replace(STATE_FILE.with_suffix(".bak"))
        except OSError:
            pass
    # Unique tmp name: a shared one lets two writers interleave into the same
    # file and then rename the result into place.
    tmp = STATE_FILE.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def _derived_from_snapshot(snapshot: dict) -> list[Item]:
    out: list[Item] = []
    for row in snapshot.get("owed", []):
        who = row.get("who") or ""
        out.append(Item(
            id=_sid(DERIVED_OWED, who),
            kind=DERIVED_OWED,
            title=who,
            detail=row.get("subject", ""),
            days=row.get("days"),
            msg=row.get("msg"),
            thread=row.get("thread"),
        ))
    for row in snapshot.get("quiet", []):
        name = row.get("name") or ""
        out.append(Item(
            id=_sid(DERIVED_QUIET, name),
            kind=DERIVED_QUIET,
            title=name,
            detail=f"ball with {row.get('ball', '—')}",
            days=row.get("days"),
        ))
    return out


def build(snapshot: dict | None = None) -> list[Item]:
    """The list as it stands: derived items, manual items, state applied."""
    if snapshot is None:
        from .webconfig import read_snapshot

        snapshot = read_snapshot()

    state = _load_state()
    per_item = state.get("items", {})

    # Kiran's own list comes FIRST, because it is the real one. The dashboard's
    # first version showed only mail-derived items and called that "Today",
    # producing a second to-do list that disagreed with the one Kiran was
    # actually keeping. Wei found the two lists before anyone else did.
    items: list[Item] = []
    try:
        from . import todos as todos_mod

        for t in todos_mod.load():
            items.append(Item(
                id=t.id, kind=TODO, title=t.title, detail=t.detail,
                section=t.section, number=t.number, from_source=t.done,
            ))
    except Exception:  # noqa: BLE001 — a missing list must not break the page
        pass

    items += _derived_from_snapshot(snapshot)

    for raw in state.get("manual", []):
        items.append(Item(
            id=raw["id"], kind=MANUAL, title=raw.get("title", ""),
            detail=raw.get("detail", ""),
        ))

    visible: list[Item] = []
    for n, item in enumerate(items):
        st = per_item.get(item.id, {})
        item.bucket = st.get("bucket") or default_section(item.kind, item.section)
        if item.bucket not in SECTIONS:
            item.bucket = "backlog"
        # Ranks start spread out so the first few drags never need a rebalance.
        item.rank = st.get("rank", float(n) * 1000)
        item.priority = st.get("priority", 0)
        item.comments = st.get("comments", [])
        item.done = st.get("done", False)
        item.done_at = st.get("done_at")
        item.dismissed_at_signal = st.get("dismissed_at_signal")

        # Kiran writing **DONE** counts, and so does Wei ticking it. Either
        # alone is enough; neither silently undoes the other.
        if item.from_source:
            item.done = True

        if item.done and item.kind not in (MANUAL, TODO):
            # A dismissed derived item comes back when the situation moves.
            # `days` counts up from their last message, so a SMALLER number
            # than when it was dismissed means they have written again.
            since = st.get("dismissed_at_days")
            if since is not None and item.days is not None and item.days < since:
                item.done = False
                item.done_at = None
        visible.append(item)

    # Kiran's list first and in its own order — the numbering and the section
    # headings are meaningful and it wrote them deliberately. Mail-derived
    # items follow, oldest first.
    visible.sort(key=lambda i: (i.done, SECTIONS.index(i.bucket), i.rank))
    return visible


# The channels a reply can happen on. "other" is deliberate: the point is
# recording THAT it was handled, not building a taxonomy.
CHANNELS = ("email", "sms", "whatsapp", "linkedin", "phone", "in-person",
            "other")


def handle_owed(name: str, channel: str = "", note: str = "",
                snapshot: dict | None = None) -> str:
    """Mark a person's owed reply as handled — on any channel.

    Wei: the waiting list "flags any contact with a recent inbound email
    that has no outbound reply in the same thread" — but he replies on SMS,
    WhatsApp, LinkedIn and the phone too, and those replies are invisible
    to mail. People he had already answered kept surfacing for 70+ days.

    This is the existing dismissal (tick the row), given a memory: which
    channel, when, and a note. Same persistence, same revival rule — a
    NEWER message from the person brings them back, because a new message
    is newly owed whatever happened to the old one.
    """
    item = _owed_by_name(name, snapshot)
    channel = (channel or "").strip().lower()
    if channel and channel not in CHANNELS:
        channel = "other"
    act(item.id, "done", snapshot=snapshot)
    stamp = f"replied via {channel}" if channel else "handled elsewhere"
    if note.strip():
        stamp += f" — {note.strip()}"
    act(item.id, "comment", stamp, snapshot=snapshot)
    state = _load_state()
    entry = _touch(state, item.id)
    entry["handled_via"] = channel or "other"
    _save_state(state)
    return f"{item.title}: {stamp}. They will come back if they write again."


def reopen_owed(name: str, snapshot: dict | None = None) -> str:
    """Undo — they are still waiting after all."""
    item = _owed_by_name(name, snapshot, include_done=True)
    act(item.id, "undone", snapshot=snapshot)
    state = _load_state()
    _touch(state, item.id).pop("handled_via", None)
    _save_state(state)
    return f"{item.title} is back on the waiting list."


def _owed_by_name(name: str, snapshot: dict | None,
                  include_done: bool = False):
    """The one owed item a name means. Ambiguity is an error, not a guess —
    archiving the wrong person's thread is worse than asking."""
    needle = (name or "").strip().lower()
    if not needle:
        raise ValueError("Whose reply? Give me a name.")
    rows = [i for i in build(snapshot) if i.kind == DERIVED_OWED
            and (include_done or not i.done)]
    exact = [i for i in rows if i.title.lower() == needle]
    if len(exact) == 1:
        return exact[0]
    part = [i for i in rows if needle in i.title.lower()]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise ValueError(f"{name!r} matches several people: "
                         + "; ".join(i.title for i in part[:4])
                         + ". Say more of the name.")
    raise ValueError(f"Nobody on the waiting list matches {name!r}.")


def owed_overrides(rows: list[dict]) -> dict[str, dict]:
    """Which of these owed rows are handled, by the same rule build() uses.

    Keyed by the row's `who`. Presentation surfaces — who-is-waiting, the
    digest, instant answers, `cos owed` — call this so a reply on WhatsApp
    silences the nagging everywhere, not only on the Tasks panel.
    """
    state = _load_state()
    out: dict[str, dict] = {}
    for row in rows:
        who = row.get("who") or ""
        st = (state.get("items") or {}).get(_sid(DERIVED_OWED, who)) or {}
        if not st.get("done"):
            continue
        since = st.get("dismissed_at_days")
        days = row.get("days")
        if since is not None and days is not None and days < since:
            continue          # they wrote again — the dismissal has lapsed
        comments = st.get("comments") or []
        out[who] = {
            "at": (st.get("done_at") or "")[:10],
            "via": st.get("handled_via", ""),
            "note": comments[-1]["text"] if comments else "",
        }
    return out


@_serialised
def move(item_id: str, bucket: str, above: str | None = None,
         below: str | None = None, snapshot: dict | None = None) -> str:
    """Drop an item into a bucket, optionally between two others.

    `above` and `below` are the ids of the rows the item was dropped between.
    Named for position rather than "before"/"after", which read as opposites
    depending on whether you mean document order or sequence — an ambiguity
    that got the two halves of this function pointing in different directions.

    Rank is the midpoint of its new neighbours, so a drag rewrites one row.
    Renumbering the whole list on every drop would be correct too, but it makes
    every drag a write of everything and turns a stale tab into a reordering of
    items it has not seen.
    """
    if bucket not in SECTIONS:
        return f"unknown section {bucket!r}"

    items = build(snapshot)
    by_id = {i.id: i for i in items}
    if item_id not in by_id:
        return "unknown item"

    siblings = sorted([i for i in items if i.bucket == bucket and i.id != item_id],
                      key=lambda i: i.rank)
    up = by_id.get(above or "")
    down = by_id.get(below or "")

    if up is not None and down is not None:
        rank = (up.rank + down.rank) / 2
    elif down is not None:                      # nothing above it: goes first
        rank = down.rank - 1000
    elif up is not None:                        # nothing below it: goes last
        rank = up.rank + 1000
    else:
        rank = (siblings[-1].rank + 1000) if siblings else 0.0

    state = _load_state()
    entry = _touch(state, item_id)
    entry["bucket"] = bucket
    entry["rank"] = rank

    # Two neighbours can drift close enough that the midpoint stops separating
    # them. Rare, but silent when it happens — the item just refuses to stay
    # put — so respace the section instead of hoping.
    if up is not None and down is not None and abs(up.rank - down.rank) < 0.001:
        for n, sib in enumerate(siblings):
            _touch(state, sib.id)["rank"] = float(n) * 1000
        entry["rank"] = float(siblings.index(up) if up in siblings else 0) * 1000 + 500

    _save_state(state)
    return f"moved to {SECTION_LABELS[bucket]}"


def top(limit: int = 7, snapshot: dict | None = None) -> list[Item]:
    """The short list. Capped on purpose — a top list of thirty is a list."""
    return [i for i in build(snapshot) if not i.done][:limit]


# --------------------------------------------------------------------------
# Actions


def _touch(state: dict, item_id: str) -> dict:
    return state.setdefault("items", {}).setdefault(item_id, {})


@_serialised
def act(item_id: str, action: str, text: str = "", snapshot: dict | None = None) -> str:
    """Apply one change. Returns a short description of what happened."""
    state = _load_state()
    entry = _touch(state, item_id)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if action == "done":
        entry["done"] = True
        entry["done_at"] = now
        current = next((i for i in build(snapshot) if i.id == item_id), None)
        if current and current.days is not None:
            # Remember how stale it was, so a newer message can revive it.
            entry["dismissed_at_days"] = current.days
        result = "marked done"
    elif action == "undone":
        entry.pop("done", None)
        entry.pop("done_at", None)
        entry.pop("dismissed_at_days", None)
        result = "reopened"
    # promote/demote are deliberately gone. Order is set by dragging, and
    # keeping a second mechanism that no longer moves anything would be a
    # control that silently does nothing — the failure mode this project keeps
    # tripping over.
    elif action == "comment":
        if not text.strip():
            return "empty comment ignored"
        entry.setdefault("comments", []).append({"ts": now, "text": text.strip()})
        result = "comment added"
    else:
        return f"unknown action {action!r}"

    _save_state(state)
    return result


@_serialised
def add(title: str, detail: str = "") -> Item:
    if not title.strip():
        raise ValueError("An item needs a title.")
    state = _load_state()
    item = Item(
        id=_sid(MANUAL, f"{title}{time.time()}"),
        kind=MANUAL, title=title.strip(), detail=detail.strip(),
    )
    state.setdefault("manual", []).append(
        {"id": item.id, "title": item.title, "detail": item.detail,
         "added": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    )
    _save_state(state)
    return item


@_serialised
def remove(item_id: str) -> bool:
    """Only manual items can be removed. A derived one would just reappear on
    the next refresh, so 'done' is the right verb for those."""
    state = _load_state()
    before = len(state.get("manual", []))
    state["manual"] = [m for m in state.get("manual", []) if m["id"] != item_id]
    state.get("items", {}).pop(item_id, None)
    _save_state(state)
    return len(state["manual"]) < before


# --------------------------------------------------------------------------


def render(items: list[Item], now: datetime | None = None) -> str:
    now = now or datetime.now()
    lines = [
        "---", "title: Today", "generated_by: cos",
        f"generated_at: {now:%Y-%m-%d %H:%M}", "---", "",
        "# Today", "",
        "> Written by `cos`. Change things on the dashboard rather than here —",
        "> this file is rewritten on every refresh.", "",
    ]
    live = [i for i in items if not i.done]
    done = [i for i in items if i.done]

    if not live:
        lines.append("_Nothing outstanding._")
    for i in live:
        age = f" · {i.days}d" if i.days is not None else ""
        star = " ⭑" if i.priority > 0 else ""
        lines.append(f"- [ ] **{i.title}**{star}{age} — {i.detail}".rstrip(" —"))
        for c in i.comments:
            lines.append(f"      - _{c['ts'][:10]}_: {c['text']}")

    if done:
        lines += ["", "## Dealt with", ""]
        for i in done:
            lines.append(f"- [x] {i.title} — {i.detail}".rstrip(" —"))
            for c in i.comments:
                lines.append(f"      - _{c['ts'][:10]}_: {c['text']}")
    return "\n".join(lines) + "\n"


def write_page(vault_root: Path, items: list[Item]) -> Path:
    """Mirror the list into the vault so Obsidian and the agent can see it.

    The web page is not the system of record for anything durable — comments
    written here end up in git, indexed, and readable by the agent, which is
    the whole reason to put them somewhere other than a JSON file.
    """
    path = vault_root / "90_agent" / PAGE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(items), encoding="utf-8")
    return path
