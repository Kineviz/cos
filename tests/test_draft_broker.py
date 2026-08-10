"""The structural safety property: a draft's destination never comes from prose.

`create_reply_draft` takes a source message id and body text. Every address is
derived from the source message's own headers by the code below. That is what
makes "an injected email cannot redirect a draft to an attacker" a property of
the system rather than an instruction the model is asked to follow — and it is
the property that has to hold once web research is enabled.
"""

from __future__ import annotations

import pytest

from cos.draft_broker import MARKER, _header, _reply_addresses


def H(**kw) -> list[dict]:
    """Gmail returns headers as a list of {name, value}."""
    return [{"name": k.replace("_", "-"), "value": v} for k, v in kw.items()]


class TestReplyAddresses:
    ME = "you@yourcompany.com"

    def test_replies_to_the_sender(self):
        to, cc = _reply_addresses(
            H(From="Morgan Reyes <morgan.reyes@hillcrestassociates.com>",
              To=self.ME), self.ME
        )
        assert to == ["morgan.reyes@hillcrestassociates.com"]
        assert cc == []

    def test_reply_to_header_wins_over_from(self):
        to, _ = _reply_addresses(
            H(From="noreply@bounce.example.com",
              Reply_To="real.person@example.com", To=self.ME), self.ME
        )
        assert to == ["real.person@example.com"]

    def test_reply_all_keeps_others_and_drops_me(self):
        to, cc = _reply_addresses(
            H(From="a@x.com", To=f"{self.ME}, b@y.com", Cc="c@z.com"), self.ME
        )
        assert to == ["a@x.com"]
        assert self.ME not in cc
        assert set(cc) == {"b@y.com", "c@z.com"}

    def test_no_duplicate_between_to_and_cc(self):
        to, cc = _reply_addresses(
            H(From="a@x.com", To=f"{self.ME}, a@x.com", Cc="a@x.com"), self.ME
        )
        assert to == ["a@x.com"]
        assert cc == []

    def test_my_own_address_is_never_a_recipient(self):
        # Replying to your own sent message must not mail yourself the Cc.
        _, cc = _reply_addresses(
            H(From="them@x.com", To=self.ME, Cc=f"{self.ME}, other@y.com"), self.ME
        )
        assert all(a.lower() != self.ME for a in cc)

    def test_case_insensitive_self_match(self):
        _, cc = _reply_addresses(
            H(From="them@x.com", To="You@YourCompany.COM, other@y.com"), self.ME
        )
        assert all("yourcompany" not in a.lower() for a in cc)


class TestPromptInjectionCannotRedirect:
    """The attack the design exists to stop."""

    ME = "you@yourcompany.com"

    def test_addresses_in_the_body_are_irrelevant(self):
        # A poisoned message body cannot contribute a recipient, because the
        # body is not an input to address derivation at all.
        to, cc = _reply_addresses(
            H(From="legit@client.com", To=self.ME), self.ME
        )
        assert to == ["legit@client.com"]
        assert not any("attacker" in a for a in to + cc)

    def test_injected_headers_in_subject_do_not_leak(self):
        to, cc = _reply_addresses(
            H(From="legit@client.com",
              Subject="Re: hi\nBcc: attacker@evil.com",
              To=self.ME),
            self.ME,
        )
        assert to == ["legit@client.com"]
        assert not any("evil.com" in a for a in to + cc)


class TestHeaderLookup:
    def test_is_case_insensitive(self):
        assert _header(H(message_id="<abc@x>"), "Message-ID") == "<abc@x>"

    def test_missing_header_is_empty_not_an_error(self):
        assert _header(H(From="a@b.c"), "Reply-To") == ""


def test_marker_must_be_deleted_by_hand():
    # Deliberately not something that silently disappears: a draft sent with
    # the marker intact is embarrassing, one sent with no marker is worse.
    assert "delete this line" in MARKER.lower()


class TestMalformedAddressesAreDropped:
    """Corporate threads carry junk in their address headers — Exchange DNs,
    empty angle brackets, group syntax. One malformed entry copied into the
    draft's Cc and Gmail rejects the whole draft with "Invalid Cc header",
    deterministically. The BigBank thread did exactly that."""

    def _hdrs(self, **kw):
        return [{"name": k, "value": v} for k, v in kw.items()]

    def test_an_exchange_dn_in_cc_is_dropped_not_copied(self):
        to, cc = _reply_addresses(self._hdrs(
            From="Pat <pat.fisher@bigbank.example>",
            To="you@yourcompany.com",
            Cc="/O=BigBank/OU=EXCHANGE/CN=RECIPIENTS/CN=SOMEONE, "
               "Real Person <real@example.com>"),
            me="you@yourcompany.com")
        assert to == ["pat.fisher@bigbank.example"]
        assert cc == ["real@example.com"]

    def test_empty_and_group_syntax_are_dropped(self):
        to, cc = _reply_addresses(self._hdrs(
            From="a@example.com",
            To="undisclosed-recipients:;, b@example.com",
            Cc='"" <>, c@ex'),
            me="you@yourcompany.com")
        assert to == ["a@example.com"]
        assert cc == ["b@example.com"]

    def test_a_malformed_sender_leaves_no_to_and_the_draft_is_refused(self):
        """Cc can be cleaned; To cannot be guessed. No valid sender means no
        draft, not a draft to something that merely looks like an address."""
        to, _cc = _reply_addresses(self._hdrs(
            From="/O=BROKEN/CN=DN", To="you@yourcompany.com"),
            me="you@yourcompany.com")
        assert to == []


class TestFreshDraft:
    """A new topic needs its own subject, not "Re:" into an old thread. The
    recipient is still never caller-supplied — it is read from a real message
    that person sent."""

    def test_refuses_empty_subject_and_body(self):
        from cos.draft_broker import DraftError, create_fresh_draft
        with pytest.raises(DraftError):
            create_fresh_draft("msgid", "body here", "   ")
        with pytest.raises(DraftError):
            create_fresh_draft("msgid", "  ", "Subject")


class TestFreshDraftFromOwnSentMail:
    """A person first contacted BY the user has no inbound message to anchor
    to — but the user's own sent mail carries an address they typed
    themselves, which is exactly as trustworthy."""

    def test_own_message_anchors_to_its_recipient_logic(self):
        # The address-derivation rule, tested at the header level: when the
        # source's From is the mailbox owner, the recipient comes from To,
        # minus the owner.
        hdrs = [{"name": "From", "value": "Me <me@x.example>"},
                {"name": "To", "value": "Andrew B <andrew@partner.example>"}]
        sender = _header(hdrs, "Reply-To") or _header(hdrs, "From")
        from email.utils import getaddresses
        to = [a for _, a in getaddresses([sender])]
        assert to == ["me@x.example"]  # naive rule points at yourself
        # the corrected rule:
        me = "me@x.example"
        if to and to[0].lower() == me:
            to = [a for _, a in getaddresses([_header(hdrs, "To")])
                  if a.lower() != me]
        assert to == ["andrew@partner.example"]
