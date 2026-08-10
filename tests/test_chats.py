"""Chat sessions.

The reason these are sessions rather than a flat list of questions is not the
sidebar. `hermes -z` starts cold every time — there is no server-side memory to
resume — so without threading the earlier turns into the prompt, "and what
about the proposal?" reaches a model that never saw the question before it.
The grouping is the visible half; the context is the point.
"""

from __future__ import annotations

import pytest

from cos import chats


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "STORE", tmp_path / "chats.json")


def _turn(q, a="an answer"):
    return {"question": q, "answer": a, "hits": [], "asked_at": "2026-08-07 23:00"}


class TestSessions:
    def test_create_and_list(self):
        s = chats.create()
        assert [x["id"] for x in chats.summaries()] == [s["id"]]

    def test_title_comes_from_the_first_question(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("What is the status of Northwind?"))
        assert chats.summaries()[0]["title"] == "What is the status of Northwind?"

    def test_a_long_question_is_trimmed(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("x" * 200))
        assert len(chats.summaries()[0]["title"]) <= chats.TITLE_MAX + 1

    def test_the_second_question_does_not_retitle(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("first"))
        chats.add_turn(s["id"], _turn("second"))
        assert chats.summaries()[0]["title"] == "first"

    def test_rename_sticks(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("first"))
        assert chats.rename(s["id"], "Northwind")
        assert chats.summaries()[0]["title"] == "Northwind"

    def test_a_renamed_session_is_not_retitled_by_a_new_turn(self):
        s = chats.create()
        chats.rename(s["id"], "Kept")
        chats.add_turn(s["id"], _turn("something else"))
        assert chats.summaries()[0]["title"] == "Kept"

    def test_an_empty_rename_is_refused(self):
        s = chats.create()
        chats.rename(s["id"], "Real")
        assert chats.rename(s["id"], "   ") is False
        assert chats.summaries()[0]["title"] == "Real"

    def test_delete(self):
        s = chats.create()
        assert chats.delete(s["id"])
        assert chats.summaries() == []
        assert chats.delete(s["id"]) is False

    def test_summaries_do_not_carry_the_turns(self):
        """A hundred sessions of full answers would be megabytes on every page
        load."""
        s = chats.create()
        chats.add_turn(s["id"], _turn("q", "a very long answer " * 200))
        row = chats.summaries()[0]
        assert row["turns"] == 1 and "answer" not in row


class TestOrder:
    def test_newest_activity_first_before_any_dragging(self):
        a, b = chats.create(), chats.create()
        chats.add_turn(a["id"], _turn("touched later"))
        assert chats.summaries()[0]["id"] == a["id"]

    def test_a_move_freezes_the_whole_order(self):
        """Otherwise the untouched sessions keep re-sorting by recency and
        shuffle around the one that was pinned."""
        a, b, c = chats.create(), chats.create(), chats.create()
        ids = [x["id"] for x in chats.summaries()]
        chats.move(ids[2], above=ids[0], below=ids[1])
        assert [x["id"] for x in chats.summaries()] == [ids[0], ids[2], ids[1]]
        chats.add_turn(ids[1], _turn("newly active"))
        assert [x["id"] for x in chats.summaries()] == [ids[0], ids[2], ids[1]]

    def test_move_to_the_top(self):
        a, b = chats.create(), chats.create()
        ids = [x["id"] for x in chats.summaries()]
        chats.move(ids[1], below=ids[0])
        assert chats.summaries()[0]["id"] == ids[1]

    def test_moving_an_unknown_session_is_refused(self):
        assert chats.move("nope", None, None) is False


class TestContext:
    def test_prior_turns_are_available(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("who decides at Northwind", "Morgan."))
        ctx = chats.context(s["id"])
        assert ctx[0]["answer"] == "Morgan."

    def test_context_is_bounded(self):
        """Every prior turn is tokens on every later question in the session."""
        s = chats.create()
        for n in range(20):
            chats.add_turn(s["id"], _turn(f"q{n}"))
        assert len(chats.context(s["id"])) == chats.CONTEXT_TURNS

    def test_a_turn_with_no_answer_is_not_context(self):
        s = chats.create()
        chats.add_turn(s["id"], {"question": "failed one", "answer": ""})
        assert chats.context(s["id"]) == []

    def test_unknown_session_has_no_context(self):
        assert chats.context("nope") == []


class TestSearch:
    def test_finds_by_the_question(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("what did we agree with Google"))
        assert chats.search("google")[0]["id"] == s["id"]

    def test_finds_by_something_in_the_answer(self):
        """What you remember is usually a phrase from the answer, not how you
        happened to word the question."""
        s = chats.create()
        chats.add_turn(s["id"], _turn("who decides", "Morgan is the decision maker."))
        hit = chats.search("decision maker")
        assert hit and hit[0]["id"] == s["id"]
        assert "Morgan" in hit[0]["excerpt"]

    def test_finds_by_title(self):
        s = chats.create()
        chats.rename(s["id"], "Agency paperwork")
        assert chats.search("agency")[0]["id"] == s["id"]

    def test_case_insensitive(self):
        s = chats.create()
        chats.add_turn(s["id"], _turn("NORTHWIND status"))
        assert chats.search("northwind")

    def test_empty_query_returns_nothing(self):
        chats.create()
        assert chats.search("  ") == []


class TestResilience:
    def test_a_corrupt_store_does_not_crash(self):
        chats.STORE.parent.mkdir(parents=True, exist_ok=True)
        chats.STORE.write_text("{ not json")
        assert chats.summaries() == []
        assert chats.create()

    def test_adding_to_an_unknown_session_is_survivable(self):
        assert chats.add_turn("nope", _turn("q")) is None
