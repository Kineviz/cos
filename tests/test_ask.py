"""Asking the assistant from the dashboard.

The interesting part is the cache. An answer costs up to 35 seconds of agent
loop, so reusing it matters — but "who is waiting on me" is only true for as
long as the mail behind it is. So the cache is keyed to the DATA, not to a
clock: an answer is valid while the snapshot it was computed against is still
current. The 15-minute refresh writes a new snapshot and every answer expires
with it.
"""

from __future__ import annotations

import json
from datetime import date
import time
from types import SimpleNamespace

import pytest

from cos import ask


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "CACHE_FILE", tmp_path / "ask-cache.json")
    snap = tmp_path / "dashboard.json"
    snap.write_text("{}")
    from cos import webconfig
    monkeypatch.setattr(webconfig, "SNAPSHOT", snap)
    return snap


class TestCache:
    def test_an_answer_is_reused(self):
        ask._remember("who is waiting", "Bob and Max.")
        assert ask.cached_answer("who is waiting")["answer"] == "Bob and Max."

    def test_wording_is_normalised(self):
        """Same question typed differently must not cost another 35 seconds."""
        ask._remember("Who Is  Waiting?", "Bob.")
        assert ask.cached_answer("who is waiting?")["answer"] == "Bob."

    def test_a_different_question_is_a_miss(self):
        ask._remember("who is waiting", "Bob.")
        assert ask.cached_answer("what did we agree with Google") is None

    def test_new_data_invalidates_everything(self, isolated):
        """Changed FACTS invalidate. This is the whole design."""
        isolated.write_text('{"owed_total": 33, "owed": [], "quiet": []}')
        ask._remember("who is waiting", "Bob and Max.")
        assert ask.cached_answer("who is waiting") is not None
        isolated.write_text('{"owed_total": 31, "owed": [], "quiet": []}')
        assert ask.cached_answer("who is waiting") is None

    def test_a_rewritten_but_unchanged_snapshot_keeps_the_answer(self, isolated):
        """The refresh rewrites dashboard.json every cycle whether or not
        anything moved, because generated_epoch always differs. Keying on the
        file's mtime meant 1 of 13 cached answers was still valid — an 8% hit
        rate on a 60-150 second operation, bought for no correctness at all."""
        isolated.write_text('{"owed_total": 33, "owed": [], "quiet": [], "generated_epoch": 1}')
        ask._remember("who is waiting", "Bob and Max.")
        time.sleep(0.01)
        isolated.write_text('{"owed_total": 33, "owed": [], "quiet": [], "generated_epoch": 2}')
        assert ask.cached_answer("who is waiting") is not None

    def test_an_ancient_answer_expires_even_without_new_data(self, monkeypatch):
        """If the refresh has died the snapshot never moves, and without this
        an answer would be served as current forever."""
        ask._remember("who is waiting", "Bob.")
        monkeypatch.setattr(ask, "CACHE_HARD_TTL", -1)
        assert ask.cached_answer("who is waiting") is None

    def test_age_is_reported(self):
        ask._remember("q", "a")
        assert ask.cached_answer("q")["age_seconds"] >= 0

    def test_forget_drops_it(self):
        ask._remember("q", "a")
        ask.forget("q")
        assert ask.cached_answer("q") is None

    def test_an_empty_answer_is_not_cached(self):
        """Otherwise one failure poisons the question until the next refresh."""
        ask._remember("q", "   ")
        assert ask.cached_answer("q") is None

    def test_cache_is_bounded(self):
        for n in range(ask.CACHE_MAX + 20):
            ask._remember(f"question {n}", f"answer {n}")
        assert len(ask._load_cache()) <= ask.CACHE_MAX

    def test_a_corrupt_cache_is_a_miss_not_a_crash(self):
        ask.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ask.CACHE_FILE.write_text("{ not json")
        assert ask.cached_answer("q") is None


class TestStart:
    def test_a_cached_question_returns_done_immediately(self):
        ask._remember("who is waiting", "Bob.")
        job = ask.start("who is waiting")
        assert job.status == "done"
        assert job.cached_age is not None

    def test_fresh_bypasses_the_cache(self, monkeypatch):
        ask._remember("who is waiting", "Bob.")
        started = {}
        monkeypatch.setattr(ask.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda s: started.setdefault("yes", True)})())
        job = ask.start("who is waiting", fresh=True)
        assert job.status == "running"
        assert ask.cached_answer("who is waiting") is None

    def test_an_empty_question_is_refused(self):
        with pytest.raises(ValueError):
            ask.start("   ")

    def test_an_enormous_question_is_refused(self):
        with pytest.raises(ValueError):
            ask.start("x" * 3000)


