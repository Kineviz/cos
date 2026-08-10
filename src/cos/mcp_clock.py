"""A clock, as a tool. Kiran asks; it never has to remember.

Everything before this tried to *tell* Kiran the date — a line in SOUL.md, a
heading in today.md, a computed brief. All of it went stale the moment it was
written, and none of it survived a session whose context header was frozen three
days earlier. Kiran then answered "what do I have tomorrow" with a meeting from
June and was completely confident about it.

A tool has none of that failure mode. The value is computed at the moment of the
call, so there is nothing to refresh, nothing to restart, and no way for a stale
copy to win an argument with a live one.

Three tools, because "what time is it" is rarely the real question:

  current_time   now, with timezone and day of week
  days_between   how long since / until — the arithmetic Kiran was doing in
                 prose and getting wrong
  todays_agenda  today's and tomorrow's meetings, read live from Google Calendar

`todays_agenda` reads the calendar directly rather than the indexed pages. The
pages are refreshed every 15 minutes, which is fine for search and wrong for
"am I free at 3pm".
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

LOCAL_TZ = datetime.now().astimezone().tzinfo

server = Server("cos-clock")


def _now() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="current_time",
            description=(
                "The current date and time, authoritative. Call this before "
                "answering anything involving today, tomorrow, this week, "
                "recently, upcoming, or how long since. Never infer the date "
                "from a document — a two-month-old email whose subject reads "
                "'meeting tomorrow' is not today."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA name, e.g. Europe/London. Defaults to local.",
                    }
                },
            },
        ),
        Tool(
            name="days_between",
            description=(
                "Days between two dates (YYYY-MM-DD). Omit `to` to measure "
                "from that date to now — 'how long since Morgan last wrote'. "
                "Negative means the date is in the future."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "YYYY-MM-DD"},
                    "to": {"type": "string", "description": "YYYY-MM-DD; defaults to today"},
                },
                "required": ["from"],
            },
        ),
        Tool(
            name="todays_agenda",
            description=(
                "Meetings for today and tomorrow, read live from Google "
                "Calendar with attendees and status. Use this for 'what do I "
                "have on', not search — the indexed calendar pages lag by up "
                "to 15 minutes and cannot answer 'am I free at 3pm'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How many days ahead. Default 2 (today + tomorrow).",
                    }
                },
            },
        ),
    ]


def _current_time(tz_name: str | None) -> str:
    tz = LOCAL_TZ
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            return f"Unknown timezone {tz_name!r}. Use an IANA name like Europe/London."
    now = datetime.now(tz=tz)
    return json.dumps(
        {
            "iso": now.isoformat(),
            "date": f"{now:%Y-%m-%d}",
            "time": f"{now:%H:%M}",
            "day_of_week": f"{now:%A}",
            "human": f"{now:%A %d %B %Y, %H:%M %Z}",
            "timezone": str(tz),
            "week_number": int(now.strftime("%V")),
        },
        indent=2,
    )


def _days_between(frm: str, to: str | None) -> str:
    try:
        a = datetime.strptime(frm, "%Y-%m-%d").date()
    except ValueError:
        return f"Could not read {frm!r} as YYYY-MM-DD."
    if to:
        try:
            b = datetime.strptime(to, "%Y-%m-%d").date()
        except ValueError:
            return f"Could not read {to!r} as YYYY-MM-DD."
    else:
        b = _now().date()
    delta = (b - a).days
    direction = "ago" if delta > 0 else ("from now" if delta < 0 else "today")
    return json.dumps(
        {"from": str(a), "to": str(b), "days": delta,
         "human": f"{abs(delta)} days {direction}"},
        indent=2,
    )


def _todays_agenda(days: int) -> str:
    try:
        from .calendar_source import is_worth_a_page, load_events
        from .config import Config

        cfg = Config.load()
        principals = {p.lower() for p in cfg.principal_addresses}
        now = datetime.now(timezone.utc)
        events = load_events(days_back=0, days_forward=max(1, days), now=now)
    except Exception as exc:  # calendar not connected, or offline
        return (
            f"Could not read the calendar ({type(exc).__name__}). "
            "This is a wiring fault, not an empty schedule — say so rather than "
            "reporting that there is nothing on."
        )

    local_today = _now().date()
    out: list[dict] = []
    for e in sorted(events, key=lambda x: x.start):
        if e.start < now or not is_worth_a_page(e, principals)[0]:
            continue
        day = e.start.astimezone(LOCAL_TZ).date()
        label = (
            "today" if day == local_today
            else "tomorrow" if (day - local_today).days == 1
            else f"{day:%A %d %b}"
        )
        out.append(
            {
                "when": label,
                "start": f"{e.start.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M}",
                "title": e.summary,
                "status": e.status,
                "attendees": [a for a, _ in e.attendees if a.lower() not in principals],
            }
        )
    if not out:
        return json.dumps(
            {"as_of": f"{_now():%Y-%m-%d %H:%M}", "meetings": [],
             "note": "Calendar read successfully; nothing scheduled in this window."},
            indent=2,
        )
    return json.dumps(
        {"as_of": f"{_now():%Y-%m-%d %H:%M}", "meetings": out}, indent=2
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    if name == "current_time":
        text = _current_time(args.get("timezone"))
    elif name == "days_between":
        text = _days_between(args.get("from", ""), args.get("to"))
    elif name == "todays_agenda":
        text = _todays_agenda(int(args.get("days", 2)))
    else:
        text = f"Unknown tool {name!r}."
    return [TextContent(type="text", text=text)]


async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="cos-clock",
                server_version="1.0.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
