"""The stretch of time a question is asking about.

Every page in the brain carries an `effective_date`, and until now retrieval
ignored it completely. Asked "how was the response to my talk at BigBank this
morning?", the search returned threads from February 2026, November 2025 and
February 2026 — nothing from the week the talk was actually in. It answered
correctly anyway, because BigBank appears in few enough threads that the newest
was the right one. That is luck, and on a busier subject the same luck returns
a confident answer about the wrong year.

Wei: *"Prioritize information that are closer to today. If a date is given, on
and around that date."* That is two different rules, and they are kept
separate here on purpose:

- **A window**, when the question names one. "This morning", "last week", "in
  July", "on 3 August". A page inside the window is worth far more than a page
  outside it, whatever the embedding thinks.
- **A recency prior**, always. Between two pages that match the words equally
  well, the newer one is almost always the one meant. This has to stay gentle:
  "Who is the real decision maker at Northwind?" is answered by a 2025 email,
  and a recency rule strong enough to bury it would trade four temporal
  questions for six recall ones.

`on and around` is why a window has shoulders. Wei asked about "this morning"
on a day when the talk had been the morning before; a hard filter on today
would have excluded the very page that answered the question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# How far either side of a window still counts as "around" it.
SHOULDER_DAYS = 3

_MONTHS = {m: n for n, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
_MONTHS.update({m[:3]: n for m, n in list(_MONTHS.items())})


@dataclass(frozen=True)
class Window:
    """A closed date range, plus what in the question asked for it."""

    start: date
    end: date
    phrase: str
    # "Lately" is not a claim about a fortnight — it means *the most recent
    # ones*, relative to whatever the question is about. When the newest Falcon
    # thread is 68 days old, a 30-day window contains nothing and the ranking
    # falls back to wording, which puts 2024 on top of its own question. A soft
    # window orders the candidates by date instead of filtering them by it.
    soft: bool = False

    def holds(self, d: date) -> bool:
        return self.start <= d <= self.end

    def near(self, d: date) -> bool:
        """Inside the shoulders — "on and around that date"."""
        return (self.start - timedelta(days=SHOULDER_DAYS) <= d
                <= self.end + timedelta(days=SHOULDER_DAYS))

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _week_of(d: date) -> tuple[date, date]:
    """The ISO week containing `d` — Monday to Sunday."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def _month(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1))
    return start, end


def parse(question: str, today: date) -> Window | None:
    """The window a question asks about, or None if it does not ask for one.

    Returning None matters as much as returning a window. Most questions are
    not about a date at all, and inventing one for them would filter away the
    page that answers them.
    """
    q = (question or "").lower()

    # An explicit date beats every relative phrase: "what happened on
    # 2026-08-03" means that day, even in a sentence containing "last week".
    m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", q)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            exact = date(y, mo, d)
        except ValueError:
            exact = None
        if exact:
            return Window(exact, exact, m.group(0))

    # "on 3 August", "August 3", "Aug 3rd" — with or without a year.
    m = (re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\b", q)
         or re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", q))
    if m:
        a, b = m.groups()
        day, mon = (int(a), _MONTHS[b]) if a.isdigit() else (int(b), _MONTHS[a])
        yr = re.search(r"\b(20\d\d)\b", q)
        year = int(yr.group(1)) if yr else today.year
        try:
            exact = date(year, mon, day)
        except ValueError:
            exact = None
        if exact:
            # No year given and the date is in the future — they meant last
            # year. Asked in January, "December 20th" is a month ago.
            if not yr and exact > today:
                try:
                    exact = date(year - 1, mon, day)
                except ValueError:
                    pass
            return Window(exact, exact, m.group(0))

    # A bare month: "in July", "back in December".
    m = re.search(r"\b(?:in|during|back in)\s+(" + "|".join(_MONTHS) + r")\b", q)
    if m:
        mon = _MONTHS[m.group(1)]
        yr = re.search(r"\b(20\d\d)\b", q)
        year = int(yr.group(1)) if yr else today.year
        if not yr and mon > today.month:
            year -= 1
        return Window(*_month(year, mon), phrase=m.group(0))

    # "3 days ago", "two weeks ago", "6 months ago".
    words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
             "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    m = re.search(r"\b(\d{1,3}|" + "|".join(words) + r")\s+"
                  r"(day|week|month|year)s?\s+ago\b", q)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
        unit = m.group(2)
        span = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        then = today - timedelta(days=span)
        # A day named exactly is a day; a month named vaguely is a month.
        pad = {"day": 0, "week": 3, "month": 15, "year": 60}[unit]
        return Window(then - timedelta(days=pad), then + timedelta(days=pad),
                      m.group(0))

    # Relative phrases, longest first so "this morning" is not eaten by
    # "morning" and "last week" is not eaten by "week".
    if re.search(r"\b(this morning|this afternoon|this evening|tonight|"
                 r"today|so far today|right now|just now)\b", q):
        phrase = re.search(r"\b(this morning|this afternoon|this evening|"
                           r"tonight|today|so far today|right now|just now)\b", q)
        return Window(today, today, phrase.group(0))

    if re.search(r"\b(yesterday|last night)\b", q):
        y = today - timedelta(days=1)
        return Window(y, y, "yesterday")

    if re.search(r"\btomorrow\b", q):
        t = today + timedelta(days=1)
        return Window(t, t, "tomorrow")

    if re.search(r"\blast week\b", q):
        mon, sun = _week_of(today - timedelta(days=7))
        return Window(mon, sun, "last week")

    if re.search(r"\b(this week|so far this week)\b", q):
        return Window(*_week_of(today), phrase="this week")

    if re.search(r"\bnext week\b", q):
        return Window(*_week_of(today + timedelta(days=7)), phrase="next week")

    if re.search(r"\blast month\b", q):
        first = today.replace(day=1)
        prev = first - timedelta(days=1)
        return Window(*_month(prev.year, prev.month), phrase="last month")

    if re.search(r"\bthis month\b", q):
        return Window(*_month(today.year, today.month), phrase="this month")

    if re.search(r"\blast year\b", q):
        return Window(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31),
                      "last year")

    # Vague recency. Wide on purpose: "lately" is a nudge toward the present,
    # not a claim about a fortnight.
    m = re.search(r"\b(lately|recently|these days|of late|latest|"
                  r"in the last few days|past few days)\b", q)
    if m:
        return Window(today - timedelta(days=30), today, m.group(1), soft=True)

    return None
