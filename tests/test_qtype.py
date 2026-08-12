"""The question-kind router: right playbook, and lookup when unsure.

The costly mistake runs one way. A lookup routed to a wide playbook wastes
a few pages of prompt; a count routed to "six best pages" answers from a
sample and presents it as the total. So the tests hold the specialised
kinds to distinctive wording and everything vague to lookup.
"""

from __future__ import annotations

from cos import qtype


def kind(q: str) -> str:
    return qtype.classify(q).kind


class TestKinds:
    def test_plain_lookup_is_the_default(self):
        assert kind("Who is the decision maker at Northwind?") == "lookup"
        assert kind("What did Morgan say about the pilot?") == "lookup"

    def test_counting_is_a_sweep(self):
        assert kind("How many deals mention pricing?") == "sweep"
        assert kind("List all my meetings in July") == "sweep"
        assert kind("Which deals have stalled this quarter?") == "sweep"

    def test_chains_are_multihop(self):
        assert kind("Who introduced me to Sam?") == "multihop"
        assert kind("How did I know the Acme CTO?") == "multihop"

    def test_did_we_ever_is_absence(self):
        assert kind("Did we ever invoice Northwind?") == "absence"
        assert kind("Is there any record of a signed NDA?") == "absence"
        assert kind("Anything I promised Morgan and never delivered?") == "absence"

    def test_stories_over_time_are_timeline(self):
        assert kind("What happened with the Falcon project since June?") == "timeline"
        assert kind("Catch me up on Northwind") == "timeline"
        assert kind("How has the Acme deal progressed over the past month?") == "timeline"

    def test_judgement_across_candidates_is_compare(self):
        assert kind("Which deal should I chase first?") == "compare"
        assert kind("Is this price in line with our past deals?") == "compare"

    def test_two_asks_in_one_sentence_are_multipart(self):
        assert kind("When is the talk due, and who has the draft?") == "multipart"

    def test_a_compound_sweep_is_still_a_sweep(self):
        """Any kind can be compound; the more specific playbook wins because
        it already enumerates."""
        assert kind("How many deals are open, and which is the largest?") == "sweep"

    def test_empty_and_vague_stay_lookup(self):
        assert kind("") == "lookup"
        assert kind("northwind?") == "lookup"
        assert kind("thoughts on the pilot") == "lookup"


class TestRouting:
    def test_every_kind_carries_its_reason(self):
        got = qtype.classify("Did we ever invoice Northwind?")
        assert got.kind == "absence" and "ever" in got.reason

    def test_wide_kinds_retrieve_wider_than_lookup(self):
        assert qtype.QType("sweep").width > qtype.QType("lookup").width
        assert qtype.QType("timeline").width > qtype.QType("lookup").width

    def test_every_specialised_kind_has_marching_orders(self):
        for k in qtype.WIDTH:
            if k != "lookup":
                assert k in qtype.PLAYBOOK, k

    def test_the_job_records_the_kind(self, monkeypatch, tmp_path):
        from cos import ask
        monkeypatch.setattr(ask, "CACHE_FILE", tmp_path / "c.json")
        # No agent run: build the job the way start() does, without starting.
        job = ask.Job(id="t", question="Did we ever invoice Northwind?")
        from cos import qtype as q
        job.qtype = q.classify(job.question).kind
        assert job.as_dict()["qtype"] == "absence"

    def test_timeline_playbook_reaches_the_prompt(self, monkeypatch, tmp_path):
        from cos import ask
        monkeypatch.setattr(ask, "CACHE_FILE", tmp_path / "c.json")
        job = ask.Job(id="t", question="Catch me up on Northwind",
                      qtype="timeline")
        prompt = ask._prompt(job)
        assert "oldest" in prompt and "story over time" in prompt

    def test_lookup_prompt_is_unchanged(self, monkeypatch, tmp_path):
        """The 11-second median lives on this path; nothing may be added."""
        from cos import ask
        monkeypatch.setattr(ask, "CACHE_FILE", tmp_path / "c.json")
        job = ask.Job(id="t", question="Who runs Northwind?", qtype="lookup")
        prompt = ask._prompt(job)
        for words in qtype.PLAYBOOK.values():
            assert words[:40] not in prompt
