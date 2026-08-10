"""Settings you change in the browser, stored where the agent cannot reach them.

**The location is the security control, so it is not a detail.** Kiran can write
to three folders in the vault (`COS_WRITE_ROOTS`). If this file lived in any of
them, an email from a stranger could talk the agent into adding a recipient to
its own send list — and then the allow-list is decoration. It lives in
`~/.config/cos/`, beside the Google token, which nothing in the agent's toolset
can open.

Two other rules that matter:

**Secrets are masked on read and preserved on write.** The browser is sent
`••••••••abcd`, never the key. If a save comes back still masked, the stored
value is kept rather than overwritten — otherwise loading the page and pressing
Save would silently destroy the API key. Copied from how kineviz-agent handles
`AgentModelConfig.apiKey`.

**Every change is journalled.** `settings-audit.jsonl` records what changed,
when, and from which address. If the send list ever grows an entry nobody
remembers adding, that is the file to read.

Values here override `.env`, which stays supported so an existing install does
not break.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compat import config_dir

SETTINGS_FILE = config_dir() / "settings.json"
AUDIT_FILE = config_dir() / "settings-audit.jsonl"

MASK = "••••••••"

# Keys whose values are never sent to the browser in the clear.
SECRET_KEYS = {"model.api_key", "model.extract_api_key"}

DEFAULTS: dict[str, Any] = {
    # Setup
    "agent.name": "Kiran",
    "agent.paused": False,
    "owner.name": "",
    "owner.company": "",
    "owner.addresses": [],
    # Model
    "model.provider": "openrouter",
    "model.id": "deepseek/deepseek-v4-flash-0731",
    "model.base_url": "",
    "model.api_key": "",
    "model.extract_provider": "ollama",
    "model.extract_id": "qwen3.5:9b",
    "model.extract_base_url": "http://127.0.0.1:11434/v1",
    "model.extract_api_key": "",
    # Permissions. Empty means "nobody", deliberately — a permission list that
    # defaults to permissive is a permission list that does nothing.
    "send.allowed": [],
    "send.enabled": False,
    "send.delay_minutes": 10,
    "telegram.allowed": [],
    # Rhythm
    "digest.time": "07:30",
    "digest.target": "telegram",
    "quiet_hours.start": "22:00",
    "quiet_hours.end": "07:00",
    # Tuning
    "report.quiet_days": 30,
    "report.owed_window_days": 90,
    # Where it reads
    "source.vault_root": "",
    "source.brain_root": "",
    # Where it may write. Two separate controls, and conflating them would be
    # a lie: hermes_safe_root governs the agent's own file writing, write.roots
    # governs only this tool's vault edits.
    "write.hermes_safe_root": "",
    "write.roots": [],
}

# Paths nothing should ever be granted write access to, whatever the user
# types. Each one is a route from "can write a file" to "can remove every
# other restriction":
#   the repo        -> edit the code that enforces all of this
#   ~/.config       -> edit the settings file, including this list
#   ~/.hermes       -> edit the agent's own config and its .env of API keys
#   ~/.ssh, ~/.aws  -> credentials
#   ~/Library/LaunchAgents -> schedule anything, as you, forever
FORBIDDEN_WRITE_ROOTS = [
    Path.home(),
    Path("/"),
    Path.home() / ".config",
    Path.home() / ".hermes",
    Path.home() / ".ssh",
    Path.home() / ".aws",
    Path.home() / ".gnupg",
    Path.home() / "Library" / "LaunchAgents",
    Path(__file__).resolve().parents[2],  # the repo itself
]


def _resolve(p: str) -> Path:
    return Path(p).expanduser().resolve()


def validate_write_root(raw: str) -> str | None:
    """Return why this path must not be writable, or None if it is fine."""
    if not raw.strip():
        return None
    try:
        path = _resolve(raw)
    except (OSError, RuntimeError):
        return f"{raw}: not a usable path"

    if not path.exists():
        return f"{path}: does not exist"
    if not path.is_dir():
        return f"{path}: not a folder"

    for bad in FORBIDDEN_WRITE_ROOTS:
        bad_r = bad.resolve()
        # Refuse if the candidate IS the forbidden path, or CONTAINS it.
        # Containing it is the subtle one: granting ~/Documents is fine, but
        # granting ~ would hand over ~/.ssh along with everything else.
        if path == bad_r or bad_r.is_relative_to(path):
            return f"{path}: would give write access to {bad_r}"

    if SETTINGS_FILE.resolve().is_relative_to(path):
        return (f"{path}: contains the settings file, so the agent could edit "
                "its own permissions")
    return None


def validate_source_root(raw: str) -> str | None:
    if not raw.strip():
        return None
    try:
        path = _resolve(raw)
    except (OSError, RuntimeError):
        return f"{raw}: not a usable path"
    if not path.is_dir():
        return f"{path}: not a folder"
    if not (path / ".git").exists():
        return (f"{path}: not a git repository. The brain indexes by commit, "
                "so an untracked folder would stay invisible.")
    return None


def validate(updates: dict[str, Any]) -> dict[str, str]:
    """Reasons to reject, keyed by setting. Empty means everything is fine."""
    errors: dict[str, str] = {}

    for key in ("write.hermes_safe_root",):
        if key in updates:
            why = validate_write_root(str(updates[key] or ""))
            if why:
                errors[key] = why

    if "write.roots" in updates:
        raw = updates["write.roots"]
        items = raw if isinstance(raw, list) else [
            x.strip() for x in str(raw).replace("\n", ",").split(",") if x.strip()
        ]
        bad = [w for w in (validate_write_root(i) for i in items) if w]
        if bad:
            errors["write.roots"] = "; ".join(bad)

    for key in ("source.vault_root", "source.brain_root"):
        if key in updates:
            why = validate_source_root(str(updates[key] or ""))
            if why:
                errors[key] = why

    return errors


@dataclass
class Settings:
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.values:
            return self.values[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def public(self) -> dict[str, Any]:
        """Everything the browser may see. Secrets become a mask that reveals
        only the last four characters, enough to tell two keys apart."""
        out = {**DEFAULTS, **self.values}
        for key in SECRET_KEYS:
            raw = out.get(key) or ""
            out[key] = f"{MASK}{raw[-4:]}" if raw else ""
        return out


def load() -> Settings:
    try:
        return Settings(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Settings({})
    except (OSError, json.JSONDecodeError):
        # A corrupt file must not silently become an empty permission list that
        # looks deliberate. Refuse rather than guess.
        raise SettingsError(
            f"{SETTINGS_FILE} is unreadable or not valid JSON. "
            "Fix or delete it; it will be recreated with defaults."
        )


class SettingsError(RuntimeError):
    pass


def _is_masked(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(MASK)


def save(updates: dict[str, Any], actor: str = "web") -> tuple[list[str], dict[str, str]]:
    """Merge `updates`; return (changed keys, rejections keyed by setting).

    A masked secret means "unchanged" — the browser was never given the real
    value, so echoing it back must not erase it.

    Rejections are RETURNED rather than silently dropped. An earlier version
    ignored anything it could not parse, which is fine for a typo in a number
    and unacceptable for a write path: the user would set a folder, see no
    error, and believe a permission had been granted or revoked when nothing
    had happened.
    """
    errors = validate(updates)
    updates = {k: v for k, v in updates.items() if k not in errors}
    current = load()
    changed: list[str] = []
    merged = dict(current.values)

    for key, new in updates.items():
        if key not in DEFAULTS:
            continue  # ignore unknown keys rather than storing junk
        if key in SECRET_KEYS and _is_masked(new):
            continue
        old = current.get(key)
        if isinstance(DEFAULTS[key], bool):
            new = bool(new)
        elif isinstance(DEFAULTS[key], int) and not isinstance(DEFAULTS[key], bool):
            try:
                new = int(new)
            except (TypeError, ValueError):
                continue
        elif isinstance(DEFAULTS[key], list) and isinstance(new, str):
            new = [x.strip() for x in new.replace("\n", ",").split(",") if x.strip()]
        if new == old:
            continue
        merged[key] = new
        changed.append(key)

    if not changed:
        return [], errors

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS_FILE)
    _audit(changed, current, merged, actor)
    return changed, errors


def _audit(changed: list[str], before: Settings, after: dict, actor: str) -> None:
    """What changed, when, and from where. Secret VALUES are never written —
    only the fact that the key was set."""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actor": actor,
        "changes": {
            k: {
                "from": "<set>" if k in SECRET_KEYS else before.get(k),
                "to": "<set>" if k in SECRET_KEYS else after.get(k),
            }
            for k in changed
        },
    }
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        os.chmod(AUDIT_FILE, 0o600)
    except OSError:
        pass


def writable_by_agent() -> list[str]:
    """Paths the agent may write to that would compromise this file.

    Called at startup so a misconfiguration is loud rather than latent. If the
    settings file ever ends up inside a folder the agent can write, the send
    allow-list stops meaning anything.
    """
    from .config import load_env

    roots = load_env().get("COS_WRITE_ROOTS", "")
    bad = []
    for root in (r.strip() for r in roots.split(",") if r.strip()):
        try:
            SETTINGS_FILE.resolve().relative_to(Path(root).expanduser().resolve())
            bad.append(root)
        except ValueError:
            continue
    return bad
