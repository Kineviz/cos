"""Reading the to-do list Kiran actually keeps.

The dashboard's first version built its own list from mail headers and called
it "Today", while Kiran had been maintaining the real one — Northwind, Brad at
BigBank, the CDL talk — from what Wei told it in conversation. Two lists that
disagreed. Wei noticed before anyone else: "what Kiran gathered is not what
dashboard is showing."

So these tests are about faithfully reading someone else's file. Kiran writes
it naturally through the brain; nothing here writes back.
"""

from __future__ import annotations

import pytest

from cos import todos

# Trimmed from Wei's real list, kept in Kiran's own shape.
SAMPLE = """---
type: note
title: To-dos — Thu 2026-08-06
---

# To-dos — Thursday 06 August 2026

_(Note: list carried forward; edits through Fri 2026-08-07.)_

## Today

1. **Finish the secret SeekerXR AI deliverable** (XRI) — **DONE** (2026-08-06).
2. **Talk applications** — **ODSC: submitted**. CDL: draft sent to Jordan;
   due end of Aug.

## Next 30 days (not urgent)

5. **Piyush + Bei (Google) — Spanner Graph demo and blog.** They published
   connecting data commons to proprietary data.
7. **Publish "Why enterprise needs dynamic ontology"** — blog series.

_Source for 5 and 6: the 14:00 GTM meeting._

## Follow-ups

8. **Reconnect with Northwind** — Wei to re-establish contact. _Kiran drafted email._
"""


class TestParsing:
    def test_finds_every_item(self):
        assert len(todos.parse(SAMPLE)) == 5

    def test_keeps_kirans_numbering(self):
        """Kiran renumbers deliberately and refers to items by number in
        conversation, so the dashboard must not silently renumber."""
        assert [t.number for t in todos.parse(SAMPLE)] == [1, 2, 5, 7, 8]

    def test_keeps_sections(self):
        got = {t.number: t.section for t in todos.parse(SAMPLE)}
        assert got[1] == "Today"
        assert got[8] == "Follow-ups"
        assert "Next 30 days" in got[5]

    def test_title_comes_from_the_bold_part(self):
        first = todos.parse(SAMPLE)[0]
        assert first.title == "Finish the secret SeekerXR AI deliverable"

    def test_detail_keeps_the_rest(self):
        assert "XRI" in todos.parse(SAMPLE)[0].detail

    def test_done_is_detected(self):
        by_num = {t.number: t for t in todos.parse(SAMPLE)}
        assert by_num[1].done
        assert not by_num[8].done

    def test_continuation_lines_are_joined(self):
        by_num = {t.number: t for t in todos.parse(SAMPLE)}
        assert "end of Aug" in by_num[2].detail

    def test_trailing_prose_is_not_glued_to_the_last_item(self):
        """The italic "_Source for 5 and 6…_" paragraph follows item 7 and
        must not become part of it."""
        by_num = {t.number: t for t in todos.parse(SAMPLE)}
        assert "GTM meeting" not in by_num[7].detail

    def test_quotes_in_a_title_survive(self):
        by_num = {t.number: t for t in todos.parse(SAMPLE)}
        assert "dynamic ontology" in by_num[7].title

    def test_ids_are_stable(self):
        a = {t.number: t.id for t in todos.parse(SAMPLE)}
        b = {t.number: t.id for t in todos.parse(SAMPLE)}
        assert a == b

    def test_id_survives_renumbering(self):
        """Kiran renumbers the list whenever it inserts something. Keying on
        the number would lose every tick each time that happened."""
        renumbered = SAMPLE.replace("8. **Reconnect with Northwind**",
                                    "9. **Reconnect with Northwind**")
        before = {t.title: t.id for t in todos.parse(SAMPLE)}
        after = {t.title: t.id for t in todos.parse(renumbered)}
        assert before["Reconnect with Northwind"] == after["Reconnect with Northwind"]

    def test_empty_input_is_empty_not_an_error(self):
        assert todos.parse("") == []

    def test_prose_with_no_items_yields_nothing(self):
        assert todos.parse("# Notes\n\nJust some thoughts.\n") == []


class TestFileSelection:
    def test_no_directory_is_survivable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(todos, "NOTES_DIR", tmp_path / "missing")
        assert todos.load() == []
        assert todos.latest_file() is None

    def test_newest_dated_file_wins(self, monkeypatch, tmp_path):
        monkeypatch.setattr(todos, "NOTES_DIR", tmp_path)
        (tmp_path / "2026-08-01-todos.md").write_text("1. **Old**")
        (tmp_path / "2026-08-06-todos.md").write_text("1. **New**")
        assert todos.load()[0].title == "New"

    def test_age_is_reported(self, monkeypatch, tmp_path):
        monkeypatch.setattr(todos, "NOTES_DIR", tmp_path)
        (tmp_path / "2020-01-01-todos.md").write_text("1. **Ancient**")
        assert todos.age_days() > 2000
