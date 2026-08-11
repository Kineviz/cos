"""Tell Wei when something breaks, without becoming the reason he mutes the bot.

The refresh job runs every 15 minutes. A check that alerts whenever it is
failing would send **96 messages a day** for a single unfixed problem. The
predictable result is a muted bot, and a muted bot is strictly worse than no
monitoring at all: it looks like coverage while providing none.

So alerts fire on **transitions**, not on states:

    ok -> broken     alert once
    broken -> broken silent, until the cooldown expires
    broken -> ok     one recovery line, so a fixed thing is known to be fixed

The cooldown re-alerts a still-broken check once a day. Without it a failure
that breaks on Monday and is never fixed is silently forgotten by Tuesday,
which is how the vault went two days unnoticed in the first place.

State lives in ~/.cos/alert-state.json. If it is lost the worst case is one
duplicate alert, which is the right direction to fail in.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

STATE_FILE = Path.home() / ".cos" / "alert-state.json"
COOLDOWN_SECONDS = 24 * 3600

# Only these reach the phone between digests. WARN and UNKNOWN are real signals
# and belong in the daily digest, but they are not "wake him up" material — the
# whole point of the digest is that it is where non-urgent things go.
ALERTING_STATUSES = ("fail",)


@dataclass
class Decision:
    to_alert: list          # checks newly broken, or past cooldown
    recovered: list[str]    # names that were broken and now pass
    suppressed: int         # broken but within cooldown


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError:
        pass


def decide(checks, now: float | None = None) -> Decision:
    """Work out what is worth saying, and record what was said."""
    now = time.time() if now is None else now
    state = _load()
    to_alert, recovered = [], []
    suppressed = 0

    broken = {c.name: c for c in checks if c.status in ALERTING_STATUSES}
    for name, check in broken.items():
        # "Never alerted" is its own case, not a very old timestamp. Defaulting
        # to 0 and letting `now - 0 >= COOLDOWN` decide happens to work against
        # a real epoch clock and silently fails for any smaller value — the
        # kind of implicit assumption that holds until it doesn't.
        previous = state.get(name, {})
        last = previous.get("alerted_at")
        first_time = last is None
        if first_time or (now - last) >= COOLDOWN_SECONDS:
            to_alert.append(check)
            state[name] = {"alerted_at": now, "detail": check.detail}
        else:
            suppressed += 1

    # Recovered means PASSING, not "no longer in the failing set". A check
    # that was FAILing and degrades to UNKNOWN — brain unreachable, psql gone,
    # agent log unreadable — left `broken` and was announced to Wei's phone as
    # "🟢 Recovered". That is the exact inversion this module exists to
    # prevent, and it fires hardest during a real outage.
    passing = {c.name for c in checks if c.status == "ok"}
    for name in list(state):
        if name in passing:
            recovered.append(name)
            state.pop(name, None)

    _save(state)
    return Decision(to_alert=to_alert, recovered=recovered, suppressed=suppressed)


def format_alert(decision: Decision) -> str | None:
    """The message, or None when there is nothing worth sending."""
    parts: list[str] = []
    if decision.to_alert:
        parts.append("🔴 *cos — something is broken*")
        for c in decision.to_alert:
            parts.append(f"*{c.name}*: {c.detail}")
            if c.evidence:
                # Wide enough for a cause, not just a step name. 120 turned
                # "brief failed — LedgerIncomplete: 6 of 4226 threads…" into
                # "brief failed", which told Wei something broke and nothing
                # else.
                parts.append(f"  `{c.evidence[:300]}`")
    if decision.recovered:
        parts.append("🟢 *Recovered:* " + ", ".join(sorted(decision.recovered)))
    if not parts:
        return None
    parts.append("_Full picture: `cos health`_")
    return "\n".join(parts)


def run(send: bool = True) -> str | None:
    """Check, decide, and deliver. Returns the message sent, or None."""
    from . import digest, health

    decision = decide(health.run_all())
    message = format_alert(decision)
    if message and send:
        ok, detail = digest.send(message)
        if not ok:
            # An alerting system that fails silently is the joke version of
            # this module. Leave a trace where the refresh log will show it.
            return f"ALERT DELIVERY FAILED: {detail}\n{message}"
    return message
