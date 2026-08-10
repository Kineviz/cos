"""The list is the first place Wei's own judgement is an input, so the rules
about what his actions mean have to be exactly right.

The subtle one is "done" on a derived item. Ticking "Pat Fisher, waiting 73
days" does not mean Bob stopped waiting — no reply was sent. It means *I have
dealt with this*. If it silently came back on the next 15-minute refresh the
list would be unusable; if it never came back, a fresh email from Bob would be
invisible. It has to be dismissed until the situation actually changes.
"""

from __future__ import annotations

import json

import pytest

from cos import agenda


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda, "STATE_FILE", tmp_path / "agenda.json")
    # Kiran's real to-do list lives outside the repo. Without this the suite
    # reads Wei's actual list and the counts move whenever he talks to Kiran.
    from cos import todos as todos_mod
    empty = tmp_path / "notes"
    empty.mkdir()
    monkeypatch.setattr(todos_mod, "NOTES_DIR", empty)


SNAP = {
    "owed": [
        {"who": "Pat Fisher", "subject": "CBP Demo", "days": 73},
        {"who": "Max Latey", "subject": "Lunch", "days": 65},
    ],
    "quiet": [{"name": "Nightowl", "days": 57, "ball": "them"}],
}


def _by_title(items, title):
    return next(i for i in items if i.title == title)


class TestDerivedItems:
    def test_mail_becomes_items(self):
        items = agenda.build(SNAP)
        assert {i.title for i in items} == {"Pat Fisher", "Max Latey", "Nightowl"}

    def test_ids_are_stable_across_rebuilds(self):
        """State is keyed by id. If ids moved, every refresh would lose your
        ticks."""
        a = {i.title: i.id for i in agenda.build(SNAP)}
        b = {i.title: i.id for i in agenda.build(SNAP)}
        assert a == b

    def test_id_ignores_the_subject(self):
        """Keyed on WHO. Otherwise a second email from the same person becomes
        a second item and resurrects something already dealt with."""
        other = {"owed": [{"who": "Pat Fisher", "subject": "totally different",
                           "days": 73}], "quiet": []}
        assert _by_title(agenda.build(SNAP), "Pat Fisher").id == \
               _by_title(agenda.build(other), "Pat Fisher").id


