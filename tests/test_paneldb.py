"""The panel store — the master copy for editable panels.

Wei: "For each panel, we should have a table in DB. With name, state…
Database take over as the master copy." Seeded once from the markdown
files; from then on the files are a generated view.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cos import paneldb


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    paneldb.reset_for_tests(tmp_path / "panels.db")
    paneldb.ensure_panel("prospects", "Prospects", [])
    yield
    paneldb.reset_for_tests(tmp_path / "panels.db")


class TestItems:
    def test_add_and_list(self):
        paneldb.add_item("prospects", "Northwind", state="Qualified")
        rows = paneldb.list_items("prospects")
        assert [r["name"] for r in rows] == ["Northwind"]
        assert rows[0]["state"] == "Qualified"

    def test_a_new_state_joins_the_panel(self):
        """States are data, not schema — Wei's stages are his own words."""
        paneldb.add_item("prospects", "A", state="Qualified")
        paneldb.add_item("prospects", "B", state="Weird New Stage")
        assert "Weird New Stage" in paneldb.states("prospects")

    def test_update_changes_only_what_was_given(self):
        r = paneldb.add_item("prospects", "Hillcrest", state="Expansion",
                             note="enablement")
        paneldb.update_item(r["id"], note="new note")
        got = paneldb.get_item(r["id"])
        assert got["note"] == "new note"
        assert got["state"] == "Expansion"
        assert got["name"] == "Hillcrest"

    def test_archive_is_reversible_and_hides_the_row(self):
        r = paneldb.add_item("prospects", "Old Deal")
        paneldb.update_item(r["id"], archived=True)
        assert paneldb.list_items("prospects") == []
        paneldb.update_item(r["id"], archived=False)
        assert len(paneldb.list_items("prospects")) == 1

    def test_move_drops_above_a_named_row(self):
        a = paneldb.add_item("prospects", "A", state="Engaged")
        b = paneldb.add_item("prospects", "B", state="Engaged")
        c = paneldb.add_item("prospects", "C", state="Qualified")
        paneldb.move_item(c["id"], "Engaged", above_id=b["id"])
        names = [r["name"] for r in paneldb.list_items("prospects")
                 if r["state"] == "Engaged"]
        assert names == ["A", "C", "B"]

    def test_an_empty_name_is_refused(self):
        with pytest.raises(ValueError):
            paneldb.add_item("prospects", "   ")


class TestFindByName:
    """The assistant edits by name. A name matching two items must match
    nothing — renaming the wrong deal quietly is worse than asking."""

    def test_exact_beats_substring(self):
        paneldb.add_item("prospects", "Google")
        paneldb.add_item("prospects", "Google Cloud")
        assert paneldb.find_item("prospects", "google")["name"] == "Google"

    def test_ambiguous_substring_matches_nothing(self):
        paneldb.add_item("prospects", "Northwind")
        paneldb.add_item("prospects", "Northwindtion Corp")
        assert paneldb.find_item("prospects", "constell") is None

    def test_unique_substring_matches(self):
        paneldb.add_item("prospects", "Nightowl")
        assert paneldb.find_item("prospects", "night")["name"] == "Nightowl"


class TestSeeding:
    @pytest.fixture
    def vault(self, tmp_path):
        tm = tmp_path / "05_workspace" / "Task_management"
        tm.mkdir(parents=True)
        (tm / "Pipeline.md").write_text(
            "# Pipeline\n\n## At a glance\n\n"
            "| Deal | Stage | Owner | Next step | Paper? |\n"
            "|---|---|---|---|---|\n"
            "| Northwind | Qualified | Wei | sandboxes | ❌ none |\n"
            "| Hillcrest | Expansion | Wei | enablement | ✅ signed |\n\n"
            "## Notes\n\nhand-written notes stay.\n",
            encoding="utf-8")
        return tmp_path

    def test_seed_imports_once_and_never_overwrites(self, vault):
        assert paneldb.seed_prospects(vault) == 2
        r = paneldb.find_item("prospects", "Northwind")
        paneldb.update_item(r["id"], state="Won")
        assert paneldb.seed_prospects(vault) == 0
        assert paneldb.find_item("prospects", "Northwind")["state"] == "Won"

    def test_export_rewrites_the_table_and_keeps_the_prose(self, vault):
        paneldb.seed_prospects(vault)
        r = paneldb.find_item("prospects", "Northwind")
        paneldb.update_item(r["id"], state="Committed", note="contract out")
        paneldb.export_markdown(vault)
        text = (vault / "05_workspace" / "Task_management"
                / "Pipeline.md").read_text(encoding="utf-8")
        assert "| Committed |" in text
        assert "contract out" in text
        assert "hand-written notes stay." in text
        assert "generated from the panel database" in text

    def test_export_keeps_columns_the_panel_does_not_edit(self, tmp_path):
        """The real table has Campaign and Value columns. A generated view
        that loses them is not a view, it is a downgrade — and the first
        export did exactly that."""
        tm = tmp_path / "05_workspace" / "Task_management"
        tm.mkdir(parents=True)
        (tm / "Pipeline.md").write_text(
            "## At a glance\n\n"
            "| Deal | Campaign | Stage | Owner | Next step | Paper? |\n"
            "|---|---|---|---|---|---|\n"
            "| [[#Acme]] | C9 | Lead | Wei | call them | ❌ |\n\n"
            "> the note below the table stays.\n\n"
            "## Acme\ndetails\n", encoding="utf-8")
        paneldb.seed_prospects(tmp_path)
        r = paneldb.find_item("prospects", "Acme")
        paneldb.update_item(r["id"], state="Won")
        paneldb.export_markdown(tmp_path)
        text = (tm / "Pipeline.md").read_text(encoding="utf-8")
        assert "| C9 |" in text, "Campaign column lost"
        assert "| Won |" in text
        assert "[[#Acme]]" in text, "wikilink lost"
        assert "the note below the table stays." in text
        assert "## Acme" in text