class TestTheTwoThingsThatBrokeIt:
    """Both of these were silent: no error, no exception, just a wrong or
    absent answer. Wei found them by using the thing."""

    def test_the_toolset_is_pinned(self):
        """Unpinned, the CLI path loads 47 plugins and every enabled toolset on
        top of gbrain's 106 tools. Three real questions in a row then ran past
        the 300s ceiling; "what did we discuss on falcon lately" never returned.
        Pinned, the same question answered in 20.8s."""
        assert ask.TOOLSET == "gbrain,clock,panels"

    def test_hermes_is_invoked_with_the_toolset(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

        monkeypatch.setattr(ask.subprocess, "run", fake_run)
        monkeypatch.setattr(ask, "search", lambda *a, **k: [])
        job = ask.Job(id="j", question="anything")
        ask._run(job)
        assert "-t" in seen["argv"]
        assert seen["argv"][seen["argv"].index("-t") + 1] == "gbrain,clock,panels"

    def test_search_runs_from_the_brain_directory(self, monkeypatch):
        """gbrain picks which brain to search from the WORKING DIRECTORY. Run
        from $HOME the same query returned four unrelated people's wiki pages
        instead of the Falcon email threads."""
        seen = {}

        def fake_run(argv, **kw):
            seen["cwd"] = kw.get("cwd")
            raise OSError("stop here")

        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run", fake_run)
        ask.search("falcon")
        assert seen["cwd"] == str(ask.BRAIN_DIR)
        assert seen["cwd"] != str(ask.Path.home())

    def test_the_word_i_is_not_treated_as_a_proper_noun(self):
        """It is capitalised in every English sentence. Keeping it turned
        "What did I do last week?" into "I last week", which scored 0 and then
        ran past the timeout."""
        assert ask.search_terms("What did I do last week?") == "last week"
        assert "BigBank" in ask.search_terms("How was my talk at BigBank?")

    def test_search_runs_the_reduced_terms_and_the_whole_question(self, monkeypatch):
        """Neither query is right alone: stripping sharpened sources 71%→86%
        but erased the phrase that makes a time question a time question."""
        queries, pages = [], {
            "last week": [{"slug": "a", "score": 0.9},
                          {"slug": "b", "score": 0.7}],
            "What did I do last week?": [{"slug": "c", "score": 0.8},
                                         {"slug": "a", "score": 0.9}],
        }

        def fake_run(argv, **kw):
            q = json.loads(argv[-1])["query"]
            queries.append(q)
            return SimpleNamespace(stdout=json.dumps(pages.get(q, [])))

        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run", fake_run)
        monkeypatch.setattr(ask.dateindex, "pages_between", lambda *a, **k: [])
        hits = ask.search("What did I do last week?")
        assert queries == ["last week", "What did I do last week?"]
        # Both legs contribute and each page appears once. The ORDER is no
        # longer asserted: under rank fusion it is a property of how many legs
        # agree, not of one leg's score, and pinning it here would test the
        # arithmetic rather than the behaviour.
        assert sorted(h["slug"] for h in hits) == ["a", "b", "c"]

    def test_a_page_dated_in_the_window_beats_a_better_worded_old_one(
            self, monkeypatch):
        """The failure that started this. "What did I do last week?" matched
        the WORDS "last week" and returned a 2020 email titled "things did
        last week and plan for this week" — perfect wording, five years out."""
        old = {"slug": "email/2020-03-30-things-did-last-week", "score": 0.95}

        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(stdout=json.dumps([old])))
        monkeypatch.setattr(ask.dateindex, "read_head", lambda *a, **k: "notes")
        win = ask.when.parse("What did I do last week?", ask._today())
        monkeypatch.setattr(ask.dateindex, "pages_between", lambda *a, **k: [
            {"slug": "calendar/x-standup", "date": win.start.isoformat()}])

        hits = ask.search("What did I do last week?")
        assert hits[0]["slug"] == "calendar/x-standup"

    def test_a_question_with_a_topic_keeps_its_topic(self, monkeypatch):
        """Injecting window pages as candidates drowned "What did we discuss
        on Falcon lately?" — all six sources came back as unrelated meetings
        from the most recent day and Falcon vanished from its own question."""
        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(stdout=json.dumps(
                                [{"slug": "email/2025-09-03-falcon", "score": 0.8}])))
        called = []
        monkeypatch.setattr(ask.dateindex, "pages_between",
                            lambda *a, **k: called.append(1) or [])
        # The recency leg reads the real brain off disk; stub it so this test
        # measures the window rule and not the machine it runs on.
        monkeypatch.setattr(ask.dateindex, "newest_matching", lambda *a, **k: [])
        hits = ask.search("What did we discuss on Falcon lately?")
        assert not called, "the window must only re-rank when there is a topic"
        assert hits[0]["slug"] == "email/2025-09-03-falcon"

    def test_lately_reaches_the_newest_page_even_when_nothing_is_in_window(
            self, monkeypatch):
        """"Lately" means the most recent ones, not the last thirty days.

        For three straight runs this question was answered out of 2024 and
        2025: the newest Falcon thread was 68 days old, a 30-day window held
        none of them, so nothing scored on time and wording decided — and 2024
        words the question better than 2026 does.
        """
        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(stdout=json.dumps(
                                [{"slug": "email/2024-01-09-falcon-kickoff",
                                  "score": 0.9}])))
        monkeypatch.setattr(ask.dateindex, "read_head", lambda *a, **k: "notes")
        newest = "email/2026-06-01-falcon-track-and-trace"
        monkeypatch.setattr(ask.dateindex, "newest_matching",
                            lambda *a, **k: [{"slug": newest,
                                              "date": "2026-06-01"}])
        hits = ask.search("What did we discuss on Falcon lately?")
        assert newest in [h["slug"] for h in hits]

    def test_the_recency_prior_stays_gentle_without_a_soft_window(self,
                                                                 monkeypatch):
        """The strong prior must not leak into questions that never asked for
        it. "Who is the real decision maker at Northwind?" is answered by a
        2025 email, and a rule strong enough to bury it trades six recall
        questions for four temporal ones."""
        monkeypatch.setattr(ask.dateindex, "newest_matching",
                            lambda *a, **k: pytest.fail(
                                "no soft window, so no recency leg"))
        assert ask._recency_rows("Who runs Northwind?", None, {}) == []

    def test_the_date_is_taken_from_the_slug_when_the_field_is_empty(self):
        """37% of retrieved rows come back with effective_date empty —
        measured, 11 of 30 on one query — and a date-blind row is invisible to
        every rule that follows. The date is in the slug."""
        assert ask._dated({"slug": "email/2025-08-11-what-i-wrote"}) == date(2025, 8, 11)
        assert ask._dated({"slug": "atoms/2015-01-28/tap-your-network"}) == date(2015, 1, 28)
        assert ask._dated({"slug": "people/wei"}) is None
        # The real field still wins where it exists.
        assert ask._dated({"slug": "email/2025-08-11-x",
                           "effective_date": "2026-01-02"}) == date(2026, 1, 2)

    def test_search_asks_once_when_stripping_changes_nothing(self, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append(json.loads(argv[-1])["query"])
            return SimpleNamespace(stdout="[]")

        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run", fake_run)
        ask.search("Northwind")
        assert calls == ["Northwind"]

    def test_search_never_returns_more_than_a_page(self, monkeypatch):
        """Two queries, still one page. Handing the model more context than it
        needs cost 18 points of accuracy in run 2 of the benchmark."""
        def fake_run(argv, **kw):
            q = json.loads(argv[-1])["query"]
            return SimpleNamespace(stdout=json.dumps(
                [{"slug": f"{q[:4]}-{n}"} for n in range(6)]))

        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run", fake_run)
        assert len(ask.search("What did I do last week?", limit=6)) == 6

    def test_the_child_gets_a_path_that_can_run_bun(self):
        """~/.bun/bin/gbrain is a symlink to a TypeScript file with an
        `env bun` shebang, so finding the binary is not enough."""
        assert str(ask.Path.home() / ".bun" / "bin") in ask._env()["PATH"]


class TestTheExcerptFindsTheAnswer:
    """Six pages at 1,200 characters each is the whole prompt. Which 1,200
    matters more than how many."""

    def test_a_long_page_is_cut_where_the_question_is_answered(self):
        """The task dashboard answers "why has the Northwind deal stalled?"
        in one line, and that line sits well past the first 1,200 characters.
        Sending the top of the page sent everything except the answer."""
        filler = "\n".join(f"unrelated line {n}" for n in range(200))
        page = filler + "\n- Stalled because their security review is stuck.\n"
        out = ask._context(page, terms="Why has the Northwind deal stalled?")
        assert "security review is stuck" in out

    def test_a_page_with_no_match_still_reads_from_the_top(self):
        """Degrading to the old behaviour is the point: an arbitrary slice of
        the middle is worse than the opening on a page that never mentions the
        subject."""
        page = "\n".join(f"line {n}" for n in range(400))
        out = ask._context(page, terms="northwind security review")
        assert out.startswith("line 0")

    def test_a_short_page_is_kept_whole(self):
        page = "Morgan is the decision maker.\nTaylor is the CEO."
        assert ask._context(page, terms="decision maker") == page


class TestPanelQuestions:
    """A question typed under the Tasks or Prospects panel is about the rows
    on that panel. It was going to whatever the last chat was about."""

    def test_the_screen_reaches_the_prompt(self):
        job = ask.Job(id="j", question="which should I chase first?",
                      screen="Prospects panel:\n- Northwind · 51 days quiet")
        text = ask._prompt(job)
        assert "Northwind · 51 days quiet" in text
        assert "asking about" in text

    def test_a_panel_question_skips_the_cache_both_ways(self):
        """"Summarize" from Prospects and "summarize" from Tasks are different
        questions with the same words."""
        ask._remember("summarize", "the cached answer")
        job = ask.start("summarize", session="s1",
                        screen="Tasks panel:\n- [today] ship the demo")
        assert job.cached_age is None
        assert job.answer != "the cached answer"

    def test_an_oversized_screen_is_clipped_not_refused(self):
        job = ask.Job(id="j", question="q", screen="x")
        started = ask.start("what about all this?", session="s2",
                            screen="row\n" * 5000)
        assert len(started.screen) <= 6000
