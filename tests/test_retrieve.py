"""Fusing four ways of finding a page into one ranking.

Their scores are not comparable — a cosine similarity and a message count do
not live on the same scale — so pages are fused by rank. That makes agreement
between independent methods the evidence, which is the property worth having,
and it has one failure mode: a page only one method knows about.
"""

from __future__ import annotations

from cos import retrieve


class TestEachMethodKeepsItsBestAnswer:
    """Rank fusion averages down a page that one leg is certain about. The CDL
    talk page was first out of the full-question search and thirtieth out of
    the reduced-terms one, fused to eighth, and fell off a six-slot list behind
    five pages that merely have "draft" in the title."""

    def test_a_confident_single_leg_hit_survives_fusion(self):
        legs = {
            "vector": [{"slug": f"email/filler-{n}"} for n in range(30)],
            "lexical": [{"slug": "email/2026-06-29-cdl"}],
        }
        ranked = retrieve.fuse(legs)
        kept = [r["slug"] for r in
                retrieve.group(ranked, 6, keep=retrieve.champions(legs))]
        assert "email/2026-06-29-cdl" in kept
        assert len(kept) == 6

    def test_the_graph_leg_gets_no_reservation(self):
        """Backlinks come back in the order the database held them, which is
        not a ranking — promoting its first row put a Spanner catch-up thread
        into the middle of "who is the decision maker at Northwind"."""
        legs = {"graph": [{"slug": "email/unrelated"}],
                "vector": [{"slug": "email/on-topic"}]}
        assert "email/unrelated" not in retrieve.champions(legs)

    def test_a_date_question_reserves_nothing(self):
        """When the window has fired the question was topicless, so the vector
        leg's confident answer is the 2025 out-of-office reply titled "what I
        wrote last week" that the date index exists to beat."""
        legs = {"date": [{"slug": "calendar/2026-07-31-standup"}],
                "vector": [{"slug": "email/2025-08-11-what-i-wrote-last-week"}]}
        assert retrieve.champions(legs) == []

    def test_reservations_are_capped(self):
        legs = {"vector": [{"slug": "a"}], "lexical": [{"slug": "b"}],
                "recency": [{"slug": "c"}]}
        ranked = retrieve.fuse(legs)
        kept = [r["slug"] for r in
                retrieve.group(ranked, 3, keep=retrieve.champions(legs))]
        assert len(kept) == 3
