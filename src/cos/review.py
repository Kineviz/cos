"""Watch how Kiran actually behaves, and say what is going wrong.

Every real failure so far was invisible until Wei happened to notice it. The
gateway ran three days on a stale prompt; a run hung for forty minutes in
silence; Kiran insisted it could not see a calendar that was sitting indexed in
its own brain. None of it appeared in a log anyone was reading.

So this reads the conversation history and the gateway log, and reports what it
finds. Two classes, kept apart on purpose:

**Mechanical** — a hung run, a dead gateway, an MCP server with no tools. No
judgement involved, safe to fix automatically, and `--fix` does.

**Judgement** — Kiran said it could not see something; a question went
unanswered; a tool kept failing. These are *reported*, never auto-applied.

That split is deliberate and worth defending. Diagnosing the calendar failure
took three wrong answers — lint, then retrieval, then the date anchor — before
the real cause turned out to be a process that had not been restarted. A loop
that rewrote its own instructions from the first plausible diagnosis would have
"fixed" lint, twice, and buried the actual problem under its own changes. Prompt
edits stay a human decision.
"""

from __future__ import annotations

import re
import sqlite3
import time
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

STATE_DB = Path.home() / ".hermes" / "state.db"
GATEWAY_LOG = Path.home() / ".hermes" / "logs" / "gateway.log"

# Kiran declining a capability. The calendar case proves why this matters: it
# said "I only reliably see email-indexed invites" while 1,595 calendar pages
# sat in its own brain. Either the capability is missing or the wiring is —
# both need a human to look.
# Only claims about a CONNECTED source. "I can't see prior 1:1 correspondence
# because none is indexed" is Kiran declaring a gap correctly — SOUL.md asks for
# exactly that, and flagging it would train Wei to ignore this report, which is
# how the last five notification surfaces died. What matters is Kiran denying
# something it is actually wired to: it insisted it could not see the calendar
# while 1,595 calendar pages sat in its own brain.
DENIAL = re.compile(
    r"(can'?t|cannot|don'?t|do not|unable to)\s+(see|access|read|reach|view)\s+"
    r"(your |the |live )*(calendar|email|inbox|mail|gmail|vault|drafts|schedule)",
    re.I,
)

# A denial immediately explained by absent data is correct behaviour, not a bug.
JUSTIFIED = re.compile(
    r"because (there )?(is|are)n'?t any|because none|not indexed|no .{0,20}indexed|"
    r"nothing .{0,20}indexed|doesn'?t exist",
    re.I,
)

# Real tool failures are SHORT and lead with the failure. Page bodies routinely
# contain the word "error" in prose — the first version flagged
# mcp__gbrain__get_page four times on the strength of that, and every one was a
# healthy result.
TOOL_ERROR = re.compile(
    r"\A\s*(<[^>]+>\s*)?(error|failed|exception|traceback|tool execution failed)[:\s]",
    re.I,
)
_MAX_ERROR_CHARS = 600

# Kiran leaking its own plumbing into a reply. Wei has asked repeatedly to be
# written to in plain language, and the drift is always the same shape: naming
# the machinery instead of answering. "I found 3 pages in the brain" instead of
# "you met her twice in March".
#
# Deliberately narrow. Only terms that have no ordinary business meaning, so
# this cannot fire on Wei's own vocabulary — "vault", "page", "brain" and
# "search" are all words he uses himself and are NOT listed. A style reviewer
# that cries wolf gets ignored, and then so does the rest of the report.
JARGON = re.compile(
    r"\b(gbrain|mcp__\w+|MCP server|embedding|chunk(s|ed|ing)?|"
    r"get_page|put_page|traverse_graph|tool call|stdio|frontmatter)\b"
)

INBOUND = re.compile(r"inbound message: platform=(\w+).*?msg=(.{0,60})")
RESPONDED = re.compile(r"response ready: platform=(\w+)")


@dataclass
class Finding:
    kind: str
    severity: str          # "mechanical" (auto-fixable) or "judgement"
    detail: str
    evidence: str = ""


@dataclass
class Review:
    findings: list[Finding] = field(default_factory=list)
    messages_seen: int = 0
    window_hours: int = 24

    @property
    def mechanical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "mechanical"]

    @property
    def judgement(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "judgement"]


def _recent_messages(hours: int) -> list[tuple]:
    """Messages from the last `hours`, newest-first cap of 400.

    The `hours` argument used to be accepted and then ignored — the query was
    an unconditional `LIMIT 400`. On a quiet couple of days that reached back
    far enough to re-report problems that had already been fixed: the run on
    2026-08-07 surfaced two "I can't see your calendar" denials from 2026-08-05,
    a complaint answered by the clock tool the following day. A reviewer that
    reports fixed problems is one nobody reads, which is how the five dead
    scheduled artifacts in this vault died.

    The cap stays as a backstop for a busy window; the time bound is what makes
    the report mean what it says.
    """
    if not STATE_DB.exists():
        return []
    cutoff = time.time() - hours * 3600
    conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    try:
        rows = list(
            conn.execute(
                "SELECT id, role, content, tool_name FROM messages "
                "WHERE timestamp >= ? ORDER BY id DESC LIMIT 400",
                (cutoff,),
            )
        )
    finally:
        conn.close()
    return list(reversed(rows))