class TestDone:
    def test_ticking_hides_it(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        assert _by_title(agenda.build(SNAP), "Pat Fisher").done
        assert "Pat Fisher" not in [i.title for i in agenda.top(snapshot=SNAP)]

    def test_it_stays_hidden_while_nothing_changes(self):
        """The 15-minute refresh must not undo your decision."""
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        for _ in range(5):
            assert _by_title(agenda.build(SNAP), "Pat Fisher").done

    def test_a_new_message_brings_it_back(self):
        """days counts up from their last message, so a smaller number means
        they wrote again — the situation moved, so it is live again."""
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        fresh = {"owed": [{"who": "Pat Fisher", "subject": "following up", "days": 0}],
                 "quiet": []}
        assert not _by_title(agenda.build(fresh), "Pat Fisher").done

    def test_getting_older_does_not_bring_it_back(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        older = {"owed": [{"who": "Pat Fisher", "subject": "CBP Demo", "days": 99}],
                 "quiet": []}
        assert _by_title(agenda.build(older), "Pat Fisher").done

    def test_can_be_reopened(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        agenda.act(bob.id, "undone", snapshot=SNAP)
        assert not _by_title(agenda.build(SNAP), "Pat Fisher").done


class TestManualItems:
    def test_add_and_appear(self):
        agenda.add("Call the accountant")
        assert "Call the accountant" in [i.title for i in agenda.build(SNAP)]

    def test_manual_done_is_final(self):
        """No mail signal can revive it, because there is none."""
        item = agenda.add("Book flights")
        agenda.act(item.id, "done", snapshot=SNAP)
        for _ in range(3):
            assert _by_title(agenda.build(SNAP), "Book flights").done

    def test_remove_only_works_on_manual(self):
        item = agenda.add("Temporary")
        assert agenda.remove(item.id)
        assert "Temporary" not in [i.title for i in agenda.build(SNAP)]

    def test_removing_a_derived_item_does_not_pretend_to_work(self):
        """It would just reappear on the next refresh, so 'done' is the right
        verb for those and remove must report that it did nothing."""
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        assert agenda.remove(bob.id) is False
        assert "Pat Fisher" in [i.title for i in agenda.build(SNAP)]

    def test_empty_title_refused(self):
        with pytest.raises(ValueError):
            agenda.add("   ")


class TestSectionsAndOrder:
    """Order is set by dragging, so these are about `move`. The arrows are gone
    — Wei found them too slow, and a control that no longer moves anything
    would be worse than none."""

    def test_items_start_in_a_sensible_section(self):
        """Mail-derived work lands at the BACK, never at the top of Today. A
        mis-parse must not shout at him from the front of the list."""
        assert all(i.bucket == "backlog" for i in agenda.build(SNAP))

    def test_a_manual_item_starts_in_today(self):
        agenda.add("Call the accountant")
        assert _by_title(agenda.build(SNAP), "Call the accountant").bucket == "today"

    def test_move_between_sections(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.move(bob.id, "today", snapshot=SNAP)
        assert _by_title(agenda.build(SNAP), "Pat Fisher").bucket == "today"

    def test_move_survives_a_rebuild(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.move(bob.id, "soon", snapshot=SNAP)
        for _ in range(3):
            assert _by_title(agenda.build(SNAP), "Pat Fisher").bucket == "soon"

    def test_dropping_between_two_items_lands_between_them(self):
        items = agenda.build(SNAP)
        for i in items:
            agenda.move(i.id, "today", snapshot=SNAP)
        a, b, c = [i.id for i in agenda.build(SNAP) if i.bucket == "today"]
        agenda.move(c, "today", above=a, below=b, snapshot=SNAP)
        order = [i.id for i in agenda.build(SNAP) if i.bucket == "today"]
        assert order == [a, c, b]

    def test_dropping_at_the_top_puts_it_first(self):
        items = agenda.build(SNAP)
        for i in items:
            agenda.move(i.id, "today", snapshot=SNAP)
        order = [i.id for i in agenda.build(SNAP) if i.bucket == "today"]
        agenda.move(order[-1], "today", below=order[0], snapshot=SNAP)
        assert [i.id for i in agenda.build(SNAP) if i.bucket == "today"][0] == order[-1]

    def test_sections_stay_in_a_fixed_order(self):
        items = agenda.build(SNAP)
        agenda.move(items[0].id, "backlog", snapshot=SNAP)
        agenda.move(items[1].id, "today", snapshot=SNAP)
        agenda.move(items[2].id, "soon", snapshot=SNAP)
        got = [i.bucket for i in agenda.build(SNAP)]
        assert got == ["today", "soon", "backlog"]

    def test_an_unknown_section_is_refused(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        assert "unknown section" in agenda.move(bob.id, "someday", snapshot=SNAP)

    def test_moving_an_unknown_item_is_refused(self):
        assert agenda.move("nope", "today", snapshot=SNAP) == "unknown item"

    def test_done_items_sort_last(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        assert agenda.build(SNAP)[-1].title == "Pat Fisher"

    def test_top_is_capped(self):
        for n in range(20):
            agenda.add(f"thing {n}")
        assert len(agenda.top(7, SNAP)) == 7


class TestComments:
    def test_comment_is_kept(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "comment", text="Morgan is the real decision maker")
        got = _by_title(agenda.build(SNAP), "Pat Fisher").comments
        assert got[0]["text"] == "Morgan is the real decision maker"

    def test_comments_survive_being_ticked(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "comment", text="rang him instead")
        agenda.act(bob.id, "done", snapshot=SNAP)
        assert _by_title(agenda.build(SNAP), "Pat Fisher").comments

    def test_empty_comment_ignored(self):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "comment", text="   ")
        assert _by_title(agenda.build(SNAP), "Pat Fisher").comments == []


class TestVaultMirror:
    def test_page_contains_the_live_items(self, tmp_path):
        agenda.add("Call the accountant")
        page = agenda.write_page(tmp_path, agenda.build(SNAP))
        text = page.read_text()
        assert "Pat Fisher" in text and "Call the accountant" in text
        assert "- [ ]" in text

    def test_comments_reach_the_vault(self, tmp_path):
        """The reason comments are not left in a JSON file: in the vault they
        are in git and the agent can read them."""
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "comment", text="Morgan decides, not Bob")
        page = agenda.write_page(tmp_path, agenda.build(SNAP))
        assert "Morgan decides, not Bob" in page.read_text()

    def test_done_items_are_separated(self, tmp_path):
        bob = _by_title(agenda.build(SNAP), "Pat Fisher")
        agenda.act(bob.id, "done", snapshot=SNAP)
        text = agenda.write_page(tmp_path, agenda.build(SNAP)).read_text()
        assert "## Dealt with" in text and "- [x] Pat Fisher" in text


class TestResilience:
    def test_missing_state_file_is_fine(self):
        assert agenda.build(SNAP)

    def test_corrupt_state_refuses_rather_than_looking_empty(self):
        """This used to return an empty state and carry on, on the theory that
        ticks are only bookkeeping. They are not: manual items and comments
        exist ONLY in this file — nothing parses them back out of the rendered
        markdown — so an empty read silently erased every to-do Wei typed, and
        the page rendered that as a clean list."""
        agenda.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        agenda.STATE_FILE.write_text("{ not json")
        with pytest.raises(agenda.StateCorrupt):
            agenda.build(SNAP)

    def test_a_corrupt_file_is_kept_for_inspection(self):
        agenda.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        agenda.STATE_FILE.write_text("{ not json")
        with pytest.raises(agenda.StateCorrupt):
            agenda.build(SNAP)
        assert agenda.STATE_FILE.with_suffix(".corrupt").exists()

    def test_the_previous_good_state_is_kept(self):
        agenda.add("Call the accountant")
        agenda.add("Book flights")
        assert agenda.STATE_FILE.with_suffix(".bak").exists()

    def test_concurrent_writes_do_not_lose_each_other(self):
        """The real failure: two overlapping clicks both read, both mutated,
        and the last write won."""
        import threading

        def work(n):
            agenda.add(f"item {n}")

        threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        titles = {i.title for i in agenda.build(SNAP)}
        assert {f"item {n}" for n in range(12)} <= titles

    def test_empty_snapshot_gives_an_empty_list(self):
        assert agenda.build({}) == []
