"""Four ways of finding a page, fused into one ranking.

Wei: *"We should experiment graph search early on. It may slow down the
execution at the beginning, but it has great potential… maybe it's time to
start designing around mixed graph+vector+full_text search?"* Right on both
counts — the vector path has plateaued at a 25-second median, and the wins left
in it are small.

**Which graph, and why.** There are two. The Kuzu mail mirror is far richer —
12,800 people and 165,355 SENT edges against gbrain's 13,074 wikilinks — but
Wei: *"Kuzu from gmail sync is no longer being updated. We need a graph that
lives in gbrain."* So this runs on gbrain's Postgres (PGlite, Postgres 17,
pgvector). A richer graph of stale facts loses to a sparser graph of live ones.

The gbrain graph is sparse today and that is the point of putting retrieval on
it: it is the one that will grow.

## The four legs

    vector    meaning        gbrain search over 52,876 embedded chunks
    lexical   exact words    the same index, queried with the bare terms
    graph     who it is about  gbrain's link graph — resolve a name to its
                             entity page, take the threads that link to it
    date      when           the date index, when the question names a time

Each is strong exactly where the others are blind. Vector cannot find a page by
its date; the graph cannot find a page by what it means; lexical cannot
paraphrase.

## Fusing them

Their scores are not comparable — a cosine similarity and a message count do
not live on the same scale — so they are fused by **rank**, not by score:
reciprocal rank fusion, `Σ 1/(K + rank)` across the legs a page appears in.
A page found by three legs beats a page any one leg loved, which is the
property worth having: agreement between independent methods is evidence, and
a single leg's confidence is not.

The date and recency adjustments then apply on top, because "when" is a
different kind of claim from "how well does this match".

## Grouping

Six slots must not go to six messages from one thread, or to one person's whole
correspondence. Results are collapsed per thread and capped per person before
the cut, so what reaches the model is six *different* things.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date as _date

from . import dateindex, when

# Reciprocal rank fusion's only knob. 60 is the value from the original paper
# and behaves well: it stops rank 1 from dominating so hard that agreement
# between legs stops mattering.
RRF_K = 60

# What each leg is worth before fusion. Vector earns the most because it is the
# only leg that can find a page by what it means; the others are there to catch
# what it structurally cannot.
LEG_WEIGHT = {"vector": 1.0, "lexical": 0.7, "graph": 0.8, "date": 0.9,
              # Only ever populated when the question said "lately". It re-ranks
              # what the other legs found rather than adding anything, so it can
              # afford to be heavy: a page has to be topical to be in the pool
              # at all.
              "recency": 0.9}

# One thread should not be able to take the whole answer.
MAX_PER_THREAD = 1
MAX_PER_PERSON = 2

# The brain changes hourly at most, and a burst of questions about the same
# subject is the normal shape of a conversation.
CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()


def _cached(key: str):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    return None


def _remember(key: str, value: list) -> None:
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (time.time(), value)


# --------------------------------------------------------------------------
# The graph leg


_CAP = re.compile(r"\b([A-Z][A-Za-z0-9&.-]{2,})\b")
# Words that are capitalised in a question without naming anything.
_NOT_A_NAME = {
    "What", "Who", "When", "Where", "Why", "How", "Is", "Are", "Was", "Were",
    "Did", "Do", "Does", "Can", "Should", "Would", "Will", "The", "This",
    "That", "There", "Wei", "And", "But", "For", "With",
}


def entities(question: str) -> list[str]:
    """The proper nouns a question is about.

    Crude on purpose. A wrong guess here costs one wasted graph query, which is
    a millisecond; a clever extractor costs a model call, which is two seconds.
    """
    found = [w for w in _CAP.findall(question or "") if w not in _NOT_A_NAME]
    seen, out = set(), []
    for w in found:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:3]


def _gb_call(tool: str, args: dict) -> list:
    """One gbrain tool call, or an empty list.

    Everything here degrades to "this leg found nothing" rather than to an
    error, because a retrieval leg failing must never cost the answer.
    """
    import json
    import subprocess

    from .ask import BRAIN_DIR, _env, _gbrain

    gb = _gbrain()
    if not gb:
        return []
    try:
        out = subprocess.run(
            [gb, "call", tool, json.dumps(args)],
            capture_output=True, text=True, timeout=15,
            cwd=str(BRAIN_DIR), env=_env()).stdout
        rows = json.loads(out or "[]")
    except Exception:  # noqa: BLE001
        return []
    if isinstance(rows, dict):
        for key in ("results", "pages", "slugs", "orphans"):
            if isinstance(rows.get(key), list):
                return rows[key]
        return []
    return rows if isinstance(rows, list) else []


def graph_leg(question: str, limit: int = 12) -> list[dict]:
    """Pages that link to the people and companies the question names.

    **This runs on gbrain's Postgres, not on the mail mirror.** The Kuzu mirror
    has a far richer graph — 165,355 SENT edges against 13,074 wikilinks — but
    Wei: *"Kuzu from gmail sync is no longer being updated. We need a graph
    that lives in gbrain."* A richer graph of stale facts loses to a sparser
    graph of current ones, every time.

    The shape: resolve the name to an entity page, then take its backlinks.
    `people/jordan-lee` is linked from the threads that mention her, so its
    backlinks are exactly "the correspondence about this person" — which is
    what "who is the decision maker at Northwind" is asking for, and what
    similarity was guessing at.
    """
    names = entities(question)
    if not names:
        return []
    key = "graph:" + "|".join(names)
    hit = _cached(key)
    if hit is not None:
        return hit

    out, seen = [], set()
    for person in people_for(names):
        for link in _gb_call("get_backlinks", {"slug": person})[:8]:
            src = link.get("from_slug") if isinstance(link, dict) else None
            if not src or src in seen:
                continue
            seen.add(src)
            out.append({"slug": src, "_leg": "graph", "_via": person,
                        "_person": person})
            if len(out) >= limit:
                _remember(key, out)
                return out
    _remember(key, out)
    return out


_people_cache: dict | None = None


def people_for(names: list[str]) -> list[str]:
    """The person pages a set of names refers to. Deterministic.

    Two doors, both exact, because the loose ones were worse than nothing:
    `find_experts` matched "Northwind" to two people with no connection to it
    and the backlinks faithfully followed it somewhere else, and
    `resolve_slugs` returns the threads whose names contain the word rather
    than the entity the question is about.

    A name is either a person the mail already names, or a company — and a
    company is its email domain. "Northwind" resolves through
    `northwindintelligence.com` to the three people who write from it, which
    is what the question was about all along.
    """
    global _people_cache
    if _people_cache is None:
        from . import graphbuild

        by_addr = graphbuild.person_by_address()
        real = graphbuild.existing_pages("person")
        by_addr = {a: s for a, s in by_addr.items() if s in real}
        domains: dict[str, list[str]] = {}
        for addr, slug in by_addr.items():
            domains.setdefault(addr.rsplit("@", 1)[-1], []).append(slug)
        _people_cache = {"slugs": real, "domains": domains}

    real, domains = _people_cache["slugs"], _people_cache["domains"]
    out: list[str] = []
    for raw in names:
        needle = re.sub(r"[^a-z0-9]", "", raw.lower())
        if len(needle) < 3:
            continue
        # A person, named directly.
        for slug in real:
            if needle in slug.rsplit("/", 1)[-1].replace("-", ""):
                if slug not in out:
                    out.append(slug)
        # A company, which is its domain.
        for domain, slugs in domains.items():
            if needle in domain.replace(".", ""):
                for slug in slugs:
                    if slug not in out:
                        out.append(slug)
    return out[:8]


def _as_day(ts) -> _date | None:
    text = str(ts or "")[:10]
    try:
        return _date(*(int(x) for x in text.split("-")))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Fusion


def fuse(legs: dict[str, list[dict]]) -> list[tuple[float, dict]]:
    """Reciprocal rank fusion across the legs.

    Rank, not score: a cosine similarity and a message count do not live on the
    same scale, and normalising them would be inventing a comparison that does
    not exist.
    """
    pooled: dict[str, dict] = {}
    scores: dict[str, float] = {}
    legs_hit: dict[str, set] = {}

    for leg, rows in legs.items():
        weight = LEG_WEIGHT.get(leg, 0.5)
        for rank, row in enumerate(rows):
            slug = row.get("slug")
            if not slug:
                continue
            scores[slug] = scores.get(slug, 0.0) + weight / (RRF_K + rank + 1)
            legs_hit.setdefault(slug, set()).add(leg)
            if slug not in pooled:
                pooled[slug] = dict(row)

    for slug, row in pooled.items():
        row["_legs"] = sorted(legs_hit[slug])
    return sorted(((scores[s], pooled[s]) for s in pooled), key=lambda p: -p[0])


# --------------------------------------------------------------------------
# Grouping


def champions(legs: dict[str, list[dict]]) -> list[str]:
    """Each method's own best answer, in leg-weight order.

    Fusion by rank has one failure mode, and it is the mirror of the property
    that makes it work: a page that one leg is *certain* about is averaged down
    by the legs that never saw it. "When is the CDL talk due?" put
    `email/2026-06-29-cdl` first out of the full-question search and thirtieth
    out of the reduced-terms one, so it fused to eighth and fell off a
    six-slot list — behind five pages that merely have "draft" in the title.

    So each leg keeps its top answer, and fusion decides everything else. It is
    a small reservation on purpose: at most two of six slots, or the legs stop
    having to agree about anything.

    Two legs are deliberately excluded. The **graph** leg's order is the order
    backlinks came back in, which is not a ranking, so its "best" answer is not
    a claim about anything — promoting it put a Spanner catch-up thread into
    the middle of "who is the decision maker at Northwind". And when the
    **date** leg has fired at all, the question was a topicless one about a
    stretch of time; the window is the query, and the vector leg's confident
    top answer there is the 2025 out-of-office reply titled "what I wrote last
    week" that the whole date index exists to beat.
    """
    if legs.get("date"):
        return []
    out: list[str] = []
    for leg in sorted(CHAMPION_LEGS, key=lambda k: -LEG_WEIGHT.get(k, 0.5)):
        rows = legs.get(leg) or []
        if rows and rows[0].get("slug") and rows[0]["slug"] not in out:
            out.append(rows[0]["slug"])
    return out


# Legs whose order is a ranking by relevance, and therefore whose first row is
# a claim worth reserving a slot for.
CHAMPION_LEGS = ("vector", "lexical", "recency")


MAX_CHAMPIONS = 2


def group(ranked: list[tuple[float, dict]], limit: int,
          keep: list[str] | None = None) -> list[dict]:
    """Six different things, not six messages from one thread.

    Without this the top of the list is routinely one conversation, because
    every leg agrees about it — which is the one case where agreement is not
    informative.
    """
    out: list[dict] = []
    per_thread: dict[str, int] = {}
    per_person: dict[str, int] = {}

    # The reserved slots go first, so the caps below count them like anything
    # else and one leg's champion cannot also take a second slot on rank.
    reserved = set()
    if keep:
        by_slug = {r["slug"]: r for _s, r in ranked if r.get("slug")}
        for slug in keep[:MAX_CHAMPIONS]:
            row = by_slug.get(slug)
            if row is None or len(out) >= limit:
                continue
            reserved.add(slug)
            per_thread[_thread_of(slug)] = per_thread.get(_thread_of(slug), 0) + 1
            out.append(row)

    for _score, row in ranked:
        if row["slug"] in reserved:
            continue
        thread = _thread_of(row["slug"])
        person = row.get("_person") or ""
        if per_thread.get(thread, 0) >= MAX_PER_THREAD:
            continue
        if person and per_person.get(person, 0) >= MAX_PER_PERSON:
            continue
        per_thread[thread] = per_thread.get(thread, 0) + 1
        if person:
            per_person[person] = per_person.get(person, 0) + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


_DATED_SLUG = re.compile(r"^(.*/)(\d{4})-(\d{2})-(\d{2})-(.*)$")


def _thread_of(slug: str) -> str:
    """A stable key for "the same conversation".

    Reply chains land as separate pages a day apart with the same subject, so
    the date is dropped and the subject stem kept.
    """
    m = _DATED_SLUG.match(slug or "")
    if not m:
        return slug or ""
    folder, _y, _mo, _d, rest = m.groups()
    stem = re.sub(r"^(re-|fwd-|fw-)+", "", rest)
    return folder + stem
