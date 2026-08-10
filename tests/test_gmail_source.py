"""The Gmail source has to filter and parse exactly like the mirror path.

Not for tidiness: extraction is keyed by content_hash, so any divergence in
which messages are kept, or in how their text is produced, silently invalidates
part of a 30,284-insight backfill. Measured preservation is 83–88%; the residue
is Gmail's authoritative threading regrouping conversations the mirror had
reconstructed, which is a correctness gain, not a bug.
"""

from __future__ import annotations

from cos.contacts import BULK_LABELS, CALENDAR_SUBJECT_PREFIXES
from cos.gmail_source import _CATEGORY_EXCLUSIONS, GmailApiSource
from cos.mailtext import read_message_body, read_message_bytes


class TestFilterParity:
    def test_every_bulk_category_is_excluded_in_the_query(self):
        for label in BULK_LABELS:
            if label.startswith("Category "):
                token = label.split()[-1].lower()
                assert f"-category:{token}" in _CATEGORY_EXCLUSIONS, label

    def test_spam_and_trash_are_not_in_the_query(self):
        # Gmail omits them unless includeSpamTrash is set, so naming them would
        # be noise — but they must not be *included* either.
        assert "spam" not in _CATEGORY_EXCLUSIONS.lower()
        assert "trash" not in _CATEGORY_EXCLUSIONS.lower()

    def test_calendar_robot_subjects_are_dropped(self):
        for prefix in CALENDAR_SUBJECT_PREFIXES:
            assert GmailApiSource._is_calendar_robot(prefix + "Some meeting")

    def test_ordinary_subjects_survive(self):
        for subject in ("Quote request", "Re: Kineviz deployment", "Invitation to speak"):
            assert not GmailApiSource._is_calendar_robot(subject)


class TestHeaderParsing:
    RAW = (
        b"From: Morgan Reyes <Morgan.Reyes@HillcrestAssociates.com>\r\n"
        b"To: Weidong Yang <you@yourcompany.com>, Sony <sony@kineviz.com>\r\n"
        b"Cc: ben@kineviz.com\r\n"
        b"Subject: Re: Seeker XR\r\n"
        b"Message-ID: <abc@hillcrest>\r\n"
        b"\r\n"
        b"Body text here.\r\n"
    )

    def test_sender_is_lowercased(self):
        got = GmailApiSource._headers_from_raw(self.RAW)
        assert got["sender"] == "morgan.reyes@hillcrestassociates.com"
        assert got["sender_name"] == "Morgan Reyes"

    def test_recipients_include_to_and_cc_lowercased(self):
        got = GmailApiSource._headers_from_raw(self.RAW)
        assert set(got["recipients"]) == {
            "you@yourcompany.com", "sony@kineviz.com", "ben@kineviz.com",
        }

    def test_missing_headers_do_not_raise(self):
        got = GmailApiSource._headers_from_raw(b"Subject: bare\r\n\r\nhi\r\n")
        assert got["sender"] == ""
        assert got["recipients"] == []
        assert got["subject"] == "bare"


class TestOneParserForBothSources:
    """The property that preserves the backfill."""

    def test_bytes_and_file_paths_produce_identical_text(self, tmp_path):
        raw = (
            b"From: a@x.com\r\nTo: b@y.com\r\nSubject: t\r\n\r\n"
            b"The novel sentence.\r\n\r\n"
            b"On Mon, 3 Jun 2026 at 14:02, Alice <a@x.com> wrote:\r\n"
            b"> quoted material that must be stripped\r\n"
        )
        p = tmp_path / "msg.eml"
        p.write_bytes(raw)

        from_file = read_message_body(p)
        from_bytes = read_message_bytes(raw)

        assert from_file.body == from_bytes.body
        assert "quoted material" not in from_bytes.body
        assert "The novel sentence." in from_bytes.body
