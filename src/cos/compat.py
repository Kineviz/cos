"""Bridges from the old name to the new one, so a rename cannot break a
running install.

The tool used to be called `kiran`. That was one person's agent's name, which
is the wrong thing to call a tool other people install — their agent has a
different name and types the same command. So the tool is `cos` and the agent
keeps whatever name its owner gave it.

Two things outlive a rename and must keep working:

  * **Environment.** Thirty-odd variables under the old prefix are read across
    this package and set in `.env` files, launchd plists and shell profiles.
  * **Credentials.** The old config directory holds `token.json`, which IS the
    mailbox grant. Losing track of it means a re-consent, and on a machine
    running unattended jobs it means silent auth failures until someone
    notices.

Rather than touch thirty read sites, `adopt_legacy_env()` copies any variable
under the old prefix into its new name at import time, and only when the new
name is unset — so an explicitly-set new variable always wins and the shim can
never override a deliberate setting.

The prefixes below are assembled rather than written as literals on purpose: a
bulk rename sweep across this package would otherwise rewrite the old prefix
into the new one and silently turn this whole module into a no-op. It did,
once, while this was being written.

Remove this module once no install has a variable under the old prefix or an
old config directory left. Dated deliberately: 2026-08-07.
"""

from __future__ import annotations

import os
from pathlib import Path

_OLD = "KIR" "AN"  # split so a sweep for the old name cannot rewrite it
LEGACY_PREFIX = _OLD + "_"
PREFIX = "COS_"
LEGACY_DIRNAME = _OLD.lower()


def adopt_legacy_env(environ: dict[str, str] | None = None) -> list[str]:
    """Fill unset new-prefix variables from their old-prefix predecessors.

    Returns the names adopted, so `cos check` can report that an install is
    still leaning on the old names instead of failing mysteriously later.
    """
    env = os.environ if environ is None else environ
    adopted: list[str] = []
    for key in [k for k in env if k.startswith(LEGACY_PREFIX)]:
        new_key = PREFIX + key[len(LEGACY_PREFIX) :]
        if new_key not in env:
            env[new_key] = env[key]
            adopted.append(new_key)
    return adopted


def config_dir() -> Path:
    """Where the OAuth client and token live.

    Prefers `~/.config/cos`, but falls back to the old directory when that is
    where the token actually is. Returning the OLD directory when it holds the
    credentials is the whole point: a rename must not force a re-consent, and
    must not quietly start writing a refreshed token to a new empty directory
    while the real one goes stale.
    """
    new = Path.home() / ".config" / "cos"
    old = Path.home() / ".config" / LEGACY_DIRNAME
    if not new.exists() and (old / "token.json").is_file():
        return old
    return new
