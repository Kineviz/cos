"""The Microsoft backend, tested without Microsoft.

Wei: "build the backend. however I will need to test from another
computer, so it does not mess up Kiran." The second half of that sentence
is a testable property and gets its own class: on a machine where Gmail
is connected, the Microsoft path must change nothing.
"""

from __future__ import annotations

import json

import pytest

from cos import backend, ms_auth, ms_ledger


class TestSelectionCannotHijackKiran:
    def test_gmail_wins_when_both_tokens_exist(self, tmp_path, monkeypatch):
        gmail_tok = tmp_path / "g.json"; gmail_tok.write_text("{}")
        ms_tok = tmp_path / "ms.json"; ms_tok.write_text("{}")
        monkeypatch.setattr(backend, "TOKEN_FILE", gmail_tok)
        monkeypatch.setattr(ms_auth, "TOKEN_FILE", ms_tok)

        class Cfg:
            principal_addresses = ("you@yourcompany.com",)
            kuzu_url = "http://nowhere"

        got = backend.open_backend(Cfg())
        assert isinstance(got, backend.GmailBackend)

    def test_ms_is_used_when_it_is_the_only_grant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backend, "TOKEN_FILE", tmp_path / "absent.json")
        ms_tok = tmp_path / "ms.json"; ms_tok.write_text("{}")
        monkeypatch.setattr(ms_auth, "TOKEN_FILE", ms_tok)

        class Cfg:
            principal_addresses = ("sam@example.com",)
            kuzu_url = "http://nowhere"

        got = backend.open_backend(Cfg())
        assert isinstance(got, backend.MsGraphBackend)


class TestLedgerFromGraphMessages:
    """Same Counterparty rows as the Gmail builder, from Graph's structured
    fields — who wrote last, when, and the id a reply would anchor to."""

    def _wire(self, monkeypatch, msgs):
        from cos import msgraph
        monkeypatch.setattr(msgraph, "messages",
                            lambda days=90, **k: msgs)

    def test_inbound_and_outbound_fold_correctly(self, monkeypatch):
        self._wire(monkeypatch, [
            {"id": "m2", "subject": "Re: pilot",
             "receivedDateTime": "2026-08-08T10:00:00Z",
             "from": {"emailAddress": {"address": "sam@bsr.example",
                                       "name": "Sam K"}},
             "toRecipients": [{"emailAddress": {"address": "me@x.example"}}]},
            {"id": "m1", "subject": "pilot",
             "receivedDateTime": "2026-08-01T10:00:00Z",
             "from": {"emailAddress": {"address": "me@x.example"}},
             "toRecipients": [{"emailAddress": {"address": "sam@bsr.example",
                                                "name": "Sam K"}}]},
        ])
        ledger = ms_ledger.build_ledger(("me@x.example",))
        cp = ledger["sam@bsr.example"]
        assert cp.inbound_count == 1 and cp.outbound_count == 1
        assert cp.last_inbound_id == "m2"
        assert cp.last_inbound_subject == "Re: pilot"
        assert cp.name == "Sam K"

    def test_drafts_and_calendar_invites_are_skipped(self, monkeypatch):
        self._wire(monkeypatch, [
            {"id": "d1", "isDraft": True, "subject": "unsent",
             "receivedDateTime": "2026-08-08T10:00:00Z",
             "from": {"emailAddress": {"address": "me@x.example"}}},
            {"id": "c1", "subject": "Accepted: standup",
             "receivedDateTime": "2026-08-08T10:00:00Z",
             "from": {"emailAddress": {"address": "sam@bsr.example"}}},
        ])
        assert ms_ledger.build_ledger(("me@x.example",)) == {}


class TestDeviceFlow:
    def test_pending_then_token_is_saved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_auth, "TOKEN_FILE", tmp_path / "tok.json")
        monkeypatch.setattr(ms_auth.time, "sleep", lambda s: None)
        replies = iter([
            {"device_code": "dc", "user_code": "ABCD",
             "verification_uri": "https://microsoft.com/devicelogin",
             "interval": 0, "expires_in": 60,
             "message": "go enter the code"},
            {"error": "authorization_pending"},
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        ])
        monkeypatch.setattr(ms_auth, "_post", lambda url, data: next(replies))
        ms_auth.device_login("client-id", log=lambda s: None)
        saved = json.loads((tmp_path / "tok.json").read_text())
        assert saved["access_token"] == "at"
        assert saved["refresh_token"] == "rt"

    def test_a_refusal_is_an_error_not_a_hang(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_auth, "TOKEN_FILE", tmp_path / "tok.json")
        monkeypatch.setattr(ms_auth.time, "sleep", lambda s: None)
        replies = iter([
            {"device_code": "dc", "interval": 0, "expires_in": 60,
             "message": "go"},
            {"error": "access_denied", "error_description": "user said no"},
        ])
        monkeypatch.setattr(ms_auth, "_post", lambda url, data: next(replies))
        with pytest.raises(ms_auth.MsAuthError, match="user said no"):
            ms_auth.device_login("client-id", log=lambda s: None)

    def test_an_expired_token_refreshes(self, tmp_path, monkeypatch):
        tok = tmp_path / "tok.json"
        tok.write_text(json.dumps({"client_id": "c", "access_token": "old",
                                   "refresh_token": "rt", "expires_at": 0}))
        monkeypatch.setattr(ms_auth, "TOKEN_FILE", tok)
        monkeypatch.setattr(
            ms_auth, "_post",
            lambda url, data: {"access_token": "new", "expires_in": 3600})
        assert ms_auth.access_token() == "new"
        assert json.loads(tok.read_text())["refresh_token"] == "rt"
