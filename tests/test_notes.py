"""Notes are the only authored data in Kiran — everything else is derived and
disposable. These tests exist because losing a note is unrecoverable."""

from datetime import datetime, timedelta, timezone

import pytest

from cos import notes as N

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=1)


@pytest.fixture
def conn(tmp_path):
    return N.connect(tmp_path / "notes.sqlite3")


DASH = """# Dashboard

## 🔴 Overdue / needs review

- an item from the user's own task system
- [ ] a checkbox task

### Northwind

<!-- kiran:begin deal:northwind -->
| Quiet | Ball |
|---|---|
| 45d | them |
- a line inside the computed block
<!-- kiran:end deal:northwind -->

**Notes**

- Morgan is the real decision maker.
- _None yet._

### Hillcrest

**Notes**

- Jesse hinted at more money.
"""


class TestExtraction:
    def test_only_notes_sections_are_captured(self):
        """Regression: every bullet in the file was captured, swallowing the
        user's task list and placeholders as if they were judgement."""
        texts = [n.text for n in N.extract_from_dashboard(DASH)]
        assert "Morgan is the real decision maker." in texts
        assert "Jesse hinted at more money." in texts
        assert "an item from the user's own task system" not in texts
        assert not any(t.startswith("[ ]") for t in texts)
        assert not any(t.startswith("_") for t in texts)

    def test_computed_block_content_is_never_a_note(self):
        texts = [n.text for n in N.extract_from_dashboard(DASH)]
        assert "a line inside the computed block" not in texts

    def test_notes_are_attached_to_their_section(self):
        by_text = {n.text: n.entity for n in N.extract_from_dashboard(DASH)}
        assert by_text["Morgan is the real decision maker."] == "deal:northwind"
        assert by_text["Jesse hinted at more money."] == "deal:hillcrest"

    def test_log_extraction_reads_the_about_tag(self):
        log = (
            "# Kiran Log\n\n"
            "## 2026-08-02 09:00 · about **northwind**\n\n- Morgan decides.\n\n"
            "## 2026-08-02 10:00\n\n- Seed round closes Q3.\n"
        )
        got = {n.text: n.entity for n in N.extract_from_log(log)}
        assert got["Morgan decides."] == "deal:northwind"
        assert got["Seed round closes Q3."] == "journal"


class TestLifecycle:
    def test_identity_survives_regeneration(self, conn):
        notes = N.extract_from_dashboard(DASH)
        N.sync(conn, notes, NOW)
        first = {r["text"]: r["first_seen"] for r in N.query(conn)}
        N.sync(conn, notes, LATER)          # dashboard regenerated, note unchanged
        again = {r["text"]: r["first_seen"] for r in N.query(conn)}
        assert first == again, "first_seen must mean when it was written"

    def test_deleting_a_note_records_it_rather_than_dropping_it(self, conn):
        N.sync(conn, N.extract_from_dashboard(DASH), NOW)
        remaining = [n for n in N.extract_from_dashboard(DASH) if "Jesse" not in n.text]
        stats = N.sync(conn, remaining, LATER)
        assert stats["removed"] == 1
        assert not any("Jesse" in r["text"] for r in N.query(conn))
        assert any("Morgan" in r["text"] for r in N.query(conn, include_removed=True))

    def test_restoring_a_note_clears_the_deletion(self, conn):
        all_notes = N.extract_from_dashboard(DASH)
        N.sync(conn, all_notes, NOW)
        N.sync(conn, [n for n in all_notes if "Jesse" not in n.text], LATER)
        stats = N.sync(conn, all_notes, LATER + timedelta(days=1))
        assert stats["restored"] == 1
        assert any("Morgan" in r["text"] for r in N.query(conn))


class TestContext:
    def test_state_at_the_time_is_stored(self, conn):
        ctx = {"deal:northwind": {"quiet_days": 45, "ball_with": "them"}}
        N.sync(conn, N.extract_from_dashboard(DASH), NOW, ctx)
        row = [r for r in N.query(conn) if "Morgan" in r["text"]][0]
        assert '"quiet_days": 45' in row["context"]

    def test_context_is_not_overwritten_on_later_runs(self, conn):
        """The point is what it looked like WHEN WRITTEN, not now."""
        N.sync(conn, N.extract_from_dashboard(DASH), NOW,
               {"deal:northwind": {"quiet_days": 45}})
        N.sync(conn, N.extract_from_dashboard(DASH), LATER,
               {"deal:northwind": {"quiet_days": 99}})
        row = [r for r in N.query(conn) if "Morgan" in r["text"]][0]
        assert '"quiet_days": 45' in row["context"]


class TestRebuildable:
    def test_index_can_be_rebuilt_from_markdown(self, conn):
        """The DB is a view. If it cannot be rebuilt, notes are not safe."""
        log = "## 2026-08-02 09:00 · about **hillcrest**\n\n- from the log\n"
        N.reindex(conn, DASH, log, NOW)
        texts = {r["text"] for r in N.query(conn)}
        assert "Morgan is the real decision maker." in texts
        assert "from the log" in texts

    def test_reindex_is_idempotent(self, conn):
        log = "## 2026-08-02 09:00\n\n- from the log\n"
        N.reindex(conn, DASH, log, NOW)
        a = len(N.query(conn))
        N.reindex(conn, DASH, log, LATER)
        assert len(N.query(conn)) == a


class TestReindexPreservesHistory:
    def test_context_and_first_seen_survive_a_rebuild(self, conn):
        """A rebuild recovers TEXT from markdown, but context and first_seen
        record a moment that is gone. Dropping them would destroy the most
        valuable column."""
        ctx = {"deal:northwind": {"quiet_days": 45, "ball_with": "them"}}
        N.sync(conn, N.extract_from_dashboard(DASH), NOW, ctx)
        before = {r["text"]: (r["first_seen"], r["context"]) for r in N.query(conn)}

        N.reindex(conn, DASH, "", LATER + timedelta(days=30))
        after = {r["text"]: (r["first_seen"], r["context"]) for r in N.query(conn)}

        morgan = "Morgan is the real decision maker."
        assert after[morgan] == before[morgan], "rebuild must not reset history"
        assert '"quiet_days": 45' in after[morgan][1]
