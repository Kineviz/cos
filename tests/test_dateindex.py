"""Pages by date, read out of the page names.

There is no way to ask this brain "what happened between two dates": search and
query take no date filter, list_pages filters on sync time, and the timeline
holds 120 entries for 62,781 pages. 99.9% of the pages carry their date in the
filename, so the index that was missing was in the directory listing.
"""

from __future__ import annotations

from datetime import date

import pytest

from cos import dateindex


@pytest.fixture
def brain(tmp_path, monkeypatch):
    for rel in [
        "email/2026-07-28-a-thread.md",
        "email/2026-07-31-another-thread.md",
        "email/2026-08-05-outside-the-window.md",
        "calendar/2026-07-31-standup.md",
        "calendar/2026-07-27-kickoff.md",
        "people/wei.md",                       # undated, must not appear
        "email/not-a-date-here.md",            # undated
        "email/2026-13-45-impossible.md",      # unparseable, must not crash
    ]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {p.stem}\n\nsome body text\n")
    monkeypatch.setattr(dateindex, "BRAIN_DIR", tmp_path)
    dateindex.refresh()
    return tmp_path


class TestIndex:
    def test_only_dated_pages_are_indexed(self, brain):
        assert dateindex.count_between(date(2020, 1, 1), date(2030, 1, 1)) == 5

    def test_a_window_returns_only_that_window(self, brain):
        got = dateindex.pages_between(date(2026, 7, 27), date(2026, 8, 2))
        assert {p["slug"] for p in got} == {
            "email/2026-07-28-a-thread", "email/2026-07-31-another-thread",
            "calendar/2026-07-31-standup", "calendar/2026-07-27-kickoff"}

    def test_what_wei_did_outranks_what_arrived(self, brain):
        """For "what did I do last week" a meeting he sat in beats a
        newsletter that landed the same day, and nothing in the text says
        so — only the folder does.

        Within a day, since the days now take turns: covering the week matters
        more than putting every meeting above every email, or a Friday with
        eight meetings is the whole answer again.
        """
        got = dateindex.pages_between(date(2026, 7, 27), date(2026, 8, 2))
        friday = [p["slug"] for p in got if p["date"] == "2026-07-31"]
        assert friday[0].startswith("calendar/")

    def test_newest_first_within_a_kind(self, brain):
        got = [p for p in dateindex.pages_between(date(2026, 7, 27), date(2026, 8, 2))
               if p["slug"].startswith("calendar/")]
        assert got[0]["date"] == "2026-07-31"

    def test_an_empty_window_is_empty_not_an_error(self, brain):
        assert dateindex.pages_between(date(2019, 1, 1), date(2019, 12, 31)) == []

    def test_a_missing_brain_is_empty_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dateindex, "BRAIN_DIR", tmp_path / "nope")
        dateindex.refresh()
        assert dateindex.pages_between(date(2026, 1, 1), date(2026, 12, 31)) == []

    def test_read_head_returns_the_page(self, brain):
        assert "some body text" in dateindex.read_head("calendar/2026-07-31-standup")

    def test_read_head_on_a_missing_page_is_empty(self, brain):
        assert dateindex.read_head("calendar/no-such-page") == ""


class TestOneDayIsNotTheWeek:
    """Friday had eight meetings, so all six sources for "what did I do last
    week?" landed on Friday and the answer described one day and called it the
    week."""

    @pytest.fixture
    def lopsided(self, tmp_path, monkeypatch):
        names = [f"calendar/2026-07-31-meeting-{n}.md" for n in range(8)]
        names += ["calendar/2026-07-29-debrief.md",
                  "calendar/2026-07-28-kickoff.md"]
        for rel in names:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# x\n")
        monkeypatch.setattr(dateindex, "BRAIN_DIR", tmp_path)
        dateindex.refresh()
        return tmp_path

    def test_the_days_take_turns(self, lopsided):
        got = dateindex.pages_between(date(2026, 7, 27), date(2026, 8, 2),
                                      limit=3)
        assert [r["date"] for r in got] == ["2026-07-31", "2026-07-29",
                                            "2026-07-28"]

    def test_a_thin_week_still_returns_everything(self, lopsided):
        got = dateindex.pages_between(date(2026, 7, 27), date(2026, 8, 2),
                                      limit=40)
        assert len(got) == 10
