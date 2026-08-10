"""Timezone handling.

Regression guard for a real bug found 2026-08-01. Kuzu stores TIMESTAMP values
naive, but they are UTC: a message whose `Date:` header reads
`Sat, 1 Aug 2026 21:30:02 -0700` is stored as `2026-08-02T04:30:02`.

Kiran was comparing those against a naive local `datetime.now()`, so every
elapsed-time figure was skewed by the UTC offset — 7 hours in PDT. That is
enough to move a value across a day boundary, and it produced *negative* wait
times for mail that had just arrived.
"""

from datetime import datetime, timedelta, timezone

from cos.contacts import Counterparty, _parse_ts, utc_now
from cos.identity import classify_address
from cos.reports import owed_replies


class TestParseTimestamp:
    def test_naive_input_is_treated_as_utc(self):
        parsed = _parse_ts("2026-08-02T04:30:02")
        assert parsed.tzinfo is timezone.utc
        assert parsed.hour == 4

    def test_the_real_example_round_trips(self):
        """Date: Sat, 1 Aug 2026 21:30:02 -0700  ==  2026-08-02T04:30:02Z"""
        header_time = datetime(
            2026, 8, 1, 21, 30, 2, tzinfo=timezone(timedelta(hours=-7))
        )
        assert _parse_ts("2026-08-02T04:30:02") == header_time

    def test_offset_aware_input_is_left_alone(self):
        parsed = _parse_ts("2026-08-02T04:30:02+02:00")
        assert parsed.utcoffset() == timedelta(hours=2)

    def test_bad_and_empty_input(self):
        assert _parse_ts(None) is None
        assert _parse_ts("") is None
        assert _parse_ts("not a timestamp") is None


class TestClock:
    def test_utc_now_is_aware(self):
        assert utc_now().tzinfo is timezone.utc

    def test_comparable_with_parsed_timestamps(self):
        """The bug was a TypeError waiting to happen and a silent skew in the
        meantime; both sides must be aware."""
        assert (utc_now() - _parse_ts("2020-01-01T00:00:00")).days > 0


class TestNoNegativeWaits:
    def test_message_from_minutes_ago_is_not_negative(self):
        """With the old naive-local comparison this produced -1 days for any
        message that arrived within the UTC offset."""
        now = utc_now()
        cp = Counterparty(
            address="a@x.com",
            name=None,
            verdict=classify_address("a@x.com"),
            last_inbound=now - timedelta(minutes=10),
            last_outbound=now - timedelta(days=3),
            inbound_count=4,
            outbound_count=4,
        )
        owed = owed_replies({"a@x.com": cp}, now, 90)
        assert len(owed) == 1
        assert owed[0].days_waiting == 0

    def test_day_boundary_is_not_shifted(self):
        """Exactly 30 days must read as 30, not 29 or 31."""
        now = utc_now()
        cp = Counterparty(
            address="a@x.com",
            name=None,
            verdict=classify_address("a@x.com"),
            last_inbound=now - timedelta(days=30),
            last_outbound=now - timedelta(days=60),
            inbound_count=4,
            outbound_count=4,
        )
        assert owed_replies({"a@x.com": cp}, now, 90)[0].days_waiting == 30
