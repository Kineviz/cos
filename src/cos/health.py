"""Does the pipeline actually work right now?

`review.py` watches how the agent *behaves* — what it said, whether it
answered. This watches whether the machinery underneath it *ran*. They are
different questions and both have failed independently.

**Every check asserts positive evidence, with a freshness bound.** That is the
whole design, and it comes from the failures this project has actually had,
all of which looked like success:

  * The vault commit ran 168 times and never once worked. `git status` failed
    on a permission error, wrote to stderr, and returned empty stdout — which
    the caller read as "nothing to commit". Absence of error meant nothing.
  * `cos review` accepted a time window and ignored it, so a clean report and
    a report over the wrong data were indistinguishable.
  * The Gmail ledger's batch callback did `if exception: return`, silently
    dropping 247 counterparties while reporting success.
  * Every gbrain dream phase fell back to an Anthropic model regardless of
    config, on a brain with no Anthropic key.

So "no errors in the log" is not evidence of anything. A check here has to
name a thing that must be true — a commit that reached the index, a message
newer than N hours, a tool that is registered — and prove it.

The second rule follows from the first: **a check that cannot determine its
answer reports UNKNOWN, never ok.** Guessing ok is how the two days of silent
vault failure happened.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path.home() / ".cos"
REFRESH_LOG = STATE_DIR / "refresh.log"
HERMES = Path.home() / ".hermes"
AGENT_LOG = HERMES / "logs" / "agent.log"

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
_BAD = (WARN, FAIL, UNKNOWN)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    evidence: str = ""

    @property
    def bad(self) -> bool:
        return self.status in _BAD

    def line(self) -> str:
        glyph = {OK: "ok", WARN: "warn", FAIL: "FAIL", UNKNOWN: "????"}[self.status]
        return f"[{glyph}] {self.name}: {self.detail}"


def _run(*args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, **kw)


def _age(ts: datetime) -> timedelta:
    return datetime.now(timezone.utc) - ts.astimezone(timezone.utc)


def _human(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


# --------------------------------------------------------------------------
# The scheduled job


def check_refresh_ran(max_age_min: int = 45) -> Check:
    """The 15-minute job is the heartbeat. Everything else assumes it ran."""
    if not REFRESH_LOG.exists():
        return Check("refresh", UNKNOWN, "no refresh log at all", str(REFRESH_LOG))
    age = timedelta(seconds=time.time() - REFRESH_LOG.stat().st_mtime)
    if age > timedelta(minutes=max_age_min):
        return Check(
            "refresh", FAIL,
            f"has not run for {_human(age)} (expected every 15m)",
            "launchctl list | grep com.cos.refresh",
        )
    return Check("refresh", OK, f"last ran {_human(age)} ago")


def _last_run_block() -> list[str]:
    """Lines of the most recent run, which starts at the last date banner."""
    if not REFRESH_LOG.exists():
        return []
    try:
        lines = REFRESH_LOG.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("──"):
            return lines[i:]
    return lines[-40:]


def check_refresh_steps() -> Check:
    """The script prefixes every failed step with '  !'. Those lines existed
    for two days and nobody was reading them, which is the actual reason this
    module exists."""
    block = _last_run_block()
    if not block:
        return Check("refresh steps", UNKNOWN, "could not read the refresh log")
    bad = [l.strip() for l in block if l.lstrip().startswith("!")]
    if bad:
        return Check(
            "refresh steps", FAIL,
            f"{len(bad)} step(s) failed in the last run",
            " · ".join(b[:90] for b in bad[:3]),
        )
    if not any("done" in l for l in block):
        # A run in progress has no "done" line yet, and a full cycle takes a
        # couple of minutes. Calling that a fault would make this check fire
        # every time it happened to run alongside the refresh job — noise that
        # trains you to ignore the one time it means something.
        age = timedelta(seconds=time.time() - REFRESH_LOG.stat().st_mtime)
        if age < timedelta(minutes=10):
            return Check("refresh steps", OK, "a run is in progress")
        return Check(
            "refresh steps", FAIL,
            f"last run started and never finished ({_human(age)} ago)",
            block[0][:90] if block else "",
        )
    return Check("refresh steps", OK, "all steps completed")


# --------------------------------------------------------------------------
# Did work reach the index?


def _git_head(repo: Path) -> str | None:
    r = _run("git", "-C", str(repo), "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def _git_dirty(repo: Path) -> tuple[bool, str] | None:
    r = _run("git", "-C", str(repo), "status", "--porcelain")
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return bool(out), out


def _sources_row(name: str) -> dict | None:
    from .config import Config

    url = os.environ.get(
        "COS_BRAIN_URL", "postgresql://localhost:5435/kiran_brain"
    )
    r = _run(
        "psql", url, "-tAF\x1f", "-c",
        "select last_commit, extract(epoch from now()-last_sync_at), local_path "
        f"from sources where name = '{name}'",
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.strip().split("\x1f")
    if len(parts) < 3:
        return None
    return {"last_commit": parts[0], "sync_age_s": float(parts[1]), "path": parts[2]}


def check_indexed(source: str, max_lag_min: int = 60) -> Check:
    """The commit the brain has indexed must be the commit the repo is on.

    This is the check that would have caught the vault failure on day one. The
    refresh log looked healthy; the brain was simply pinned to an old commit
    because nothing new was ever committed for it to see.
    """
    row = _sources_row(source)
    if row is None:
        return Check(f"{source} indexed", UNKNOWN, "cannot read the brain's sources table")
    repo = Path(row["path"])
    head = _git_head(repo)
    if head is None:
        return Check(
            f"{source} indexed", UNKNOWN,
            f"cannot read git HEAD of {repo.name} (permissions?)",
            str(repo),
        )
    if head != row["last_commit"]:
        behind = _run("git", "-C", str(repo), "rev-list", "--count",
                      f"{row['last_commit']}..HEAD").stdout.strip() or "?"
        return Check(
            f"{source} indexed", FAIL,
            f"brain is {behind} commit(s) behind {repo.name}",
            f"indexed {row['last_commit'][:8]}, HEAD {head[:8]}",
        )
    lag = timedelta(seconds=row["sync_age_s"])
    if lag > timedelta(minutes=max_lag_min):
        return Check(f"{source} indexed", WARN,
                     f"up to date, but last synced {_human(lag)} ago")
    return Check(f"{source} indexed", OK, f"current ({head[:8]})")


def check_committed(name: str, repo: Path) -> Check:
    """Uncommitted work is invisible to the brain, because gbrain syncs by
    commit range. This is the failure itself, one step earlier than
    check_indexed sees it."""
    dirty = _git_dirty(repo)
    if dirty is None:
        return Check(
            f"{name} committed", FAIL,
            "cannot read git status — permission denied?",
            f"git -C {repo} status  (bash has no TCC grant for ~/Documents)",
        )
    is_dirty, out = dirty
    if not is_dirty:
        return Check(f"{name} committed", OK, "clean")
    n = len(out.splitlines())
    return Check(
        f"{name} committed", WARN,
        f"{n} uncommitted change(s) — invisible to the brain until committed",
        " · ".join(out.splitlines()[:3]),
    )


# --------------------------------------------------------------------------
# Sources


def check_mail(max_age_h: int = 12) -> Check:
    try:
        from .backend import open_backend
        from .config import Config
        from .contacts import corpus_freshness

        newest = corpus_freshness(open_backend(Config.load()))
    except Exception as e:  # noqa: BLE001 — any failure here is a real failure
        return Check("mail", FAIL, f"mail source unreachable: {type(e).__name__}", str(e)[:120])
    if newest is None:
        return Check("mail", UNKNOWN, "mail source gave no timestamp")
    age = _age(newest)
    if age > timedelta(hours=max_age_h):
        return Check("mail", WARN, f"newest message is {_human(age)} old")
    return Check("mail", OK, f"newest message {_human(age)} ago")


def check_google_auth() -> Check:
    try:
        from .google_auth import check as gcheck

        out = gcheck()
    except Exception as e:  # noqa: BLE001
        return Check("google auth", FAIL, f"grant not usable: {type(e).__name__}", str(e)[:140])
    return Check("google auth", OK, f"{out.get('address')} · calendar {out.get('calendar')!r}")


# --------------------------------------------------------------------------
# The agent


def check_gateway() -> Check:
    if _run("pgrep", "-f", "hermes_cli.main gateway run").stdout.strip():
        return Check("gateway", OK, "running")
    return Check("gateway", FAIL, "Telegram gateway is not running — the agent is unreachable")


_TOOLS_RE = re.compile(r"MCP: registered (\d+) tool\(s\) from (\d+) server\(s\)")
_SERVER_RE = re.compile(r"MCP server '([^']+)' \(stdio\): registered (\d+) tool")


def check_agent_tools(expect: tuple[str, ...] = ("gbrain", "clock")) -> Check:
    """A misconfigured MCP server does not crash the gateway — it reports
    `0 tool(s) available` and the agent simply cannot do that thing any more.
    That failure mode has bitten twice, both times from a config type error."""
    if not AGENT_LOG.exists():
        return Check("agent tools", UNKNOWN, "no agent log")
    try:
        text = AGENT_LOG.read_text(errors="replace")
    except OSError:
        return Check("agent tools", UNKNOWN, "cannot read agent log")
    found = dict((m.group(1), int(m.group(2))) for m in _SERVER_RE.finditer(text))
    if not found:
        return Check("agent tools", UNKNOWN, "no MCP registration seen in the log")
    missing = [s for s in expect if not found.get(s)]
    if missing:
        return Check(
            "agent tools", FAIL,
            f"MCP server(s) with no tools: {', '.join(missing)}",
            "; ".join(f"{k}={v}" for k, v in found.items()),
        )
    return Check("agent tools", OK,
                 " · ".join(f"{k} {v}" for k, v in sorted(found.items())))


def check_server_current() -> Check:
    """Is the dashboard running the code that is checked out?

    `cos serve` imports its modules once and launchd restarts it only on a
    crash, so an edit can sit unserved indefinitely. /api/page did exactly
    that. The refresh already restarts the Hermes gateway when its prompt
    changes; this is the same idea for the server.
    """
    try:
        import json as _json
        import urllib.request

        port = os.environ.get("COS_SERVE_PORT", "8787")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version",
                                    timeout=4) as r:
            data = _json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return Check("dashboard", UNKNOWN, f"not answering: {type(e).__name__}")
    if data.get("running") == "unknown" or data.get("head") == "unknown":
        return Check("dashboard", UNKNOWN, "cannot compare its commit to HEAD")
    if data["running"] != data["head"]:
        return Check(
            "dashboard", WARN,
            f"serving {data['running']}, but HEAD is {data['head']}",
            "launchctl kickstart -k gui/$(id -u)/com.cos.serve",
        )
    return Check("dashboard", OK, f"current ({data['running']})")


def check_autopilot() -> Check:
    if not _run("pgrep", "-f", "autopilot").stdout.strip():
        return Check("autopilot", WARN, "not running (background enrichment is stopped)")
    log = Path.home() / ".gbrain" / "autopilot.log"
    if not log.exists():
        return Check("autopilot", OK, "running")
    age = timedelta(seconds=time.time() - log.stat().st_mtime)
    if age > timedelta(minutes=30):
        return Check("autopilot", WARN, f"running but silent for {_human(age)}")
    return Check("autopilot", OK, f"running, last cycle {_human(age)} ago")


# --------------------------------------------------------------------------


def run_all() -> list[Check]:
    from .config import Config

    cfg = Config.load()
    brain = Path.home() / "brain"
    checks = [
        check_refresh_ran(),
        check_refresh_steps(),
        check_committed("vault", cfg.vault_root),
        check_committed("brain", brain),
        check_indexed("vault"),
        check_indexed("default"),
        check_mail(),
        check_google_auth(),
        check_gateway(),
        check_agent_tools(),
        check_autopilot(),
        check_server_current(),
    ]
    return checks


def render(checks: list[Check]) -> str:
    bad = [c for c in checks if c.bad]
    lines = [f"# cos health — {datetime.now():%Y-%m-%d %H:%M}", ""]
    if not bad:
        lines.append(f"All {len(checks)} checks pass.")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(bad)} of {len(checks)} checks need attention.")
    lines.append("")
    for c in bad:
        lines.append(c.line())
        if c.evidence:
            lines.append(f"      {c.evidence}")
    ok = [c for c in checks if not c.bad]
    if ok:
        lines += ["", "Passing: " + ", ".join(c.name for c in ok)]
    return "\n".join(lines) + "\n"
