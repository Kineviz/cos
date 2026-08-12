"""Panel tools for the assistant: read and edit Tasks and Prospects.

Wei: *"the chat in the panel allow us to modify the item, change state,
etc."* These are the tools that make that true — from the dashboard chat and
from Telegram alike, because both go through the same assistant.

One interface over two stores. Tasks lives in the agenda store, Prospects in
the panel database; the assistant should not have to know that. `panel` is
"tasks" or "prospects", `item` is the name a person would use, and
resolution is deliberately strict: a name matching two items is an error,
not a guess, because renaming the wrong deal quietly is worse than asking.

Nothing here can delete. Done and archived are reversible states; deletion
is a human's move, in the GUI.
"""

from __future__ import annotations

import asyncio
import json

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

server = Server("cos-panels")

TASKS_STATES = ("today", "soon", "backlog")


def _agenda():
    from . import agenda
    return agenda


def _pdb():
    from . import paneldb
    return paneldb


def _task_by_name(needle: str):
    ag = _agenda()
    items = [i for i in ag.build() if not i.done]
    needle = (needle or "").strip().lower()
    exact = [i for i in items if i.title.lower() == needle]
    if len(exact) == 1:
        return exact[0]
    part = [i for i in items if needle in i.title.lower()]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise ValueError(
            f"{needle!r} matches several tasks: "
            + "; ".join(i.title[:40] for i in part[:4])
            + ". Say more of the name.")
    raise ValueError(f"No open task matches {needle!r}.")


def _resolve(panel: str) -> str:
    """The database panel a name refers to. "prospects" and every panel the
    user created resolve by id or title; anything else is an error that
    lists what exists, because guessing which panel to edit is how an item
    lands somewhere the user never looks."""
    pdb = _pdb()
    needle = (panel or "").strip().lower()
    if needle in ("", pdb.PROSPECTS):
        pdb.ensure_panel(pdb.PROSPECTS, "Prospects", [])
        return pdb.PROSPECTS
    for p in pdb.list_panels():
        if needle in (p["id"], p["title"].lower()):
            return p["id"]
    known = ", ".join(["tasks", "prospects"]
                      + [p["title"] for p in pdb.list_panels()
                         if p["id"] != pdb.PROSPECTS])
    raise ValueError(f"No panel called {panel!r}. The panels are: {known}. "
                     f"To make a new one, call panel_create.")


def _panel_items(panel: str) -> str:
    if panel == "tasks":
        rows = [{"name": i.title, "state": i.bucket, "note": i.detail,
                 "kind": i.kind}
                for i in _agenda().build() if not i.done]
        return json.dumps(rows, ensure_ascii=False)
    rows = []
    for r in _pdb().list_items(_resolve(panel)):
        # Newest note first; the full dated history rides along so "what did
        # I say about X in June" is answerable without another tool.
        rows.append({"name": r["name"], "state": r["state"],
                     "note": r["note"],
                     "notes": list(reversed(r["notes"]))})
    return json.dumps(rows, ensure_ascii=False)


def _panel_add(panel: str, name: str, state: str, note: str) -> str:
    if not (name or "").strip():
        return "An item needs a name."
    if panel == "tasks":
        item = _agenda().add(name, note)
        if state in TASKS_STATES:
            _agenda().move(item.id, state)
        return f"Added task: {name}"
    pid = _resolve(panel)
    _pdb().add_item(pid, name, state=state, note=note)
    _export(pid)
    return f"Added to {pid}: {name}" + (f" ({state})" if state else "")


def _panel_create(title: str, states: list[str]) -> str:
    """A new dashboard panel, made from a chat message. Wei: "UI should not
    be static. we should be able to on demand add a new dashboard tab."."""
    try:
        made = _pdb().create_panel(title, states)
    except ValueError as e:
        return str(e)
    return (f"Created the {made['title']} panel — it is on the dashboard "
            f"now. Add items with panel_add (panel: {made['id']!r}).")


