"""Questions answered without a model.

"What is today's date?" measured 5.8 seconds, 1,305 tokens in and 55 out — for
a date the pipeline had already written into the prompt. Nearly all of it was
booting an agent and one round trip to OpenRouter so a remote model could read
our own sentence back to us.

The risk is the opposite of the reward: a miss costs five seconds, a false
match costs trust. So most of this file is about what must NOT be answered.
"""

from __future__ import annotations

import pytest

from cos import instant

SNAP = {
    "generated_at": "2026-08-08T08:00:00",
    "owed_total": 33,
    "owed": [
        {"who": "Pat Fisher", "days": 73, "subject": "CBP Demo Strategy"},
        {"who": "Robin Vale", "days": 71, "subject": "intro"},
        {"who": "Max Story", "days": 66, "subject": "pricing"},
    ],
    "quiet": [
        {"name": "Nightowl", "days": 58, "ball": "them"},
        {"name": "Northwind", "days": 50, "ball": "them"},
    ],
}


class TestAnswered:
    @pytest.mark.parametrize("q", [
        "What is today's date?", "what's the date", "what day is it today",
        "what time is it", "What is today?",
    ])
    def test_date_questions(self, q):
        got = instant.answer(q, SNAP)
        assert got and "2026" in got

    @pytest.mark.parametrize("q", [
        "Who has been waiting longest for a reply from me?",
        "who is waiting on me", "who has been waiting the longest",
        "who is waiting", "how many people are waiting for a response",
    ])
    def test_waiting_questions(self, q):
        got = instant.answer(q, SNAP)
        assert got and "Pat Fisher" in got and "73" in got

    def test_waiting_names_the_total_as_well_as_the_leader(self):
        assert "33" in instant.answer("who is waiting on me", SNAP)

    @pytest.mark.parametrize("q", [
        "Which deals have gone quiet?", "what deals are cold",
        "which deals have gone silent",
    ])
    def test_quiet_questions(self, q):
        got = instant.answer(q, SNAP)
        assert got and "Nightowl" in got and "Northwind" in got

    def test_numbers_match_the_snapshot_exactly(self):
        """The whole reason this exists. Asked the same question three times
        the model answered "50 days quiet", "50 days", and "roughly 45 days"
        for a number the pipeline had computed exactly."""
        got = instant.answer("which deals have gone quiet", SNAP)
        assert "58d" in got and "50d" in got
        assert "45" not in got


class TestMustFallThrough:
    """A false match is worse than being slow. Each of these asks something
    the snapshot does not contain, or asks a second thing as well."""

    @pytest.mark.parametrize("q", [
        # A second clause. The extra question is judgement, which is the
        # model's job — and this exact string matched once, because `^a|b|c$`
        # anchors only the outer alternatives.
        "What is on my to-do list and which should I do first?",
        "who has been waiting longest, and what should I send them?",
        "Which deals have gone quiet and why?",
        # About one specific thing, not the computed list.
        "Who is waiting on the Northwind proposal?",
        "who is waiting for the CDL draft",
        "why is Bob waiting",
        # Not questions this pipeline has precomputed at all.
        "what did I do last week?",
        "What is the single most overdue thing I should deal with today?",
        "what meetings do I have today?",
        "How much did we invoice Northwind last quarter?",
        "Who is the real decision maker at Northwind?",
        "",
        "   ",
    ])
    def test_falls_through(self, q):
        assert instant.answer(q, SNAP) is None

    def test_no_snapshot_means_no_answer(self):
        """Without a snapshot the pipeline does not know either, and guessing
        from a stale file is exactly the failure this avoids."""
        assert instant.answer("who is waiting on me", {}) is None
        assert instant.answer("which deals have gone quiet", {}) is None

    def test_the_date_still_works_without_a_snapshot(self):
        """It comes from the clock, not the pipeline."""
        assert instant.answer("what is today's date", {}) is not None


class TestEmptyStates:
    def test_nobody_waiting(self):
        snap = {"generated_at": "x", "owed": [], "owed_total": 0}
        assert "Nobody" in instant.answer("who is waiting on me", snap)

    def test_no_quiet_deals(self):
        snap = {"generated_at": "x", "quiet": []}
        assert "No deals" in instant.answer("which deals have gone quiet", snap)
