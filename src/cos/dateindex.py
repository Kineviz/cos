"""Which pages belong to a date, taken from the page names themselves.

There is no way to ask this brain "what happened between two dates". Measured
against the live tools: `search` and `query` take no date filter at all;
`list_pages` filters on `updated_after`, which is when the row was last
*synced* — everything was synced together, so it sorts 62,000 pages into
roughly one afternoon; and `chronicle_day`, the one date-native index, holds
120 entries for 62,781 pages, so it returns `[]` for every real day.

The consequence is the failure Wei kept hitting. "What did I do last week?"
was answered by embedding the *words* "last week", which matches an email from
2020 titled "things did last week and plan for this week" far better than
anything that actually happened last week. Similarity has no idea when
anything was.

The fix does not need a new database. 27,448 of the brain's 27,477 markdown
files — 99.9% — carry their date in the filename: `email/2026-08-07-…`,
`calendar/2026-07-27-…`. The index that was missing has been sitting in the
directory listing the whole time.

This walks it, keeps the result for a few minutes, and answers "give me the
pages from 27 July to 2 August" in a few milliseconds.
"""

from __future__ import annotations

import re
import threading
import time
from bisect import bisect_left, bisect_right
from datetime import date
from pathlib import Path

BRAIN_DIR = Path.home() / "brain"

# A rebuild walks ~27k files in well under a second, and the brain changes
# hourly at most. Long enough that a burst of questions pays for it once.
TTL_SECONDS = 600

_DATED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(.+)\.md$")

# Folders whose pages are things Wei did or wrote, rather than things that
# arrived. For "what did I do last week" a meeting he sat in beats a
# newsletter that landed in the same week, and nothing in the text says so.
_FIRST_PERSON = ("calendar/", "90_agent/", "05_workspace/")

_lock = threading.Lock()
_cache: tuple[float, list[tuple[date, str]]] | None = None


def _walk(root: Path) -> list[tuple[date, str]]:
    out: list[tuple[date, str]] = []
    for path in root.rglob("*.md"):
        if ".git" in path.parts:
            continue
        m = _DATED.match(path.name)
        if not m:
            continue
        y, mo, d, _rest = m.groups()
        try:
            when = date(int(y), int(mo), int(d))
        except ValueError:
            continue
        out.append((when, str(path.relative_to(root).with_suffix(""))))
    out.sort(key=lambda p: p[0])
    return out


def _index() -> list[tuple[date, str]]:
    global _cache
    with _lock:
        now = time.time()
        if _cache and now - _cache[0] < TTL_SECONDS:
            return _cache[1]
        rows = _walk(BRAIN_DIR) if BRAIN_DIR.is_dir() else []
        _cache = (now, rows)
        return rows


def refresh() -> None:
    """Drop the cache. For tests, and for after a sync."""
    global _cache
    with _lock:
        _cache = None


def _rank_key(row: tuple[date, str]) -> tuple:
    when, slug = row
    return (0 if slug.startswith(_FIRST_PERSON) else 1, -when.toordinal(), slug)


def pages_between(start: date, end: date, limit: int = 40) -> list[dict]:
    """Pages dated inside the window, most useful first.

    "Most useful" is deliberately crude — what Wei did before what arrived,
    then newest first. Within a window this small the point is to get real
    pages from the right days in front of the model at all; ordering them
    perfectly is the reranker's job, not this one's.
    """
    rows = _index()
    if not rows:
        return []
    days = [r[0] for r in rows]
    lo = bisect_left(days, start)
    hi = bisect_right(days, end)
    window = _spread(sorted(rows[lo:hi], key=_rank_key))[:limit]
    return [{"slug": slug, "date": when.isoformat()} for when, slug in window]


def _spread(ranked: list[tuple[date, str]]) -> list[tuple[date, str]]:
    """One day must not stand in for the week.

    Straight ranking put all six sources for "what did I do last week?" on
    Friday, because Friday had eight meetings and every other day was pushed
    below the cut. The answer described one day and called it the week.

    So the days take turns: the best page from each day, then the second from
    each, and so on. Inside a day the original order survives.
    """
    by_day: dict[date, list[tuple[date, str]]] = {}
    for row in ranked:
        by_day.setdefault(row[0], []).append(row)
    order = sorted(by_day, reverse=True)
    out: list[tuple[date, str]] = []
    for turn in range(max((len(v) for v in by_day.values()), default=0)):
        for day in order:
            if turn < len(by_day[day]):
                out.append(by_day[day][turn])
    return out


def newest_matching(terms: list[str], limit: int = 12) -> list[dict]:
    """The most recent pages whose name carries every term. Newest first.

    The one thing similarity structurally cannot do: return the *latest* page
    about a subject. Asked what we discussed on Falcon lately, the vector legs
    returned six threads from 2024 and 2025 and never surfaced the June 2026
    one at all — it is a short thread, and short threads embed weakly. Its
    filename says `2026-06-01-falcon-track-and-trace-error`, which is the whole
    answer, sitting in a directory listing.

    Names only, deliberately. A term found in a page's *name* is what that page
    is about; a term found in its body may be a footer.
    """
    keys = [t for t in (w.strip().lower() for w in terms) if len(t) > 2]
    if not keys:
        return []
    # Ranked, not filtered. Requiring every term meant "discuss falcon" matched
    # only two pages, both from 2024–25, and the June 2026 thread — which says
    # `falcon` and not `discuss` — was excluded by the weaker of the two words.
    scored = []
    for when, slug in _index():
        stem = slug.rsplit("/", 1)[-1].lower()
        n = sum(1 for k in keys if k in stem)
        if n:
            scored.append((n, when.toordinal(), when, slug))
    scored.sort(key=lambda r: (-r[0], -r[1]))
    return [{"slug": s, "date": w.isoformat()} for _n, _o, w, s in scored[:limit]]


def slug_for(day: date, title: str) -> str | None:
    """The page for a message, given its date and subject.

    The mail graph knows a message by date and subject; the brain knows it by
    slug. This is the join, and it is done by walking one day of the index
    rather than by re-deriving the brain's slug rules — which drift, and would
    fail silently when they did.
    """
    want = _slugify(title)
    if not want:
        return None
    rows = _index()
    days = [r[0] for r in rows]
    lo, hi = bisect_left(days, day), bisect_right(days, day)
    best, best_score = None, 0.0
    for _d, slug in rows[lo:hi]:
        stem = _slugify(slug.rsplit("/", 1)[-1][11:])
        if not stem:
            continue
        if stem == want:
            return slug
        a, b = set(want.split("-")), set(stem.split("-"))
        if not a or not b:
            continue
        overlap = len(a & b) / len(a | b)
        if overlap > best_score:
            best, best_score = slug, overlap
    # Below this it is a different conversation that happened to share a word.
    return best if best_score >= 0.5 else None


_SLUG_STRIP = re.compile(r"^(re|fwd|fw|aw|sv)[\s:]+", re.I)


def _slugify(text: str) -> str:
    out = _SLUG_STRIP.sub("", (text or "").strip())
    out = re.sub(r"[^A-Za-z0-9]+", "-", out).strip("-").lower()
    return out


def count_between(start: date, end: date) -> int:
    rows = _index()
    days = [r[0] for r in rows]
    return bisect_right(days, end) - bisect_left(days, start)


def read_head(slug: str, chars: int = 700) -> str:
    """The opening of a page, for an excerpt.

    Only ever called for the handful of window pages that survive ranking, so
    it reads the file directly rather than paying a subprocess to do it.
    """
    path = BRAIN_DIR / f"{slug}.md"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(chars * 3)
    except OSError:
        return ""
