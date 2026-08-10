"""The rename must not break an install that is already running.

The tool was called `kiran` before 2026-08-07. These tests pin the three
bridges that let an existing machine keep working across the change. Each one
protects against a failure that would be silent — no exception, no log line,
just a system that quietly does the wrong thing.

Delete this file when compat.py goes.
"""

from __future__ import annotations

from cos import compat
from cos.cli import _SOUL_BLOCK_RE
from cos.dashboard import BEGIN, _user_lines, parse_blocks


class TestLegacyEnvironment:
    """Thirty-odd variables under the old prefix are set in .env files, launchd
    plists and shell profiles that this rename does not reach."""

    def test_old_variable_fills_the_new_name(self):
        env = {"KIRAN_QUIET_DAYS": "30"}
        adopted = compat.adopt_legacy_env(env)
        assert env["COS_QUIET_DAYS"] == "30"
        assert adopted == ["COS_QUIET_DAYS"]

    def test_explicit_new_value_wins(self):
        """Otherwise a stale variable in a shell profile would silently
        override the value someone just set on purpose."""
        env = {"KIRAN_MODEL": "old", "COS_MODEL": "deliberate"}
        compat.adopt_legacy_env(env)
        assert env["COS_MODEL"] == "deliberate"

    def test_unrelated_variables_untouched(self):
        env = {"PATH": "/usr/bin", "HOME": "/Users/x"}
        assert compat.adopt_legacy_env(env) == []
        assert env == {"PATH": "/usr/bin", "HOME": "/Users/x"}

    def test_the_two_prefixes_are_actually_different(self):
        """A bulk rename sweep across this package rewrote the old prefix into
        the new one inside compat.py itself, which turned the shim into a
        no-op that still passed a naive round-trip test. This asserts the
        thing that regression destroyed."""
        assert compat.LEGACY_PREFIX != compat.PREFIX
        assert compat.LEGACY_PREFIX == "KIRAN_"


class TestDotEnvIsAlsoBridged:
    """compat.adopt_legacy_env rewrites os.environ at import, but a .env file
    is read afterwards and never passes through it. Right after the rename
    every install's .env still used the old prefix — Wei's had 48 of them — so
    a lookup under the new prefix found nothing and silently fell back to the
    built-in default. Here that default happened to equal the configured
    value; on anyone else's machine COS_VAULT_ROOT would have pointed at Wei's
    vault."""

    def test_legacy_dotenv_key_is_adopted(self, tmp_path, monkeypatch):
        from cos import config as config_mod

        (tmp_path / ".env").write_text(
            "KIRAN_VAULT_ROOT=/somewhere/else\nKIRAN_QUIET_DAYS=45\n"
        )
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        env = config_mod.load_env()
        assert env["COS_VAULT_ROOT"] == "/somewhere/else"
        assert env["COS_QUIET_DAYS"] == "45"

    def test_config_honours_a_legacy_dotenv(self, tmp_path, monkeypatch):
        from cos import config as config_mod

        (tmp_path / ".env").write_text(
            "KIRAN_VAULT_ROOT=/somewhere/else\nKIRAN_QUIET_DAYS=45\n"
        )
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        monkeypatch.delenv("COS_VAULT_ROOT", raising=False)
        monkeypatch.delenv("COS_QUIET_DAYS", raising=False)
        cfg = config_mod.Config.load()
        assert str(cfg.vault_root) == "/somewhere/else"
        assert cfg.quiet_days == 45

    def test_new_key_in_dotenv_still_wins(self, tmp_path, monkeypatch):
        from cos import config as config_mod

        (tmp_path / ".env").write_text(
            "KIRAN_QUIET_DAYS=45\nCOS_QUIET_DAYS=7\n"
        )
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        monkeypatch.delenv("COS_QUIET_DAYS", raising=False)
        assert config_mod.Config.load().quiet_days == 7


