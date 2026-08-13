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

    def test_catch_me_up_is_not_searched_for_literally(self):
        """A request to be told, not a subject. Harmless at six pages and not
        at sixteen: "Catch me up on HKJC" filled eight of the timeline's
        slots with catch-up emails about other people, and the answer lost
        half its facts. The timeline routing still reads the phrase."""
        terms = ask.search_terms("Catch me up on HKJC — what has happened "
                                 "since June?")
        assert "atch" not in terms
        assert "HKJC" in terms and "June" in terms
        # A real subject that merely contains the word is untouched.
        assert "Catchpole" in ask.search_terms("What did Catchpole send?")

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
            return SimpleNamespace(stdout=json.dumps(pages.get(q, [])).encode())

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
                            lambda *a, **k: SimpleNamespace(
                                stdout=json.dumps([old]).encode()))
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
                                [{"slug": "email/2025-09-03-falcon",
                                  "score": 0.8}]).encode()))
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
                                  "score": 0.9}]).encode()))
        monkeypatch.setattr(ask.dateindex, "read_head", lambda *a, **k: "notes")
        newest = "email/2026-06-01-falcon-track-and-trace"
        monkeypatch.setattr(ask.dateindex, "newest_matching",
                            lambda *a, **k: [{"slug": newest,
                                              "date": "2026-06-01"}])
        hits = ask.search("What did we discuss on Falcon lately?")
        assert newest in [h["slug"] for h in hits]

    def test_a_reply_cut_at_64k_keeps_the_rows_that_arrived(self, monkeypatch):
        """The regression that put the median at 28.6s and timed out three
        questions. `gbrain call search` truncates stdout at 65,536 bytes.
        qtype's wide kinds asked for limit*5 = 70 or 80 rows, the array came
        back severed mid-object, `json.loads` raised, and the except returned
        [] — so "is there anything I promised and have not delivered?" reached
        the model with an EMPTY sources block and a playbook telling it to
        search. It searched for five minutes. Measured: four of six real
        questions truncate at limit=40, and 29–38 of their rows are whole.
        """
        whole = json.dumps([{"slug": f"email/p{n}", "score": 0.9}
                            for n in range(40)])
        cut = whole[:len(whole) // 2].encode()      # severed mid-object
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(stdout=cut))
        rows = ask._search_once("/fake/gbrain", "promised not delivered", 40)
        assert 10 < len(rows) < 40, "the whole rows must survive the cut"
        assert rows[0]["slug"] == "email/p0"

    def test_a_cut_through_a_curly_quote_does_not_kill_the_question(
            self, monkeypatch):
        """The same truncation lands wherever it lands, and when that is the
        middle of a “ the decode raised UnicodeDecodeError from inside
        subprocess.run — a ValueError, caught by nothing on this path, so it
        took the whole question down rather than one leg. This is the "never
        returned at all" symptom."""
        payload = json.dumps([{"slug": "email/p0", "score": 0.9},
                              {"slug": "email/p1", "note": "“quoted”"}],
                             ensure_ascii=False)
        raw = payload.encode()
        cut = raw[:raw.index("“".encode()) + 1]     # half of a 3-byte char
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(stdout=cut))
        rows = ask._search_once("/fake/gbrain", "anything", 40)
        assert [r["slug"] for r in rows] == ["email/p0"]

    def test_the_deep_fetch_stays_under_the_truncation_cliff(self, monkeypatch):
        """Rows past DEEP_MAX are computed, serialised and then cut off — paid
        for and never delivered. limit*5 for a sixteen-page timeline asked for
        eighty."""
        asked = []
        monkeypatch.setattr(ask, "_gbrain", lambda: "/fake/gbrain")
        monkeypatch.setattr(ask.subprocess, "run",
                            lambda argv, **k: asked.append(
                                json.loads(argv[-1])["limit"])
                            or SimpleNamespace(stdout=b"[]"))
        ask.search("Catch me up on HKJC — what has happened since June?",
                   limit=16)
        assert asked and max(asked) <= ask.DEEP_MAX
        # The tuned lookup path is untouched: still thirty.
        asked.clear()
        ask.search("Who runs Northwind?", limit=6)
        assert max(asked) == 30

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
            return SimpleNamespace(stdout=b"[]")

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
                [{"slug": f"{q[:4]}-{n}"} for n in range(6)]).encode())

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


