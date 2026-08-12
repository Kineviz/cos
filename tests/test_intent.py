"""Question or instruction.

The reported failure: "archive Insight2" and "add these prospects" came back
as summaries of the things Wei had asked to change. Every case below is one
of those two shapes, or the shape that must NOT flip — a question that would
become a wasted tool call if this got greedy.
"""

from __future__ import annotations

import pytest

from cos import intent

PANEL = "Prospects panel — the tracked deals:\n- Constella · Engaged"


class TestActions:
    @pytest.mark.parametrize("text", [
        "archive Insight2",
        "add these prospects",
        "add task: hand dashboard to Jacob",
        "mark Europol DPO unblocked",
        "move Northwind to today",
        "set Constella stage to Engaged",
        "create a GTM panel",
        "remind me to call Morgan on Friday",
        "assign the security review to Pat",
    ])
    def test_an_instruction_is_an_action(self, text):
        assert intent.classify(text).is_action, text

    @pytest.mark.parametrize("text", [
        "can you archive Insight2?",
        "could you please add a task to call Morgan",
        "Kiran, please mark the Acme review done",
        "hey can u move Northwind to soon",
    ])
    def test_politeness_does_not_hide_the_verb(self, text):
        """"Can you archive X?" has a question mark and is not a question.
        This is the commonest way a real person types an instruction."""
        assert intent.classify(text).is_action, text

    def test_the_panel_shorthand_is_a_write(self):
        """On a panel, "Constella: ball with Alberto" is a field assignment.
        There is nothing else it could mean with those rows on screen."""
        assert intent.classify("Constella: ball with Alberto",
                               screen=PANEL).is_action

    def test_the_same_shorthand_is_not_a_write_in_the_chat_box(self):
        """Off the panel there is no field to write it to, and the safe
        reading of a bare statement is that Wei is thinking out loud."""
        assert not intent.classify("Constella: ball with Alberto").is_action


class TestQuestions:
    @pytest.mark.parametrize("text", [
        "what's open with Constella?",
        "who is waiting on me",
        "which of these should I chase first?",
        "how did the Northwind deal stall",
        "why has Acme gone quiet",
        "do I owe Morgan a reply?",
        "is the security review done",
        "when did I last talk to Northwind",
    ])
    def test_a_question_stays_a_question(self, text):
        assert not intent.classify(text, screen=PANEL).is_action, text

    @pytest.mark.parametrize("text", [
        "show me my tasks",
        "list the quiet deals",
        "summarise this panel",
        "tell me about Morgan",
        "find the Acme contract",
    ])
    def test_a_read_imperative_is_a_question(self, text):
        """Typed like a command, answered like a question — and routing it as
        an action would send the assistant hunting for a write tool that does
        not exist."""
        assert not intent.classify(text, screen=PANEL).is_action, text

    def test_do_as_an_auxiliary_is_not_do_as_a_verb(self):
        assert not intent.classify("do we have a date for the Acme review?").is_action
        assert intent.classify("do the Northwind follow-up").is_action

    def test_nothing_is_a_question(self):
        assert not intent.classify("").is_action
        assert not intent.classify("   ").is_action


class TestSafety:
    def test_deleting_is_flagged_for_confirmation(self):
        got = intent.classify("delete the Northwind row")
        assert got.is_action and got.destructive

    def test_archiving_is_not(self):
        """Archive and done are reversible in the dashboard. Putting a
        confirmation in front of the most common action there is would make
        the panel chat useless."""
        got = intent.classify("archive Insight2")
        assert got.is_action and not got.destructive

    def test_the_reason_is_recorded(self):
        """Misroutes are only findable if the decision says what decided it."""
        assert intent.classify("add a task").reason
        assert intent.classify("what is open?").reason
