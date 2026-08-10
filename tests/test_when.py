"""The stretch of time a question asks about.

Retrieval was date-blind. Every page carries a date, none of it reached the
ranking, and "how was the response to my talk at BigBank this morning?" was
answered out of February 2026 and November 2025.
"""

from __future__ import annotations

from datetime import date

from cos import when

# A Saturday, so the week arithmetic has to be right rather than lucky.
TODAY = date(2026, 8, 8)


def w(question):
    return when.parse(question, TODAY)


class TestRelative:
    def test_this_morning_is_today(self):
        assert w("How was the response to my talk at BigBank this morning?") == \
            when.Window(TODAY, TODAY, "this morning")

    def test_last_week_is_the_previous_monday_to_sunday(self):
        """Not "the last seven days" — Wei means the week that ended, and on a
        Saturday those are five days apart."""
        got = w("What did I do last week?")
        assert (got.start, got.end) == (date(2026, 7, 27), date(2026, 8, 2))

    def test_this_week_starts_on_monday(self):
        got = w("what have I done this week")
        assert (got.start, got.end) == (date(2026, 8, 3), date(2026, 8, 9))

    def test_yesterday(self):
        assert w("who did I meet yesterday").start == date(2026, 8, 7)

    def test_last_month_is_a_calendar_month(self):
        got = w("what shipped last month")
        assert (got.start, got.end) == (date(2026, 7, 1), date(2026, 7, 31))

    def test_n_units_ago(self):
        got = w("what did we agree three weeks ago")
        assert got.start <= date(2026, 7, 18) <= got.end

    def test_lately_is_a_nudge_not_a_fortnight(self):
        """"Lately" is vague and the window is wide on purpose. Narrow, it
        would filter away the thread that answers the question."""
        got = w("What did we discuss on Falcon lately?")
        assert got.days >= 20


class TestExplicit:
    def test_an_iso_date_wins_over_a_relative_phrase(self):
        got = w("what happened on 2026-08-03, was that last week")
        assert (got.start, got.end) == (date(2026, 8, 3), date(2026, 8, 3))

    def test_day_and_month_either_way_round(self):
        assert w("notes from August 3").start == date(2026, 8, 3)
        assert w("notes from 3 August").start == date(2026, 8, 3)

    def test_a_month_with_no_year_that_has_not_happened_means_last_year(self):
        """Asked in August, "December 20th" is eight months back, not four
        months forward."""
        assert w("what did we send on December 20th").start.year == 2025

    def test_a_bare_month(self):
        got = w("what did I say in July")
        assert (got.start, got.end) == (date(2026, 7, 1), date(2026, 7, 31))


class TestNoWindow:
    def test_a_question_with_no_time_in_it_gets_none(self):
        """Returning None matters as much as returning a window. Inventing one
        would filter away the page that answers the question."""
        assert w("Who is the real decision maker at Northwind?") is None
        assert w("Who is Casey and what does he work on?") is None
        assert w("") is None


class TestShoulders:
    def test_around_means_around(self):
        """Wei asked about "this morning" on a day when the talk had been the
        morning before. A hard filter would have dropped the one page that
        answered him."""
        win = w("how did the talk go this morning")
        assert not win.holds(date(2026, 8, 7))
        assert win.near(date(2026, 8, 7))
        assert not win.near(date(2026, 7, 1))
