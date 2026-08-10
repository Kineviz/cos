"""Calendar events as brain pages.

Kiran could reconstruct the entire history of a relationship and none of its
future — it did not know the meeting existed. This closes that: events become
pages alongside email, so "what do I need to know before my call with Morgan?"
can find the call, and "who have I actually met, versus only emailed?" becomes
answerable at all.

**Read-only, and deliberately so.** The grant is `calendar.readonly`. Creating
an event would mean inviting attendees, and inviting attendees sends mail —
handing the agent the outbound channel the whole design keeps away from it.
Same reasoning as the draft broker, one layer up.

Two traps, both handled here because both are silent when you get them wrong:

* **All-day events.** Google returns `date` (no time) rather than `dateTime`.
  Parsed naively that becomes midnight UTC, which is the *previous evening* in
  California — a Tuesday event reported as Monday. All-day events keep their
  local date and are marked, never converted.

* **Cancelled events are kept, not dropped.** "That Hillcrest call was cancelled
  twice" is a real question, and filtering cancellations at ingest makes it
  unanswerable. They are written with their status instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "untitled"


@dataclass
class Event:
    event_id: str
    summary: str
    start: datetime
    end: datetime | None
    all_day: bool
    status: str
    organizer: str
    attendees: list[tuple[str, str]] = field(default_factory=list)  # (email, response)
    location: str = ""
    description: str = ""
    recurring_event_id: str = ""
    html_link: str = ""

    @property
    def page_name(self) -> str:
        return f"{self.start:%Y-%m-%d}-{slugify(self.summary)}"


def _service():
    from googleapiclient.discovery import build

    from .google_auth import load_credentials

    return build(
        "calendar", "v3", credentials=load_credentials(interactive=False),
        cache_discovery=False,
    )


def _parse_when(node: dict) -> tuple[datetime, bool]:
    """(timestamp, is_all_day).

    An all-day event carries `date`; anything else carries `dateTime`. Treating
    the former as midnight UTC lands it on the wrong day in every timezone west
    of London, so it keeps its own date and is flagged instead.
    """
    if "date" in node:
        d = datetime.strptime(node["date"], "%Y-%m-%d")
        return d.replace(tzinfo=timezone.utc), True
    raw = node.get("dateTime")
    if not raw:
        return datetime.now(timezone.utc), False
    return datetime.fromisoformat(raw.replace("Z", "+00:00")), False


def load_events(
    days_back: int = 180,
    days_forward: int = 120,
    calendar_id: str = "primary",
    now: datetime | None = None,
) -> list[Event]:
    """Events in a window around today, recurring series expanded to instances."""
    svc = _service()
    now = now or datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_forward)).isoformat()

    events: list[Event] = []
    token = None
    while True:
        resp = (
            svc.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                # Expand recurrence into real instances; a rule is not a meeting.
                singleEvents=True,
                orderBy="startTime",
                showDeleted=True,  # cancellations are signal — see module docs
                maxResults=2500,
                pageToken=token,
            )
            .execute()
        )
        for e in resp.get("items", []):
            summary = e.get("summary") or "(no title)"
            start, all_day = _parse_when(e.get("start", {}))
            end, _ = _parse_when(e.get("end", {})) if e.get("end") else (None, False)
            events.append(
                Event(
                    event_id=e.get("id", ""),
                    summary=summary,
                    start=start,
                    end=end,
                    all_day=all_day,
                    status=e.get("status", "confirmed"),
                    organizer=(e.get("organizer", {}) or {}).get("email", ""),
                    attendees=[
                        (a.get("email", ""), a.get("responseStatus", ""))
                        for a in e.get("attendees", []) or []
                        if a.get("email")
                    ],
                    location=e.get("location", "") or "",
                    description=e.get("description", "") or "",
                    recurring_event_id=e.get("recurringEventId", "") or "",
                    html_link=e.get("htmlLink", "") or "",
                )
            )
        token = resp.get("nextPageToken")
        if not token:
            break
    return events


def is_worth_a_page(event: Event, principals: set[str]) -> tuple[bool, str]:
    """A meeting earns a page if a human other than you was involved.

    Solo blocks — focus time, reminders, travel holds — are calendar furniture.
    They crowd out the meetings when Kiran is asked what the week looks like.
    """
    others = {
        a.lower() for a, _ in event.attendees if a.lower() not in principals
    }
    if not others:
        return False, "no attendees other than you"
    if not event.summary.strip():
        return False, "no title"
    return True, ""


def render(event: Event, principals: set[str]) -> str:
    """One event as a markdown page, in the brain's existing style."""
    others = [(a, r) for a, r in event.attendees if a.lower() not in principals]
    when = (
        f"{event.start:%Y-%m-%d}"
        if event.all_day
        else f"{event.start:%Y-%m-%d %H:%M} – {event.end:%H:%M}"
        if event.end
        else f"{event.start:%Y-%m-%d %H:%M}"
    )

    lines = [
        "---",
        "type: meeting",
        f"title: {event.summary}",
        f"date: {event.start:%Y-%m-%d}",
        f"all_day: {str(event.all_day).lower()}",
        f"status: {event.status}",
        f"organizer: {event.organizer}",
        f"attendees: {len(others)}",
        "source: google-calendar",
        "generated_by: cos",
        "---",
        "",
        f"# {event.summary}",
        "",
        f"**When** {when}"
        + ("  ·  **all day**" if event.all_day else "")
        + (f"  ·  **{event.status.upper()}**" if event.status != "confirmed" else ""),
    ]
    if event.location:
        lines.append(f"**Where** {event.location}")
    if event.recurring_event_id:
        lines.append("**Recurring** part of a series")
    lines.append("")

    if others:
        lines.append("## Attendees")
        for addr, response in sorted(others):
            mark = {
                "accepted": "accepted",
                "declined": "declined",
                "tentative": "tentative",
                "needsAction": "no reply",
            }.get(response, response or "—")
            lines.append(f"- `{addr}` — {mark}")
        lines.append("")

    body = (event.description or "").strip()
    if body:
        lines += ["## Notes from the invitation", "", body, ""]

    if event.html_link:
        lines.append(f"[Open in Google Calendar]({event.html_link})")
    return "\n".join(lines) + "\n"


def write_pages(
    events: list[Event], out_dir: Path, principals: set[str]
) -> tuple[int, int]:
    """Write one page per meeting. Returns (written, skipped)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for e in events:
        keep, _ = is_worth_a_page(e, principals)
        if not keep:
            skipped += 1
            continue
        (out_dir / f"{e.page_name}.md").write_text(
            render(e, principals), encoding="utf-8"
        )
        written += 1
    return written, skipped