class TestInstructionsAreExecutedNotAnswered:
    """"archive Insight2" and "add these prospects" came back as summaries of
    the things Wei had asked to change. The pipeline had one route — retrieve,
    synthesise, cite — and an instruction went down it like everything else."""

    def test_an_instruction_is_marked_as_one(self, monkeypatch):
        monkeypatch.setattr(ask.threading, "Thread", _no_thread)
        job = ask.start("archive Insight2")
        assert job.intent == "action"
        assert ask.start("what's open with Constella?").intent == "question"

    def test_the_prompt_tells_it_to_act(self):
        job = ask.Job(id="j", question="archive Insight2", intent="action")
        text = ask._prompt(job)
        assert "INSTRUCTION" in text
        assert "panel_add" in text

    def test_a_question_is_left_alone(self):
        text = ask._prompt(ask.Job(id="j", question="who is waiting"))
        assert "INSTRUCTION" not in text

    def test_the_retrieved_pages_stop_being_the_answer(self):
        """Told to "answer from these pages", the model answers instead of
        acting — so for an instruction the same pages are handed over as
        background for finding the target, and nothing more."""
        hits = [{"slug": "10_wiki/clients/insight2", "date": "", "context": "…"}]
        assert "Answer from these" in ask._sources_block(hits)
        assert "Answer from these" not in ask._sources_block(hits, action=True)

    def test_every_retrieved_page_reaches_the_prompt(self):
        """qtype asks for sixteen pages for a timeline and this handed over
        six, so the wide kinds paid for the retrieval and got the narrow
        prompt. Worse for a timeline, whose hits are sorted oldest-first: the
        six were the six OLDEST, so "what has happened since June" was
        answered from pages that stopped before June."""
        hits = [{"slug": f"email/2026-0{n}-01-hkjc", "date": f"2026-0{n}-01",
                 "context": f"beat {n}"} for n in range(1, 9)]
        block = ask._sources_block(hits)
        assert all(h["slug"] in block for h in hits)
        assert "beat 8" in block

    def test_a_deletion_asks_first(self):
        job = ask.Job(id="j", question="delete the Northwind row",
                      intent="action", destructive=True)
        assert "ask before doing it" in ask._prompt(job)

    def test_an_instruction_never_reads_the_cache(self, monkeypatch):
        """A cached receipt confirms work that never ran."""
        monkeypatch.setattr(ask.threading, "Thread", _no_thread)
        ask._remember("archive Insight2", "Archived: Insight2.")
        job = ask.start("archive Insight2")
        assert job.status == "running"
        assert job.cached_age is None

    def test_an_instruction_never_writes_the_cache(self, monkeypatch):
        monkeypatch.setattr(ask.subprocess, "run", lambda *a, **k: SimpleNamespace(
            stdout="Archived: Insight2.", stderr="", returncode=0))
        job = ask.Job(id="j", question="archive Insight2", intent="action")
        ask._synthesise(job)
        assert job.answer == "Archived: Insight2."
        assert ask.cached_answer("archive Insight2") is None

    def test_a_question_still_caches(self, monkeypatch):
        monkeypatch.setattr(ask.subprocess, "run", lambda *a, **k: SimpleNamespace(
            stdout="Bob and Max.", stderr="", returncode=0))
        ask._synthesise(ask.Job(id="j", question="who is waiting"))
        assert ask.cached_answer("who is waiting")["answer"] == "Bob and Max."

    def test_the_instant_path_cannot_swallow_an_instruction(self, monkeypatch):
        """The snapshot can say what the to-do list holds; it cannot add to
        it. An instruction always reaches the assistant."""
        monkeypatch.setattr(ask.instant, "answer", lambda *a, **k: "canned")
        monkeypatch.setattr(ask.threading, "Thread", _no_thread)
        assert ask.start("what is on my to-do list").answer == "canned"
        assert ask.start("add breakfast to my to-do list").status == "running"


def _no_thread(**kw):
    """A job that is never actually run — these tests are about the routing
    decision, which is made before the thread starts."""
    return SimpleNamespace(start=lambda: None, daemon=True)
