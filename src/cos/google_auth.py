"""Google OAuth for the Gmail API mail source.

One consent screen replaces the whole local-mirror setup — an IMAP app password,
mbsync, a launchd job, a graph server and a nightly rebuild. That trade is the
main reason this module exists.

**Read-only, by request.** The scopes below can list and read mail and calendar
and nothing else. They cannot send, label, archive or delete. Drafting is
deliberately absent: it needs `gmail.compose`, which Google documents as
"Manage drafts and send emails" — there is no draft-only scope — so it lives
behind a separate broker process that holds its own credential and has no send
function in it. See docs/DESIGN-email-drafting.md.

**The token is not the client.** `oauth_client.json` identifies the *app* and is
downloaded from Google Cloud Console; `token.json` is the *grant* and appears
after the consent screen. Both live in ~/.config/cos with mode 600, outside
the repo. Revoke at myaccount.google.com without touching anything else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cos import compat

# Read-only. Adding a write scope here would silently widen every existing
# grant on the next refresh, so treat this list as a security boundary.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# ~/.config/cos, unless an older install's token is still sitting in the
# previous directory — see compat.config_dir(). Resolved once at import so
# every module agrees on one location for the life of the process.
CONFIG_DIR = compat.config_dir()
CLIENT_FILE = CONFIG_DIR / "oauth_client.json"
TOKEN_FILE = CONFIG_DIR / "token.json"


class AuthError(RuntimeError):
    pass


def _secure(path: Path) -> None:
    """0600. These files are a standing grant to a mailbox."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_credentials(interactive: bool = True):
    """Return usable credentials, running the consent flow only if needed.

    Order: a stored token, then a silent refresh, then — and only when
    `interactive` — the browser consent screen. Background jobs pass
    `interactive=False` so a expired grant fails loudly instead of silently
    waiting on a browser nobody is watching.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_FILE.exists():
        raise AuthError(
            f"No OAuth client at {CLIENT_FILE}.\n"
            "Create one: console.cloud.google.com -> APIs & Services -> "
            "Credentials -> OAuth client ID -> Desktop app, then download the "
            "JSON to that path."
        )

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except ValueError:
            # Written by an older build, or the scope list changed. Re-consent
            # rather than guess at what the stored grant covers.
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        _secure(TOKEN_FILE)
        return creds

    if not interactive:
        raise AuthError(
            "No valid Google credentials and not running interactively. "
            "Run `cos google-auth` once at a terminal."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    # port=0 takes a free port; the client's registered redirect is
    # http://localhost, which matches any port for installed apps.
    creds = flow.run_local_server(port=0, prompt="consent")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    _secure(TOKEN_FILE)
    return creds


def check() -> dict:
    """Prove the grant works, without reading anyone's content.

    Reads the Gmail profile (address, message count) and the primary calendar's
    name. Both are metadata; neither opens a message or an event.
    """
    from googleapiclient.discovery import build

    creds = load_credentials(interactive=False)
    out: dict = {}

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = gmail.users().getProfile(userId="me").execute()
    out["address"] = profile.get("emailAddress")
    out["messages_total"] = profile.get("messagesTotal")
    out["threads_total"] = profile.get("threadsTotal")

    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    primary = cal.calendars().get(calendarId="primary").execute()
    out["calendar"] = primary.get("summary")
    out["timezone"] = primary.get("timeZone")

    granted = json.loads(TOKEN_FILE.read_text()).get("scopes", [])
    out["scopes"] = granted
    return out
