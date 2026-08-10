"""Mail from the Gmail API instead of the local mirror.

Same two methods as `LocalMirrorSource`, so nothing above the seam changes.
What changes for the user: a consent screen replaces an IMAP app password,
mbsync, a launchd job, a graph server and a nightly rebuild — and mail is
current the moment it arrives instead of up to four hours later.

── Byte-identical pages, on purpose ─────────────────────────────────────────

Extraction is keyed by `content_hash`, so a thread that produces the same page
costs nothing to re-source. Two rules make that happen and neither is optional:

* **`format=raw`.** Gmail returns the original RFC-822 bytes — the same bytes
  mbsync wrote to disk — which then go through the same `mailtext.py`. Using
  Gmail's pre-parsed `payload` instead would change the text, change the hash,
  and re-extract 30,284 insights for nothing.

* **The same filters.** The mirror path drops Spam, Trash and the Promotions /
  Social / Forums / Updates categories in Cypher, and calendar robot mail by
  subject prefix. Those become Gmail search operators and a subject check here.
  Different filtering means a different set of pages, which is the other way to
  accidentally invalidate the backfill.

The one difference that cannot be avoided is threading. Gmail's `threadId` is
authoritative; the mirror reconstructed it and at one point had 8,868 of 11,526
messages with no thread id at all. Conversations Gmail groups differently will
land under a different `{date}-{subject}` slug and be re-extracted. That is a
one-time cost, and the result is more correct than what it replaces.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from .contacts import BULK_LABELS, CALENDAR_SUBJECT_PREFIXES
from .mailtext import MessageText, read_message_bytes

# Gmail search equivalents of BULK_LABELS. Spam and Trash are already excluded
# unless includeSpamTrash is set, so only the categories need naming.
_CATEGORY_EXCLUSIONS = " ".join(
    f"-category:{label.split()[-1].lower()}"
    for label in BULK_LABELS
    if label.startswith("Category ")
)

# Gmail caps a single messages.get at a reasonable size; batching keeps the
# round-trips down. 50 is comfortably under the batch limit and keeps one
# failure from costing much.
_BATCH = 50


class GmailApiSource:
    """Threads and bodies straight from Gmail."""

    def __init__(self, principals: tuple[str, ...] = ()) -> None:
        self._principals = tuple(p.lower() for p in principals)
        self._svc = None
        self._raw_cache: dict[str, bytes] = {}

    # ── plumbing ────────────────────────────────────────────────────────────

    def _service(self):
        if self._svc is None:
            from googleapiclient.discovery import build

            from .google_auth import load_credentials

            self._svc = build(
                "gmail", "v1", credentials=load_credentials(interactive=False),
                cache_discovery=False,
            )
        return self._svc

    @staticmethod
    def _is_calendar_robot(subject: str) -> bool:
        return any(subject.startswith(p) for p in CALENDAR_SUBJECT_PREFIXES)

    # ── the interface ───────────────────────────────────────────────────────

    def load_threads(self, principals: tuple[str, ...], since: datetime,
                     until: datetime | None = None) -> dict:
        """Every non-bulk message after `since`, grouped by Gmail's threadId."""
        from .export_brain import Message, Thread

        svc = self._service()
        me = {p.lower() for p in (principals or self._principals)}

        # Bound the window server-side. Without `before:` a comparison over an
        # old month would fetch every message since that date — two years of
        # mail, one raw fetch at a time.
        window = f"after:{since:%Y/%m/%d}"
        if until is not None:
            window += f" before:{until:%Y/%m/%d}"
        query = f"{window} {_CATEGORY_EXCLUSIONS}".strip()

        ids: list[str] = []
        page_token = None
        while True:
            resp = (
                svc.users()
                .messages()
                .list(userId="me", q=query, maxResults=500, pageToken=page_token)
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        threads: dict[str, Thread] = {}
        for i in range(0, len(ids), _BATCH):
            for mid in ids[i : i + _BATCH]:
                msg = svc.users().messages().get(
                    userId="me", id=mid, format="raw"
                ).execute()

                raw = base64.urlsafe_b64decode(msg["raw"].encode())
                self._raw_cache[mid] = raw

                parsed = self._headers_from_raw(raw)
                subject = parsed["subject"]
                if self._is_calendar_robot(subject):
                    continue

                ts = datetime.fromtimestamp(
                    int(msg["internalDate"]) / 1000, tz=timezone.utc
                )

                m = Message(
                    msg_id=mid,
                    sender=parsed["sender"],
                    sender_name=parsed["sender_name"],
                    timestamp=ts,
                    subject=subject,
                    # No file on disk. The body comes from the raw bytes above,
                    # keyed by msg_id — see `body()`.
                    maildir_path="",
                )
                m.recipients = [a for a in parsed["recipients"] if a not in me]
                tid = msg.get("threadId") or mid
                threads.setdefault(tid, Thread(thread_id=tid)).messages.append(m)

        for t in threads.values():
            t.messages.sort(key=lambda m: m.timestamp)
        return threads

    @staticmethod
    def _headers_from_raw(raw: bytes) -> dict:
        """Sender, recipients and subject, parsed from the original bytes.

        Deliberately not Gmail's parsed header list: one parser for everything
        means one set of quirks, and the page hash depends on it.
        """
        import email
        import email.policy
        from email.utils import getaddresses

        msg = email.message_from_bytes(raw, policy=email.policy.default)
        sender_name, sender = "", ""
        for name, addr in getaddresses([str(msg.get("From", ""))]):
            sender_name, sender = name, (addr or "").lower()
            break
        recipients = [
            a.lower()
            for _, a in getaddresses(
                [str(msg.get("To", "")), str(msg.get("Cc", ""))]
            )
            if a
        ]
        return {
            "sender": sender,
            "sender_name": sender_name or None,
            "subject": str(msg.get("Subject", "") or ""),
            "recipients": recipients,
        }

    def body(self, message) -> MessageText:
        """Novel text, through the same stripper the mirror path uses."""
        raw = self._raw_cache.get(message.msg_id)
        if raw is None:
            svc = self._service()
            msg = svc.users().messages().get(
                userId="me", id=message.msg_id, format="raw"
            ).execute()
            raw = base64.urlsafe_b64decode(msg["raw"].encode())
            self._raw_cache[message.msg_id] = raw
        return read_message_bytes(raw)

    def describe(self) -> str:
        return "Gmail API (real-time)"
