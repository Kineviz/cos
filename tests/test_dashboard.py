"""The dashboard is regenerated on every run, so the one thing it must never do
is lose what the user wrote in it."""

from datetime import datetime, timedelta, timezone

from cos.contacts import Counterparty
from cos.dashboard import parse_blocks, render
from cos.identity import classify_address
from cos.reports import deal_status, owed_replies
from cos.vault import Deal

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _fixture():
    cp = Counterparty(
        address="a@acme.com", name="A Person",
        verdict=classify_address("a@acme.com"),
        last_inbound=NOW - timedelta(days=40),
        last_outbound=NOW - timedelta(days=60),
        inbound_count=5, outbound_count=5, last_inbound_subject="Pricing",
    )
    ledger = {"a@acme.com": cp}
    deals = [Deal(name="Acme", source_file="Pipeline.md", domains=["acme.com"])]
    return deal_status(deals, ledger, NOW), owed_replies(ledger, NOW, 90)


def _first(existing=None):
    statuses, owed = _fixture()
    return render(NOW, statuses, owed, 30, existing)


class TestPreservation:
    def test_prose_outside_blocks_survives(self):
        doc, _ = _first()
        doc = doc.replace("# Dashboard\n", "# Dashboard\n\nMonday: paper, not demos.\n")
        again, _ = _first(doc)
        assert "Monday: paper, not demos." in again

    def test_notes_under_a_deal_survive(self):
        doc, _ = _first()
        doc = doc.replace("**Notes**\n", "**Notes**\n\n- Morgan is the decision maker.\n")
        again, _ = _first(doc)
        assert "- Morgan is the decision maker." in again

    def test_user_invented_section_survives(self):
        doc, _ = _first()
        doc += "\n## My own section\n\nSeed round closes Q3.\n"
        again, _ = _first(doc)
        assert "## My own section" in again and "Seed round closes Q3." in again

    def test_computed_content_is_actually_refreshed(self):
        doc, _ = _first()
        stale = doc.replace("40d", "999d")
        again, _ = _first(stale)
        assert "999d" not in again

    def test_repeated_runs_are_stable(self):
        """Regeneration must not accumulate cruft — a common failure in
        managed-block schemes."""
        a, _ = _first()
        b, _ = _first(a)
        c, _ = _first(b)
        assert b == c


class TestEditsInsideComputedBlocks:
    def test_user_line_is_rescued_not_destroyed(self):
        doc, _ = _first()
        doc = doc.replace(
            "<!-- cos:end overview -->",
            "- my note inside the block\n<!-- cos:end overview -->",
        )
        again, rescued = _first(doc)
        assert "- my note inside the block" in again
        assert "overview" in rescued

    def test_only_the_user_line_is_moved_not_the_whole_block(self):
        """Rescuing the entire computed body would dump a table into the
        user's notes on every run."""
        doc, _ = _first()
        doc = doc.replace(
            "<!-- cos:end overview -->",
            "- just this line\n<!-- cos:end overview -->",
        )
        again, _ = _first(doc)
        # The rescued region runs from the marker to the next structural break.
        tail = again.split("keep it here instead:_")[1]
        moved = tail.split("\n---")[0]
        assert "just this line" in moved
        assert "|" not in moved  # the computed table did not come along


class TestAdoption:
    def test_adopting_a_handwritten_file_adds_every_block(self):
        """Regression: only per-deal blocks were appended, so a pre-existing
        Dashboard.md never gained the overview or waiting-on sections."""
        existing = "# My Dashboard\n\nHand-written.\n\n## Personal\n- renew domain\n"
        out, _ = _first(existing)
        keys = parse_blocks(out).keys()
        assert "overview" in keys
        assert "owed" in keys
        assert any(k.startswith("deal:") for k in keys)
        assert "Hand-written." in out
        assert "- renew domain" in out


class TestRemovedDeals:
    def test_block_for_a_removed_deal_is_kept_with_a_note(self):
        doc, _ = _first()
        doc = doc.replace(
            "<!-- cos:begin deal:acme -->",
            "<!-- cos:begin deal:ghost -->\nold\n<!-- cos:end deal:ghost -->\n"
            "<!-- cos:begin deal:acme -->",
        )
        again, _ = _first(doc)
        assert "deal:ghost" in again
        assert "No longer produced by Kiran" in again