def _gateway_hung() -> Finding | None:
    """An inbound message with no matching response is the signature of a hang.

    The 40-minute silence looked exactly like this: `inbound message` logged,
    no `response ready` after it, and nothing else to indicate anything was
    wrong. Cheap to detect, and the fix is mechanical.
    """
    if not GATEWAY_LOG.exists():
        return None
    try:
        tail = GATEWAY_LOG.read_text(errors="replace").splitlines()[-400:]
    except OSError:
        return None

    last_inbound = last_response = None
    inbound_text = ""
    for line in tail:
        if INBOUND.search(line):
            last_inbound = line[:19]
            m = INBOUND.search(line)
            inbound_text = (m.group(2) if m else "")[:60]
        elif RESPONDED.search(line):
            last_response = line[:19]

    if not last_inbound:
        return None
    if last_response and last_response >= last_inbound:
        return None

    try:
        started = datetime.strptime(last_inbound, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    stalled_min = (datetime.now() - started).total_seconds() / 60
    if stalled_min < 8:
        return None  # still plausibly working

    return Finding(
        kind="hung_run",
        severity="mechanical",
        detail=(
            f"A message has gone {stalled_min:.0f} minutes with no reply. "
            "The gateway accepted it and never answered."
        ),
        evidence=f"last inbound {last_inbound}: {inbound_text.strip()}",
    )


def _gateway_alive() -> Finding | None:
    out = subprocess.run(
        ["pgrep", "-f", "hermes_cli.main gateway run"],
        capture_output=True, text=True,
    )
    if out.stdout.strip():
        return None
    return Finding(
        kind="gateway_down",
        severity="mechanical",
        detail="The Telegram gateway is not running. Kiran cannot be reached.",
    )


def run(hours: int = 24) -> Review:
    rev = Review(window_hours=hours)
    rows = _recent_messages(hours)
    rev.messages_seen = len(rows)

    for finder in (_gateway_alive, _gateway_hung):
        f = finder()
        if f:
            rev.findings.append(f)

    denials: list[str] = []
    jargon: list[str] = []
    tool_failures: dict[str, int] = {}
    for _id, role, content, tool_name in rows:
        text = content or ""
        if role == "assistant" and (m := JARGON.search(text)):
            start = max(0, m.start() - 60)
            jargon.append(
                f"{m.group(0)} — …{text[start:m.end() + 60]}…".replace("\n", " ").strip()
            )
        if role == "assistant" and DENIAL.search(text):
            m = DENIAL.search(text)
            window = text[m.start() : m.end() + 160]
            if JUSTIFIED.search(window):
                continue  # correctly reporting absent data
            start = max(0, m.start() - 40)
            denials.append(text[start : m.end() + 90].replace("\n", " ").strip())
        elif role == "tool" and len(text) < _MAX_ERROR_CHARS and TOOL_ERROR.search(text):
            tool_failures[tool_name or "unknown"] = (
                tool_failures.get(tool_name or "unknown", 0) + 1
            )

    if denials:
        rev.findings.append(
            Finding(
                kind="capability_denial",
                severity="judgement",
                detail=(
                    f"Kiran declined a capability {len(denials)} time(s). Either "
                    "it is genuinely missing, or it has it and does not know — "
                    "the calendar was the second kind."
                ),
                evidence="\n".join(f"    · …{d}…" for d in denials[:4]),
            )
        )

    if jargon:
        rev.findings.append(
            Finding(
                kind="jargon_in_replies",
                severity="judgement",
                detail=(
                    f"Kiran used its own machinery's vocabulary {len(jargon)} time(s) "
                    "talking to Wei. He has asked repeatedly for plain language — "
                    "the answer, not how it was found. Tighten the style section at "
                    "the top of SOUL.md."
                ),
                evidence="\n".join(f"    · {j[:150]}" for j in jargon[:4]),
            )
        )

    for tool, n in sorted(tool_failures.items(), key=lambda kv: -kv[1]):
        if n >= 3:
            rev.findings.append(
                Finding(
                    kind="tool_failing",
                    severity="judgement",
                    detail=f"`{tool}` returned an error {n} times.",
                )
            )

    return rev


def remediate(rev: Review) -> list[str]:
    """Fix only what has no judgement in it. Returns what was done."""
    done: list[str] = []
    for f in rev.mechanical:
        if f.kind in ("hung_run", "gateway_down"):
            r = subprocess.run(
                [
                    str(Path.home() / ".hermes/hermes-agent/venv/bin/python"),
                    "-m", "hermes_cli.main", "gateway", "restart",
                ],
                cwd=str(Path.home() / ".hermes"),
                capture_output=True, text=True, timeout=180,
            )
            done.append(
                f"restarted the gateway ({f.kind})"
                if r.returncode == 0
                else f"gateway restart FAILED ({f.kind}): {r.stderr[:100]}"
            )
    return done


def render(rev: Review, fixed: list[str] | None = None) -> str:
    now = datetime.now()
    lines = [
        f"# Kiran review — {now:%Y-%m-%d %H:%M}",
        "",
        f"{rev.messages_seen} messages from the last {rev.window_hours}h examined.",
        "",
    ]
    if not rev.findings:
        lines += ["Nothing to report. Kiran is answering and the gateway is healthy."]
        return "\n".join(lines) + "\n"

    if rev.mechanical:
        lines += ["## Fixed automatically" if fixed else "## Mechanical", ""]
        for f in rev.mechanical:
            lines.append(f"- **{f.kind}** — {f.detail}")
            if f.evidence:
                lines.append(f"  `{f.evidence}`")
        if fixed:
            lines += [""] + [f"  → {d}" for d in fixed]
        lines.append("")

    if rev.judgement:
        lines += ["## Needs your judgement", ""]
        for f in rev.judgement:
            lines.append(f"- **{f.kind}** — {f.detail}")
            if f.evidence:
                lines += ["", f.evidence, ""]
    return "\n".join(lines) + "\n"
