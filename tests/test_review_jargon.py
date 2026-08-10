"""Kiran must not talk to Wei in its own machinery's vocabulary.

Wei has asked for plain language many times. The detector has to be narrow
enough that it never fires on his own words — a style reviewer that cries wolf
gets ignored, and takes the rest of the report with it. That is not
hypothetical here: five scheduled artifacts in this vault died of exactly that.
"""

from __future__ import annotations

from cos.review import JARGON


class TestFires:
    def test_names_its_own_tools(self):
        assert JARGON.search("Let me call mcp__gbrain__search for that")

    def test_names_the_brain_software(self):
        assert JARGON.search("I checked gbrain and found nothing")

    def test_talks_about_chunks(self):
        assert JARGON.search("That page was split into 12 chunks")

    def test_narrates_tool_calls(self):
        assert JARGON.search("My tool call failed so I retried")

    def test_mentions_embeddings(self):
        assert JARGON.search("the embedding did not match")

    def test_mentions_frontmatter(self):
        assert JARGON.search("the frontmatter says status: active")


class TestStaysQuiet:
    """Every string here is ordinary business English Wei uses himself. A hit
    on any of them would make the check noise."""

    def test_plain_answers_are_clean(self):
        for reply in [
            "You met her twice in March — the 4th and the 19th.",
            "Pat Fisher has been waiting 73 days on CBP Demo Strategy.",
            "Nothing scheduled today. Tomorrow you have Gopi at 09:00.",
            "I can't find anything after March.",
            "Nightowl has gone quiet — 57 days, ball with them.",
        ]:
            assert not JARGON.search(reply), reply

    def test_his_own_vocabulary_is_not_flagged(self):
        """vault, page, brain, search — all words Wei uses. Deliberately not
        in the pattern."""
        for reply in [
            "It's in your vault under 90_agent.",
            "That's on the second page of the deck.",
            "Let me search your email for it.",
            "Worth picking his brain about the Google deal.",
        ]:
            assert not JARGON.search(reply), reply

    def test_business_words_containing_the_letters_are_safe(self):
        for reply in [
            "The chunky part of the quarter is Q3.",
            "We should stdio— no, ignore that.".replace("stdio—", "start"),
        ]:
            assert not JARGON.search(reply), reply
