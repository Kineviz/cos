"""Configuration, loaded from .env with sane defaults.

Stage 1 needs almost nothing: the Kuzu endpoint, the vault path, and which
addresses are the principal's own. Everything else in .env.example belongs to
later stages and is deliberately not read here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader. No interpolation, no export, no quotes stripping
    beyond the obvious — we control the file format."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def load_env() -> dict[str, str]:
    """The effective environment: .env, overlaid by the real environment, with
    pre-rename names folded into their current ones.

    The legacy fold has to happen HERE and not only on `os.environ`. The shim
    in compat.py runs at import and rewrites the process environment, but a
    `.env` file is read later and never passes through it — so an install whose
    `.env` still says `KIRAN_VAULT_ROOT` (all of them, right after the rename)
    would look up `COS_VAULT_ROOT`, find nothing, and fall back to the built-in
    default. On this machine the default happened to equal the configured
    value and nothing moved. On anyone else's machine it would have silently
    pointed at Wei's vault path.
    """
    from .compat import adopt_legacy_env

    env = {**_load_dotenv(REPO_ROOT / ".env"), **_from_settings(), **os.environ}
    adopt_legacy_env(env)
    return env


# Web-page settings that drive this module. Anything not listed here is stored
# but has no effect, and a settings page whose fields silently do nothing is
# worse than no settings page — so the list is explicit and the UI says which
# fields are not wired yet.
_SETTINGS_TO_ENV = {
    "owner.addresses": "COS_PRINCIPAL_ADDRESSES",
    "report.quiet_days": "COS_QUIET_DAYS",
    "report.owed_window_days": "COS_OWED_WINDOW_DAYS",
    "digest.target": "COS_DIGEST_TARGET",
    "source.vault_root": "COS_VAULT_ROOT",
    "write.roots": "COS_WRITE_ROOTS",
}


def _from_settings() -> dict[str, str]:
    """Values chosen in the browser.

    Ranked above `.env` and below the real environment: the page is a more
    deliberate act than a config file someone edited months ago, but an
    explicitly exported variable should still win for a one-off run.
    """
    try:
        from .settings import load

        s = load()
    except Exception:  # noqa: BLE001 — settings must never break the CLI
        return {}
    out: dict[str, str] = {}
    for key, envname in _SETTINGS_TO_ENV.items():
        val = s.values.get(key)
        if val in (None, "", []):
            continue
        out[envname] = ",".join(str(v) for v in val) if isinstance(val, list) else str(val)
    return out


@dataclass(frozen=True)
class Config:
    kuzu_url: str
    vault_root: Path
    gmail_root: Path
    principal_addresses: tuple[str, ...]
    quiet_days: int
    owed_window_days: int
    deal_domains_path: Path = field(default=REPO_ROOT / "config" / "deal_domains.yaml")
    # Derived index over the markdown notes. Rebuildable — the vault is the home.
    notes_db: Path = field(default=REPO_ROOT / "data" / "notes.sqlite3")
    # The Entra application id for the Microsoft backend. Not a secret —
    # public clients have none — it names the app on the consent screen.
    ms_client_id: str = ""

    @classmethod
    def load(cls) -> "Config":
        env = load_env()
        principals = env.get("COS_PRINCIPAL_ADDRESSES", "")
        return cls(
            kuzu_url=env.get("COS_KUZU_URL", "http://127.0.0.1:7001/kuzudb/graph"),
            vault_root=Path(
                env.get("COS_VAULT_ROOT", str(Path.home() / "vault"))
            ),
            # Email.maildir_path in the graph is stored relative to this root
            # (e.g. "maildir/new/1770087880.…"), so bodies are unreadable
            # without it.
            gmail_root=Path(
                env.get("COS_GMAIL_ROOT", str(Path.home() / "Gmail"))
            ),
            principal_addresses=tuple(
                a.strip().lower() for a in principals.split(",") if a.strip()
            ),
            quiet_days=int(env.get("COS_QUIET_DAYS", "30")),
            owed_window_days=int(env.get("COS_OWED_WINDOW_DAYS", "90")),
            ms_client_id=env.get("COS_MS_CLIENT_ID", ""),
        )
