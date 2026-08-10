"""The settings page carries security weight, so these are not cosmetic tests.

Two properties matter more than anything else on the page:

  * The file lives where the agent cannot write. If it ever moves inside a
    folder the agent can edit, an email from a stranger could add itself to the
    send list and the whole allow-list idea collapses.
  * The network guard admits Tailscale and loopback only. Not the LAN — Wei's
    Ollama already sits unauthenticated on his home network, so "anyone on the
    wifi" is demonstrably not a safe audience for the page that decides who his
    assistant may email.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cos import settings as settings_mod
from cos import webconfig


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "AUDIT_FILE", tmp_path / "audit.jsonl")


class TestNetworkGuard:
    def test_loopback_allowed(self):
        assert webconfig.peer_allowed("127.0.0.1")
        assert webconfig.peer_allowed("::1")

    def test_tailscale_allowed(self):
        for ip in ["100.64.0.1", "100.77.213.1", "100.127.255.254"]:
            assert webconfig.peer_allowed(ip), ip

    def test_home_network_refused(self):
        """The point of the whole design. Wei's Ollama is open on this same
        network, so it is already a place where unauthenticated services live."""
        for ip in ["192.168.1.50", "10.0.0.5", "172.16.0.9"]:
            assert not webconfig.peer_allowed(ip), ip

    def test_public_internet_refused(self):
        assert not webconfig.peer_allowed("8.8.8.8")

    def test_addresses_just_outside_tailscale_refused(self):
        """100.64.0.0/10 ends at 100.127.255.255. Off-by-one here would admit
        real public addresses."""
        assert not webconfig.peer_allowed("100.63.255.255")
        assert not webconfig.peer_allowed("100.128.0.0")

    def test_garbage_refused(self):
        for junk in ["", "localhost", "not-an-ip", "999.1.1.1"]:
            assert not webconfig.peer_allowed(junk), junk


class TestSecrets:
    def test_key_is_masked_for_the_browser(self):
        settings_mod.save({"model.api_key": "sk-or-v1-abcdefgh1234"})
        shown = settings_mod.load().public()["model.api_key"]
        assert "sk-or" not in shown
        assert shown.endswith("1234"), "last 4 kept so two keys can be told apart"

    def test_saving_the_mask_back_does_not_erase_the_key(self):
        """Open the page, press Save without touching anything. A naive
        implementation writes the mask over the real key and the assistant
        silently loses its model access."""
        settings_mod.save({"model.api_key": "sk-real-key-9999"})
        settings_mod.save({"model.api_key": settings_mod.load().public()["model.api_key"]})
        assert settings_mod.load().values["model.api_key"] == "sk-real-key-9999"

    def test_a_new_key_does_replace_the_old(self):
        settings_mod.save({"model.api_key": "sk-old"})
        settings_mod.save({"model.api_key": "sk-new"})
        assert settings_mod.load().values["model.api_key"] == "sk-new"

    def test_audit_records_the_change_but_never_the_value(self):
        settings_mod.save({"model.api_key": "sk-super-secret-value"})
        text = settings_mod.AUDIT_FILE.read_text()
        assert "sk-super-secret-value" not in text
        assert "model.api_key" in text

    def test_file_is_owner_only(self):
        settings_mod.save({"model.api_key": "x"})
        assert oct(settings_mod.SETTINGS_FILE.stat().st_mode)[-3:] == "600"


class TestSaving:
    def test_unknown_keys_are_ignored(self):
        assert settings_mod.save({"nonsense.key": "x"})[0] == []
        assert "nonsense.key" not in settings_mod.load().values

    def test_unchanged_values_are_not_recorded_as_changes(self):
        settings_mod.save({"agent.name": "Ada"})
        assert settings_mod.save({"agent.name": "Ada"})[0] == []

    def test_textarea_becomes_a_list(self):
        settings_mod.save({"send.allowed": "a@x.com\nb@y.com\n"})
        assert settings_mod.load().values["send.allowed"] == ["a@x.com", "b@y.com"]

    def test_numbers_are_coerced(self):
        settings_mod.save({"report.quiet_days": "45"})
        assert settings_mod.load().values["report.quiet_days"] == 45

    def test_a_bad_number_is_dropped_not_stored_as_text(self):
        """Otherwise int() blows up somewhere far away, at 3am, in a cron job."""
        settings_mod.save({"report.quiet_days": "soon"})
        assert "report.quiet_days" not in settings_mod.load().values

    def test_send_list_defaults_to_nobody(self):
        """A permission list that defaults to permissive is not a permission
        list."""
        assert settings_mod.DEFAULTS["send.allowed"] == []
        assert settings_mod.DEFAULTS["send.enabled"] is False

    def test_corrupt_file_refuses_rather_than_looking_empty(self):
        settings_mod.SETTINGS_FILE.write_text("{ not json")
        with pytest.raises(settings_mod.SettingsError):
            settings_mod.load()


class TestSettingsDriveBehaviour:
    def test_settings_override_dotenv(self, tmp_path, monkeypatch):
        from cos import config as config_mod

        (tmp_path / ".env").write_text("COS_QUIET_DAYS=30\n")
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        monkeypatch.delenv("COS_QUIET_DAYS", raising=False)
        settings_mod.save({"report.quiet_days": 45})
        assert config_mod.Config.load().quiet_days == 45

    def test_real_environment_still_wins(self, tmp_path, monkeypatch):
        """So a one-off run can override the page without editing it."""
        from cos import config as config_mod

        (tmp_path / ".env").write_text("")
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        settings_mod.save({"report.quiet_days": 45})
        monkeypatch.setenv("COS_QUIET_DAYS", "7")
        assert config_mod.Config.load().quiet_days == 7

    def test_empty_settings_do_not_override(self, tmp_path, monkeypatch):
        from cos import config as config_mod

        (tmp_path / ".env").write_text("COS_QUIET_DAYS=30\n")
        monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
        monkeypatch.delenv("COS_QUIET_DAYS", raising=False)
        settings_mod.save({"owner.addresses": ""})
        assert config_mod.Config.load().quiet_days == 30


class TestWriteRootsAreRefused:
    """Each of these is a route from "can write a file" to "can remove every
    other restriction". They must be refused however they are typed."""

    def test_home_folder_refused(self):
        why = settings_mod.validate_write_root(str(Path.home()))
        assert why and "write access to" in why

    def test_root_refused(self):
        assert settings_mod.validate_write_root("/")

    def test_the_repo_itself_refused(self):
        """Write access to the code means write access to the code that
        enforces all of this."""
        repo = Path(settings_mod.__file__).resolve().parents[2]
        assert settings_mod.validate_write_root(str(repo))

    def test_hermes_folder_refused(self):
        """It holds the agent's own config and a .env full of API keys."""
        p = Path.home() / ".hermes"
        if p.is_dir():
            assert settings_mod.validate_write_root(str(p))

    def test_config_folder_refused(self):
        p = Path.home() / ".config"
        if p.is_dir():
            assert settings_mod.validate_write_root(str(p))

    def test_launchagents_refused(self):
        p = Path.home() / "Library" / "LaunchAgents"
        if p.is_dir():
            assert settings_mod.validate_write_root(str(p))

    def test_a_parent_of_a_forbidden_path_is_refused(self):
        """The subtle one. ~/Library is not on the list, but it CONTAINS
        LaunchAgents, so granting it grants that too."""
        p = Path.home() / "Library"
        if (p / "LaunchAgents").is_dir():
            why = settings_mod.validate_write_root(str(p))
            assert why and "LaunchAgents" in why

    def test_tilde_is_expanded_before_checking(self):
        """Otherwise "~" sails through as an ordinary relative path."""
        assert settings_mod.validate_write_root("~")

    def test_a_path_that_walks_up_is_refused(self):
        assert settings_mod.validate_write_root(str(Path.home() / "Documents" / ".." ))

    def test_nonexistent_path_refused(self):
        assert settings_mod.validate_write_root("/no/such/folder/anywhere")

    def test_an_ordinary_folder_is_allowed(self, tmp_path):
        # NOT tmp_path itself: the fixture puts the settings file there, and
        # refusing it is correct behaviour, not a bug.
        ok = tmp_path / "notes"
        ok.mkdir()
        assert settings_mod.validate_write_root(str(ok)) is None

    def test_a_folder_containing_the_settings_file_is_refused(self, tmp_path):
        """The self-referential hole: grant this and the agent can rewrite its
        own permission list."""
        why = settings_mod.validate_write_root(str(tmp_path))
        assert why and "own permissions" in why

    def test_blank_is_allowed(self):
        assert settings_mod.validate_write_root("") is None


class TestRejectionsAreVisible:
    def test_a_bad_write_root_is_reported_not_silently_dropped(self):
        changed, errors = settings_mod.save({"write.hermes_safe_root": str(Path.home())})
        assert changed == []
        assert "write.hermes_safe_root" in errors

    def test_a_bad_value_does_not_block_the_good_ones(self, tmp_path):
        changed, errors = settings_mod.save({
            "write.hermes_safe_root": "/",
            "agent.name": "Ada",
        })
        assert "agent.name" in changed
        assert "write.hermes_safe_root" in errors
        assert settings_mod.load().values["agent.name"] == "Ada"

    def test_one_bad_entry_rejects_the_whole_list(self, tmp_path):
        """Partially applying a permission list would leave Wei unsure which
        half took effect."""
        changed, errors = settings_mod.save(
            {"write.roots": f"{tmp_path}\n{Path.home()}"})
        assert "write.roots" in errors
        assert "write.roots" not in settings_mod.load().values

    def test_a_source_folder_must_be_a_git_repo(self, tmp_path):
        why = settings_mod.validate_source_root(str(tmp_path))
        assert why and "git" in why

    def test_a_git_folder_is_accepted(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert settings_mod.validate_source_root(str(tmp_path)) is None
