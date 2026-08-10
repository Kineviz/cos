"""Write email threads into the brain as markdown pages.

This is the "code for data" half of gbrain's email pattern. The Gmail
integration recipe pulls from the Gmail API over OAuth; we don't need it —
the whole mailbox is already mirrored locally and spec §3.1 says to reuse that
mirror rather than reconnect to Gmail. So Kiran plays the deterministic
collector: it selects threads by rule, extracts novel text, and writes pages.
gbrain then does the judgment half — entities, links, synthesis.

Selection is deliberately conservative. Every page written costs embedding
time and, more importantly, dilutes retrieval. A thread only becomes a page if
a human was on the other end.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .contacts import _not_bulk, _parse_ts
from .wiki_people import (
    AddressIndex,
    PersonPage,
    build_address_index,
    build_name_index,
    load_people_pages,
    resolve_by_name,
)
from .identity import classify_address, is_freemail
from .kuzu import KuzuClient
from .mailtext import read_message_body
from .entity_cards import write_entity_cards

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_RE_PREFIX = re.compile(r"^\s*((re|fwd?|fw|aw|sv|vs|antw)\s*(\[\d+\])?\s*:\s*)+", re.I)


def slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "untitled"


def normalize_subject(subject: str) -> str:
    return _RE_PREFIX.sub("", (subject or "").strip()) or "(no subject)"


@dataclass
class Message:
    msg_id: str
    sender: str
    sender_name: str | None
    timestamp: datetime
    subject: str
    maildir_path: str
    recipients: list[str] = field(default_factory=list)


@dataclass
class Thread:
    thread_id: str
    messages: list[Message] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return normalize_subject(self.messages[0].subject if self.messages else "")

    @property
    def start(self) -> datetime:
        return min(m.timestamp for m in self.messages)

    @property
    def last(self) -> datetime:
        return max(m.timestamp for m in self.messages)

    def counterparties(self, principals: set[str]) -> list[str]:
        out: list[str] = []
        for m in self.messages:
            for addr in [m.sender, *m.recipients]:
                a = addr.lower()
                if a and a not in principals and a not in out:
                    out.append(a)
        return out


def _quote(values) -> str:
    return "[" + ", ".join("'" + str(v).replace("'", "\\'") + "'" for v in values) + "]"


def load_threads(
    client, principals: tuple[str, ...], since: datetime
) -> dict[str, Thread]:
    """Every non-bulk message since `since`, grouped by thread."""
    if getattr(client, "is_gmail", False):
        return client.source().load_threads(principals, since)
    me = _quote(principals)
    rows = client.query(
        f"MATCH (p:Person)-[:SENT]->(e:Email) "
        f"WHERE e.timestamp > timestamp('{since:%Y-%m-%d}') AND {_not_bulk('e')} "
        f"RETURN e.id AS id, e.thread_id AS tid, e.subject AS subject, "
        f"e.timestamp AS ts, e.maildir_path AS path, p.id AS sender, p.name AS sname"
    )
    threads: dict[str, Thread] = {}
    by_id: dict[str, Message] = {}
    for r in rows:
        ts = _parse_ts(r.get("ts"))
        tid, mid = r.get("tid"), r.get("id")
        if not ts or not tid or not mid or not r.get("path"):
            continue
        msg = Message(
            msg_id=mid,
            sender=(r.get("sender") or "").lower(),
            sender_name=r.get("sname"),
            timestamp=ts,
            subject=r.get("subject") or "",
            maildir_path=r["path"],
        )
        threads.setdefault(tid, Thread(thread_id=tid)).messages.append(msg)
        by_id[mid] = msg

    recips = client.query(
        f"MATCH (e:Email)-[:RECEIVED]->(p:Person) "
        f"WHERE e.timestamp > timestamp('{since:%Y-%m-%d}') AND {_not_bulk('e')} "
        f"RETURN e.id AS id, p.id AS addr"
    )
    for r in recips:
        msg = by_id.get(r.get("id"))
        if msg and r.get("addr"):
            msg.recipients.append(r["addr"].lower())

    for t in threads.values():
        t.messages.sort(key=lambda m: m.timestamp)
    return threads


def is_worth_a_page(thread: Thread, principals: set[str]) -> tuple[bool, str]:
    """A thread earns a page only if a human was on the other end."""
    others = thread.counterparties(principals)
    if not others:
        return False, "no counterparty (self-only)"
    if not any(classify_address(a).is_person for a in others):
        return False, "all counterparties are robot/role addresses"
    if not any(m.sender in principals for m in thread.messages) and len(
        thread.messages
    ) < 2:
        return False, "single inbound message, never replied to"
    return True, ""


def thread_to_markdown(
    thread: Thread,
    principals: set[str],
    gmail_root: Path,
    resolver: "PeopleResolver | None" = None,
    reader=None,
) -> str | None:
    """Render a thread as a brain page. None if nothing survives cleaning."""
    parts, total_chars = [], 0
    for m in thread.messages:
        # A bounce notice or calendar robot can land inside an otherwise human
        # thread. The thread still earns a page; the robot's message does not
        # belong in it.
        if m.sender not in principals and not classify_address(m.sender).is_person:
            continue
        # Bodies come from wherever the backend keeps them: a maildir file on
        # the mirror path, the message's own raw bytes over the API. Both land
        # in the same parser, so the page — and its content hash — is identical.
        if reader is not None:
            text = reader(m)
        else:
            text = read_message_body(gmail_root / m.maildir_path)
        if text.is_empty:
            continue
        who = m.sender_name or m.sender
        direction = "→ sent" if m.sender in principals else "← received"
        parts.append(
            f"### {m.timestamp:%Y-%m-%d %H:%M} · {who} {direction}\n\n{text.body}"
        )
        total_chars += len(text.body)
    if not parts or total_chars < 40:
        return None

    others = [a for a in thread.counterparties(principals) if classify_address(a).is_person]
    orgs = sorted({a.split("@")[1] for a in others if not is_freemail(a)})

    # gbrain has no `[person: x]` syntax — link-extraction.ts matches markdown
    # links, `[[dir/slug]]`, and generic `[[bare-name]]` (resolved by basename
    # because link_resolution.global_basename is on). Emitting anything else
    # produces a page with zero edges, which is exactly what happened first
    # time round.
    lines, linked, unlinked = [], 0, 0
    for addr in others[:20]:
        page = resolver.resolve(addr) if resolver else None
        if page is not None:
            lines.append(f"  - [[{page.slug}|{page.title}]] — `{addr}`")
            linked += 1
        else:
            lines.append(f"  - `{addr}`")
            unlinked += 1
    people_links = "\n" + "\n".join(lines) if lines else ""

    header = "\n".join(
        [
            "---",
            "type: email",
            f"title: {thread.subject}",
            f"date: {thread.last:%Y-%m-%d}",
            f"thread_id: {thread.thread_id}",
            f"first_message: {thread.start:%Y-%m-%d}",
            f"last_message: {thread.last:%Y-%m-%d}",
            f"message_count: {len(thread.messages)}",
            f"organizations: [{', '.join(orgs)}]" if orgs else "organizations: []",
            "source: gmail-mirror",
            "generated_by: cos",
            "---",
            "",
            f"# {thread.subject}",
            "",
            f"Email thread, {thread.start:%Y-%m-%d} → {thread.last:%Y-%m-%d}, "
            f"{len(thread.messages)} message(s).",
            "",
            "Participants:" + (people_links or " (none resolved)"),
            "",
            "---",
            "",
        ]
    )
    return header + "\n\n".join(parts) + "\n"


class PeopleResolver:
    """Address claim first, exact unique display name second, nothing third.

    Tier order matters: an address a page explicitly claims is direct evidence,
    a matching name is only corroboration. Never fuzzy-match — the review
    traced the vault's worst identity bug to substring name matching.
    """

    def __init__(self, vault_root: Path) -> None:
        pages = load_people_pages(vault_root)
        self._addr: AddressIndex = build_address_index(pages)
        self._name = build_name_index(pages)
        self._graph_names: dict[str, str] = {}
        self.stats = {"by_address": 0, "by_name": 0, "unresolved": 0}

    def learn_names(self, threads: dict[str, "Thread"]) -> None:
        """Remember each address's display name as seen in the mail graph."""
        for t in threads.values():
            for m in t.messages:
                if m.sender_name and m.sender not in self._graph_names:
                    self._graph_names[m.sender] = m.sender_name

    def resolve(self, address: str) -> PersonPage | None:
        page = self._addr.resolve(address)
        if page is not None:
            self.stats["by_address"] += 1
            return page
        page = resolve_by_name(self._graph_names.get(address), self._name)
        if page is not None:
            self.stats["by_name"] += 1
            return page
        self.stats["unresolved"] += 1
        return None

    @property
    def conflicts(self) -> dict[str, list[str]]:
        return self._addr.conflicts


