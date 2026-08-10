"""Alerting must be quiet enough to stay believed.

The failure mode being defended against is not "we missed an alert" — it is
"Wei muted the bot in week two", after which every alert is missed and the
system still reports itself healthy.
"""

from __future__ import annotations

import json

import pytest

from cos import alerting
from cos.health import FAIL, OK, UNKNOWN, WARN, Check


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(alerting, "STATE_FILE", tmp_path / "alert-state.json")


BROKEN = [Check("vault committed", FAIL, "cannot read git status")]
FIXED = [Check("vault committed", OK, "clean")]


class TestTransitions:
    def test_first_breakage_alerts(self):
        d = alerting.decide(BROKEN, now=1000)
        assert [c.name for c in d.to_alert] == ["vault committed"]

    def test_still_broken_stays_quiet(self):
        """96 runs a day. This is the whole point."""
        alerting.decide(BROKEN, now=1000)
        for minute in range(15, 60 * 12, 15):
            d = alerting.decide(BROKEN, now=1000 + minute * 60)
            assert d.to_alert == []
            assert d.suppressed == 1

    def test_still_broken_re_alerts_after_a_day(self):
        """A problem that is never fixed must not be silently forgotten."""
        alerting.decide(BROKEN, now=1000)
        d = alerting.decide(BROKEN, now=1000 + alerting.COOLDOWN_SECONDS + 1)
        assert [c.name for c in d.to_alert] == ["vault committed"]

    def test_recovery_is_reported_once(self):
        alerting.decide(BROKEN, now=1000)
        d = alerting.decide(FIXED, now=2000)
        assert d.recovered == ["vault committed"]
        again = alerting.decide(FIXED, now=3000)
        assert again.recovered == []

    def test_healthy_from_the_start_says_nothing(self):
        d = alerting.decide(FIXED, now=1000)
        assert d.to_alert == [] and d.recovered == []
        assert alerting.format_alert(d) is None


class TestWhatReachesThePhone:
    def test_warn_and_unknown_do_not_page(self):
        """They are real signals and belong in the daily digest. Paging on
        them is how the digest becomes the thing that gets muted."""
        checks = [
            Check("mail", WARN, "newest message is 14h old"),
            Check("vault indexed", UNKNOWN, "cannot read the sources table"),
        ]
        d = alerting.decide(checks, now=1000)
        assert d.to_alert == []
        assert alerting.format_alert(d) is None

    def test_fail_pages(self):
        d = alerting.decide(BROKEN, now=1000)
        msg = alerting.format_alert(d)
        assert msg is not None
        assert "vault committed" in msg
        assert "cannot read git status" in msg

    def test_evidence_is_included_when_present(self):
        checks = [Check("x", FAIL, "broken", evidence="git -C /vault status")]
        msg = alerting.format_alert(alerting.decide(checks, now=1000))
        assert "git -C /vault status" in msg


class TestStateFile:
    def test_lost_state_causes_at_most_a_duplicate(self, tmp_path, monkeypatch):
        alerting.decide(BROKEN, now=1000)
        alerting.STATE_FILE.unlink()
        d = alerting.decide(BROKEN, now=1100)
        assert len(d.to_alert) == 1, "re-alerting once is the safe direction"

    def test_corrupt_state_does_not_crash(self):
        alerting.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        alerting.STATE_FILE.write_text("{not json")
        d = alerting.decide(BROKEN, now=1000)
        assert len(d.to_alert) == 1

    def test_state_records_when_it_alerted(self):
        alerting.decide(BROKEN, now=1000)
        state = json.loads(alerting.STATE_FILE.read_text())
        assert state["vault committed"]["alerted_at"] == 1000


class TestMultipleChecks:
    def test_each_check_has_its_own_cooldown(self):
        a = Check("a", FAIL, "x")
        b = Check("b", FAIL, "y")
        alerting.decide([a], now=1000)
        d = alerting.decide([a, b], now=1600)
        assert [c.name for c in d.to_alert] == ["b"]
        assert d.suppressed == 1


class TestRecoveryMeansPassing:
    """A check that was FAILing and degrades to UNKNOWN used to leave the
    failing set and be announced as "🟢 Recovered" — during exactly the outage
    that made it unknown."""

    def test_degrading_to_unknown_is_not_recovery(self):
        alerting.decide([Check("brain", FAIL, "unreachable")], now=1000)
        d = alerting.decide([Check("brain", UNKNOWN, "cannot tell")], now=2000)
        assert d.recovered == []

    def test_degrading_to_warn_is_not_recovery(self):
        alerting.decide([Check("mail", FAIL, "down")], now=1000)
        assert alerting.decide([Check("mail", WARN, "slow")], now=2000).recovered == []

    def test_actually_passing_is_recovery(self):
        alerting.decide([Check("mail", FAIL, "down")], now=1000)
        assert alerting.decide([Check("mail", OK, "fine")], now=2000).recovered == ["mail"]

    def test_a_check_that_vanishes_is_not_announced_as_fixed(self):
        """It may have disappeared because health itself crashed."""
        alerting.decide([Check("mail", FAIL, "down")], now=1000)
        assert alerting.decide([], now=2000).recovered == []
