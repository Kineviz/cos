"""Our own graph, added to gbrain. Deliberately one edge type.

gbrain already has a link graph and it is bigger than it looks: 33,368 of
62,782 pages carry an inbound link. But every edge runs the same way —
**email → person**, written by the wikilink extractor when a thread mentions
someone. So `backlinks` on a person works, and `graph people/x --depth 2`
returns nothing at all, because there is no edge leaving a person to follow.

That single missing direction is what blocks multi-hop. Add it and the walk
opens up:

    person → the threads that mention them → the other people in those threads

which is the shape of "who else is involved in Northwind", "who does Morgan
talk to here", "who should be on this reply" — all questions similarity can
only guess at.

**Kept simple on purpose.** One edge type, `mentioned_in`, provenance
`cos-graph`, derived entirely from links gbrain already holds. Nothing is
invented: if the extractor did not think a thread mentions someone, neither
does this. That makes it disposable — `--undo` removes exactly what it wrote
and leaves the wikilinks untouched — and it makes it honest, because a derived
index that adds facts is a second source of truth, and this adds none.

Run it again whenever the brain has been synced; it is idempotent.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

LINK_TYPE = "mentioned_in"
LINK_SOURCE = "cos-graph"

# The page types worth walking out of. Threads mention people and companies;
# they do not mention emails.
ENTITY_TYPES = ("person", "organization", "company", "concept", "project")


# --------------------------------------------------------------------------
# Meetings
#
# All 1,613 meeting pages have ZERO edges, which is why "person → meeting note
# → person" cannot be walked. It is not a graph-engine limit: the two page
# types were written by different importers and only one of them used
# wikilinks.
#
#   an email page:    - [[jordan-lee|a colleague]] — `jordan.lee77@gmail.example`
#   a meeting page:   - `guest@partner.example` — declined
#
# So emails joined the graph and meetings did not. The addresses are right
# there; nobody had connected them to the person pages the emails already
# name. That mapping is harvestable from the email pages themselves — 863
# addresses across 25,835 files in 0.7 seconds — so this invents nothing
# either. It reuses the identity resolution the mail importer already did.

_WIKILINK = re.compile(r"\[\[([a-z0-9-]+)\|([^\]]*)\]\][^`]*`([^`]+)`")
_ATTENDEE = re.compile(r"^\s*-\s+`([^`]+@[^`]+)`", re.M)


def person_by_address() -> dict[str, str]:
    """Every address the mail importer has already tied to a person page.

    Read off disk rather than out of the database: it is 25,835 files and
    takes under a second, against thousands of subprocess round trips.
    """
    from .dateindex import BRAIN_DIR

    out: dict[str, str] = {}
    folder = BRAIN_DIR / "email"
    if not folder.is_dir():
        return out
    for path in folder.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for slug, _name, addr in _WIKILINK.findall(text):
            out.setdefault(addr.strip().lower(), f"people/{slug}")
    return out


def meeting_attendees() -> dict[str, list[str]]:
    """Meeting page → the addresses it lists as attendees."""
    from .dateindex import BRAIN_DIR

    out: dict[str, list[str]] = {}
    folder = BRAIN_DIR / "calendar"
    if not folder.is_dir():
        return out
    for path in folder.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        addrs = [a.strip().lower() for a in _ATTENDEE.findall(text)]
        if addrs:
            out[f"calendar/{path.stem}"] = addrs
    return out


def link_meetings(limit: int = 0, undo: bool = False,
                  log=lambda s: None) -> "Result":
    """Put meetings into the graph, in both directions.

    Both, because one direction is what was wrong with the graph in the first
    place: the meeting needs to reach its attendees, and a person needs to
    reach their meetings, or "person → meeting → person" still has a missing
    hop.
    """
    res = Result()
    people = person_by_address()
    if not people:
        return res

    # Only link to person pages that actually EXIST, which is far fewer than
    # the wikilinks suggest. The mail importer writes
    # `[[alex-doe|Alex Doe]]` but never creates people/alex-doe,
    # so most wikilinks point at nothing — which is the real reason the graph
    # looked sparse, and why link-sources reported two resolved edges against
    # 13,074 rows. Linking to a page that does not exist fails with "page not
    # found", so this filters first rather than generating thousands of errors.
    real = existing_pages("person")
    people = {a: s for a, s in people.items() if s in real}
    if not people:
        return res
    for slug, addrs in list(meeting_attendees().items())[:limit or None]:
        res.entities += 1
        matched = {people[a] for a in addrs if a in people}
        if not matched:
            res.skipped += 1
            continue
        for person in sorted(matched):
            verb = "unlink" if undo else "link"
            for a, b in ((slug, person), (person, slug)):
                out = _gb(verb, a, b, "--link-type", LINK_TYPE,
                          "--link-source", LINK_SOURCE)
                if out == "" and not undo:
                    res.errors += 1
                else:
                    res.edges += 1
        log(f"  {slug} ↔ {len(matched)} person page(s)")
    return res


@dataclass
class Result:
    entities: int = 0
    edges: int = 0
    skipped: int = 0
    errors: int = 0


def _gb(*args: str) -> str:
    from .ask import BRAIN_DIR, _env, _gbrain

    gb = _gbrain()
    if not gb:
        return ""
    try:
        return subprocess.run([gb, *args], capture_output=True, text=True,
                              timeout=30, cwd=str(BRAIN_DIR),
                              env=_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _call(tool: str, args: dict) -> list:
    try:
        rows = json.loads(_gb("call", tool, json.dumps(args)) or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(rows, dict):
        for key in ("pages", "results", "rows"):
            if isinstance(rows.get(key), list):
                return rows[key]
        return []
    return rows if isinstance(rows, list) else []


def existing_pages(kind: str, cap: int = 5000) -> set[str]:
    """Slugs of every page of a type, fetched in pages.

    One call with limit=5000 returned 73KB and came back truncated, so the
    JSON never parsed and `_call` quietly returned nothing — which read
    downstream as "no person pages exist" immediately after creating 656 of
    them. Paginating keeps each response small enough to survive.
    """
    out: set[str] = set()
    step = 200
    for offset in range(0, cap, step):
        rows = _call("list_pages", {"type": kind, "limit": step,
                                    "offset": offset})
        if not rows:
            break
        out.update(r["slug"] for r in rows
                   if isinstance(r, dict) and r.get("slug"))
        if len(rows) < step:
            break
    return out


def entities(limit: int = 0) -> list[str]:
    """Entity pages, newest first."""
    out: list[str] = []
    for kind in ENTITY_TYPES:
        rows = _call("list_pages", {"type": kind,
                                    "limit": limit or 1000})
        out.extend(r["slug"] for r in rows if isinstance(r, dict) and r.get("slug"))
        if limit and len(out) >= limit:
            return out[:limit]
    return out


def build(limit: int = 0, undo: bool = False, log=lambda s: None) -> Result:
    """Write (or remove) the reverse edge for every entity with backlinks."""
    res = Result()
    for slug in entities(limit):
        res.entities += 1
        back = _call("get_backlinks", {"slug": slug})
        sources = [r.get("from_slug") for r in back
                   if isinstance(r, dict) and r.get("from_slug")]
        if not sources:
            res.skipped += 1
            continue
        for src in sources:
            verb = "unlink" if undo else "link"
            out = _gb(verb, slug, src, "--link-type", LINK_TYPE,
                      "--link-source", LINK_SOURCE)
            if out == "" and not undo:
                res.errors += 1
            else:
                res.edges += 1
        log(f"  {slug} → {len(sources)} thread(s)")
    return res


# --------------------------------------------------------------------------
# Step 1: the nodes


def people_with_threads() -> dict[str, dict]:
    """Every person the mail importer named, with the threads that named them.

    This is the whole of step 1's input, and it contains nothing inferred. The
    importer already decided that this address belongs to this person and gave
    them a slug; it simply never created the page. Harvesting its own output
    back off disk is cheaper than asking a model to re-derive it, and cannot
    disagree with the links that already exist.
    """
    from .dateindex import BRAIN_DIR

    out: dict[str, dict] = {}
    folder = BRAIN_DIR / "email"
    if not folder.is_dir():
        return out
    for path in folder.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for slug, name, addr in _WIKILINK.findall(text):
            row = out.setdefault(slug, {"name": "", "addresses": set(),
                                        "threads": []})
            if name.strip() and not row["name"]:
                row["name"] = name.strip()
            row["addresses"].add(addr.strip().lower())
            if len(row["threads"]) < 12:
                row["threads"].append(f"email/{path.stem}")
    return out


def _person_page(slug: str, row: dict) -> str:
    name = row["name"] or slug.replace("-", " ").title()
    addrs = sorted(row["addresses"])
    lines = [
        "---", "type: person", f"title: {name}",
        "generated_by: cos graph-people",
        "status: unverified",
        "---", "",
        f"# {name}", "",
        "Created so the links the mail importer already wrote have somewhere "
        "to point. Everything here comes from mail headers; nothing is "
        "inferred.", "",
        "## Addresses", "",
    ]
    lines += [f"- `{a}`" for a in addrs]
    lines += ["", "## Seen in", ""]
    lines += [f"- [[{t}]]" for t in row["threads"]]
    return "\n".join(lines) + "\n"


def build_people(limit: int = 0, dry_run: bool = True,
                 log=lambda s: None) -> "Result":
    """Create one page per person the mail already names.

    Existing pages are never overwritten: eight of these were written by hand
    or by another importer, and a generated stub must not replace one that
    someone curated.
    """
    res = Result()
    have = existing_pages("person")
    rows = people_with_threads()
    for slug, row in list(rows.items())[:limit or None]:
        target = f"people/{slug}"
        res.entities += 1
        if target in have:
            res.skipped += 1
            continue
        if dry_run:
            log(f"  would create {target} — {row['name'] or '(no name)'}, "
                f"{len(row['addresses'])} address(es), "
                f"{len(row['threads'])} thread(s)")
            res.edges += 1
            continue
        body = _person_page(slug, row)
        out = _gb_stdin("put", target, body)
        if out is None:
            res.errors += 1
        else:
            res.edges += 1
            log(f"  created {target}")
    return res


def _gb_stdin(verb: str, slug: str, body: str) -> str | None:
    from .ask import BRAIN_DIR, _env, _gbrain

    gb = _gbrain()
    if not gb:
        return None
    try:
        r = subprocess.run([gb, verb, slug], input=body, capture_output=True,
                           text=True, timeout=30, cwd=str(BRAIN_DIR),
                           env=_env())
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
