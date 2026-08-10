"""Build the counterparty ledger from the Gmail graph.

Everything here works at *counterparty* level (last inbound vs last outbound
per address), never at thread level. That is a deliberate design choice, not a
simplification: 8,868 of 11,526 emails from 2026 carry a NULL `thread_id`,
because Gmail thread ids came from the original mbox export and IMAP-synced
mail does not have them. Any thread-based logic would go blind in exactly the
window a chief-of-staff cares about. Last-in-vs-last-out needs no thread id and
is exact.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .identity import AddressVerdict, classify_address, domain_of, is_freemail
from .kuzu import KuzuClient


def _quote_list(values: tuple[str, ...] | list[str]) -> str:
    escaped = [v.replace("'", "\\'") for v in values]
    return "[" + ", ".join(f"'{v}'" for v in escaped) + "]"


# Gmail has already classified this mail; there is no reason to re-derive it.
# `Category Updates` is excluded too — a shipping notification is not a
# conversation you can owe a reply to.
BULK_LABELS = (
    "Spam",
    "Trash",
    "Category Promotions",
    "Category Social",
    "Category Forums",
    "Category Updates",
)


# Google Calendar's auto-generated mail. These arrive *from* a real colleague's
# address but nobody wrote them, so they are not contact and you cannot owe a
# reply to one. 220 of these landed in the last three months alone.
CALENDAR_SUBJECT_PREFIXES = (
    "Canceled: ",
    "Canceled event: ",
    "Cancelled: ",
    "Invitation: ",
    "Updated invitation",
    "Accepted: ",
    "Declined: ",
    "Tentative: ",
    "Notification: ",
    "Reminder: ",
)


def _not_bulk(alias: str) -> str:
    """Cypher predicate excluding Gmail's own bulk categories and calendar
    robot mail.

    NULL labels are kept: `CONTAINS` on NULL yields NULL, which would silently
    drop the message, and unlabelled mail is mostly ordinary correspondence.
    """
    label_clauses = " AND ".join(
        f"NOT {alias}.gmail_labels CONTAINS '{label}'" for label in BULK_LABELS
    )
    subject_clauses = " AND ".join(
        f"NOT {alias}.subject STARTS WITH '{p}'" for p in CALENDAR_SUBJECT_PREFIXES
    )
    return (
        f"({alias}.gmail_labels IS NULL OR ({label_clauses})) "
        f"AND ({alias}.subject IS NULL OR ({subject_clauses}))"
    )


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a Kuzu TIMESTAMP as UTC-aware.

    Kuzu stores these naive but they are UTC: a message whose Date header reads
    `21:30:02 -0700` is stored as `2026-08-02T04:30:02`. Comparing that against
    a naive local `datetime.now()` skews every elapsed-time figure by the UTC
    offset — 7 hours in PDT, enough to move a threshold across a day boundary
    and to produce negative wait times for mail that just arrived.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utc_now() -> datetime:
    """The `now` every report must be computed against."""
    return datetime.now(timezone.utc)


@dataclass
class Counterparty:
    address: str
    name: str | None
    verdict: AddressVerdict
    last_inbound: datetime | None = None
    last_outbound: datetime | None = None
    inbound_count: int = 0
    outbound_count: int = 0
    last_inbound_subject: str | None = None
    # The Gmail id of that newest inbound message. Carried so a reply can be
    # threaded to the right email — `draft_broker` derives every address, the
    # subject and the References headers from the source message precisely so
    # that no addressee ever comes from model output.
    last_inbound_id: str | None = None
    # The Gmail THREAD id. The local mirror stores RFC Message-IDs rather than
    # Gmail API message ids, so on that path this is what identifies the
    # conversation, and the API message to reply to is resolved from it at
    # drafting time.
    last_inbound_thread: str | None = None

    @property
    def domain(self) -> str:
        return domain_of(self.address)

    @property
    def last_contact(self) -> datetime | None:
        stamps = [t for t in (self.last_inbound, self.last_outbound) if t]
        return max(stamps) if stamps else None

    @property
    def total_messages(self) -> int:
        return self.inbound_count + self.outbound_count

    def days_since_contact(self, now: datetime) -> int | None:
        last = self.last_contact
        return (now - last).days if last else None

    def ball_in_our_court(self) -> bool:
        """They wrote last and we have not answered."""
        if self.last_inbound is None:
            return False
        return self.last_outbound is None or self.last_inbound > self.last_outbound

    def days_waiting(self, now: datetime) -> int | None:
        return (now - self.last_inbound).days if self.ball_in_our_court() else None


def load_people(client: KuzuClient) -> dict[str, str | None]:
    rows = client.query("MATCH (p:Person) RETURN p.id AS id, p.name AS name")
    return {r["id"].lower(): r["name"] for r in rows if r.get("id")}


def domain_name_ratios(people: dict[str, str | None]) -> dict[str, float]:
    """Distinct display names per distinct address, per domain.

    A domain with many more names than addresses is a relay or a shared
    mailbox — `info@` carrying 141 different display names, for instance.
    """
    names: dict[str, set[str]] = defaultdict(set)
    addrs: dict[str, set[str]] = defaultdict(set)
    for addr, name in people.items():
        d = domain_of(addr)
        if not d:
            continue
        addrs[d].add(addr)
        if name:
            names[d].add(name.strip().lower())
    return {
        d: (len(names[d]) / len(addrs[d]) if addrs[d] else 0.0) for d in addrs
    }


def load_ledger(client, principals: tuple[str, ...]) -> dict[str, Counterparty]:
    """One row per address the principal has exchanged mail with."""
    # Gmail backend: the ledger is built (and cached) from message headers.
    if getattr(client, "is_gmail", False):
        return client.ledger()
    people = load_people(client)
    ratios = domain_name_ratios(people)
    me = _quote_list(principals)

    inbound = client.query(
        f"MATCH (p:Person)-[:SENT]->(e:Email)-[:RECEIVED]->(me:Person) "
        f"WHERE me.id IN {me} AND NOT p.id IN {me} AND {_not_bulk('e')} "
        f"RETURN p.id AS addr, max(e.timestamp) AS last_ts, count(e) AS n"
    )
    outbound = client.query(
        f"MATCH (me:Person)-[:SENT]->(e:Email)-[:RECEIVED]->(p:Person) "
        f"WHERE me.id IN {me} AND NOT p.id IN {me} AND {_not_bulk('e')} "
        f"RETURN p.id AS addr, max(e.timestamp) AS last_ts, count(e) AS n"
    )

    ledger: dict[str, Counterparty] = {}

    def _get(addr: str) -> Counterparty:
        key = addr.lower()
        if key not in ledger:
            ledger[key] = Counterparty(
                address=key,
                name=people.get(key),
                verdict=classify_address(key, ratios.get(domain_of(key))),
            )
        return ledger[key]

    for row in inbound:
        if not row.get("addr"):
            continue
        cp = _get(row["addr"])
        cp.last_inbound = _parse_ts(row.get("last_ts"))
        cp.inbound_count = int(row.get("n") or 0)

    for row in outbound:
        if not row.get("addr"):
            continue
        cp = _get(row["addr"])
        cp.last_outbound = _parse_ts(row.get("last_ts"))
        cp.outbound_count = int(row.get("n") or 0)

    return ledger


def attach_recent_subjects(
    client,
    ledger: dict[str, Counterparty],
    principals: tuple[str, ...],
    since: datetime,
) -> None:
    """Fill in the subject of the most recent inbound message per address.

    Bounded to the report window so this stays one small query rather than a
    join over 165k emails.

    No-op on the Gmail backend: that sweep reads every header once and fills
    the subject at the same time, so there is nothing left to attach.
    """
    if getattr(client, "is_gmail", False):
        return
    me = _quote_list(principals)
    rows = client.query(
        f"MATCH (p:Person)-[:SENT]->(e:Email)-[:RECEIVED]->(me:Person) "
        f"WHERE me.id IN {me} AND NOT p.id IN {me} AND {_not_bulk('e')} "
        f"AND e.timestamp > timestamp('{since.strftime('%Y-%m-%d')}') "
        f"RETURN p.id AS addr, e.timestamp AS ts, e.subject AS subject, "
        f"e.thread_id AS thread"
    )
    newest: dict[str, tuple[datetime, str, str | None]] = {}
    for row in rows:
        addr = (row.get("addr") or "").lower()
        ts = _parse_ts(row.get("ts"))
        if not addr or not ts:
            continue
        if addr not in newest or ts > newest[addr][0]:
            newest[addr] = (ts, row.get("subject") or "(no subject)",
                            row.get("thread"))
    for addr, (_, subject, thread) in newest.items():
        if addr in ledger:
            ledger[addr].last_inbound_subject = subject
            ledger[addr].last_inbound_thread = thread


def humans(ledger: dict[str, Counterparty]) -> list[Counterparty]:
    return [cp for cp in ledger.values() if cp.verdict.is_person]


def organizations(ledger: dict[str, Counterparty]) -> dict[str, list[Counterparty]]:
    """Group human counterparties by employer domain, excluding freemail."""
    orgs: dict[str, list[Counterparty]] = defaultdict(list)
    for cp in humans(ledger):
        if is_freemail(cp.address):
            continue
        orgs[cp.domain].append(cp)
    return dict(orgs)


def corpus_freshness(client) -> datetime | None:
    if getattr(client, "is_gmail", False):
        return client.freshness()
    rows = client.query("MATCH (e:Email) RETURN max(e.timestamp) AS newest")
    return _parse_ts(rows[0]["newest"]) if rows else None


def window_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