class TestNotesAreADatedHistory:
    """Wei: "show latest note, but keep earlier notes, collapse them though.
    each note is dated." A note never overwrites the ones before it."""

    def test_a_new_note_goes_on_top_and_the_old_ones_stay(self):
        r = paneldb.add_item("prospects", "Northwind", note="first note")
        paneldb.update_item(r["id"], note="second note")
        got = paneldb.get_item(r["id"])
        assert [n["text"] for n in got["notes"]] == ["first note",
                                                     "second note"]
        assert got["note"] == "second note"
        assert all(n["ts"] for n in got["notes"])

    def test_an_empty_note_adds_nothing(self):
        r = paneldb.add_item("prospects", "Hillcrest", note="real note")
        paneldb.update_item(r["id"], note="   ")
        assert len(paneldb.get_item(r["id"])["notes"]) == 1

    def test_a_state_change_does_not_touch_the_notes(self):
        r = paneldb.add_item("prospects", "Google", note="the note")
        paneldb.update_item(r["id"], state="Won")
        got = paneldb.get_item(r["id"])
        assert got["state"] == "Won"
        assert [n["text"] for n in got["notes"]] == ["the note"]


class TestFocusIsNotAStage:
    """Wei: "I need a top view, those I need to pay attention right now."
    Urgency this week says nothing about whether a deal is Qualified or
    Engaged, so flagging must never touch the stage."""

    def test_flagging_leaves_stage_and_notes_alone(self):
        r = paneldb.add_item("prospects", "HKJC", state="Identified",
                             note="racing integrity")
        paneldb.set_focus(r["id"], True)
        got = paneldb.get_item(r["id"])
        assert got["extra"]["focus"] is True
        assert got["state"] == "Identified"
        assert [n["text"] for n in got["notes"]] == ["racing integrity"]

    def test_clearing_removes_the_flag(self):
        r = paneldb.add_item("prospects", "Gamely")
        paneldb.set_focus(r["id"], True)
        paneldb.set_focus(r["id"], False)
        assert "focus" not in paneldb.get_item(r["id"])["extra"]

    def test_a_stage_change_keeps_the_flag(self):
        """Moving a flagged deal along the pipeline must not silently
        un-flag it — only dragging it out of the attention list does."""
        r = paneldb.add_item("prospects", "Woodline", state="Identified")
        paneldb.set_focus(r["id"], True)
        paneldb.update_item(r["id"], state="Pilot/Eval")
        got = paneldb.get_item(r["id"])
        assert got["state"] == "Pilot/Eval"
        assert got["extra"]["focus"] is True


class TestAttentionListOrder:
    """Dragging inside "needs attention now" did nothing: the list had no
    order of its own, so rows fell back to stage order."""

    def _flagged(self, *names):
        out = []
        for n in names:
            r = paneldb.add_item("prospects", n, state="Identified")
            paneldb.set_focus(r["id"], True)
            out.append(paneldb.get_item(r["id"]))
        return out

    def _order(self):
        rows = [r for r in paneldb.list_items("prospects")
                if r["extra"].get("focus")]
        rows.sort(key=lambda r: r["extra"].get("focus_pos", 0.0))
        return [r["name"] for r in rows]

    def test_new_flags_land_at_the_bottom_in_order(self):
        self._flagged("A", "B", "C")
        assert self._order() == ["A", "B", "C"]

    def test_a_row_can_be_dragged_to_the_top(self):
        a, b, c = self._flagged("A", "B", "C")
        paneldb.move_focus(c["id"], above_id=a["id"])
        assert self._order() == ["C", "A", "B"]

    def test_a_row_can_be_dropped_between_two_others(self):
        a, b, c = self._flagged("A", "B", "C")
        paneldb.move_focus(c["id"], above_id=b["id"])
        assert self._order() == ["A", "C", "B"]

    def test_clearing_then_reflagging_puts_it_at_the_bottom(self):
        a, b, c = self._flagged("A", "B", "C")
        paneldb.set_focus(a["id"], False)
        assert "focus_pos" not in paneldb.get_item(a["id"])["extra"]
        paneldb.set_focus(a["id"], True)
        assert self._order() == ["B", "C", "A"]
