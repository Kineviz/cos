#!/usr/bin/env python
"""How much of the backfill survives a switch to the Gmail API?

Extraction is keyed by `content_hash` and a page is identified by
`{date-of-last-message}-{slugified-subject}`. So the cost of re-sourcing is
exactly: how many pages come out with a different name or different content.

This generates pages from both sources over the same window and compares them.
It writes nothing to the brain and calls nothing that mutates Gmail.

    python scripts/compare_sources.py --since 2026-07-01
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone

from cos.config import Config
from cos.export_brain import Thread, is_worth_a_page, slugify
from cos.kuzu import KuzuClient
from cos.mailsource import LocalMirrorSource
from cos.gmail_source import GmailApiSource


def page_name(t: Thread) -> str:
    return f"{t.last:%Y-%m-%d}-{slugify(t.subject)}"


def pages(threads: dict[str, Thread], principals: set[str], until=None) -> dict[str, Thread]:
    """Thread set reduced to the pages that would actually be written."""
    out: dict[str, Thread] = {}
    for t in threads.values():
        if not t.messages:
            continue
        if until is not None and t.last > until:
            continue
        keep, _ = is_worth_a_page(t, principals)
        if keep:
            out[page_name(t)] = t
    return out


def body_hash(t: Thread, source) -> str:
    h = hashlib.sha256()
    for m in t.messages:
        h.update(source.body(m).body.encode("utf-8", "replace"))
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-01")
    ap.add_argument("--until", default=None, help="Exclude threads whose last message is after this date.")
    args = ap.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    until = (datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             if args.until else None)

    cfg = Config.load()
    principals = set(cfg.principal_addresses)

    print(f"window: since {args.since}\n")

    with KuzuClient(cfg.kuzu_url) as client:
        mirror = LocalMirrorSource(client, cfg.gmail_root)
        mirror_pages = pages(mirror.load_threads(tuple(principals), since), principals, until)
        print(f"  local mirror : {len(mirror_pages)} pages")

        api_src = GmailApiSource(tuple(principals))
        api_pages = pages(api_src.load_threads(tuple(principals), since, until), principals, until)
        print(f"  gmail api    : {len(api_pages)} pages\n")

        shared = set(mirror_pages) & set(api_pages)
        only_mirror = set(mirror_pages) - set(api_pages)
        only_api = set(api_pages) - set(mirror_pages)

        identical = changed = 0
        for name in sorted(shared):
            if body_hash(mirror_pages[name], mirror) == body_hash(api_pages[name], api_src):
                identical += 1
            else:
                changed += 1

    total_api = len(api_pages) or 1
    print(f"  same name AND same content : {identical}   <- costs nothing")
    print(f"  same name, content differs : {changed}")
    print(f"  only in mirror (regrouped) : {len(only_mirror)}")
    print(f"  only in api  (regrouped)   : {len(only_api)}   <- re-extracted")
    print()
    print(f"  preserved: {identical / total_api:.0%} of the API's pages need no new work")

    if only_api:
        print("\n  examples of pages that would be new:")
        for n in sorted(only_api)[:5]:
            print(f"    {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
