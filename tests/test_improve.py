"""The self-improvement loop, tested without running any agent.

The dangerous part of a loop that edits its own code is everything around
the agent: what gets queued, what counts as protected, when a fix may merge
without a human. Those decisions are code, so they are tested as code — the
agent itself is never invoked here.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from cos import improve


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """No test may read or write the real queue, policy, or conversation DB."""
    monkeypatch.setattr(improve, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(improve, "POLICY_FILE", tmp_path / "policy.yaml")
    monkeypatch.setattr(improve, "HERMES_DB", tmp_path / "absent.db")


class TestPolicy:
    def test_missing_file_means_defaults(self):
        assert improve.load_policy() == improve.DEFAULT_POLICY

    def test_file_overrides_key_by_key(self, tmp_path):
        improve.POLICY_FILE.write_text("auto_apply: false\n")
        policy = improve.load_policy()
        assert policy["auto_apply"] is False
        # One key set must not silently drop the protected list.
        assert policy["protected"] == improve.DEFAULT_POLICY["protected"]

    def test_unreadable_policy_fails_closed(self):
        """A broken fence must stop the loop, not free it."""
        improve.POLICY_FILE.write_text("auto_apply: [unclosed")
        assert improve.load_policy()["auto_apply"] is False

    def test_the_loop_itself_is_protected_by_default(self):
        hits = improve.protected_hits(
            ["src/cos/improve.py", "src/cos/retrieve.py"],
            improve.DEFAULT_POLICY)
        assert hits == ["src/cos/improve.py"]

    def test_protected_prefix_covers_directories(self):
        hits = improve.protected_hits(
            ["scripts/cos-refresh.sh"], improve.DEFAULT_POLICY)
        assert hits == ["scripts/cos-refresh.sh"]


class TestQueue:
    def test_add_and_read_back(self):
        improve.add("flagged", "who leads the Northwind deal?",
                    complaint="named the wrong person")
        (item,) = improve.queue("open")
        assert item["kind"] == "flagged"
        assert "Northwind" in item["question"]

    def test_the_same_question_files_once(self):
        """Flagged twice in frustration, or scanned twice — one item."""
        improve.add("slow", "What did  I do last week?")
        improve.add("flagged", "what did I do last week?",
                    complaint="missed the Northwind call")
        items = improve.queue("open")
        assert len(items) == 1
        assert "Northwind" in items[0]["complaint"]

    def test_status_moves_by_id_and_by_branch(self):
        a = improve.add("flagged", "q1")
        improve.set_status(a["id"], improve.PROPOSED, branch="improve/x")
        improve.set_status("", improve.APPLIED, branch="improve/x")
        assert improve.queue(improve.APPLIED)[0]["id"] == a["id"]


class TestSlowScan:
    def _db(self, tmp_path, rows):
        db_path = tmp_path / "state.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE messages (session_id TEXT, role TEXT, "
                   "content TEXT, timestamp REAL)")
        db.executemany("INSERT INTO messages VALUES (?,?,?,?)", rows)
        db.commit()
        db.close()
        return db_path

    def test_a_slow_answer_is_queued_with_its_latency(self, monkeypatch, tmp_path):
        now = time.time()
        db = self._db(tmp_path, [
            ("s1", "user", "Today is Monday.\n\nComputed facts.\n\n"
                           "who is waiting on me?", now - 400),
            ("s1", "assistant", "Here are the people waiting…", now - 100),
        ])
        monkeypatch.setattr(improve, "HERMES_DB", db)
        (item,) = improve.scan_slow(hours=1, threshold_s=120)
        assert item["question"] == "who is waiting on me?"
        assert item["latency_s"] == pytest.approx(300, abs=2)

    def test_fast_answers_and_alerts_are_not_problems(self, monkeypatch, tmp_path):
        now = time.time()
        db = self._db(tmp_path, [
            ("s1", "user", "quick one", now - 10),
            ("s1", "assistant", "done", now - 5),
            ("s2", "user", "cron tick", now - 900),
            ("s2", "assistant", "🔴 *cos — something is broken*", now - 200),
        ])
        monkeypatch.setattr(improve, "HERMES_DB", db)
        assert improve.scan_slow(hours=1, threshold_s=120) == []

    def test_the_injected_preamble_is_stripped(self):
        content = ("Today is Monday 10 August 2026.\n\n"
                   "Computed facts as of 10:24 — authoritative:\n- 33 people\n\n"
                   "suggest HKJC pricing")
        assert improve._question_of(content) == "suggest HKJC pricing"


class TestBenchRegressions:
    def _write_runs(self, monkeypatch, tmp_path, accuracies):
        """One nightly file per run; `accuracies` is per-run {qid: accuracy}."""
        from cos import bench
        monkeypatch.setattr(bench, "RESULTS_DIR", tmp_path / "bench")
        bench.RESULTS_DIR.mkdir()
        for n, run in enumerate(accuracies):
            rows = [{"id": qid, "question": f"question {qid}", "accuracy": a,
                     "answer": "…"} for qid, a in run.items()]
            (bench.RESULTS_DIR / f"2026080{n + 1}-000000-nightly.json").write_text(
                json.dumps({"rows": rows}))

    def test_used_to_pass_now_fails_is_a_regression(self, monkeypatch, tmp_path):
        self._write_runs(monkeypatch, tmp_path, [
            {"t1": 1.0}, {"t1": 1.0}, {"t1": 1.0}, {"t1": 0.5},
        ])
        (item,) = improve.bench_regressions()
        assert item["kind"] == "bench"
        assert "t1" in item["complaint"]

    def test_a_question_that_always_fails_is_not_a_regression(
            self, monkeypatch, tmp_path):
        """Chronic failure is a known problem, not a regression — filing it
        nightly would fill the queue with the same item forever."""
        self._write_runs(monkeypatch, tmp_path, [
            {"t1": 0.0}, {"t1": 0.5}, {"t1": 0.0}, {"t1": 0.0},
        ])
        assert improve.bench_regressions() == []

    def test_too_little_history_stays_silent(self, monkeypatch, tmp_path):
        """Two runs cannot tell noise from regression — the benchmark swings
        ±6 points between identical runs."""
        self._write_runs(monkeypatch, tmp_path, [{"t1": 1.0}, {"t1": 0.0}])
        assert improve.bench_regressions() == []


class TestAdvisor:
    def test_a_clear_verdict_is_parsed(self, monkeypatch, tmp_path):
        class Out:
            stdout = 'Some preamble {"ship": true, "reason": "narrow, tested"}'
        monkeypatch.setattr(improve.subprocess, "run",
                            lambda *a, **k: Out())
        monkeypatch.setattr(improve, "_git",
                            lambda *a, **k: type("R", (), {"stdout": ""})())
        v = improve._advisor_verdict(tmp_path, "HEAD")
        assert v["ship"] is True

    def test_no_readable_verdict_is_a_no(self, monkeypatch, tmp_path):
        """The advisor failing to answer must never count as approval."""
        class Out:
            stdout = "I think this looks fine overall!"
        monkeypatch.setattr(improve.subprocess, "run",
                            lambda *a, **k: Out())
        monkeypatch.setattr(improve, "_git",
                            lambda *a, **k: type("R", (), {"stdout": ""})())
        assert improve._advisor_verdict(tmp_path, "HEAD")["ship"] is False


class TestCoverage:
    """The weekly pass: what was actually asked, against what the exam
    grades. Wei: "we can look at the conversation to check our coverage and
    see what we need to improve"."""

    def _chats(self, tmp_path, monkeypatch, questions):
        f = tmp_path / "chats.json"
        f.write_text(json.dumps({"sessions": [{"turns": [
            {"question": q, "asked_at": time.time()} for q in questions]}]}))
        monkeypatch.setattr(improve, "CHATS_FILE", f)
        monkeypatch.setattr(improve, "COVERAGE_STAMP", tmp_path / "stamp")

    def test_questions_are_classified_by_kind(self, tmp_path, monkeypatch):
        self._chats(tmp_path, monkeypatch, [
            "Who runs Northwind?",
            "How many deals mention pricing?",
            "Catch me up on the Falcon project",
            "archive the Northwind row",
        ])
        cov = improve.coverage()
        assert cov["kinds"]["lookup"] == 1
        assert cov["kinds"]["sweep"] == 1
        assert cov["kinds"]["timeline"] == 1
        assert cov["actions"] == 1

    def test_a_kind_asked_weekly_but_ungraded_files_a_gap(
            self, tmp_path, monkeypatch):
        from cos import bench
        self._chats(tmp_path, monkeypatch,
                    ["Who introduced me to Sam?"] * 3)
        monkeypatch.setattr(bench, "QUESTIONS", [
            q for q in bench.QUESTIONS if q.category != "multihop"])
        improve.coverage_pass()
        assert any("multihop" in i["question"] for i in improve.queue())

    def test_graded_kinds_file_nothing(self, tmp_path, monkeypatch):
        """A gap report that cries every week gets ignored like any other
        noisy alarm."""
        self._chats(tmp_path, monkeypatch,
                    ["How many deals mention pricing?"] * 4)
        improve.coverage_pass()          # sweep IS graded (k1)
        assert improve.queue() == []

    def test_the_pass_runs_weekly_not_nightly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(improve, "COVERAGE_STAMP", tmp_path / "stamp")
        assert improve._coverage_due() is True
        (tmp_path / "stamp").write_text(
            improve.datetime.now().strftime("%Y-%m-%d"))
        assert improve._coverage_due() is False

    def test_pushback_is_counted(self, tmp_path, monkeypatch):
        self._chats(tmp_path, monkeypatch, [])
        db_path = tmp_path / "state.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE messages (session_id TEXT, role TEXT, "
                   "content TEXT, timestamp REAL)")
        now = time.time()
        db.executemany("INSERT INTO messages VALUES (?,?,?,?)", [
            ("s", "user", "who is waiting on me?", now - 60),
            ("s", "user", "that's wrong, I meant this week", now - 30),
        ])
        db.commit(); db.close()
        monkeypatch.setattr(improve, "HERMES_DB", db_path)
        assert improve.coverage()["pushback"] == 1
