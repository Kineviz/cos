"""The counterparty ledger, built from Gmail instead of the Kuzu graph.

`cos owed` and `cos quiet` ask timestamp questions — who wrote last, and how
long since anyone spoke — over 90- and 30-day windows. They never needed twelve
years of history, which is what makes this cheap: one windowed pass over message
*headers*, no bodies, no graph, no nightly rebuild.

Three Kuzu queries collapse into it. `load_people`, `load_ledger` and
`attach_recent_subjects` each walked the graph separately; here a single sweep
fills names, directions, counts and the latest inbound subject together, because
every one of those facts is already in the headers being read.

Counts are window-scoped, not all-time. That is a real difference from the graph
version and it is deliberate: an all-time count needs a full-corpus scan, and
nothing in the reports uses it for more than colour.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses

from .contacts import (
    BULK_LABELS,
    CALENDAR_SUBJECT_PREFIXES,
    Counterparty,
    domain_name_ratios,
    utc_now,
)
from .identity import classify_address, domain_of

# Batch the header fetches. Gmail allows 100 per batch; 100 round-trips for a
# 180-day window instead of ~7,000 is the difference between a report that runs
# in seconds and one nobody waits for.
_BATCH = 40

# Gmail's ceiling is ~15,000 quota units per minute per user, and threads.get
# costs 10 units — so a 40-wide batch is 400 units. At one second per batch that
# is 24,000/min, which is how the first version discovered the limit as a 403
# a third of the way through. Two seconds puts it at ~12,000/min with margin.
_PACE_SECONDS = 2.0

# Rebuilding takes minutes, so reports read a cache and `cos sync` refreshes
# it. A stale ledger answers "who owes me a reply" wrongly, so freshness is
# reported rather than assumed.
CACHE_MAX_AGE_MINUTES = 60

# Gmail's categories are NOT used to filter, deliberately. They looked like the
# obvious equivalent of the mirror's label check, and they silently removed real
# correspondents: deb@knowledgegraph.tech (3 messages) sits in `updates`, and
# judson.t@block71.co in `promotions`. `owed` would simply stop mentioning
# people who were waiting on a reply.
#
# The mirror kept messages whose labels were NULL, which is most of them, so
# excluding categories was never equivalent anyway. Noise is removed instead by
# identity.classify_address — role/robot/bulk detection that the reports already
# apply via humans(), works the same for both sources, and is unit-tested.
_CATEGORY_EXCLUSIONS = ""

_HEADERS = ["From", "To", "Cc", "Subject", "Date"]

# Wider than the 90-day owed window so a report never sits at the edge of its
# own data, but far short of the corpus.
DEFAULT_WINDOW_DAYS = 180


class LedgerIncomplete(RuntimeError):
    """Raised rather than returning a ledger with holes in it."""


def _service():
    from googleapiclient.discovery import build

    from .google_auth import load_credentials

    return build(
        "gmail", "v1", credentials=load_credentials(interactive=False),
        cache_discovery=False,
    )


def _hdr(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "") or ""
    return ""


def build_ledger(
    principals: tuple[str, ...],
    days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, Counterparty]:
    """One row per address exchanged with, over the last `days`."""
    svc = _service()
    me = {p.lower() for p in principals}
    since = (now or utc_now()) - timedelta(days=days)
    query = f"after:{since:%Y/%m/%d} {_CATEGORY_EXCLUSIONS}".strip()  # noqa: E501

    # Threads, not messages. One threads.get returns every message's headers in
    # a single call, so a window costs ~3x fewer requests — which is the
    # difference between finishing and being refused by the per-minute quota.
    ids: list[str] = []
    token = None
    while True:
        resp = (
            svc.users()
            .threads()
            .list(userId="me", q=query, maxResults=500, pageToken=token)
            .execute()
        )
        ids.extend(t["id"] for t in resp.get("threads", []))
        token = resp.get("nextPageToken")
        if not token:
            break

    # (address, is_inbound, timestamp, subject, display name)
    events: list[tuple[str, bool, datetime, str, str | None]] = []
    failed: dict[str, str] = {}

    def _collect(req_id, response, exception):
        # NEVER swallow. An earlier version returned quietly here, and Gmail's
        # per-minute quota then dropped 247 counterparties out of the ledger
        # without a word — `owed` simply stopped mentioning people who were
        # waiting on a reply. Failures are recorded and retried; whatever still
        # fails is reported to the caller rather than silently missing.
        if exception is not None:
            failed[_pending[int(req_id)]] = type(exception).__name__
            return
        if not response:
            failed[_pending[int(req_id)]] = "empty response"
            return
        for _msg in response.get("messages", []):
            _ingest(_msg)

    def _ingest(response) -> None:
        headers = response.get("payload", {}).get("headers", [])
        # Kept so a reply can be threaded to this exact message later.
        msg_id = response.get("id") or ""
        subject = _hdr(headers, "Subject")
        if any(subject.startswith(p) for p in CALENDAR_SUBJECT_PREFIXES):
            return
        try:
            ts = datetime.fromtimestamp(
                int(response["internalDate"]) / 1000, tz=timezone.utc
            )
        except (KeyError, ValueError, TypeError):
            return

        from_name, from_addr = "", ""
        for name, addr in getaddresses([_hdr(headers, "From")]):
            from_name, from_addr = name, (addr or "").lower()
            break
        if not from_addr:
            return

        if from_addr in me:
            # Outbound: every non-self recipient is a counterparty.
            for name, addr in getaddresses(
                [_hdr(headers, "To"), _hdr(headers, "Cc")]
            ):
                a = (addr or "").lower()
                if a and a not in me:
                    events.append((a, False, ts, subject, name or None, msg_id))
        else:
            events.append((from_addr, True, ts, subject, from_name or None, msg_id))

    def _run(id_list: list[str]) -> None:
        """Fetch headers for these ids, pacing under Gmail's per-user quota.

        The limit is ~250 quota units/second/user and messages.get costs 5, so
        roughly 50/second. A 100-wide batch fired back-to-back bursts straight
        through it and came back 403 rateLimitExceeded.
        """
        for i in range(0, len(id_list), _BATCH):
            chunk = id_list[i : i + _BATCH]
            _pending.clear()
            batch = svc.new_batch_http_request(callback=_collect)
            for n, mid in enumerate(chunk):
                _pending[n] = mid
                batch.add(
                    svc.users().threads().get(
                        userId="me", id=mid, format="metadata",
                        metadataHeaders=_HEADERS,
                    ),
                    request_id=str(n),
                )
            batch.execute()
            if i + _BATCH < len(id_list):
                time.sleep(_PACE_SECONDS)

    _pending: dict[int, str] = {}
    _run(ids)

    # Retry whatever the quota refused, backing off each round.
    for attempt in range(1, 4):
        if not failed:
            break
        retry_ids = list(failed)
        failed.clear()
        time.sleep(_PACE_SECONDS * (2 ** attempt))
        _run(retry_ids)

    if failed:
        raise LedgerIncomplete(
            f"{len(failed)} of {len(ids)} threads could not be read after "
            f"retries (e.g. {next(iter(failed.values()))}). The ledger would be "
            f"missing counterparties, so it is not being returned — rerun in a "
            f"minute, or lower the window with --days."
        )

    names: dict[str, str | None] = {}
    for addr, _, _, _, name, _ in events:
        if name and not names.get(addr):
            names[addr] = name
    ratios = domain_name_ratios(names)

    ledger: dict[str, Counterparty] = {}
    for addr, inbound, ts, subject, _, msg_id in events:
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
                cp.last_inbound_subject = subject or "(no subject)"
                cp.last_inbound_id = msg_id
        else:
            cp.outbound_count += 1
            if cp.last_outbound is None or ts > cp.last_outbound:
                cp.last_outbound = ts

    return ledger


def corpus_freshness() -> datetime | None:
    """Timestamp of the newest message Gmail will show us.

    The graph version answered "how stale is the nightly rebuild". Against the
    API the honest answer is almost always "now", and this exists so
    `cos check` can prove that rather than assert it.
    """
    svc = _service()
    resp = svc.users().messages().list(userId="me", maxResults=1).execute()
    msgs = resp.get("messages", [])
    if not msgs:
        return None
    full = (
        svc.users()
        .messages()
        .get(userId="me", id=msgs[0]["id"], format="metadata", metadataHeaders=["Date"])
        .execute()
    )
    try:
        return datetime.fromtimestamp(
            int(full["internalDate"]) / 1000, tz=timezone.utc
        )
    except (KeyError, ValueError, TypeError):
        return None


# ── cache ────────────────────────────────────────────────────────────────────
#
# A full rebuild is minutes of API calls. Reports must not pay that, so the
# ledger is written once and read back until it goes stale. `cos sync`
# refreshes it; `cos owed` reads it.

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from .google_auth import CONFIG_DIR  # noqa: E402

CACHE_FILE: Path = CONFIG_DIR / "ledger-cache.json"


def save_cache(ledger: dict[str, Counterparty], window_days: int) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for cp in ledger.values():
        rows.append({
            "address": cp.address,
            "name": cp.name,
            "last_inbound": cp.last_inbound.isoformat() if cp.last_inbound else None,
            "last_outbound": cp.last_outbound.isoformat() if cp.last_outbound else None,
            "inbound_count": cp.inbound_count,
            "outbound_count": cp.outbound_count,
            "last_inbound_subject": cp.last_inbound_subject,
            "last_inbound_id": cp.last_inbound_id,
        })
    CACHE_FILE.write_text(json.dumps({
        "built_at": utc_now().isoformat(),
        "window_days": window_days,
        "rows": rows,
    }), encoding="utf-8")


def load_cache() -> tuple[dict[str, Counterparty], datetime] | None:
    """Ledger plus the moment it was built, or None when absent/unreadable."""
    if not CACHE_FILE.exists():
        return None
    try:
        blob = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        built = datetime.fromisoformat(blob["built_at"])
    except (ValueError, KeyError, OSError):
        return None

    names = {r["address"]: r.get("name") for r in blob.get("rows", [])}
    ratios = domain_name_ratios(names)
    out: dict[str, Counterparty] = {}
    for r in blob.get("rows", []):
        addr = r["address"]
        cp = Counterparty(
            address=addr,
            name=r.get("name"),
            verdict=classify_address(addr, ratios.get(domain_of(addr))),
        )
        for field in ("last_inbound", "last_outbound"):
            if r.get(field):
                setattr(cp, field, datetime.fromisoformat(r[field]))
        cp.inbound_count = r.get("inbound_count", 0)
        cp.outbound_count = r.get("outbound_count", 0)
        cp.last_inbound_subject = r.get("last_inbound_subject")
        cp.last_inbound_id = r.get("last_inbound_id")
        out[addr] = cp
    return out, built


def load_or_build(
    principals: tuple[str, ...],
    days: int = DEFAULT_WINDOW_DAYS,
    max_age_minutes: int = CACHE_MAX_AGE_MINUTES,
) -> tuple[dict[str, Counterparty], datetime]:
    """Cached ledger when it is fresh enough, otherwise a rebuild."""
    cached = load_cache()
    if cached is not None:
        ledger, built = cached
        age = (utc_now() - built).total_seconds() / 60
        if age <= max_age_minutes:
            return ledger, built
    ledger = build_ledger(principals, days=days)
    save_cache(ledger, days)
    return ledger, utc_now()