class TestDashboardMarkers:
    """Vault files written before the rename carry the old marker. If the
    parser stopped recognising them, those blocks would be read as the user's
    own prose, preserved, and a second copy appended on every run — a
    dashboard that silently doubles itself."""

    def test_pre_rename_blocks_are_still_recognised(self):
        doc = "<!-- kiran:begin owed -->\nbody\n<!-- kiran:end owed -->"
        assert "owed" in parse_blocks(doc)

    def test_current_blocks_are_recognised(self):
        doc = "<!-- cos:begin owed -->\nbody\n<!-- cos:end owed -->"
        assert "owed" in parse_blocks(doc)

    def test_new_blocks_are_written_with_the_new_marker(self):
        assert BEGIN.format(key="owed") == "<!-- cos:begin owed -->"

    def test_mixed_markers_still_pair(self):
        """Each marker is matched independently, so an old opener and a new
        closer pair up. That tolerance is worth keeping: the alternative is
        that a hand-edited file stops being recognised and starts doubling.
        The keys must still match — that is a backreference, not an
        alternation."""
        doc = "<!-- kiran:begin owed -->\nbody\n<!-- cos:end owed -->"
        assert "owed" in parse_blocks(doc)

    def test_different_keys_never_pair(self):
        doc = "<!-- cos:begin owed -->\nbody\n<!-- cos:end overview -->"
        assert parse_blocks(doc) == {}


class TestSoulDateBlock:
    """The date block in SOUL.md is the one thing that cannot be outvoted by
    retrieval, so a stale copy of it is worse than none. During the rename this
    file briefly held two blocks — an old-marker one above a new-marker one —
    which on the next day would have shown the agent yesterday's date first."""

    def test_pre_rename_block_is_found(self):
        text = "prompt\n<!-- kiran:begin DATE -->\n**Today is X.**\n<!-- kiran:end DATE -->\n"
        assert len(_SOUL_BLOCK_RE.findall(text)) == 1

    def test_current_block_is_found(self):
        text = "prompt\n<!-- cos:begin DATE -->\n**Today is X.**\n<!-- cos:end DATE -->\n"
        assert len(_SOUL_BLOCK_RE.findall(text)) == 1

    def test_both_blocks_are_found_so_the_duplicate_can_be_collapsed(self):
        """Matching only one of the two would leave the other behind
        forever — refreshed at the top, stale below."""
        text = (
            "prompt\n"
            "<!-- kiran:begin DATE -->\nold\n<!-- kiran:end DATE -->\n\n"
            "<!-- cos:begin DATE -->\nnew\n<!-- cos:end DATE -->\n"
        )
        assert len(_SOUL_BLOCK_RE.findall(text)) == 2

    def test_the_match_is_not_greedy_across_blocks(self):
        """A greedy match would swallow the prose between two blocks and
        delete whatever the user had written there."""
        text = (
            "<!-- cos:begin DATE -->\na\n<!-- cos:end DATE -->\n"
            "WEI'S OWN PROSE\n"
            "<!-- cos:begin DATE -->\nb\n<!-- cos:end DATE -->\n"
        )
        assert "WEI'S OWN PROSE" not in "".join(_SOUL_BLOCK_RE.findall(text))


class TestToolMentionIsWordBounded:
    """Lines inside a computed block that name the tool are assumed to be the
    tool's own output and are discarded on regeneration. The old name was a
    distinctive five-letter token; "cos" is a substring of ordinary English,
    so a containment test would destroy the user's notes."""

    def test_user_prose_containing_cos_as_a_substring_survives(self):
        body = "\n".join(
            [
                "- chase the cost estimate",
                "- ask Costa about pricing",
                "- cosmetic changes only",
                "- because he asked",
            ]
        )
        assert len(_user_lines(body)) == 4

    def test_a_line_naming_the_tool_is_not_rescued(self):
        assert _user_lines("- see `cos owed` for the rest") == []

    def test_a_line_naming_the_old_tool_is_not_rescued(self):
        """Blocks written before the rename mention the old name."""
        assert _user_lines("- see `kiran owed` for the rest") == []
