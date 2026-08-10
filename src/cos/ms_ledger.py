"""The mail ledger, built from Microsoft Graph.

Produces exactly what `gmail_ledger.build_ledger` produces — one
`Counterparty` per address exchanged with, with the id of their newest
inbound message — and stores it in the same cache file, so everything
above the ledger (owed, quiet deals, the prospects overlay, drafting's
person lookup) works unchanged with no idea Microsoft is underneath.

Only one backend is ever active on a machine (see `backend.py`), so
sharing the cache file is not a conflict; it is the point.

Simpler than the Gmail version by an order of magnitude, because Graph
returns sender and recipients as structured fields on the message list
call — no batched header fetches, no quota dance, no getaddresses.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .contacts import Counterparty, domain_name_ratios, utc_now
from .gmail_ledger import (CALENDAR_SUBJECT_PREFIXES, DEFAULT_WINDOW_DAYS,
                           save_cache)
from .identity import classify_address, domain_of


def _addr(entry: dict) -> tuple[str, str]:
    ea = (entry or {}).get("emailAddress") or {}
    return (ea.get("name") or "", (ea.get("address") or "").lower())


def _when(msg: dict) -> datetime | None:
    raw = (msg.get("receivedDateTime") or "").replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def build_ledger(principals: tuple[str, ...],
                 days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Counterparty]:
    """One row per address exchanged with, over the last `days`."""
    from . import msgraph

    me = {p.lower() for p in principals if p}
    if not me:
        me = {msgraph.profile()["address"]}

    # (address, is_inbound, timestamp, subject, display name, message id)
    events: list[tuple[str, bool, datetime, str, str | None, str]] = []
    for msg in msgraph.messages(days=days):
        if msg.get("isDraft"):
            continue
        subject = msg.get("subject") or ""
        if any(subject.startswith(p) for p in CALENDAR_SUBJECT_PREFIXES):
            continue
        ts = _when(msg)
        if ts is None:
            continue
        from_name, from_addr = _addr(msg.get("from") or {})
        if not from_addr:
            continue
        mid = msg.get("id") or ""
        if from_addr in me:
            for r in (msg.get("toRecipients") or []) + \
                     (msg.get("ccRecipients") or []):
                name, a = _addr(r)
                if a and a not in me:
                    events.append((a, False, ts, subject, name or None, mid))
        else:
            events.append((from_addr, True, ts, subject,
                           from_name or None, mid))

    names: dict[str, str | None] = {}
    for addr, _, _, _, name, _ in events:
        if name and not names.get(addr):
            names[addr] = name
    ratios = domain_name_ratios(names)

    ledger: dict[str, Counterparty] = {}
    for addr, inbound, ts, subject, _name, mid in events:
        cp = ledger.get(addr)
        if cp is None:
            cp = Counterparty(
                address=addr,
                name=names.get(addr),
                verdict=classify_address(addr, ratios.get(domain_of(addr))),
            )
            ledger[addr] = cp
        if inbound:
            cp.inbound_count += 1
            if cp.last_inbound is None or ts > cp.last_inbound:
                cp.last_inbound = ts
                cp.last_inbound_subject = subject
                cp.last_inbound_id = mid
        else:
            cp.outbound_count += 1
            if cp.last_outbound is None or ts > cp.last_outbound:
                cp.last_outbound = ts
    return ledger


def load_or_build(principals: tuple[str, ...],
                  days: int = DEFAULT_WINDOW_DAYS,
                  max_age_minutes: int = 20) -> tuple[dict, datetime]:
    """Cached ledger when fresh, otherwise a rebuild — same contract and
    same cache file as the Gmail version."""
    from .gmail_ledger import load_cache

    cached = load_cache()
    if cached is not None:
        ledger, built = cached
        age = (utc_now() - built).total_seconds() / 60
        if age <= max_age_minutes:
            return ledger, built
    ledger = build_ledger(principals, days=days)
    save_cache(ledger, days)
    return ledger, utc_now()
