"""Sign in to Microsoft, the way a CLI has to: the device-code flow.

Wei's BSR buddy Cliff may be on Exchange, and drafting this backend on
Wei's machine must not touch the Gmail setup that runs Kiran — so nothing
here is imported unless Microsoft is actually in use, and the token lives
in its own file. Wei: *"I will need to test from another computer, so it
does not mess up Kiran."* Selection stays with `backend.py`, and it prefers
Gmail whenever a Google token exists.

Device code, not a redirect server, because setup happens on someone
else's laptop: `cos ms-auth` prints a URL and a code, they type it into a
browser — any browser, any machine — and the CLI polls until Microsoft
says yes. No localhost port, no app password, nothing to configure but a
client id.

The client id is the one per-organisation step Microsoft imposes: someone
registers "Kiran" once in Entra (a free tenant works) as a *public client*
with delegated Mail.ReadWrite, Calendars.Read, User.Read and
offline_access, and everyone after that just signs in. The id is not a
secret — public clients have no secret by design — it only names the app
on the consent screen.

Implemented on urllib rather than MSAL: three POSTs to two endpoints is
not worth a dependency tree, and this project installs with one command.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".cos" / "ms-token.json"

# The 'common' endpoint signs in work, school and personal accounts alike —
# an outlook.com test account and Cliff's corporate mailbox use the same
# flow.
_AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = "Mail.ReadWrite Calendars.Read User.Read offline_access"


class MsAuthError(RuntimeError):
    pass


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:  # noqa: BLE001
            raise MsAuthError(f"HTTP {e.code} from {url}") from e


def device_login(client_id: str, log=print) -> dict:
    """Run the device-code flow to completion and store the token."""
    if not client_id:
        raise MsAuthError(
            "No Microsoft client id. Register a public-client app in Entra "
            "and put its Application ID in the config as ms_client_id.")
    dc = _post(f"{_AUTHORITY}/oauth2/v2.0/devicecode",
               {"client_id": client_id, "scope": SCOPES})
    if "device_code" not in dc:
        raise MsAuthError(dc.get("error_description")
                          or "Device-code request failed.")
    log(dc.get("message")
        or f"Go to {dc.get('verification_uri')} and enter "
           f"{dc.get('user_code')}")

    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tok = _post(f"{_AUTHORITY}/oauth2/v2.0/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": dc["device_code"],
        })
        err = tok.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 2
            continue
        if err:
            raise MsAuthError(tok.get("error_description") or err)
        _save(client_id, tok)
        return tok
    raise MsAuthError("The code expired before it was entered. Run again.")


def _save(client_id: str, tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "client_id": client_id,
        "access_token": tok.get("access_token", ""),
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
    }), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)


def ms_available() -> bool:
    """A completed device flow is the whole prerequisite."""
    return TOKEN_FILE.exists()


def access_token() -> str:
    """A live access token, refreshed if the stored one has expired."""
    try:
        saved = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise MsAuthError("Not signed in to Microsoft. Run: cos ms-auth") from e

    if time.time() < saved.get("expires_at", 0) and saved.get("access_token"):
        return saved["access_token"]

    refresh = saved.get("refresh_token")
    if not refresh:
        raise MsAuthError("The Microsoft sign-in expired. Run: cos ms-auth")
    tok = _post(f"{_AUTHORITY}/oauth2/v2.0/token", {
        "grant_type": "refresh_token",
        "client_id": saved.get("client_id", ""),
        "refresh_token": refresh,
        "scope": SCOPES,
    })
    if "access_token" not in tok:
        raise MsAuthError(tok.get("error_description")
                          or "Token refresh failed. Run: cos ms-auth")
    # Microsoft rotates refresh tokens; keep the newest or the old one.
    tok.setdefault("refresh_token", refresh)
    _save(saved.get("client_id", ""), tok)
    return tok["access_token"]
