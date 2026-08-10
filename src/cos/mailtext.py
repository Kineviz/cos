"""Extract readable text from a maildir message.

Only 17.9% of raw body text in this corpus is novel content — the rest is
quoted replies, signatures and disclaimers. Feeding that to an index makes
retrieval return the *last* reply in a chain rather than the message that
first said the thing, and it was the direct cause of 2 of 7 measured
extraction errors in the design review (a sentence inside a quoted block
attributed to the wrong message and date).

A `>`-only stripper is not enough: 11.4% of this corpus uses Outlook
`From:/Sent:/To:` block quoting with no prefix at all, and that 11.4% is
disproportionately the important counterparties — the agency client, Racing Victoria,
Bay Street, Hillcrest and Realising Potential are all Outlook shops.
"""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass
from pathlib import Path

# ── quote boundaries ────────────────────────────────────────────────────────

# "On Mon, 3 Jun 2026 at 14:02, Alice <a@x.com> wrote:" and localised variants.
_ON_WROTE = re.compile(
    r"^\s*(On|El|Le|Am|Op|Il)\s.{0,200}?\b(wrote|escribió|a écrit|schrieb|"
    r"schreef|ha scritto)\s*:\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Outlook / Exchange block quoting. No ">" prefix anywhere — the tell is a run
# of header-ish lines after a separator or a bare "From:" line.
_OUTLOOK_BLOCK = re.compile(
    r"^\s*(-{2,}\s*Original Message\s*-{2,}|_{5,}|-{5,})?\s*$\n"
    r"^\s*From:\s*.+$\n"
    r"(^\s*(Sent|Date|To|Cc|Subject|Reply-To):\s*.*$\n){1,6}",
    re.IGNORECASE | re.MULTILINE,
)
_BARE_FROM_HEADER = re.compile(
    r"^\s*From:\s*.+\n^\s*(Sent|Date):\s*.+$", re.IGNORECASE | re.MULTILINE
)

_FORWARDED = re.compile(
    r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE
)

# Google Calendar invites paste a wall of dial-in boilerplate under whatever the
# human actually wrote. It is not a quote, so the quote rules miss it, and it is
# near-identical across thousands of messages — exactly the kind of repeated text
# that poisons a vector index.
_CALENDAR_BOILERPLATE = re.compile(
    r"^\s*(Join with Google Meet|Join by phone|More phone numbers|"
    r"Meeting link:|Video call link:|Join Zoom Meeting|"
    r"Invitation from Google Calendar|"
    r"You are receiving this (courtesy )?email|"
    r"Forgot your PIN|Learn more about Meet at:|"
    r"~~//~~|__+ ?$)",
    re.IGNORECASE | re.MULTILINE,
)

# Signature delimiter per RFC 3676: exactly "-- " on its own line.
_SIG_DELIM = re.compile(r"^-- $", re.MULTILINE)

_SIG_HEURISTIC = re.compile(
    r"^\s*(Best regards|Kind regards|Best wishes|Regards|Cheers|Thanks|"
    r"Many thanks|Sincerely|Warmly|Best|Sent from my (iPhone|iPad|Android|"
    r"Samsung|mobile))\b[,.]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_DISCLAIMER = re.compile(
    r"(This (e-?mail|message)( and any attachments)? (is|are|may be) "
    r"(confidential|intended)|CONFIDENTIALITY NOTICE|The information contained "
    r"in this (e-?mail|message)|If you are not the intended recipient|"
    r"Please consider the environment before printing|"
    r"This email has been scanned|Unsubscribe\b)",
    re.IGNORECASE,
)

# Postgres rejects NUL (0x00) in text columns outright — one 2021 message
# carried 2,885 of them and blocked the entire 25,758-page sync. PGLite
# tolerated it, so this only surfaced on the engine migration. Strip NUL and
# the other C0 controls that carry no meaning in a mail body.
_CONTROL_BYTES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CID_PLACEHOLDER = re.compile(r"\[cid:[^\]]+\]|\[image:[^\]]*\]", re.IGNORECASE)
_URL_ONLY_LINE = re.compile(r"^\s*<?https?://\S+>?\s*$", re.MULTILINE)
_MULTI_BLANK = re.compile(r"\n{3,}")


@dataclass
class MessageText:
    body: str
    truncated_at: str | None  # which rule ended the message, for debugging

    @property
    def is_empty(self) -> bool:
        return len(self.body.strip()) < 3


def _earliest(text: str, *patterns: re.Pattern[str]) -> tuple[int, str | None]:
    """Index of the earliest quote boundary, and which rule found it."""
    best, name = len(text), None
    for pattern in patterns:
        m = pattern.search(text)
        if m and m.start() < best:
            best, name = m.start(), pattern.pattern[:24]
    return best, name


def strip_quoted(text: str) -> MessageText:
    """Cut everything from the first quote boundary onward, then tidy."""
    cut, rule = _earliest(
        text,
        _ON_WROTE,
        _OUTLOOK_BLOCK,
        _BARE_FROM_HEADER,
        _FORWARDED,
        _CALENDAR_BOILERPLATE,
    )
    body = text[:cut]

    # Drop ">"-prefixed lines that survived above the boundary (inline replies).
    body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith(">"))

    # Signature: RFC delimiter first, then the sign-off heuristic, but only if
    # it lands in the last third — "Thanks" as the whole message is content.
    m = _SIG_DELIM.search(body)
    if m:
        body, rule = body[: m.start()], rule or "sig-delim"
    else:
        for m in _SIG_HEURISTIC.finditer(body):
            if m.start() > len(body) * 0.66:
                body, rule = body[: m.start()], rule or "sig-heuristic"
                break

    m = _DISCLAIMER.search(body)
    if m:
        body, rule = body[: m.start()], rule or "disclaimer"

    body = _CONTROL_BYTES.sub("", body)
    body = _CID_PLACEHOLDER.sub("", body)
    body = _URL_ONLY_LINE.sub("", body)
    body = _MULTI_BLANK.sub("\n\n", body)
    return MessageText(body=body.strip(), truncated_at=rule)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&mdash;", "—"), ("&ndash;", "–"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"&#\d+;", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _as_text(payload: object) -> str:
    """Coerce a MIME payload to str.

    `get_content()` does NOT always return str — for some charsets in this
    corpus (12 years of mail, plenty of legacy encodings) it succeeds and hands
    back bytes. The old code only decoded inside the exception path, so a
    successful-but-bytes payload crashed the whole export at message 11,413.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    return str(payload)


def read_message_body(path: str | Path, max_chars: int = 20000) -> MessageText:
    """Read one maildir file and return its novel text.

    Prefers text/plain; falls back to a crude HTML strip. Never raises on a
    malformed message — a corpus this old has plenty, and one bad file must
    not stop an export.
    """
    try:
        with open(path, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=email.policy.default)
    except (OSError, email.errors.MessageError, ValueError):
        return MessageText(body="", truncated_at="unreadable")
    return _extract(msg, max_chars)


def read_message_bytes(raw: bytes, max_chars: int = 20000) -> MessageText:
    """Same, for a message already in memory.

    The Gmail API path fetches `format=raw`, which returns the original RFC-822
    bytes — the same bytes mbsync writes to disk. Routing both sources through
    `_extract` is what lets a thread produce a byte-identical page from either
    one, and a byte-identical page is what stops 30,284 insights being
    re-extracted for nothing.
    """
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except (email.errors.MessageError, ValueError):
        return MessageText(body="", truncated_at="unreadable")
    return _extract(msg, max_chars)


def _extract(msg, max_chars: int) -> MessageText:
    """The shared body: MIME walk, quote-strip, truncate. One implementation."""
    plain, html = [], []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get_content_disposition() == "attachment":
                    continue
                ctype = part.get_content_type()
                if ctype not in ("text/plain", "text/html"):
                    continue
                try:
                    payload = part.get_content()
                except (LookupError, ValueError, UnicodeDecodeError):
                    raw = part.get_payload(decode=True) or b""
                    payload = raw.decode("utf-8", errors="replace")
                (plain if ctype == "text/plain" else html).append(_as_text(payload))
        else:
            payload = msg.get_content()
            (html if msg.get_content_type() == "text/html" else plain).append(
                _as_text(payload)
            )
    except Exception:
        return MessageText(body="", truncated_at="parse-error")

    text = "\n".join(plain).strip() or _html_to_text("\n".join(html))
    result = strip_quoted(text)
    if len(result.body) > max_chars:
        return MessageText(
            body=result.body[:max_chars] + "\n\n[… truncated]",
            truncated_at="max-chars",
        )
    return result
