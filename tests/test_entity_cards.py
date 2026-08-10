"""What may become a link target in the graph.

Both rules here exist because of measured damage, not theory. A dry run of
`gbrain extract links --by-mention` over the full 30,366-page brain proposed
12,321 links, and 3,055 of them (25%) came from two card titles:

    "Kineviz" (1,761)  — the company's own name, on the hello@ mailbox
    "graph"   (1,294)  — a display name on graphxr@, in the mail of a graph
                         visualisation company

gbrain builds its mention gazetteer from card titles, so a title that is a
common word or a brand matches constantly across the corpus.
"""

from __future__ import annotations

from cos.entity_cards import _gazetteer_safe_name
from cos.identity import classify_address


class TestGazetteerSafeName:
    def test_two_token_names_are_kept(self):
        assert _gazetteer_safe_name("Sam Green") == "Sam Green"
        assert _gazetteer_safe_name("Ana Lopes da Silva") == "Ana Lopes da Silva"

    def test_brand_and_common_words_are_rejected(self):
        # The two that caused 25% of all proposed links.
        assert _gazetteer_safe_name("graph") is None
        assert _gazetteer_safe_name("Kineviz") is None
        assert _gazetteer_safe_name("Gemini") is None

    def test_bare_first_names_are_rejected(self):
        # A real person, but not a usable link target: twelve years of mail
        # contains more than one Steve, and the gazetteer cannot tell them apart.
        assert _gazetteer_safe_name("Steve") is None

    def test_cjk_names_survive_the_one_token_rule(self):
        # One token by whitespace, but a full name and an unambiguous match.
        # The naive rule would have discarded every Chinese contact.
        assert _gazetteer_safe_name("杨明英") == "杨明英"
        assert _gazetteer_safe_name("满天飞雪") == "满天飞雪"

    def test_missing_name_is_not_an_error(self):
        assert _gazetteer_safe_name(None) is None
        assert _gazetteer_safe_name("") is None


class TestRoleAddressesAreNotPeople:
    """`write_entity_cards` skips kind == "role", so they never become targets."""

    def test_role_mailboxes_rejected(self):
        for addr in ("hello@kineviz.com", "info@artsorg.example",
                     "noreply@example.com", "support@vendor.io"):
            assert classify_address(addr).kind == "role", addr

    def test_real_counterparties_survive(self):
        for addr in ("sam.taylor@yourcompany.example", "jamie.fox@community.example",
                     "morgan.reyes@hillcrestassociates.com"):
            assert classify_address(addr).kind != "role", addr

    def test_numeric_chinese_addresses_are_kept(self):
        # Classified "robot" by the long-digits heuristic, but these are real
        # counterparties. Gating on is_person would delete all ~30 of them.
        for addr in ("5551234567@qq.com", "5559876543@163.com"):
            assert classify_address(addr).is_person is False, addr
            assert classify_address(addr).kind != "role", addr