def _panel_set(panel: str, item: str, state: str, name: str,
               note: str) -> str:
    if panel == "tasks":
        it = _task_by_name(item)
        done_bits = []
        if state:
            if state not in TASKS_STATES:
                return f"For tasks, state must be one of {TASKS_STATES}."
            _agenda().move(it.id, state)
            done_bits.append(f"moved to {state}")
        if note:
            _agenda().act(it.id, "comment", note)
            done_bits.append("note added")
        if name:
            done_bits.append("tasks keep their source titles — rename in "
                             "the to-do list itself")
        return f"{it.title}: " + (", ".join(done_bits) or "nothing to change")
    pdb = _pdb()
    pid = _resolve(panel)
    row = pdb.find_item(pid, item)
    if row is None:
        return (f"No single item in {pid} matches {item!r}. "
                "Use panel_items to see the names.")
    pdb.update_item(row["id"], name=name or None, state=state or None,
                    note=note or None)
    _export(pid)
    changed = [w for w, v in
               (("stage", state), ("name", name), ("note", note)) if v]
    return f"{row['name']}: updated " + ", ".join(changed or ["nothing"])


def _panel_focus(panel: str, item: str, on: bool) -> str:
    if panel == "tasks":
        return "The attention list is for database panels. For tasks, move "\
               "it to today instead."
    pdb = _pdb()
    pid = _resolve(panel)
    row = pdb.find_item(pid, item)
    if row is None:
        return f"No single item in {pid} matches {item!r}."
    pdb.set_focus(row["id"], on)
    _export(pid)
    return (f"{row['name']} is now at the top of {pid} under 'needs "
            f"attention now'." if on else
            f"{row['name']} cleared from 'needs attention now'.")


def _replied_elsewhere(who: str, channel: str, note: str) -> str:
    from . import agenda

    try:
        msg = agenda.handle_owed(who, channel=channel, note=note)
    except ValueError as e:
        return str(e)
    _export()
    return msg


def _still_waiting(who: str) -> str:
    from . import agenda

    try:
        msg = agenda.reopen_owed(who)
    except ValueError as e:
        return str(e)
    _export()
    return msg


def _panel_done(panel: str, item: str) -> str:
    if panel == "tasks":
        it = _task_by_name(item)
        _agenda().act(it.id, "done")
        return f"Done: {it.title}"
    pdb = _pdb()
    pid = _resolve(panel)
    row = pdb.find_item(pid, item)
    if row is None:
        return f"No single item in {pid} matches {item!r}."
    pdb.update_item(row["id"], archived=True)
    _export(pid)
    return f"Archived: {row['name']}"


def _export(panel: str = "prospects") -> None:
    """Keep the two views of the database current after a write: the
    markdown files, and the dashboard snapshot the panel is drawn from.

    The snapshot is the one that bit. Kiran archived Insight2, said so, and
    the panel kept showing it — the database was right and the page reads
    the snapshot, which nothing on this path refreshed. "Archived" while
    still on screen is worse than an error message.

    Best-effort: a failed view update must not fail the edit — the database
    is the master copy and the 15-minute refresh rebuilds both views anyway.

    Custom panels need neither view: the page reads them straight from the
    database on /api/panels, so there is nothing to go stale.
    """
    if panel != _pdb().PROSPECTS:
        return
    try:
        from .config import Config
        _pdb().export_markdown(Config.load().vault_root)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import webconfig
        webconfig._repatch_prospects()
    except Exception:  # noqa: BLE001
        pass


