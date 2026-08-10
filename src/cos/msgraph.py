"""A thin Microsoft Graph client: the five calls this project needs.

The same jobs the Gmail modules do, against /v1.0 of Graph:

    profile()               who am I           GET  /me
    messages(days)          the mail window    GET  /me/messages
    message(id)             one message        GET  /me/messages/{id}
    create_reply_draft(id)  a reply draft      POST /me/messages/{id}/createReply
    create_fresh_draft(id)  a new-topic draft  POST /me/messages
    calendar(days)          the calendar       GET  /me/calendarView

One structural difference from Gmail, and it is a gift: `createReply`
builds the recipients, subject and threading **server-side** from the
message being replied to. The whole class of bug where a malformed Cc
copied from an old thread poisons the new draft — the BigBank failure —
cannot happen here, because this code never assembles reply headers at
all. The invariant this project cares about is the same as Gmail's,
enforced by Microsoft instead of by us: the caller supplies words, never
a destination.

Fresh drafts keep the invariant the same way the Gmail path does: the
recipient is read from a real message that person sent, named by id.

urllib, not a Graph SDK, for the same reason ms_auth uses it: five calls
do not justify a dependency tree.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .ms_auth import access_token

BASE = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {access_token()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        raise GraphError(f"Graph {method} {path}: HTTP {e.code}"
                         + (f" — {detail}" if detail else "")) from e


def profile() -> dict:
    """Address and display name of the signed-in mailbox."""
    me = _call("GET", "/me?$select=mail,userPrincipalName,displayName")
    return {"address": (me.get("mail") or me.get("userPrincipalName")
                        or "").lower(),
            "name": me.get("displayName") or ""}


_MSG_FIELDS = ("id,conversationId,subject,receivedDateTime,from,toRecipients,"
               "ccRecipients,isDraft,bodyPreview")


def messages(days: int = 90, folder: str = "", limit: int = 2000) -> list[dict]:
    """Message metadata over the window, newest first, pages followed.

    Reads metadata only — enough to build the ledger of who wrote when.
    Bodies are fetched one at a time by whoever actually needs one.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    root = f"/me/mailFolders/{folder}/messages" if folder else "/me/messages"
    path = (f"{root}?$select={_MSG_FIELDS}"
            f"&$filter=receivedDateTime ge {since}"
            f"&$orderby=receivedDateTime desc&$top=100")
    out: list[dict] = []
    while path and len(out) < limit:
        page = _call("GET", path)
        out.extend(page.get("value", []))
        nxt = page.get("@odata.nextLink", "")
        path = nxt[len(BASE):] if nxt.startswith(BASE) else ""
    return out[:limit]


def message(message_id: str) -> dict:
    return _call("GET", f"/me/messages/{urllib.parse.quote(message_id)}"
                        f"?$select={_MSG_FIELDS}")


def create_reply_draft(message_id: str, body_text: str,
                       reply_all: bool = True) -> dict:
    """A reply draft in the Drafts folder. Recipients, subject and threading
    are assembled BY GRAPH from the source message — this code never sees a
    header, so it cannot copy a bad one. Nothing here can send."""
    if not (body_text or "").strip():
        raise GraphError("Refusing to create an empty draft.")
    verb = "createReplyAll" if reply_all else "createReply"
    mid = urllib.parse.quote(message_id)
    draft = _call("POST", f"/me/messages/{mid}/{verb}", {})
    did = urllib.parse.quote(draft.get("id", ""))
    # createReply quotes the original under the new body; prepend ours.
    _call("PATCH", f"/me/messages/{did}",
          {"body": {"contentType": "text",
                    "content": body_text.strip() + "\n"}})
    return draft


def create_fresh_draft(source_message_id: str, body_text: str,
                       subject: str) -> dict:
    """A new-topic draft to one person, whose address is read from a real
    message they sent — the caller cannot name a destination."""
    if not (body_text or "").strip():
        raise GraphError("Refusing to create an empty draft.")
    if not (subject or "").strip():
        raise GraphError("A fresh draft needs a subject.")
    src = message(source_message_id)
    sender = ((src.get("from") or {}).get("emailAddress") or {})
    addr = (sender.get("address") or "").strip()
    if "@" not in addr:
        raise GraphError("Source message has no usable address.")
    return _call("POST", "/me/messages", {
        "subject": subject.strip(),
        "body": {"contentType": "text", "content": body_text.strip() + "\n"},
        "toRecipients": [{"emailAddress": {"address": addr}}],
    })


def calendar(days: int = 2) -> list[dict]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    path = ("/me/calendarView?startDateTime={}&endDateTime={}"
            "&$select=subject,start,end,attendees,organizer,location"
            "&$orderby=start/dateTime&$top=50").format(
        now.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return _call("GET", path).get("value", [])
