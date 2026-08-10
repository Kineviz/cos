"""Per-person and per-org cards for `90_agent/`.

These replace the per-thread cards. The reason is arithmetic: thread cards grow
with *threads* (~6,600/year, ~17,500 over the corpus), entity cards grow with
*distinct counterparties* (a few hundred a year, and most of those already
exist). The vault holds 1,882 notes; 17,500 stubs would swamp Obsidian's index,
graph view and sync. ~2,500 entity cards do not, and they stop growing fast.

They live in the vault, not the brain repo, for one specific reason: gbrain
scopes basename link resolution per source on purpose (`extract.ts:1430`), so
only a page in the same source as `10_wiki/People` can `[[link]]` to it. Raw
message text stays in the brain repo — bulk does not belong in a curated vault.

Everything here is derived by rule. No model touches these files, so a card
cannot invent a participant, a date, or a relationship.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import re

from .identity import classify_address, is_freemail

_MAX_THREADS_LISTED = 12

# CJK ideographs, hiragana, katakana, and Hangul.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _gazetteer_safe_name(name: str | None) -> str | None:
    """A display name only becomes a card title if it reads as a person's name.

    gbrain builds its mention gazetteer from card titles, so whatever ends up
    here is matched against the prose of all 30k pages. Email display names are
    frequently a brand, a product or a bare word, and those match constantly:

      graphxr@kineviz.com  had the display name "graph"   → 1,294 links, in the
                                                            email of a *graph*
                                                            visualisation company
      hello@kineviz.com    had the display name "Kineviz" → 1,761 links, the
                                                            company's own name

    Requiring two tokens is the cheap discriminator. Real counterparties are
    "First Last"; brands and roles are one word. It also drops bare first names
    ("Steve"), which are genuine people but far too ambiguous to be a reliable
    link target — there is more than one Steve in twelve years of mail.

    CJK names are the exception: `杨明英` is one token by whitespace but is a
    full name and a precise, unambiguous match — the rule would otherwise
    discard every Chinese contact in the corpus for being "one word".

    The card is still written; only the *title* falls back to the address, so
    the person stays browsable without poisoning the graph.
    """
    if not name:
        return None
    if len(name.split()) >= 2:
        return name
    return name if _CJK.search(name) else None


def _fmt_date(dt: datetime | None) -> str:
    return f"{dt:%Y-%m-%d}" if dt else "—"


@dataclass
class EntityCardStats:
    people: int = 0
    orgs: int = 0
    people_linked_to_wiki: int = 0
    threads_referenced: int = 0


@dataclass
class _PersonAgg:
    """Everything known about one counterparty, assembled from the graph."""
    address: str
    name: str | None = None
    threads: list = field(default_factory=list)   # (thread, raw_page_name)

    @property
    def domain(self) -> str:
        return self.address.split("@")[-1]


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unknown"


def build_aggregates(
    written: list[tuple], principals: set[str]
) -> tuple[dict[str, _PersonAgg], dict[str, list[str]]]:
    """address -> aggregate, and domain -> [addresses]."""
    people: dict[str, _PersonAgg] = {}
    for thread, raw_name in written:
        for addr in thread.counterparties(principals):
            agg = people.setdefault(addr, _PersonAgg(address=addr))
            agg.threads.append((thread, raw_name))
            if agg.name is None:
                for m in thread.messages:
                    if m.sender == addr and m.sender_name:
                        agg.name = m.sender_name
                        break
    orgs: dict[str, list[str]] = defaultdict(list)
    for addr in people:
        if not is_freemail(addr):
            orgs[addr.split("@")[-1]].append(addr)
    return people, dict(orgs)


def _thread_lines(agg: _PersonAgg, principals: set[str]) -> list[str]:
    recent = sorted(agg.threads, key=lambda t: t[0].last, reverse=True)
    lines = []
    for thread, raw_name in recent[:_MAX_THREADS_LISTED]:
        last = thread.messages[-1].sender if thread.messages else ""
        ball = "them" if last in principals else "**you**"
        lines.append(
            f"- `{thread.last:%Y-%m-%d}` {thread.subject} — "
            f"{len(thread.messages)} msg, ball with {ball} · `email/{raw_name}`"
        )
    if len(recent) > _MAX_THREADS_LISTED:
        lines.append(f"- _…and {len(recent) - _MAX_THREADS_LISTED} older thread(s)_")
    return lines


def render_person(
    agg: _PersonAgg, principals: set[str], resolver, counterparty
) -> str:
    page = resolver.resolve(agg.address) if resolver else None
    # A resolved wiki page is curated by hand, so its title is trusted as-is.
    # An unresolved display name is not — see _gazetteer_safe_name.
    title = page.title if page else (_gazetteer_safe_name(agg.name) or agg.address)
    org = agg.domain if not is_freemail(agg.address) else None

    last_in = counterparty.last_inbound if counterparty else None
    last_out = counterparty.last_outbound if counterparty else None
    ball = "you" if (counterparty and counterparty.ball_in_our_court()) else "them"

    header = [
        "---",
        "type: person",
        f"title: {title}",
        f"address: {agg.address}",
        f"organization: {org or ''}",
        # Window-scoped: the ledger carries all-time counts but not a first
        # date, so this must not be labelled "first_contact" beside them.
        f"first_thread_in_window: {_fmt_date(min((t[0].start for t in agg.threads), default=None))}",
        f"last_inbound: {_fmt_date(last_in)}",
        f"last_outbound: {_fmt_date(last_out)}",
        f"threads: {len(agg.threads)}",
        f"messages_in: {counterparty.inbound_count if counterparty else 0}",
        f"messages_out: {counterparty.outbound_count if counterparty else 0}",
        f"awaiting_reply_from: {ball}",
        "source: gmail-mirror",
        "generated_by: cos",
        "---",
        "",
        f"# {title}",
        "",
    ]
    body = []
    if page:
        body.append(f"Profile: [[{page.slug}|{page.title}]]")
    else:
        body.append("_No `10_wiki/People` page yet._")
    if org:
        body.append(f"Organization: [[org-{_slug(org)}|{org}]]")
    body += [
        "",
        f"`{agg.address}` · {len(agg.threads)} thread(s) in the indexed window · "
        f"ball with **{ball}**.",
        "",
        "## Threads",
        "",
        *_thread_lines(agg, principals),
        "",
        "_Derived by `cos export-brain`. Message bodies live in the brain "
        "repo, not here. Regenerated on each run; safe to delete._",
        "",
    ]
    return "\n".join(header + body)


def render_org(
    domain: str,
    addresses: list[str],
    people: dict[str, _PersonAgg],
    principals: set[str],
    resolver,
    ledger: dict,
    deal_name: str | None,
) -> str:
    aggs = [people[a] for a in addresses]
    all_threads = {id(t[0]): t for a in aggs for t in a.threads}.values()
    last = max((t[0].last for t in all_threads), default=None)
    first = min((t[0].start for t in all_threads), default=None)

    person_lines = []
    for agg in sorted(aggs, key=lambda a: len(a.threads), reverse=True)[:40]:
        page = resolver.resolve(agg.address) if resolver else None
        label = page.title if page else (agg.name or agg.address)
        wiki = f" · [[{page.slug}|wiki]]" if page else ""
        person_lines.append(
            f"- [[person-{_slug(agg.address)}|{label}]] — "
            f"{len(agg.threads)} thread(s){wiki}"
        )

    header = [
        "---",
        "type: organization",
        f"title: {domain}",
        f"domain: {domain}",
        f"deal: {deal_name or ''}",
        f"people: {len(aggs)}",
        f"threads: {len(all_threads)}",
        f"first_contact: {_fmt_date(first)}",
        f"last_contact: {_fmt_date(last)}",
        "source: gmail-mirror",
        "generated_by: cos",
        "---",
        "",
        f"# {domain}",
        "",
    ]
    body = []
    if deal_name:
        body.append(f"Named deal in `Pipeline.md`: **{deal_name}**")
        body.append("")
    body += [
        f"{len(aggs)} known contact(s), {len(all_threads)} thread(s), "
        f"{_fmt_date(first)} → {_fmt_date(last)}.",
        "",
        "## People",
        "",
        *person_lines,
        "",
        "_Derived by `cos export-brain`. Regenerated on each run; safe to delete._",
        "",
    ]
    return "\n".join(header + body)


def write_entity_cards(
    written: list[tuple],
    principals: set[str],
    resolver,
    ledger: dict,
    out_root: Path,
    deal_domains: dict[str, list[str]],
    internal_domains: frozenset[str] = frozenset(),
    min_messages: int = 3,
) -> EntityCardStats:
    people, orgs = build_aggregates(written, principals)
    stats = EntityCardStats()

    domain_to_deal: dict[str, str] = {}
    for deal, domains in deal_domains.items():
        for d in domains:
            domain_to_deal[d] = deal

    people_dir = out_root / "People"
    orgs_dir = out_root / "Organizations"
    people_dir.mkdir(parents=True, exist_ok=True)
    orgs_dir.mkdir(parents=True, exist_ok=True)

    kept: dict[str, _PersonAgg] = {}
    for addr, agg in people.items():
        # `hello@`, `info@`, `noreply@` are not people. identity.py already
        # knows this — the card writer simply never asked, so role mailboxes
        # became "person" pages and then became mention targets: hello@ and
        # info@ alone accounted for 1,951 of 12,321 proposed graph links.
        #
        # Gate on `kind == "role"` specifically, NOT on `is_person`. The
        # classifier also returns "robot" for all-numeric local parts, which is
        # right for machine senders and wrong for QQ and 163: `5551234567@qq.com`
        # is how ~30 real Chinese counterparties in this corpus address mail.
        # `is_person` would have silently deleted every one of them.
        if classify_address(addr).kind == "role":
            continue
        cp = ledger.get(addr)
        # Two-way and non-trivial: a card for someone you exchanged one message
        # with in 2017 is noise in the vault and noise in the graph.
        if not cp or not cp.inbound_count or not cp.outbound_count:
            continue
        if cp.total_messages < min_messages:
            continue
        kept[addr] = agg
        (people_dir / f"person-{_slug(addr)}.md").write_text(
            render_person(agg, principals, resolver, cp), encoding="utf-8"
        )
        stats.people += 1
        stats.threads_referenced += len(agg.threads)
        if resolver and resolver.resolve(addr):
            stats.people_linked_to_wiki += 1

    for domain, addrs in orgs.items():
        # Your own company is not a counterparty organization.
        if domain in internal_domains:
            continue
        addrs = [a for a in addrs if a in kept]
        if not addrs:
            continue
        (orgs_dir / f"org-{_slug(domain)}.md").write_text(
            render_org(
                domain, addrs, kept, principals, resolver, ledger,
                domain_to_deal.get(domain),
            ),
            encoding="utf-8",
        )
        stats.orgs += 1
    return stats
