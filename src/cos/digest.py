"""The daily message to Telegram.

**This is the sixth scheduled artifact in this vault, and five of the previous
five are dead.** A weekly wiki lint ran 13 times with zero findings actioned
and its own log recorded *"identical to 07-24; nothing touched."* There is an
ingest-backlog notifier, a Monday pipeline digest, a task dashboard and a
Friday operating-plan review in the same condition. The bottleneck was never
producing output.

So the shape here is deliberate and worth defending:

**It leads with what is useful, not with what is working.** A daily message
that says "all 11 checks pass" trains you to swipe it away, and once you are
swiping you will also swipe the one that says the vault has not committed in
two days. So the top of the message is your day — meetings, who is waiting —
which is worth reading even when nothing is broken. Health appears only when
something is wrong, and when it does it is at the top.

**It is short.** Telegram, on a phone, once a day. Everything is capped. The
full detail already lives in `90_agent/today.md` and `cos health`.

**It can be empty of alarm but never empty of content.** If there is genuinely
nothing — no meetings, nobody waiting, nothing broken — it says so in one line
rather than sending a blank frame.

Delivery is `hermes send`, which reuses the gateway's Telegram credentials and
runs no model and no agent loop. The digest is assembled by code here; the
agent is not asked to write it. A summary that a model paraphrases is a summary
that can be wrong in a way you cannot see.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

HERMES_PY = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
MAX_MEETINGS = 6
MAX_OWED = 6
MAX_QUIET = 5


def _fmt_meetings(upcoming, now) -> list[str]:
    if not upcoming:
        return []
    today = now.astimezone().date()
    todays = [e for e in upcoming if e.start.astimezone().date() == today]
    if not todays:
        return ["*Today* — nothing scheduled."]
    out = ["*Today*"]
    for e in todays[:MAX_MEETINGS]:
        when = f"{e.start.astimezone():%H:%M}"
        flag = "" if e.status == "confirmed" else f" _({e.status})_"
        out.append(f"  {when}  {e.summary}{flag}")
    if len(todays) > MAX_MEETINGS:
        out.append(f"  _+{len(todays) - MAX_MEETINGS} more_")
    return out


def _fmt_owed(owed) -> list[str]:
    if not owed:
        return ["*Waiting on you* — nobody."]
    out = [f"*Waiting on you* ({len(owed)})"]
    for i in owed[:MAX_OWED]:
        who = i.who or i.counterparty.address
        out.append(f"  {i.days_waiting}d  {who} — {i.subject[:52]}")
    if len(owed) > MAX_OWED:
        out.append(f"  _+{len(owed) - MAX_OWED} more — `cos owed`_")
    return out


def _fmt_quiet(statuses, now, quiet_days) -> list[str]:
    quiet = [
        s for s in statuses
        if s.mapped and (s.days_quiet(now) or 0) >= quiet_days
    ]
    if not quiet:
        return []
    quiet.sort(key=lambda s: s.days_quiet(now) or 0, reverse=True)
    out = [f"*Gone quiet* ({len(quiet)})"]
    for s in quiet[:MAX_QUIET]:
        ball = "you" if s.ball_in_our_court() else "them"
        out.append(f"  {s.days_quiet(now)}d  {s.deal.name} — ball with {ball}")
    return out


def _fmt_health(checks) -> list[str]:
    bad = [c for c in checks if c.bad]
    if not bad:
        return []
    out = [f"⚠️ *System* — {len(bad)} of {len(checks)} checks need attention"]
    for c in bad:
        out.append(f"  *{c.name}*: {c.detail}")
    return out


def _fmt_drafts(summary) -> list[str]:
    if not summary or not summary.get("decided"):
        return []
    return [
        f"*Drafts* — {summary['accept_rate']:.0%} sent as written "
        f"({summary['decided']} decided, {summary['pending']} pending)"
    ]


def build(now=None) -> str:
    """Assemble the message. Pure composition — no sending, so it is testable
    and so `--dry-run` shows exactly what would be delivered."""
    from . import health as health_mod
    from .backend import open_backend
    from .cli import _upcoming_meetings
    from .config import Config
    from .contacts import utc_now
    from .reports import deal_status, owed_replies
    from .vault import attach_domains, load_deal_domains, load_deals, load_internal_domains

    now = now or utc_now()
    cfg = Config.load()

    checks = health_mod.run_all()

    sections: list[list[str]] = []
    # Anything broken goes first: it is the only part that decays if unread.
    sections.append(_fmt_health(checks))

    try:
        deals = load_deals(cfg.vault_root)
        attach_domains(deals, load_deal_domains(cfg.deal_domains_path))
        with open_backend(cfg) as client:
            from .cli import _build

            ledger = _build(cfg, client, now)
        statuses = deal_status(deals, ledger, now)
        owed = owed_replies(
            ledger, now, cfg.owed_window_days,
            internal_domains=load_internal_domains(cfg.deal_domains_path),
        )
        # A reply on WhatsApp is still a reply. The digest must not nag
        # about people Wei has marked handled on another channel.
        from . import agenda

        handled = agenda.owed_overrides(
            [{"who": i.who or i.counterparty.address, "days": i.days_waiting}
             for i in owed])
        owed = [i for i in owed
                if (i.who or i.counterparty.address) not in handled]
        sections.append(_fmt_meetings(_upcoming_meetings(cfg, now), now))
        sections.append(_fmt_owed(owed))
        sections.append(_fmt_quiet(statuses, now, cfg.quiet_days))
    except Exception as e:  # noqa: BLE001
        # Say the reports failed. Do NOT send a digest that silently omits
        # them — a short healthy-looking message is exactly how two days of
        # vault failure went unnoticed.
        sections.append([f"⚠️ *Reports unavailable* — {type(e).__name__}: {str(e)[:120]}"])

    try:
        from .draft_outcomes import classify, summarise

        sections.append(_fmt_drafts(summarise(classify())))
    except Exception:  # noqa: BLE001 — the metric is a nice-to-have
        pass

    body = "\n\n".join("\n".join(s) for s in sections if s)
    header = f"*{now.astimezone():%A %d %B}*"
    if not body:
        return f"{header}\n\nNothing scheduled, nobody waiting, nothing broken."
    return f"{header}\n\n{body}"


def default_target() -> str:
    """Where the digest goes.

    `telegram` alone means "the home channel", which is unset on a fresh
    install and fails with a message telling you to pick one. So this is
    overridable per install rather than assumed.
    """
    from .config import load_env

    return load_env().get("COS_DIGEST_TARGET", "telegram")


def send(text: str, to: str | None = None) -> tuple[bool, str]:
    """Deliver via the gateway's own credentials. No model in the path."""
    if not HERMES_PY.exists():
        return False, f"hermes python not found at {HERMES_PY}"
    r = subprocess.run(
        # NOT --quiet. It suppresses the reason a send failed, which turned a
        # missing-home-channel error into the words "unknown error" — a
        # monitoring system whose own failures are undiagnosable is a joke.
        [str(HERMES_PY), "-m", "hermes_cli.main", "send",
         "--to", to or default_target(), text],
        capture_output=True, text=True,
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return False, (err or out or "no output and no exit code").strip()[:250]
    return True, out or "sent"
