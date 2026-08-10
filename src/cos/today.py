"""A dated, deterministic anchor page for the brain.

Two failures showed up the first time real questions were asked of the brain,
and both are the same mistake — asking a language model to do arithmetic it
has no access to:

  * *"the brain does not state the current date explicitly"* — so "last month"
    got anchored to whatever document happened to be newest. Every
    time-relative question was silently answered against the wrong window.
  * *"no full inventory of emails received Jun 27–Jul 27… a complete
    correspondent-level triage is not possible"* — retrieval surfaces the
    pages most similar to the question, which is the wrong tool for "list
    everyone who wrote to me". That is a scan, not a search.

This page supplies both: today's date, and the exhaustive lists that retrieval
cannot produce. It is regenerated on each run and contains no model output, so
nothing in it can be wrong in the way a summary can be wrong.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .contacts import Counterparty
from .reports import DealStatus, OwedReply

# The page is chunked before retrieval. At 40 rows it split into two chunks
# and the model received the table without the date header, then reported
# the date as unknown. Keep it to one chunk, and repeat the date per section.
_MAX_ROWS = 12


def render(
    now: datetime,
    quiet: list[DealStatus],
    owed: list[OwedReply],
    quiet_threshold: int,
    owed_window: int,
    upcoming: list | None = None,
) -> str:
    local = now.astimezone()
    _mapped = [s for s in quiet if s.mapped]
    _n_quiet = len([s for s in _mapped if (s.days_quiet(now) or 0) >= quiet_threshold])
    lines = [
        "---",
        "type: note",
        "title: Today",
        f"date: {local:%Y-%m-%d}",
        f"generated_at: {local:%Y-%m-%d %H:%M %Z}",
        "generated_by: cos",
        "---",
        "",
        f"# Today — {local:%A %d %B %Y}",
        "",
        f"**The current date is {local:%Y-%m-%d}.** Anchor any relative time "
        f'expression ("today", "this week", "last month", "recently") to this '
        f"date, not to the newest document in the brain.",
        "",
        "Everything below is computed from mail headers by rule at "
        f"{local:%H:%M %Z}. It is exhaustive for its window, unlike retrieval, "
        "so it can be used to answer *who / how many / list them all*.",
        "",
        # Retrieval excerpts a page and truncates from the END, so a table
        # below the fold is invisible however the page is chunked. The
        # headline numbers therefore go in the first paragraph, before any
        # section that might be cut.
        "## At a glance",
        "",
        f"As of **{local:%Y-%m-%d}**: **{_n_quiet}** named deal(s) with no human "
        f"contact for {quiet_threshold}+ days, and **{len(owed)}** correspondent(s) "
        f"waiting on a reply from you"
        + (f" (longest {owed[0].days_waiting} days)." if owed else "."),
        "",
        "---",
        "",
    ]

    # Upcoming meetings live HERE, high on the page, and not in retrieval's
    # hands. There are ~1,600 calendar pages; vector search over them cannot
    # reliably answer "the next three days", because the question carries no
    # lexical anchor and every meeting page looks alike. One computed list on
    # the date-anchor page answers it exactly, every time.
    if upcoming:
        lines += [
            f"## Your next meetings (from {local:%Y-%m-%d})",
            "",
        ]
        # Group under explicit "Today" / "Tomorrow" headings. Retrieval is
        # lexical as well as vector, and the question people actually ask is
        # "what do I have tomorrow" — which otherwise matches an old email whose
        # subject happens to read "meeting tomorrow" far better than it matches
        # a page listing dates. The words have to be on the page.
        from datetime import timedelta as _td

        _today = local.date()
        _labels = {
            _today: f"Today ({local:%a %d %b %Y})",
            _today + _td(days=1): f"Tomorrow ({local + _td(days=1):%a %d %b %Y})",
        }
        _seen_day = None
        for e in upcoming:
            _day = e.start.astimezone().date()
            if _day != _seen_day:
                _seen_day = _day
                lines += ["", f"### {_labels.get(_day, _day.strftime('%A %d %B %Y'))}", ""]
            when = (
                f"{e.start.astimezone():%a %d %b}"
                if e.all_day
                else f"{e.start.astimezone():%a %d %b %H:%M}"
            )
            who = ", ".join(
                a for a, _ in e.attendees if "@" in a
            )[:110] or "no attendees listed"
            flag = "" if e.status == "confirmed" else f"  **[{e.status.upper()}]**"
            lines.append(f"- **{when}** — {e.summary}{flag}  ·  {who}")
        lines += ["", "---", ""]

    lines += [
        f"## Deals with no human contact for {quiet_threshold}+ days "
        f"(as of {local:%Y-%m-%d})",
        "",
    ]

    mapped = _mapped
    flagged = [s for s in mapped if (s.days_quiet(now) or 0) >= quiet_threshold]
    if not flagged:
        lines.append(
            f"None. All {len(mapped)} mapped deals had human contact within "
            f"{quiet_threshold} days."
        )
    else:
        lines.append("| Deal | Quiet | Ball with | Stated next step |")
        lines.append("|---|---|---|---|")
        for s in flagged:
            lines.append(
                f"| {s.deal.name} | {s.days_quiet(now)}d | "
                f"{'you' if s.ball_in_our_court() else 'them'} | "
                f"{(s.deal.next_step or '—').replace('|', '/')} |"
            )

    unmapped = [s for s in quiet if not s.mapped]
    if unmapped:
        lines += [
            "",
            f"_{len(unmapped)} deal(s) have no email domain mapped and were not "
            f"checked: {', '.join(s.deal.name for s in unmapped)}._",
        ]

    lines += ["", "---", "",
          f"## Waiting on a reply from you as of {local:%Y-%m-%d} "
          f"({owed_window}-day window)", ""]
    if not owed:
        lines.append("Nobody outside the company is waiting on a reply.")
    else:
        lines.append(f"{len(owed)} correspondent(s). Longest-waiting first.")
        lines.append("")
        lines.append("| Waiting | Who | Organization | Their last message |")
        lines.append("|---|---|---|---|")
        for item in owed[:_MAX_ROWS]:
            lines.append(
                f"| {item.days_waiting}d | {item.who.replace('|', '/')} | "
                f"{item.counterparty.domain} | {item.subject.replace('|', '/')} |"
            )
        if len(owed) > _MAX_ROWS:
            lines.append("")
            lines.append(
                f"_…and {len(owed) - _MAX_ROWS} more, all waiting "
                f"{owed[_MAX_ROWS].days_waiting}d or less. Run `cos owed` for the "
                f"complete list — this page is deliberately kept to one chunk so the "
                f"date anchor is never separated from the data._"
            )

    lines += [
        "",
        "---",
        "",
        "_Regenerated by `cos brief`. Derived from the Gmail mirror by rule — "
        "no model produced any figure on this page._",
        "",
    ]
    return "\n".join(lines)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
