from datetime import datetime, timedelta, timezone

from cos.contacts import Counterparty
from cos.identity import classify_address
from cos.reports import deal_status, owed_replies
from cos.vault import Deal, parse_markdown_table

# UTC-aware, matching what the graph actually yields — see TestTimestamps.
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def cp(address, *, inbound_days=None, outbound_days=None, n_in=5, n_out=5, subject="Hi"):
    return Counterparty(
        address=address,
        name=None,
        verdict=classify_address(address),
        last_inbound=NOW - timedelta(days=inbound_days) if inbound_days is not None else None,
        last_outbound=NOW - timedelta(days=outbound_days) if outbound_days is not None else None,
        inbound_count=n_in if inbound_days is not None else 0,
        outbound_count=n_out if outbound_days is not None else 0,
        last_inbound_subject=subject,
    )


class TestOwedReplies:
    def test_they_wrote_last_so_we_owe(self):
        ledger = {"a@x.com": cp("a@x.com", inbound_days=5, outbound_days=20)}
        owed = owed_replies(ledger, NOW, 90)
        assert len(owed) == 1
        assert owed[0].days_waiting == 5

    def test_we_wrote_last_so_we_owe_nothing(self):
        ledger = {"a@x.com": cp("a@x.com", inbound_days=20, outbound_days=5)}
        assert owed_replies(ledger, NOW, 90) == []

    def test_never_replied_to_is_excluded_by_default(self):
        """Cold outreach and newsletters have inbound but no outbound. This is
        the filter that removes almost all noise without a classifier."""
        ledger = {"a@x.com": cp("a@x.com", inbound_days=5, outbound_days=None, n_in=9)}
        assert owed_replies(ledger, NOW, 90) == []
        assert len(owed_replies(ledger, NOW, 90, require_prior_reply=False)) == 1

    def test_outside_window_is_dropped(self):
        ledger = {"a@x.com": cp("a@x.com", inbound_days=200, outbound_days=300)}
        assert owed_replies(ledger, NOW, 90) == []

    def test_internal_domains_excluded(self):
        ledger = {"a@kineviz.com": cp("a@kineviz.com", inbound_days=3, outbound_days=9)}
        assert owed_replies(ledger, NOW, 90, internal_domains=frozenset({"kineviz.com"})) == []

    def test_robots_never_appear(self):
        ledger = {"noreply@x.com": cp("noreply@x.com", inbound_days=3, outbound_days=9)}
        assert owed_replies(ledger, NOW, 90) == []

    def test_sorted_longest_wait_first(self):
        ledger = {
            "a@x.com": cp("a@x.com", inbound_days=5, outbound_days=40),
            "b@y.com": cp("b@y.com", inbound_days=30, outbound_days=40),
        }
        assert [o.days_waiting for o in owed_replies(ledger, NOW, 90)] == [30, 5]


class TestDealStatus:
    def test_quiet_days_uses_latest_contact_in_either_direction(self):
        ledger = {"a@acme.com": cp("a@acme.com", inbound_days=60, outbound_days=10)}
        deal = Deal(name="Acme", source_file="Pipeline.md", domains=["acme.com"])
        status = deal_status([deal], ledger, NOW)[0]
        assert status.days_quiet(NOW) == 10
        assert not status.ball_in_our_court()

    def test_ball_in_our_court_when_they_wrote_last(self):
        ledger = {"a@acme.com": cp("a@acme.com", inbound_days=10, outbound_days=60)}
        deal = Deal(name="Acme", source_file="Pipeline.md", domains=["acme.com"])
        assert deal_status([deal], ledger, NOW)[0].ball_in_our_court()

    def test_robot_traffic_does_not_mask_silence(self):
        """A Drive share notification must not make a dead deal look alive."""
        ledger = {
            "noreply@acme.com": cp("noreply@acme.com", inbound_days=1, outbound_days=1),
            "real@acme.com": cp("real@acme.com", inbound_days=90, outbound_days=95),
        }
        deal = Deal(name="Acme", source_file="Pipeline.md", domains=["acme.com"])
        status = deal_status([deal], ledger, NOW)[0]
        assert status.days_quiet(NOW) == 90
        assert status.contacts_seen == 1

    def test_unmapped_deal_is_reported_not_dropped(self):
        deal = Deal(name="New Thing", source_file="Prospects.md", domains=[])
        status = deal_status([deal], {}, NOW)[0]
        assert not status.mapped
        assert status.days_quiet(NOW) is None

    def test_unmapped_deals_sort_last(self):
        ledger = {"a@acme.com": cp("a@acme.com", inbound_days=5, outbound_days=5)}
        deals = [
            Deal(name="Unmapped", source_file="Prospects.md", domains=[]),
            Deal(name="Acme", source_file="Pipeline.md", domains=["acme.com"]),
        ]
        assert [s.deal.name for s in deal_status(deals, ledger, NOW)] == ["Acme", "Unmapped"]


class TestMarkdownTable:
    def test_parses_pipeline_style_table(self):
        lines = """
| Deal | Stage | Owner | Next step |
|---|---|---|---|
| [[#Hillcrest]] | Expansion | Wei | 5-step enablement |
| [[#Agency]] | **At-risk** | Wei | Unblock access |
""".splitlines()
        rows = parse_markdown_table(lines)
        assert len(rows) == 2
        assert rows[0]["deal"] == "Hillcrest"
        assert rows[1]["stage"] == "At-risk"  # bold markers stripped

    def test_stops_at_end_of_table(self):
        lines = "| A |\n|---|\n| 1 |\n\nprose after\n| B |\n|---|\n| 2 |".splitlines()
        assert parse_markdown_table(lines) == [{"a": "1"}]

    def test_no_table_returns_empty(self):
        assert parse_markdown_table(["just prose", "more prose"]) == []