_PANEL_ARG = {
    "type": "string",
    "description": 'Which panel: "tasks", "prospects", or a custom panel '
                   "by its name.",
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="panel_items",
            description=(
                "List a panel's items — tasks (with bucket today/soon/"
                "backlog) or prospects (deals, with stage and note). Call "
                "this before changing anything, and use these exact names "
                "in the other panel tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {"panel": _PANEL_ARG},
                "required": ["panel"],
            },
        ),
        Tool(
            name="panel_add",
            description=(
                "Add an item to a panel. For tasks, state is today, soon or "
                "backlog (default today). For prospects, state is the "
                "pipeline stage, in the user's own words."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "panel": _PANEL_ARG,
                    "name": {"type": "string"},
                    "state": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["panel", "name"],
            },
        ),
        Tool(
            name="panel_set",
            description=(
                "Change an item's state, name, or note; only the fields "
                "given change. `item` is its current name, or enough of it "
                "to be unambiguous. For tasks, state moves it between "
                "today, soon and backlog, and note adds a comment. For "
                "prospects, state is the stage (a new stage name is "
                "allowed) and note ADDS a dated note on top of the history "
                "— it never overwrites earlier notes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "panel": _PANEL_ARG,
                    "item": {"type": "string"},
                    "state": {"type": "string"},
                    "name": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["panel", "item"],
            },
        ),
        Tool(
            name="panel_focus",
            description=(
                "Put a prospect in the 'needs attention now' list at the top "
                "of the Prospects panel, or take it out. This is separate "
                "from its stage — flagging a deal never changes whether it "
                "is Qualified or Engaged. Set on=false to clear it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "panel": _PANEL_ARG,
                    "item": {"type": "string"},
                    "on": {"type": "boolean",
                           "description": "true to flag, false to clear. "
                                          "Defaults to true."},
                },
                "required": ["panel", "item"],
            },
        ),
        Tool(
            name="replied_elsewhere",
            description=(
                "Take someone OFF the waiting-on-a-reply list because Wei "
                "answered them outside email — SMS, WhatsApp, LinkedIn, a "
                "phone call, in person. Records the channel and an optional "
                "note, persists across refreshes, and the person returns "
                "automatically if they write again. Use when Wei says things "
                "like 'I already replied to X on WhatsApp', 'archive X', "
                "'X is handled', 'I called them back'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "who": {"type": "string",
                            "description": "Their name as it appears on the "
                                           "waiting list."},
                    "channel": {"type": "string",
                                "description": "email, sms, whatsapp, "
                                               "linkedin, phone, in-person "
                                               "or other."},
                    "note": {"type": "string",
                             "description": "Optional context, e.g. 'said "
                                            "yes to Tuesday'."},
                },
                "required": ["who"],
            },
        ),
        Tool(
            name="still_waiting",
            description=(
                "Undo replied_elsewhere: put someone back on the waiting "
                "list. Use when Wei says the archive was a mistake or they "
                "are in fact still owed a reply."
            ),
            inputSchema={
                "type": "object",
                "properties": {"who": {"type": "string"}},
                "required": ["who"],
            },
        ),
        Tool(
            name="panel_done",
            description=(
                "Mark a task done, or archive a panel item. Both are "
                "reversible in the dashboard."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "panel": _PANEL_ARG,
                    "item": {"type": "string"},
                },
                "required": ["panel", "item"],
            },
        ),
        Tool(
            name="panel_create",
            description=(
                "Create a NEW dashboard panel — a new tab with the same "
                "machinery as Prospects: items with states, dated notes, "
                "drag order and a needs-attention list. Use when Wei asks "
                "for a new panel, tab, tracker, or list on the dashboard "
                "('make me a GTM panel', 'I want a hiring tracker'). Then "
                "add his items with panel_add."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string",
                              "description": "The panel's name, in Wei's "
                                             "words, e.g. 'GTM'."},
                    "states": {"type": "array", "items": {"type": "string"},
                               "description": "Optional starting states/"
                                              "stages. States are also "
                                              "learned from use."},
                },
                "required": ["title"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    panel = (args.get("panel") or "").strip().lower()
    try:
        if name in ("replied_elsewhere", "still_waiting"):
            panel = "tasks"        # they act on the waiting list directly
        if name == "panel_create":
            text = _panel_create(
                str(args.get("title") or ""),
                [s for s in (args.get("states") or [])
                 if isinstance(s, str)])
        elif name == "panel_items":
            text = _panel_items(panel)
        elif name == "panel_add":
            text = _panel_add(panel, args.get("name", ""),
                              args.get("state", ""), args.get("note", ""))
        elif name == "panel_set":
            text = _panel_set(panel, args.get("item", ""),
                              args.get("state", ""), args.get("name", ""),
                              args.get("note", ""))
        elif name == "panel_focus":
            text = _panel_focus(panel, args.get("item", ""),
                                bool(args.get("on", True)))
        elif name == "replied_elsewhere":
            text = _replied_elsewhere(args.get("who", ""),
                                      args.get("channel", ""),
                                      args.get("note", ""))
        elif name == "still_waiting":
            text = _still_waiting(args.get("who", ""))
        elif name == "panel_done":
            text = _panel_done(panel, args.get("item", ""))
        else:
            text = f"Unknown tool {name!r}."
    except (ValueError, KeyError) as e:
        text = str(e)
    return [TextContent(type="text", text=text)]


async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="cos-panels",
                server_version="1.0.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