@dataclass
class ExportStats:
    threads_seen: int = 0
    pages_written: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    resolution: dict[str, int] = field(default_factory=dict)
    cards_written: int = 0
    people_cards: int = 0
    org_cards: int = 0
    card_links: int = 0
    conflicts: dict[str, list[str]] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1


def export(
    client,
    principals: tuple[str, ...],
    since: datetime,
    out_dir: Path,
    gmail_root: Path,
    vault_root: Path | None = None,
    cards_dir: Path | None = None,
    contacts_ledger: dict | None = None,
    deal_domains: dict | None = None,
    internal_domains: frozenset | None = None,
    limit: int | None = None,
) -> ExportStats:
    principal_set = {p.lower() for p in principals}
    # On the Gmail backend the body comes from the message's own raw bytes,
    # which load_threads has already fetched and cached; on the mirror it comes
    # off disk. Same parser either way.
    reader = None
    if getattr(client, "is_gmail", False):
        src = client.source()
        reader = lambda m: src.body(m)  # noqa: E731

    threads = load_threads(client, principals, since)
    resolver = PeopleResolver(vault_root) if vault_root else None
    if resolver:
        resolver.learn_names(threads)
    stats = ExportStats(threads_seen=len(threads))
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_names: set[str] = set()
    written: list[tuple] = []

    for thread in sorted(threads.values(), key=lambda t: t.last, reverse=True):
        if limit and stats.pages_written >= limit:
            break
        ok, reason = is_worth_a_page(thread, principal_set)
        if not ok:
            stats.skip(reason)
            continue
        body = thread_to_markdown(thread, principal_set, gmail_root, resolver, reader=reader)
        if body is None:
            stats.skip("no text survived quote-stripping")
            continue

        name = f"{thread.last:%Y-%m-%d}-{slugify(thread.subject)}"
        if name in seen_names:  # same subject, same day, different thread
            name = f"{name}-{thread.thread_id[-6:]}"
        seen_names.add(name)

        (out_dir / f"{name}.md").write_text(body, encoding="utf-8")
        written.append((thread, name))
        stats.pages_written += 1
    if resolver:
        stats.resolution = dict(resolver.stats)
        stats.conflicts = resolver.conflicts
    if cards_dir is not None and resolver is not None:
        ledger = {k: v for k, v in (contacts_ledger or {}).items()}
        cs = write_entity_cards(
            written, principal_set, resolver, ledger, cards_dir,
            deal_domains or {},
            internal_domains or frozenset(),
        )
        stats.cards_written = cs.people + cs.orgs
        stats.people_cards = cs.people
        stats.org_cards = cs.orgs
        stats.card_links = cs.people_linked_to_wiki
    return stats
