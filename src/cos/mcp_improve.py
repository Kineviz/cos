"""Flagging a bad answer, as a tool Kiran can call mid-conversation.

The self-improvement loop (improve.py) feeds on three signals: slow answers
and benchmark regressions are collected automatically, but "that answer was
wrong" only exists in Wei's head at the moment he reads it. This server turns
that moment into a queue item — he complains in the chat he is already in,
Kiran files it, and the nightly loop tries to fix it.

Deliberately, this server can only *file* problems and *report* status. The
loop's actions — running agents, merging code — have no tool here, so a
conversation cannot be talked into deploying anything. The queue file is the
only thing this touches.
"""

from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

server = Server("cos-improve")


def _flag(question: str, what_was_wrong: str, answer_given: str) -> str:
    from .improve import add

    if not question.strip():
        return ("I need the question Wei actually asked, word for word, to "
                "file this.")
    item = add("flagged", question, complaint=what_was_wrong,
               answer=answer_given)
    return (f"Filed for the nightly improvement run (id {item['id']}). "
            f"Wei will hear back when there is a fix — he does not need to "
            f"do anything now.")


def _status() -> str:
    from .improve import queue

    open_items = queue("open")
    proposed = queue("proposed")
    if not open_items and not proposed:
        return "The improvement queue is empty — nothing flagged, nothing waiting."
    out = []
    if open_items:
        out.append(f"Waiting to be worked on ({len(open_items)}):")
        for it in open_items[:8]:
            out.append(f"- [{it['kind']}] {it['question'][:90]}"
                       + (f" — {it['complaint'][:60]}" if it["complaint"] else ""))
    if proposed:
        out.append(f"Fixed, waiting for Wei's approval ({len(proposed)}):")
        for it in proposed[:8]:
            out.append(f"- {it['question'][:90]} (apply: cos improve apply "
                       f"{it['branch']})")
    return "\n".join(out)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="flag_answer",
            description=(
                "File a bad answer for the nightly self-improvement run. "
                "Call this whenever Wei says an answer was wrong, incomplete, "
                "too slow, or missed the point — 'that's wrong', 'not what I "
                "asked', 'why did that take so long'. Pass his question "
                "exactly as he asked it, his complaint in his words, and the "
                "answer that was given. Filing is cheap and never bothers "
                "him; when in doubt, file it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Wei's question, word for word.",
                    },
                    "what_was_wrong": {
                        "type": "string",
                        "description": "His complaint, in his words.",
                    },
                    "answer_given": {
                        "type": "string",
                        "description": "The answer that disappointed him "
                                       "(or its first part).",
                    },
                },
                "required": ["question", "what_was_wrong"],
            },
        ),
        Tool(
            name="improvement_status",
            description=(
                "What the self-improvement loop is working on: flagged "
                "answers waiting to be fixed, and finished fixes waiting for "
                "Wei's approval. Call when Wei asks what got flagged, "
                "whether something was fixed yet, or what Kiran is improving."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    loop = asyncio.get_running_loop()
    if name == "flag_answer":
        text = await loop.run_in_executor(
            None, _flag, str(args.get("question") or "")[:400],
            str(args.get("what_was_wrong") or "")[:400],
            str(args.get("answer_given") or "")[:500])
    elif name == "improvement_status":
        text = await loop.run_in_executor(None, _status)
    else:
        text = f"Unknown tool {name!r}."
    return [TextContent(type="text", text=text)]


async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(
            read, write,
            InitializationOptions(
                server_name="cos-improve",
                server_version="1.0.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
