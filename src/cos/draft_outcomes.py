"""What happened to the drafts — the gate on unattended sending.

`docs/PLAN-becoming-a-chief-of-staff.md` says the jump from "sends with your
approval" to "sends a narrow class on its own" is decided by a measurement
rather than a judgement call, and that the measurement costs nothing because it
accumulates in Wei's own mailbox:

    sent as written  ->  the model's judgement was good enough
    rewritten first  ->  it was close but not trusted
    deleted          ->  it was wrong

The point of computing it now, while the answer is "we have almost no data", is
that in three months there will be hundreds of real cases instead of an opinion.

**How each state is detected.** The marker line cannot do it — it is stripped
whether or not Wei edits the draft. So `draft_broker` records a SHA-256 of the
normalised proposed body at creation, and this module compares it against what
actually left the mailbox:

  * draft still exists                       -> pending
  * draft gone, sent message in thread, hash matches   -> sent unedited
  * draft gone, sent message in thread, hash differs   -> rewritten
  * draft gone, no sent message after creation         -> deleted

Nothing here writes. It reads the audit log and the mailbox.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .draft_broker import AUDIT_LOG, _normalise
from .mailtext import read_message_bytes

PENDING, SENT, EDITED, DELETED, UNKNOWN = (
    "pending", "sent_as_written", "rewritten", "deleted", "unknown",
)


@dataclass
class Outcome:
    draft_id: str
    thread_id: str
    created: str
    subject: str
    state: str


def _audit_entries() -> list[dict]:
    if not AUDIT_LOG.exists():
        return []
    out = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") == "draft_created":
            out.append(rec)
    return out


def classify(limit: int = 200) -> list[Outcome]:
    """Resolve each recorded draft against the live mailbox."""
    entries = _audit_entries()[-limit:]
    if not entries:
        return []

    from googleapiclient.discovery import build

    from .google_auth import load_credentials

    svc = build("gmail", "v1", credentials=load_credentials(interactive=False),
                cache_discovery=False)

    live_drafts = set()
    token = None
    while True:
        resp = svc.users().drafts().list(
            userId="me", maxResults=100, pageToken=token).execute()
        live_drafts.update(d["id"] for d in resp.get("drafts", []))
        token = resp.get("nextPageToken")
        if not token:
            break

    outcomes: list[Outcome] = []
    for rec in entries:
        did = rec.get("draft_id", "")
        base = dict(
            draft_id=did,
            thread_id=rec.get("thread_id", ""),
            created=rec.get("ts", ""),
            subject=rec.get("subject", ""),
        )
        if did in live_drafts:
            outcomes.append(Outcome(**base, state=PENDING))
            continue

        want = rec.get("body_sha256")
        thread_id = rec.get("thread_id")
        if not thread_id:
            # Written before thread_id was recorded. Say so rather than guess:
            # a made-up denominator is worse than a smaller honest one.
            outcomes.append(Outcome(**base, state=UNKNOWN))
            continue

        try:
            thread = svc.users().threads().get(
                userId="me", id=thread_id, format="raw").execute()
        except Exception:  # noqa: BLE001 — thread deleted, or no access
            outcomes.append(Outcome(**base, state=UNKNOWN))
            continue

        created_ms = _epoch_ms(rec.get("ts", ""))
        sent_after = [
            m for m in thread.get("messages", [])
            if "SENT" in (m.get("labelIds") or [])
            and int(m.get("internalDate", 0)) >= created_ms
        ]
        if not sent_after:
            outcomes.append(Outcome(**base, state=DELETED))
            continue

        if not want:
            outcomes.append(Outcome(**base, state=UNKNOWN))
            continue

        import hashlib

        state = EDITED
        for m in sent_after:
            raw = base64.urlsafe_b64decode(m["raw"].encode())
            body = read_message_bytes(raw)
            got = hashlib.sha256(_normalise(body).encode()).hexdigest()
            if got == want:
                state = SENT
                break
        outcomes.append(Outcome(**base, state=state))

    return outcomes


def _epoch_ms(ts: str) -> int:
    if not ts:
        return 0
    try:
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp() * 1000)
    except ValueError:
        return 0


def summarise(outcomes: list[Outcome]) -> dict:
    c = Counter(o.state for o in outcomes)
    decided = c[SENT] + c[EDITED] + c[DELETED]
    return {
        "total": len(outcomes),
        "pending": c[PENDING],
        "sent_as_written": c[SENT],
        "rewritten": c[EDITED],
        "deleted": c[DELETED],
        "unknown": c[UNKNOWN],
        "decided": decided,
        # None, not 0.0, when nothing has been decided. A rate of "0%" off zero
        # cases reads as "it is never right", which is a different claim from
        # "we do not know yet".
        "accept_rate": (c[SENT] / decided) if decided else None,
    }


def render(summary: dict) -> str:
    if not summary["total"]:
        return "No drafts written yet — nothing to measure."
    parts = [
        f"{summary['sent_as_written']} sent as written",
        f"{summary['rewritten']} rewritten",
        f"{summary['deleted']} deleted",
    ]
    if summary["pending"]:
        parts.append(f"{summary['pending']} pending")
    if summary["unknown"]:
        parts.append(f"{summary['unknown']} unknown")
    line = " · ".join(parts)
    if summary["accept_rate"] is None:
        return f"{line}\n(no decided drafts yet — accept rate undefined)"
    return f"{line}\naccepted as written: {summary['accept_rate']:.0%} of {summary['decided']} decided"
