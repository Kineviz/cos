"""A monitor that has only ever returned "ok" is not evidence of anything.

Each test here reconstructs a failure this system has actually had and asserts
the check catches it. The TCC case is the one that matters most: for two days
`git status` failed on a permission error and returned empty stdout, and the
caller read empty as clean. If check_committed ever reports ok in that
situation, this whole module is decoration.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cos import health
from cos.health import FAIL, OK, UNKNOWN, WARN, Check


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestCommitted:
    def test_permission_failure_is_not_clean(self, monkeypatch):
        """The bug, exactly. git exits non-zero with empty stdout; an
        is-the-output-empty test reads that as a clean tree."""
        monkeypatch.setattr(
            health, "_run",
            lambda *a, **k: _proc(128, "", "fatal: Unable to read current working directory"),
        )
        c = health.check_committed("vault", Path("/x"))
        assert c.status == FAIL
        assert c.bad

    def test_clean_tree_is_ok(self, monkeypatch):
        monkeypatch.setattr(health, "_run", lambda *a, **k: _proc(0, ""))
        assert health.check_committed("vault", Path("/x")).status == OK

    def test_uncommitted_work_warns(self, monkeypatch):
        monkeypatch.setattr(
            health, "_run", lambda *a, **k: _proc(0, " M a.md\n M b.md\n?? c.md\n")
        )
        c = health.check_committed("vault", Path("/x"))
        assert c.status == WARN
        assert "3 uncommitted" in c.detail


class TestIndexed:
    def test_brain_behind_repo_fails(self, monkeypatch):
        monkeypatch.setattr(health, "_sources_row", lambda s: {
            "last_commit": "a" * 40, "sync_age_s": 60.0, "path": "/vault"})
        monkeypatch.setattr(health, "_git_head", lambda r: "b" * 40)
        monkeypatch.setattr(health, "_run", lambda *a, **k: _proc(0, "7\n"))
        c = health.check_indexed("vault")
        assert c.status == FAIL
        assert "7 commit(s) behind" in c.detail

    def test_in_step_is_ok(self, monkeypatch):
        monkeypatch.setattr(health, "_sources_row", lambda s: {
            "last_commit": "a" * 40, "sync_age_s": 60.0, "path": "/vault"})
        monkeypatch.setattr(health, "_git_head", lambda r: "a" * 40)
        assert health.check_indexed("vault").status == OK

    def test_unreadable_head_is_unknown_not_ok(self, monkeypatch):
        """If we cannot read HEAD we do not know the answer. Reporting ok here
        is the original sin."""
        monkeypatch.setattr(health, "_sources_row", lambda s: {
            "last_commit": "a" * 40, "sync_age_s": 60.0, "path": "/vault"})
        monkeypatch.setattr(health, "_git_head", lambda r: None)
        c = health.check_indexed("vault")
        assert c.status == UNKNOWN
        assert c.bad, "unknown must count as needing attention"

    def test_no_sources_row_is_unknown(self, monkeypatch):
        monkeypatch.setattr(health, "_sources_row", lambda s: None)
        assert health.check_indexed("vault").status == UNKNOWN


class TestRefresh:
    def test_stale_log_fails(self, monkeypatch, tmp_path):
        log = tmp_path / "refresh.log"
        log.write_text("x")
        old = time.time() - 3 * 3600
        import os
        os.utime(log, (old, old))
        monkeypatch.setattr(health, "REFRESH_LOG", log)
        c = health.check_refresh_ran()
        assert c.status == FAIL
        assert "has not run" in c.detail

    def test_missing_log_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(health, "REFRESH_LOG", tmp_path / "nope.log")
        assert health.check_refresh_ran().status == UNKNOWN

    def test_failed_steps_are_caught(self, monkeypatch, tmp_path):
        log = tmp_path / "refresh.log"
        log.write_text(
            "── 2026-08-07 17:00:00 ───\n"
            "  ok something\n"
            "  ! brief failed\n"
            "  ! commit step failed\n"
            "  done 17:01:00\n"
        )
        monkeypatch.setattr(health, "REFRESH_LOG", log)
        c = health.check_refresh_steps()
        assert c.status == FAIL
        assert "2 step(s) failed" in c.detail

    def test_only_the_latest_run_is_judged(self, monkeypatch, tmp_path):
        """Yesterday's failure is not today's. Reporting a fixed problem is
        how a report earns the mute button."""
        log = tmp_path / "refresh.log"
        log.write_text(
            "── 2026-08-06 09:00:00 ───\n  ! everything failed\n  done 09:01:00\n"
            "── 2026-08-07 17:00:00 ───\n  all good\n  done 17:01:00\n"
        )
        monkeypatch.setattr(health, "REFRESH_LOG", log)
        assert health.check_refresh_steps().status == OK

    def test_a_run_in_progress_is_not_a_fault(self, monkeypatch, tmp_path):
        """A cycle takes minutes. Flagging every overlap would make this fire
        constantly and teach Wei to ignore it."""
        log = tmp_path / "refresh.log"
        log.write_text("── 2026-08-07 17:00:00 ───\n  calendar: 3 pages\n")
        monkeypatch.setattr(health, "REFRESH_LOG", log)
        c = health.check_refresh_steps()
        assert c.status == OK
        assert "in progress" in c.detail

    def test_a_run_that_never_finished_fails(self, monkeypatch, tmp_path):
        import os

        log = tmp_path / "refresh.log"
        log.write_text("── 2026-08-07 09:00:00 ───\n  calendar: 3 pages\n")
        old = time.time() - 2 * 3600
        os.utime(log, (old, old))
        monkeypatch.setattr(health, "REFRESH_LOG", log)
        c = health.check_refresh_steps()
        assert c.status == FAIL
        assert "never finished" in c.detail


class TestAgentTools:
    def test_server_with_zero_tools_fails(self, monkeypatch, tmp_path):
        """The config-type-coercion failure mode: the server connects, reports
        0 tools, and the agent silently loses the capability."""
        log = tmp_path / "agent.log"
        log.write_text(
            "MCP server 'gbrain' (stdio): registered 106 tool(s): a, b\n"
            "MCP server 'clock' (stdio): registered 0 tool(s)\n"
        )
        monkeypatch.setattr(health, "AGENT_LOG", log)
        c = health.check_agent_tools(("gbrain", "clock"))
        assert c.status == FAIL
        assert "clock" in c.detail

    def test_all_present_is_ok(self, monkeypatch, tmp_path):
        log = tmp_path / "agent.log"
        log.write_text(
            "MCP server 'gbrain' (stdio): registered 106 tool(s): a\n"
            "MCP server 'clock' (stdio): registered 3 tool(s): b\n"
        )
        monkeypatch.setattr(health, "AGENT_LOG", log)
        assert health.check_agent_tools(("gbrain", "clock")).status == OK

    def test_missing_log_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(health, "AGENT_LOG", tmp_path / "nope.log")
        assert health.check_agent_tools().status == UNKNOWN


class TestRendering:
    def test_unknown_counts_as_needing_attention(self):
        assert Check("x", UNKNOWN, "d").bad
        assert Check("x", WARN, "d").bad
        assert Check("x", FAIL, "d").bad
        assert not Check("x", OK, "d").bad

    def test_healthy_render_is_one_line_of_substance(self):
        out = health.render([Check("a", OK, "fine"), Check("b", OK, "fine")])
        assert "All 2 checks pass" in out

    def test_failures_lead_and_carry_evidence(self):
        out = health.render([
            Check("a", OK, "fine"),
            Check("b", FAIL, "broken", evidence="git -C /vault status"),
        ])
        assert "1 of 2 checks need attention" in out
        assert "git -C /vault status" in out
        assert out.index("broken") < out.index("Passing:")
