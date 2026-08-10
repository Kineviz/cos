from cos.identity import classify_address, domain_of, is_freemail


class TestRobotDetection:
    def test_plain_human_address_is_a_person(self):
        assert classify_address("yolande.poirier@neo4j.com").is_person
        assert classify_address("jsmith@hillcrestassociates.com").is_person

    def test_noreply_variants_are_role(self):
        for addr in (
            "noreply@google.com",
            "no-reply@google.com",
            "donotreply@bank.com",
            "notifications@github.com",
        ):
            assert not classify_address(addr).is_person, addr

    def test_compound_role_addresses(self):
        # Regression: partner-support-desk@google.com was classified a person
        # and appeared in "waiting on you" as if a human were owed a reply.
        assert not classify_address("partner-support-desk@google.com").is_person
        assert not classify_address("sales-team@acme.com").is_person

    def test_hash_and_uuid_local_parts_are_robots(self):
        assert not classify_address(
            "x+a3f9c1d84be27650fa19@mail.asana.com"
        ).is_person
        assert not classify_address(
            "550e8400-e29b-41d4-a716-446655440000@mail.figma.com"
        ).is_person
        assert not classify_address("12345678901234@notify.example.com").is_person

    def test_bulk_sender_domains(self):
        assert not classify_address("dan@customermail.microsoft.com").is_person
        assert not classify_address("hello@email.substack.com").is_person
        assert not classify_address("x@mailgun.net").is_person

    def test_shared_sender_domain_signal(self):
        """A domain with many display names per address is a relay."""
        assert classify_address("bob@acme.com", names_per_address_on_domain=1.0).is_person
        assert not classify_address(
            "bob@acme.com", names_per_address_on_domain=9.0
        ).is_person

    def test_person_name_that_collides_with_a_role_word(self):
        """first.last where last happens to be a role word still has 2 tokens;
        we knowingly accept this false positive rather than let sales@ through."""
        verdict = classify_address("mark.sales@acme.com")
        assert verdict.kind == "role"  # documents the known trade-off

    def test_malformed_input_never_crashes(self):
        assert not classify_address("").is_person
        assert not classify_address("not-an-address").is_person


class TestDomainHelpers:
    def test_domain_of(self):
        assert domain_of("Bob@Example.COM") == "example.com"

    def test_freemail(self):
        assert is_freemail("someone@gmail.com")
        assert is_freemail("qq.com")
        assert not is_freemail("you@yourcompany.com")


class TestMimePayloadCoercion:
    """Regression: get_content() can return bytes on legacy charsets, which
    crashed the 12-year export at message 11,413."""

    def test_bytes_payload_is_decoded(self):
        from cos.mailtext import _as_text
        assert _as_text(b"hello") == "hello"
        assert _as_text(bytearray(b"hi")) == "hi"
        assert _as_text("already str") == "already str"

    def test_undecodable_bytes_do_not_raise(self):
        from cos.mailtext import _as_text
        assert isinstance(_as_text(b"\xff\xfe bad bytes"), str)


class TestControlByteStripping:
    """Postgres rejects NUL in text columns; one 2021 message carried 2,885 of
    them and blocked a 25,758-page sync."""

    def test_nul_bytes_are_removed(self):
        from cos.mailtext import strip_quoted
        out = strip_quoted("hello\x00\x00world")
        assert "\x00" not in out.body
        assert "helloworld" in out.body

    def test_other_c0_controls_removed_but_newlines_kept(self):
        from cos.mailtext import strip_quoted
        out = strip_quoted("a\x01b\x1fc\nsecond line\ttabbed")
        assert not any(ch in out.body for ch in "\x01\x1f")
        assert "\n" in out.body and "\t" in out.body
