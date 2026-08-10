"""The two Stage 1 reports.

Both are pure functions over the ledger plus the vault's deal list. No model,
no network beyond the graph read, no writes. Both are allowed to come back
empty — a report that is sometimes empty is believed when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contacts import Counterparty, humans
from .vault import Deal


@dataclass
class DealStatus:
    deal: Deal
    last_inbound: datetime | None
    last_outbound: datetime | None
    last_inbound_from: str | None
    last_inbound_subject: str | None
    contacts_seen: int

    @property
    def mapped(self) -> bool:
        return bool(self.deal.domains)

    @property
    def last_contact(self) -> datetime | None:
        stamps = [t for t in (self.last_inbound, self.last_outbound) if t]
        return max(stamps) if stamps else None

    def days_quiet(self, now: datetime) -> int | None:
        last = self.last_contact
        return (now - last).days if last else None

    def ball_in_our_court(self) -> bool:
        if self.last_inbound is None:
            return False
        return self.last_outbound is None or self.last_inbound > self.last_outbound


def deal_status(
    deals: list[Deal], ledger: dict[str, Counterparty], now: datetime
) -> list[DealStatus]:
    """Per-deal last contact, counting only human correspondents.

    Robot mail is excluded on purpose: "Google went quiet" must not be
    contradicted by a Drive share notification, and it must not be *masked* by
    one either.
    """
    people = humans(ledger)
    out: list[DealStatus] = []

    for deal in deals:
        wanted = set(deal.domains)
        matched = [cp for cp in people if cp.domain in wanted] if wanted else []

        inbound = [cp.last_inbound for cp in matched if cp.last_inbound]
        outbound = [cp.last_outbound for cp in matched if cp.last_outbound]
        last_in = max(inbound) if inbound else None
        last_out = max(outbound) if outbound else None

        from_addr = subject = None
        if last_in is not None:
            newest = max(
                (cp for cp in matched if cp.last_inbound == last_in),
                key=lambda c: c.total_messages,
            )
            from_addr = newest.name or newest.address
            subject = newest.last_inbound_subject

        out.append(
            DealStatus(
                deal=deal,
                last_inbound=last_in,
                last_outbound=last_out,
                last_inbound_from=from_addr,
                last_inbound_subject=subject,
                contacts_seen=len(matched),
            )
        )

    # Quietest first; unmapped deals last, since there is nothing to act on.
    def sort_key(s: DealStatus) -> tuple[int, float]:
        if not s.mapped:
            return (2, 0.0)
        days = s.days_quiet(now)
        return (0, -float(days)) if days is not None else (1, 0.0)

    return sorted(out, key=sort_key)


@dataclass
class OwedReply:
    counterparty: Counterparty
    days_waiting: int

    @property
    def who(self) -> str:
        cp = self.counterparty
        return cp.name or cp.address

    @property
    def subject(self) -> str:
        return self.counterparty.last_inbound_subject or "(no subject)"


def owed_replies(
    ledger: dict[str, Counterparty],
    now: datetime,
    window_days: int,
    internal_domains: frozenset[str] = frozenset(),
    require_prior_reply: bool = True,
) -> list[OwedReply]:
    """People who wrote to us last and have not had an answer.

    `require_prior_reply` is the single most important quality control here.
    You can only owe a reply to someone you have actually corresponded with;
    demanding at least one outbound message removes essentially all newsletter
    and cold-outreach noise without needing a classifier for it. Pass False to
    see genuinely new inbound as well, and accept the noise that comes with it.
    """
    out: list[OwedReply] = []
    for cp in humans(ledger):
        if cp.domain in internal_domains:
            continue
        if require_prior_reply and cp.outbound_count < 1:
            continue
        if cp.total_messages < 2:
            continue
        waiting = cp.days_waiting(now)
        if waiting is None or waiting > window_days:
            continue
        out.append(OwedReply(counterparty=cp, days_waiting=waiting))
    return sorted(out, key=lambda o: o.days_waiting, reverse=True)
