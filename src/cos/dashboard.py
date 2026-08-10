"""A computed dashboard you can still write in.

The whole point of computing this page is that it cannot rot — the vault's dead
half is dead because refreshing it was a human decision. But a page that is
fully regenerated is a report, not a workspace: anything you type into it is
destroyed on the next run.

So Kiran owns only the regions between `<!-- cos:begin … -->` and
`<!-- cos:end … -->`. Everything else in the file is yours and is preserved
byte-for-byte, in position — your notes under a deal, a section you invented, a
paragraph at the top. Add whatever you like outside the markers.

Two rules make that safe:

  * A managed block is replaced only if Kiran recognises its key. An unknown
    block (a deal you removed from Pipeline.md) is left alone with a note, not
    silently deleted.
  * If you edit *inside* a managed block, that text would be lost on the next
    run — so it is not discarded, it is moved into your notes area beneath the
    block with a marker saying where it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import compat
from .reports import DealStatus, OwedReply

BEGIN = "<!-- cos:begin {key} -->"
END = "<!-- cos:end {key} -->"
# Matches the pre-rename marker as well as the current one. Vault files written
# before the rename already carry the old spelling, and a regex that only knew
# the new one would not recognise those blocks — so instead of being replaced
# they would be treated as the user's own prose and preserved, and a second
# copy appended on every run. Blocks are rewritten with the new marker as they
# are regenerated, so this decays on its own.
#
# The old name is taken from compat rather than written here, because a bulk
# rename sweep across this package rewrites any literal copy of it into the new
# name and silently removes the very compatibility this line provides. That is
# not hypothetical; it happened while this was being written.
_MARKER = rf"(?:cos|{compat.LEGACY_DIRNAME})"
_BLOCK_RE = re.compile(
    rf"<!--\s*{_MARKER}:begin\s+(?P<key>[^\s>]+)\s*-->\n?"
    rf"(?P<body>.*?)<!--\s*{_MARKER}:end\s+(?P=key)\s*-->",
    re.DOTALL,
)
_RESCUED_RE = re.compile(r"^> _rescued from the computed block.*$\n?", re.M)
# A bullet naming the tool was almost certainly written BY the tool, so it is
# not rescued as the user's prose. Word-bounded on purpose: the old name was a
# distinctive five-letter token, but "cos" is a substring of cost, cosmetic and
# Costa, and a plain containment test would classify "- chase the cost
# estimate" as machine-written and destroy it on the next run.
_TOOL_MENTION_RE = re.compile(rf"\b(?:cos|{compat.LEGACY_DIRNAME})\b", re.I)


@dataclass
class Block:
    key: str
    body: str


def parse_blocks(text: str) -> dict[str, Block]:
    return {
        m.group("key"): Block(m.group("key"), m.group("body"))
        for m in _BLOCK_RE.finditer(text)
    }


def _managed(key: str, body: str) -> str:
    return f"{BEGIN.format(key=key)}\n{body.rstrip()}\n{END.format(key=key)}"


def _deal_block(s: DealStatus, now: datetime) -> str:
    days = s.days_quiet(now)
    if not s.mapped:
        return "_No email domain mapped — add it to `config/deal_domains.yaml` to track contact._"
    ball = "**you**" if s.ball_in_our_court() else "them"
    quiet = f"{days}d" if days is not None else "—"
    return (
        f"| Quiet | Ball | Stage | Paper |\n"
        f"|---|---|---|---|\n"
        f"| {quiet} | {ball} | {s.deal.stage or '—'} | {s.deal.paper or '—'} |\n\n"
        f"Stated next step: {s.deal.next_step or '—'}\n\n"
        f"Last inbound {s.last_inbound.astimezone():%Y-%m-%d}"
        if s.last_inbound else "No inbound recorded"
    )


def _overview_block(
    now: datetime, statuses: list[DealStatus], owed: list[OwedReply], quiet_days: int
) -> str:
    mapped = [s for s in statuses if s.mapped]
    quiet = [s for s in mapped if (s.days_quiet(now) or 0) >= quiet_days]
    lines = [
        f"**As of {now.astimezone():%Y-%m-%d %H:%M %Z}** — "
        f"{len(quiet)} of {len(mapped)} tracked deals quiet {quiet_days}+ days · "
        f"{len(owed)} people waiting on a reply"
        + (f" (longest {owed[0].days_waiting}d)" if owed else ""),
        "",
    ]
    if quiet:
        lines += ["| Deal | Quiet | Ball |", "|---|---|---|"]
        lines += [
            f"| {s.deal.name} | {s.days_quiet(now)}d | "
            f"{'you' if s.ball_in_our_court() else 'them'} |"
            for s in quiet
        ]
    else:
        lines.append("_Nothing quiet._")
    return "\n".join(lines)


def _owed_block(owed: list[OwedReply], limit: int = 15) -> str:
    if not owed:
        return "_Nobody outside the company is waiting on you._"
    lines = ["| Waiting | Who | Org | Their last message |", "|---|---|---|---|"]
    for i in owed[:limit]:
        lines.append(
            f"| {i.days_waiting}d | {i.who.replace('|','/')} | "
            f"{i.counterparty.domain} | {i.subject.replace('|','/')[:60]} |"
        )
    if len(owed) > limit:
        lines.append("")
        lines.append(f"_…and {len(owed)-limit} more — `cos owed` for the full list._")
    return "\n".join(lines)


_HEADER = """---
title: Dashboard
generated_by: cos
generated_at: {ts}
---

# Dashboard

> Blocks between `cos:begin` / `cos:end` markers are **recomputed on every
> `cos dashboard` run**. Everything else in this file is yours and is
> preserved — write notes under any deal, add your own sections, reorder the
> prose. Only the marked regions are touched.

{overview}

---

## Waiting on you

{owed}

---

## Deals
"""


def render(
    now: datetime,
    statuses: list[DealStatus],
    owed: list[OwedReply],
    quiet_days: int,
    existing: str | None = None,
) -> tuple[str, list[str]]:
    """Return (markdown, notes_about_rescued_content)."""
    old = parse_blocks(existing or "")
    notes: list[str] = []

    fresh = {
        "overview": _overview_block(now, statuses, owed, quiet_days),
        "owed": _owed_block(owed),
    }
    for s in statuses:
        fresh[f"deal:{_slug(s.deal.name)}"] = _deal_block(s, now)

    if existing:
        # Preserve everything outside managed blocks, replacing block bodies in
        # place so the user's surrounding prose keeps its position.
        def _sub(m: re.Match) -> str:
            key, body = m.group("key"), m.group("body")
            if key not in fresh:
                return _managed(
                    key,
                    body.rstrip()
                    + "\n\n> _No longer produced by Kiran (deal removed from "
                    "Pipeline.md?). Left as-is; delete when you like._",
                )
            rescued = _user_lines(body)
            if rescued:
                notes.append(key)
                return _managed(key, fresh[key]) + (
                    f"\n\n> _moved out of the computed block for `{key}`, which is "
                    f"rewritten each run — keep it here instead:_\n"
                    + "\n".join(rescued)
                )
            return _managed(key, fresh[key])

        out = _BLOCK_RE.sub(_sub, existing)
        # Append anything the file does not have yet. On first adoption of a
        # hand-written Dashboard.md that is every block, so the summary
        # sections must be included — not only the per-deal ones.
        missing = [k for k in fresh if k not in old]
        if missing:
            parts = []
            if "overview" in missing:
                parts.append(f"## At a glance\n\n{_managed('overview', fresh['overview'])}")
            if "owed" in missing:
                parts.append(f"## Waiting on you\n\n{_managed('owed', fresh['owed'])}")
            deal_keys = [k for k in missing if k.startswith("deal:")]
            if deal_keys:
                parts.append("## Deals")
                parts += [
                    f"### {_title_for(k, statuses)}\n\n{_managed(k, fresh[k])}\n\n**Notes**\n"
                    for k in deal_keys
                ]
            out = out.rstrip() + "\n\n---\n\n" + "\n\n".join(parts) + "\n"
        return out, notes

    body = _HEADER.format(
        ts=f"{now.astimezone():%Y-%m-%d %H:%M %Z}",
        overview=_managed("overview", fresh["overview"]),
        owed=_managed("owed", fresh["owed"]),
    )
    for s in statuses:
        key = f"deal:{_slug(s.deal.name)}"
        body += f"\n### {s.deal.name}\n\n{_managed(key, fresh[key])}\n\n**Notes**\n\n"
    return body, notes


def _user_lines(body: str) -> list[str]:
    """Lines inside a computed block that Kiran would never have written.

    We cannot diff against the previous computed value (it is not stored), so
    this keys on shape: Kiran emits tables, bold summary lines and italic
    notes — never bare list items. Only those lines are rescued, so a
    regeneration does not dump the whole block into the user's notes.
    """
    out = []
    for line in _RESCUED_RE.sub("", body).splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")) and not _TOOL_MENTION_RE.search(stripped):
            out.append(line)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unknown"


def _title_for(key: str, statuses: list[DealStatus]) -> str:
    want = key.split(":", 1)[1]
    for s in statuses:
        if _slug(s.deal.name) == want:
            return s.deal.name
    return want
