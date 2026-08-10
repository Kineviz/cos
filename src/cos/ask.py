"""Ask the assistant a question from the dashboard.

Wei already asks Kiran things on Telegram — "when did I last talk to
Northwind", "what's my top to-do list". This is the same capability in the
browser, and deliberately **the same assistant**: it shells out to
`hermes -z`, which runs the real agent loop with the real tools and the real
prompt. A separate search over the brain would have been faster to build and
would have drifted from Kiran within a week, so that two surfaces of the same
product would answer the same question differently.

**It is slow, and the design has to admit that.** Measured on this machine:
13s for a question needing only the clock, 35s for one that searched the brain
and answered with three citations. So this runs as a job — POST starts it, GET
polls it — rather than holding an HTTP request open for a minute.

**The answer is untrusted text.** This is the part worth being careful about.
The question comes from Wei, but the answer is synthesised from twelve years of
email written by other people. A message crafted by a stranger can therefore
reach the model's output, and if the page rendered that output as HTML it would
be a path from an inbound email to script running in the dashboard — the one
page that edits the assistant's own permissions. So answers are returned as
plain text and the page must insert them as text, never as markup. `ANSWER_MAX`
exists for the same reason: a runaway answer should be truncated, not streamed
into the DOM forever.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from itertools import zip_longest
from pathlib import Path

from . import dateindex, instant, retrieve, when

HERMES_DIR = Path.home() / ".hermes"
HERMES_PY = HERMES_DIR / "hermes-agent" / "venv" / "bin" / "python"

# `cos serve` runs under launchd with a minimal PATH, so a bare command name
# finds neither of these — the same trap already documented for `tailscale`,
# and it fails as "no answer" rather than as an error.
# gbrain resolves which brain to search FROM THE WORKING DIRECTORY. Run from
# $HOME it answered "what did we discuss on falcon lately" with four people's
# wiki pages; run from the brain it returns nothing but Falcon email threads.
# Same binary, same query, same limit — only the cwd differed. This is why the
# dashboard's sources looked unrelated.
BRAIN_DIR = Path.home() / "brain"

_GBRAIN_BINS = [
    Path.home() / ".bun" / "bin" / "gbrain",
    Path("/opt/homebrew/bin/gbrain"),
    Path("/usr/local/bin/gbrain"),
]


def _gbrain() -> str | None:
    for p in _GBRAIN_BINS:
        if p.exists():
            return str(p)
    return None


def _env() -> dict:
    """PATH that can actually run these.

    Finding the binary is not enough: `~/.bun/bin/gbrain` is a symlink to a
    TypeScript file whose shebang is `env bun`, so the CHILD needs bun on its
    PATH too. Under launchd it is not, and the failure is a silent empty
    result rather than an error — the whole reason this function exists rather
    than trusting the inherited environment.
    """
    import os

    extra = [str(Path.home() / ".bun" / "bin"), str(Path.home() / ".local" / "bin"),
             "/opt/homebrew/bin", "/usr/local/bin"]
    path = os.environ.get("PATH", "")
    return {**os.environ, "PATH": ":".join(extra + ([path] if path else []))}


# Question words carry no meaning for a vector search and actively dilute the
# embedding. Measured: "Who is Casey and what does he work on" returned a
# 2015 note about work-life balance as its top hit; "Casey" alone returned
# his calendar and mail. Same index, same limit — the difference was eleven
# stopwords.
_STOP = {
    "a", "about", "all", "am", "an", "and", "any", "anything", "are", "as",
    "at", "be", "been", "but", "by", "can", "could", "did", "do", "does",
    "for", "from", "get", "give", "had", "has", "have", "he", "her", "him",
    "his", "how", "i", "if", "in", "is", "it", "its", "just", "know", "lately",
    "latest", "list", "me", "my", "of", "on", "or", "our", "recent",
    "recently", "right", "she", "should", "show", "so", "some", "tell", "that",
    "the", "their", "them", "there", "these", "they", "this", "to", "us",
    "was", "we", "were", "what", "whats", "when", "where", "which", "who",
    "whom", "why", "will", "with", "would", "you", "your",
    # Verbs of conversation. They describe the asking, not the subject, and as
    # topic words they are actively harmful: "discuss" narrowed "what did we
    # discuss on Falcon lately" to the two threads with the word "discussion" in
    # their name, both from 2024–25.
    # Only the ones that cannot also be the subject. "Talk" was in this list
    # for one run and cost "When is the CDL talk due?" its own source page —
    # the CDL talk is a thing, not an act of talking.
    "discuss", "discussed", "discussing", "mention", "mentioned",
}
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&/.-]*")

# Capitalised mid-sentence but never a proper noun.
_NEVER_PROPER = {"i", "i'm", "i've", "i'd", "i'll"}


def search_terms(question: str) -> str:
    """The part of a question worth embedding.

    Keeps anything capitalised mid-sentence — proper nouns are exactly what
    these questions are about — plus every content word. Falls back to the
    original if stripping leaves nothing, so "what is today's date" does not
    become an empty query.

    "I" is excluded from the capitalised-word rule. It is capitalised in every
    English sentence and is never a proper noun, and keeping it turned "What
    did I do last week?" into "I last week" — a query that scored 0 and then
    ran past the timeout.
    """
    words = _WORD.findall(question or "")
    kept = [w for n, w in enumerate(words)
            if w.lower() not in _STOP
            or (n > 0 and w[:1].isupper() and w.lower() not in _NEVER_PROPER)]
    out = " ".join(kept).strip()
    return out if len(out) >= 3 else (question or "").strip()


def _search_once(gb: str, query: str, limit: int) -> list:
    try:
        out = subprocess.run(
            [gb, "call", "search", json.dumps({"query": query, "limit": limit})],
            capture_output=True, text=True, timeout=30,
            cwd=str(BRAIN_DIR if BRAIN_DIR.is_dir() else Path.home()),
            env=_env(),
        ).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    return rows if isinstance(rows, list) else []


def search(question: str, limit: int = 6) -> list[dict]:
    """Pages matching the question. Sub-second.

    This exists so the wait is never an empty box. Synthesis takes 13–35s;
    this lands in about 0.8s, and for a good share of real questions — "when
    did I last talk to Northwind" — the list of pages with their dates IS the
    answer, and the prose that arrives later is a bonus.

    **Two queries, interleaved.** Stripping the question down to its content
    words sharpened source relevance from 71% to 86%, the largest single gain
    measured — but it also erased the phrase that makes a time question a time
    question, and "What did I do last week?" went from correct to timed out.
    Neither query is right on its own, so both run and the results interleave:
    the reduced query leads, the whole question fills in behind it, and a page
    found by both rises to the top by appearing in the first pair.
    """
    return search_profiled(question, limit)[0]


def search_profiled(question: str, limit: int = 6) -> tuple[list[dict], dict]:
    """`search`, plus where its time went.

    "It seems slow" is not something you can improve against, and until now the
    only number anyone had was how long the whole question took. This splits
    the part we control into its phases so the answer to "which part is slow"
    is measured rather than guessed.
    """
    clock: dict = {}
    t0 = time.time()

    gb = _gbrain()
    if not gb:
        return [], {"total": 0.0}
    terms = search_terms(question)
    whole = (question or "").strip()

    # Fetch deep, hand over shallow. Ranking cannot promote a page the search
    # never returned, and the page that answers a question about this week is
    # routinely ranked 20th by similarity alone. The extra rows cost nothing —
    # they are the same query, already paid for.
    deep = max(limit * 5, 30)
    t = time.time()
    runs = [_search_once(gb, terms, deep)]
    clock["vector_terms"] = round(time.time() - t, 3)

    t = time.time()
    if whole and whole.lower() != terms.lower():
        runs.append(_search_once(gb, whole, deep))
    clock["vector_whole"] = round(time.time() - t, 3)

    # Round-robin rather than concatenate: appending the second list would bury
    # every one of its hits below a full page from the first, which is the same
    # as not running it. zip_longest keeps the tail of the longer run.
    rows = [r for group in zip_longest(*runs) for r in group if r is not None]

    t = time.time()
    win = when.parse(question, _today())

    # Four legs, fused by rank. Their scores are not comparable — a cosine
    # similarity and a message count do not live on the same scale — so a page
    # earns its place by being found by SEVERAL methods rather than loved by
    # one. See retrieve.py.
    t = time.time()
    legs = {"vector": runs[0], "lexical": runs[1] if len(runs) > 1 else []}
    legs["date"] = _window_rows(question, win)
    legs["graph"] = retrieve.graph_leg(question)
    legs["recency"] = _recency_rows(question, win, legs)
    clock["date_window"] = round(time.time() - t, 3)
    clock["legs"] = {k: len(v) for k, v in legs.items()}
    clock["candidates"] = sum(len(v) for v in legs.values())

    t = time.time()
    fused = retrieve.fuse(legs)

    # Time is a different kind of claim from "how well does this match", so it
    # is applied after the fusion rather than mixed into it.
    adjusted = []
    for score, row in fused:
        day = _dated(row)
        bump = 0.0
        if day is not None:
            if win is not None:
                if win.holds(day):
                    bump += WINDOW_BONUS
                elif win.near(day):
                    bump += SHOULDER_BONUS
            age = max((_today() - day).days, 0)
            if win is not None and getattr(win, "soft", False):
                bump += SOFT_RECENCY_MAX * (0.5 ** (age / SOFT_HALFLIFE_DAYS))
            else:
                bump += RECENCY_MAX * (0.5 ** (age / RECENCY_HALFLIFE_DAYS))
        # Scaled to RRF's world, not to similarity's. A rank-1 hit scores
        # 1/(60+1) = 0.0164, so a date bonus of 0.35 added raw would swamp
        # everything and one of 0.35*0.02 changes nothing. 0.08 makes a full
        # in-window bonus worth about two rank positions — enough to beat a
        # better-worded page from the wrong year, not enough to beat three
        # legs agreeing.
        adjusted.append((score + bump * 0.08, row, day))
    adjusted.sort(key=lambda p: -p[0])

    hits = []
    for row in retrieve.group([(sc, r) for sc, r, _d in adjusted], limit,
                              keep=retrieve.champions(legs)):
        slug = row["slug"]
        hits.append({
            "slug": slug,
            "title": row.get("title") or slug.rsplit("/", 1)[-1].replace("-", " "),
            "kind": _kind(slug, row.get("type")),
            "date": (_dated(row).isoformat() if _dated(row) else ""),
            "excerpt": _excerpt(row.get("chunk_text") or _page_head(row)),
            "context": _context(row.get("chunk_text") or _page_head(row),
                                terms=question),
            "legs": row.get("_legs") or [],
        })
    clock["rank_filter"] = round(time.time() - t, 3)
    clock["total"] = round(time.time() - t0, 3)
    clock["kept"] = len(hits)
    return hits, clock


def _page_head(row: dict) -> str:
    """Text for a page a leg named but did not carry the body of.

    The graph and date legs return a slug and a date; the text has to come off
    disk. Only ever called for the handful that survive ranking.
    """
    return dateindex.read_head(row.get("slug") or "")


def _today() -> _date:
    return datetime.now().date()


# Beyond this a window stops being a window. "Last year" spans 12,000 pages,
# and pulling them in by date would drown the ones that match the question.
WINDOW_MAX_DAYS = 45
# How many dated pages to admit from the window before ranking. Enough that a
# real week is represented, small enough that they cannot swamp the hits that
# actually match the words.
WINDOW_ROWS = 12


def topic_of(question: str, win) -> str:
    """What the question is about, once the time phrase is taken out.

    This is the switch the whole date strategy turns on, and getting it wrong
    breaks retrieval in one of two opposite ways.

    "What did I do last week?" has no topic — strip "last week" and nothing is
    left. The window *is* the query, and similarity has nothing to work with:
    it matches the words "last week" and returns a 2020 email titled "things
    did last week and plan for this week".

    "What did we discuss on Falcon lately?" has a topic. Here the window must
    only re-order what the topic found. Injected as candidates it drowned the
    question — all six sources came back as unrelated meetings from the most
    recent day, and Falcon vanished from its own question.
    """
    terms = search_terms(question).lower()
    if win is None:
        return terms
    for word in _WORD.findall(win.phrase.lower()):
        terms = re.sub(rf"\b{re.escape(word)}\b", " ", terms)
    # Bare month and weekday names left over from a phrase like "in July".
    terms = re.sub(r"\b(morning|afternoon|evening|night|day|days|week|weeks|"
                   r"month|months|year|years|ago)\b", " ", terms)
    return " ".join(terms.split())


def _recency_rows(question: str, win, legs: dict) -> list[dict]:
    """The candidates the other legs found, newest first.

    Only for a soft window — "lately", "recently", "latest". Those words ask
    for the newest pages *about the topic*, and an absolute window cannot
    express that: "what did we discuss on Falcon lately" returned threads from
    2024 and 2025 for three straight runs, because the newest Falcon thread was
    68 days old and a 30-day window held none of them. Nothing scored, so
    wording decided, and 2024 words the question better than 2026 does.

    This adds no candidates. It re-ranks the ones already found, and because
    fusion is by rank, a page that is both topical and newest wins without a
    weight that would bury a 2025 email when nobody asked for recent.
    """
    if win is None or not getattr(win, "soft", False):
        return []
    pool: dict[str, dict] = {}
    # Seeded from the date index, because re-ranking a pool that never held
    # the newest page cannot surface it. The June 2026 Falcon thread was in no
    # leg at all.
    topic = topic_of(question, win)
    for row in dateindex.newest_matching(topic.split(), limit=WINDOW_ROWS):
        pool.setdefault(row["slug"], row)
    for name in ("vector", "lexical", "graph"):
        for row in legs.get(name) or []:
            slug = row.get("slug")
            if slug and slug not in pool:
                pool[slug] = row
    dated = [(d, r) for r in pool.values() if (d := _dated(r)) is not None]
    dated.sort(key=lambda p: p[0], reverse=True)
    return [r for _d, r in dated[:WINDOW_ROWS]]


def _window_rows(question: str, win) -> list[dict]:
    """Pages that fall inside the asked-about window, whatever they say.

    Only when the question has no topic of its own. Then this is the leg
    similarity cannot supply, and the pages that were actually on those days
    are the only real candidates there are.
    """
    if win is None or win.days > WINDOW_MAX_DAYS:
        return []
    if topic_of(question, win):
        return []
    try:
        found = dateindex.pages_between(win.start, win.end, limit=WINDOW_ROWS)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for n, p in enumerate(found):
        # Scored above what similarity returns for a topicless question. Those
        # hits are matching the phrase "last week" as *words*, which is not
        # evidence about anything; a page that was genuinely on one of those
        # days beats every one of them.
        out.append({
            "slug": p["slug"],
            "effective_date": p["date"],
            "score": 1.05 - n * 0.001,
            "chunk_text": dateindex.read_head(p["slug"]),
            "_from": "window",
        })
    return out


_SLUG_DATE = re.compile(r"(?:^|/)(\d{4})-(\d{2})-(\d{2})(?:[-/]|$)")


def _as_date(raw) -> _date | None:
    try:
        return datetime.strptime((raw or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _dated(row: dict) -> _date | None:
    """When this page is from, from whichever field actually has it.

    37% of retrieved rows come back with `effective_date` empty — measured, 11
    of 30 on one query — and a date-blind row is invisible to every rule
    below. The date is not missing, though: it is in the slug.
    `email/2025-08-11-what-i-wrote-last-week` was written on 11 August 2025,
    and 99.9% of the brain's pages are named that way.
    """
    exact = _as_date(row.get("effective_date"))
    if exact:
        return exact
    m = _SLUG_DATE.search(row.get("slug") or "")
    if not m:
        return None
    try:
        return _date(*(int(x) for x in m.groups()))
    except ValueError:
        return None


# What a page inside the asked-about window is worth. Large enough to overturn
# similarity: measured, "audience reaction to my presentation" scored a 2025
# email at 0.893 and everything from the right week below 0.55, so anything
# smaller than the gap would change nothing.
WINDOW_BONUS = 0.35
# "On AND AROUND that date" — Wei asked about "this morning" on a day when the
# talk had been the morning before. A hard filter would have dropped the one
# page that answered him.
SHOULDER_BONUS = 0.15
# The always-on recency prior, and it stays small on purpose. "Who is the real
# decision maker at Northwind?" is answered by a 2025 email; a prior strong
# enough to bury that trades six recall questions for four temporal ones.
RECENCY_MAX = 0.06
RECENCY_HALFLIFE_DAYS = 250.0

# When the question actually says "lately", the prior stops being a tie-break
# and becomes the point of the question. At this size a page from ten weeks ago
# outranks one from a year ago that words the question better — which is the
# whole difference between answering "what did we discuss on Falcon lately" out
# of June 2026 and answering it out of 2024. It only ever applies when a soft
# window was parsed, so it cannot touch "who is the decision maker".
SOFT_RECENCY_MAX = 0.5
SOFT_HALFLIFE_DAYS = 60.0


def _rank(row: dict, position: int, day, win) -> float:
    """How much this page is worth for this question.

    Similarity first, then time. Retrieval used to be date-blind: every page
    carries an `effective_date` and none of it reached the ranking, so a
    question about this morning was answered out of 2022.
    """
    base = row.get("score")
    if not isinstance(base, (int, float)):
        # No score from the engine — fall back to the order it returned, so
        # ranking degrades to "leave it alone" rather than to noise.
        base = 1.0 - min(position, 60) / 100.0

    score = float(base)
    if day is None:
        return score

    if win is not None:
        if win.holds(day):
            score += WINDOW_BONUS
        elif win.near(day):
            score += SHOULDER_BONUS

    age = max((_today() - day).days, 0)
    score += RECENCY_MAX * (0.5 ** (age / RECENCY_HALFLIFE_DAYS))
    return score


def page(slug: str) -> dict:
    """One page from the brain, for the panel behind a source link.

    Wei asked for answers to link to the data behind them. Citing a slug is
    only half of that: a citation you cannot open is a claim you have to take
    on trust, which is the opposite of the point.
    """
    gb = _gbrain()
    if not gb:
        return {"slug": slug, "error": "gbrain is not on this machine."}
    if ".." in (slug or "") or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,200}", slug or ""):
        # The slug reaches here from model output, which is written partly by
        # strangers. Anything outside this shape is not a page name.
        return {"slug": slug, "error": "That is not a page name."}
    try:
        out = subprocess.run(
            [gb, "get", slug], capture_output=True, text=True, timeout=25,
            cwd=str(BRAIN_DIR if BRAIN_DIR.is_dir() else Path.home()), env=_env(),
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return {"slug": slug, "error": f"{type(e).__name__}: {e}"}
    if not out.strip():
        return {"slug": slug, "error": "That page is not in the brain."}
    if out.startswith("---"):
        end = out.find("\n---", 3)
        if end != -1:
            out = out[end + 4:]
    return {"slug": slug, "markdown": out.strip()[:60000]}


def _kind(slug: str, page_type: str | None) -> str:
    if slug.startswith("email/"):
        return "email"
    if slug.startswith("calendar/"):
        return "meeting"
    if slug.startswith("10_wiki/people") or slug.startswith("people/"):
        return "person"
    if slug.startswith(("10_wiki/clients", "companies/")):
        return "company"
    if slug.startswith("90_agent/"):
        return "note"
    return page_type or "page"


# How much of each retrieved page the model is given. Six pages at this length
# is roughly 1,800 tokens — against the 260,202 that one question spent
# fetching pages it had already been shown the names of.
CONTEXT_CHARS = 1200

# Frontmatter, and the lines that are pure plumbing.
_FRONT = re.compile(r"^---\n.*?\n---\n", re.S)
_NOISE = re.compile(r"^(?:\[Open in [^\]]+\]\(|https?://|!\[)", re.I)


def _context(text: str, limit: int = CONTEXT_CHARS, terms: str = "") -> str:
    """The part of a page worth putting in front of the model.

    Deliberately NOT `_excerpt`. That one builds a two-line card and drops
    every line starting with `#`, `-` or `*` — which on a meeting page is the
    title, the time, the attendees and the agenda. It left "What did I do last
    week?" with a 2,222-character prompt describing six meetings and saying
    almost nothing about any of them, which is precisely why the model went and
    fetched all six.

    So this keeps the structure and removes only plumbing: the YAML
    frontmatter, bare URLs, image tags, and the calendar link every invitation
    carries.

    And when the question's own words are given, it takes the part of the page
    that contains them rather than the top. On a short email those are the same
    thing; on the task dashboard they are not. "Why has the Northwind deal
    stalled?" retrieved the dashboard, which answers it in one line — "stalled
    because their security review is stuck, not because they went cold" — and
    that line sits well past the first 1,200 characters, so the model never saw
    it and answered from a different page instead.
    """
    body = _FRONT.sub("", text or "")
    lines = [s for s in (ln.strip() for ln in body.splitlines())
             if s and not _NOISE.match(s)]
    joined = "\n".join(_focus(lines, limit, terms))
    return joined[:limit].rstrip() + ("…" if len(joined) > limit else "")


def _focus(lines: list[str], limit: int, terms: str) -> list[str]:
    """The run of lines worth keeping — the matching part, or the top.

    A page shorter than the budget is kept whole, which is most of them. Above
    that, the window that mentions the question most often wins; on a tie the
    earlier one does, so a page with no matches degrades to exactly the old
    behaviour rather than to an arbitrary slice.
    """
    if sum(len(s) + 1 for s in lines) <= limit:
        return lines
    keys = {w for w in re.findall(r"[a-z0-9]{3,}", (terms or "").lower())}
    if not keys:
        return lines[:_fits(lines, 0, limit)]
    best_start, best_score = 0, -1
    for start in range(len(lines)):
        end = _fits(lines, start, limit)
        score = sum(1 for ln in lines[start:end]
                    for k in keys if k in ln.lower())
        if score > best_score:
            best_start, best_score = start, score
    return lines[best_start:_fits(lines, best_start, limit)]


def _fits(lines: list[str], start: int, limit: int) -> int:
    total, end = 0, start
    while end < len(lines) and total + len(lines[end]) + 1 <= limit:
        total += len(lines[end]) + 1
        end += 1
    return max(end, start + 1)


def _excerpt(text: str, limit: int = 200) -> str:
    """Readable prose only — the frontmatter and the participant block are
    noise in a two-line card."""
    # Some cards read "type: email title: Declined… thread_id: 1826853…"
    # instead of the message, because a frontmatter key:value line survived
    # this filter. Sources are the entire screen for the first twenty seconds
    # of every question, so that is the first thing seen on a third of them.
    meta = re.compile(r"^[a-z_]{2,24}:\s")
    body: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "---", "-", "*", "Participants", "`", ">")):
            continue
        if meta.match(s):
            continue
        body.append(s)
        if sum(len(x) for x in body) > limit:
            break
    out = " ".join(body)
    return out[:limit].rstrip() + ("…" if len(out) > limit else "")

# A question that has not answered in this long is stuck, not thinking. Measured
# on real questions: 13s trivial, 35s with a brain search, 166s for "what is the
# status of the Northwind deal" — which reads a dozen pages and weighs them. The
# first ceiling of 180s would have cut that one off four seconds before it
# finished.
# The toolset the assistant gets for a dashboard question. Pinning this is not
# a tidiness measure — it is the difference between an answer and a hang.
#
# Left unpinned, the CLI path loads 47 plugins and every enabled toolset (file,
# todo, vision, session_search, clarify) on top of gbrain's 106 tools. Three
# real questions in a row then ran past the 300s ceiling and were killed;
# "what did we discuss on falcon lately" never returned at all. With the toolset
# pinned, the same question answered in 20.8s with two citations. Telegram was
# never affected because its platform toolset is already narrow.
# `panels` adds four small tools so a question typed under the Tasks or
# Prospects panel can DO things — move a deal's stage, add a note, mark a
# task done — not just talk about them.
TOOLSET = "gbrain,clock,panels"

TIMEOUT_SECONDS = 300
ANSWER_MAX = 20000
HISTORY_MAX = 30


@dataclass
class Job:
    id: str
    question: str
    status: str = "running"        # running | done | failed
    answer: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    # Age of the reused answer in seconds, or None if it was computed now. The
    # page shows this: an answer from twelve minutes ago presented as though it
    # were fresh is worse than waiting the thirty-five seconds.
    cached_age: int | None = None
    # True while parked behind another question. Without this, `elapsed` counts
    # from the moment Enter was pressed, so a phone asking while the laptop is
    # mid-answer confidently reports "Thinking · 75s" for something that has
    # not started.
    queued: bool = False
    queue_position: int = 0
    # Pages that matched, filled in about a second after the question is asked
    # so there is something real on screen while the answer is still coming.
    hits: list[dict] = field(default_factory=list)
    # Kiran reliably ends with an offer — "Want me to pull the exact dates?".
    # That is a button, not a paragraph.
    follow_up: str | None = None
    session: str = ""
    # What was on screen when the question was asked — the Tasks or Prospects
    # panel, rendered as text. "Which of these should I chase first?" is a
    # question about the rows in front of you, and without them it was being
    # answered against whatever the last chat happened to be about.
    screen: str = ""
    # Where the time went. "It seems slow" is not something you can improve
    # against; this is the breakdown behind the one number the page shows.
    profile: dict = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    def as_dict(self) -> dict:
        return {
            "id": self.id, "question": self.question, "status": self.status,
            "answer": self.answer, "error": self.error,
            "elapsed": round(self.elapsed, 1),
            "cached_age": self.cached_age,
            "hits": self.hits, "follow_up": self.follow_up,
            "session": self.session,
            "queued": self.queued, "queue_position": self.queue_position,
            "profile": self.profile,
        }


_jobs: dict[str, Job] = {}
_order: list[str] = []
_lock = threading.Lock()

# One question at a time.
#
# Each `hermes -z` loads a full agent and its own gbrain MCP server against the
# same Postgres, and the 15-minute refresh and the autopilot are already using
# that database. Three questions asked together — trivially done by opening the
# dashboard on a laptop and a phone — put three of those in flight at once and
# made every one of them slower than running them in sequence would have been.
#
# Queuing rather than rejecting: the second question still gets answered, it
# just waits, and the page already shows a truthful "Thinking · Ns".
_slot = threading.Semaphore(1)

# --------------------------------------------------------------------------
# Cache
#
# A 35-second answer should not be recomputed because the page was reloaded.
# But an answer about "who is waiting on me" is only true for as long as the
# mail behind it is, so the cache is keyed to the DATA rather than to a clock:
# it is valid while the dashboard snapshot it was computed against is still the
# current one. The 15-minute refresh writes a new snapshot, and every cached
# answer expires with it — which is exactly the right lifetime, and needs no
# guess about how long an answer "stays true".
#
# A cached answer is always LABELLED as cached, with its age. Serving a stale
# answer as though it were fresh is the failure this project keeps finding.

CACHE_FILE = Path.home() / ".cos" / "ask-cache.json"
CACHE_MAX = 60
# Backstop for a machine where the refresh has stopped: an answer should not
# outlive the day it was given even if the snapshot never moves.
CACHE_HARD_TTL = 12 * 3600


def _snapshot_stamp() -> str:
    """Identifies the DATA an answer was computed against.

    This used to be the snapshot's mtime, which the refresh rewrites every
    cycle whether or not anything changed — `generated_epoch` always differs.
    Measured consequence: 1 of 13 cached answers was still valid, median age 17
    minutes, so a 60–150 second operation had a hit rate near 8% and gained
    nothing in correctness for it. Hashing the facts themselves keeps the same
    rule — valid while the data behind it is — and actually holds.
    """
    try:
        import hashlib

        from .webconfig import SNAPSHOT

        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        facts = {k: data.get(k) for k in ("owed", "owed_total", "quiet")}
        return hashlib.sha1(
            json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except (OSError, ImportError, json.JSONDecodeError, TypeError):
        return "none"


def _key(question: str) -> str:
    return " ".join(question.lower().split())


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Oldest out first once it grows past CACHE_MAX.
        if len(cache) > CACHE_MAX:
            for k in sorted(cache, key=lambda k: cache[k]["ts"])[: len(cache) - CACHE_MAX]:
                cache.pop(k, None)
        import os
        import uuid as _uuid

        tmp = CACHE_FILE.with_suffix(f".tmp.{os.getpid()}.{_uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def cached_answer(question: str) -> dict | None:
    """A previous answer to this exact question, if it is still valid."""
    entry = _load_cache().get(_key(question))
    if not entry:
        return None
    if entry.get("stamp") != _snapshot_stamp():
        return None
    age = time.time() - entry["ts"]
    if age > CACHE_HARD_TTL:
        return None
    return {"answer": entry["answer"], "age_seconds": int(age),
            "hits": entry.get("hits", []), "follow_up": entry.get("follow_up")}


def _remember(question: str, answer: str, hits: list | None = None,
              follow_up: str | None = None) -> None:
    if not answer.strip():
        return
    cache = _load_cache()
    cache[_key(question)] = {
        "answer": answer, "ts": time.time(), "stamp": _snapshot_stamp(),
        "hits": hits or [], "follow_up": follow_up,
    }
    _save_cache(cache)


def forget(question: str) -> None:
    """Drop one cached answer, for the page's "ask again" affordance."""
    cache = _load_cache()
    if cache.pop(_key(question), None) is not None:
        _save_cache(cache)


_TRAILING_Q = re.compile(r"(?:^|\n)([^\n]{10,200}\?)\s*$")


def _split_follow_up(answer: str) -> tuple[str, str | None]:
    m = _TRAILING_Q.search(answer or "")
    if not m:
        return answer, None
    return answer[: m.start()].rstrip(), m.group(1).strip()


def _now_line() -> str:
    """Today's date and time, stated rather than left to be looked up.

    Wei asked "how was the response to my talk at BigBank this morning?" and got
    the right answer while ignoring "this morning" entirely — the retrieval
    never narrowed by date. It worked because BigBank appears in few enough
    threads that the newest was the right one. On a busier subject the same
    luck returns a stale answer with total confidence.

    The clock tool exists, but reaching for it is a choice the model makes and
    often does not. Stating the date costs one line and cannot be skipped —
    the same reasoning that put the date in SOUL.md rather than hoping
    retrieval would surface it.
    """
    now = datetime.now().astimezone()
    # State it, do not instruct about it. The first version added "resolve
    # 'today', 'this morning', 'lately' and 'last week' against that, and say
    # the actual dates you mean" — and the model obediently opened its answer
    # with a four-line glossary defining each term, then anchored on the first
    # search hit instead of answering. Recall accuracy fell from 100% to 50%
    # in one run. The date is context, not a task.
    # If a time window turns up nothing, widening it once is useful and
    # exhausting the corpus is not. The BigBank question — asked the morning
    # after the talk — spiralled past the 300s ceiling looking for something
    # that had happened the previous day.
    return (f"Today is {now:%A %d %B %Y}, {now:%H:%M %Z}. "
            f"If a time window has nothing in it, widen it once and say what "
            f"you widened to, rather than searching exhaustively.")


# Questions the computed facts actually help with. For "who is the decision
# maker at Northwind" the owed/quiet/to-do block is 400 tokens of noise that
# displaces the retrieved pages, and measurably did: injecting it
# unconditionally took recall from 100% to 50% while taking list questions
# from 67% to 100%. So it goes only where it belongs.
_WANTS_FACTS = re.compile(
    r"\b(waiting|owe|owed|reply|replies|quiet|cold|overdue|to.?do|todo|"
    r"task|list|agenda|today|this week|priorit|outstanding|pending|"
    r"deal with|most important|should i)\b", re.I)


def _facts_wanted(question: str) -> bool:
    return bool(_WANTS_FACTS.search(question or ""))


def _facts() -> str:
    """Numbers the pipeline has already computed, stated as authoritative.

    Asked the same question three times concurrently, the assistant answered
    "50 days quiet", "50 days", and "roughly 45 days" — while `deal_status`
    had computed 50 deterministically and written it to the snapshot. The
    dashboard and the assistant then disagree about the same number in front
    of Wei, and the one that sounds more confident is the one that guessed.

    This is the README's own rule — structure comes from rules, the model only
    writes prose — applied to the ask path, where it had not been. It also
    removes the turns the model was spending re-deriving them, which is the
    cheapest latency there is.
    """
    try:
        from .webconfig import read_snapshot

        snap = read_snapshot()
    except Exception:  # noqa: BLE001
        return ""
    if not snap.get("generated_at"):
        return ""

    bits = [f"Computed facts as of {snap['generated_at']} — these are "
            f"authoritative; use them rather than counting again:"]
    if snap.get("owed_total") is not None:
        bits.append(f"- {snap['owed_total']} people are waiting on a reply from Wei.")
    for row in (snap.get("owed") or [])[:6]:
        bits.append(f"  - {row.get('who')}: {row.get('days')} days, re "
                    f"{(row.get('subject') or '')[:60]}")
    quiet = snap.get("quiet") or []
    if quiet:
        bits.append(f"- {len(quiet)} deals have gone quiet: " + "; ".join(
            f"{q.get('name')} {q.get('days')}d (ball with {q.get('ball')})"
            for q in quiet[:6]))

    # The to-do list, which is a FILE, not something to go searching for.
    # "What is on my to-do list right now?" ran past the 300s ceiling in the
    # baseline because the model went hunting through 62,000 pages for a list
    # that sits in one note the pipeline already parses.
    try:
        from . import agenda

        items = [i for i in agenda.build(snap) if not i.done]
        mine = [i for i in items if i.kind in ("todo", "manual")]
        if mine:
            bits.append(f"- Wei's to-do list, {len(mine)} open item(s):")
            for i in mine[:12]:
                bits.append(f"  - [{i.bucket}] {i.title}"
                            + (f" — {i.detail[:70]}" if i.detail else ""))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(bits)


def _sources_block(hits: list[dict]) -> str:
    """The pages already retrieved, handed over rather than thrown away.

    `search()` finds the six best pages in under a second and the UI shows
    them — and then the model went and searched again through MCP for the same
    thing. Naming them costs nothing and removes a whole tool round trip.
    """
    if not hits:
        return ""
    lines = ["Already retrieved for this question, with the matching text "
             "included. Answer from these and cite them inline by slug. Do "
             "not open them again — what you would read is below. Fetch "
             "something new only if the answer is genuinely absent here, and "
             "at most twice; if it is not there, say so rather than widening "
             "the search further:"]
    for h in hits[:6]:
        date = f" ({h['date']})" if h.get("date") else ""
        body = h.get("context") or h.get("excerpt") or ""
        lines.append(f"- {h['slug']}{date}: {body}")
    return "\n".join(lines)


def _prompt(job: Job) -> str:
    """The question, with the date and enough of the conversation for a
    follow-up to mean something.

    `hermes -z` starts cold every time, so "and what about the proposal?" would
    otherwise reach a model that had never seen the question before it. The
    earlier turns are replayed here rather than relying on a session the
    assistant does not have.
    """
    blocks = [_now_line()]
    if job.screen:
        # "Add" was missing from this list once, and the model followed the
        # enumeration literally: told "add these six prospects", it summarised
        # the panel instead. State the rule, not an incomplete list of verbs.
        blocks.append("The user is looking at this panel and is asking about "
                      "it. Unless the question clearly says otherwise, answer "
                      "about these rows. If they ask you to change the panel "
                      "in ANY way — add items, move, rename, change state or "
                      "stage, add notes, mark done, archive — you MUST make "
                      "the change with the panel_* tools (panel_add, "
                      "panel_set, panel_done), one call per item, and then "
                      "confirm what changed. Do not merely describe or "
                      "promise the change:\n" + job.screen)
    if _facts_wanted(job.question):
        facts = _facts()
        if facts:
            blocks.append(facts)
    src = _sources_block(job.hits)
    if src:
        blocks.append(src)
    head = "\n\n".join(blocks)
    if not job.session:
        return f"{head}\n\n{job.question}"
    try:
        from . import chats

        prior = chats.context(job.session)
    except Exception:  # noqa: BLE001
        prior = []
    if not prior:
        return f"{head}\n\n{job.question}"
    parts = [head, "", "Earlier in this conversation:"]
    for t in prior:
        parts.append(f"Q: {t.get('question','')}\nA: {t.get('answer','')}")
    parts += ["", f"Now: {job.question}"]
    return "\n".join(parts)


def _run(job: Job) -> None:
    # Search first, BEFORE queueing. It is cheap, it does not contend, and it
    # means a question waiting its turn still shows real sources immediately
    # rather than sitting blank.
    hits, clock = search_profiled(job.question)
    with _lock:
        job.hits = hits
        job.profile["search"] = clock

    with _lock:
        ahead = sum(1 for j in _jobs.values()
                    if j.status == "running" and not j.queued and j.id != job.id)
        if ahead:
            job.queued = True
            job.queue_position = ahead
    with _slot:
        with _lock:
            job.queued = False
            job.queue_position = 0
            # Restart the clock: waiting in line is not thinking.
            job.started = time.time()
        _synthesise(job)


def _usage(path: Path) -> dict:
    """Tokens and turns for one question, from hermes's own `--usage-file`.

    Wall time alone cannot tell a slow model from a model taking eight turns to
    decide it has finished. Turns can, and the questions that run past the
    ceiling are the ones that keep taking another one.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    finally:
        try:
            path.unlink()
        except OSError:
            pass

    def dig(*names):
        for n in names:
            if isinstance(raw, dict) and isinstance(raw.get(n), (int, float)):
                return raw[n]
        return None

    got = {
        "tokens_in": dig("input_tokens", "prompt_tokens", "tokens_in"),
        "tokens_out": dig("output_tokens", "completion_tokens", "tokens_out"),
        "turns": dig("turns", "steps", "iterations", "rounds"),
        "tool_calls": dig("tool_calls", "tools_called"),
    }
    return {f"agent_{k}": v for k, v in got.items() if v is not None}


def _synthesise(job: Job) -> None:
    prompt = _prompt(job)
    job.profile["prompt_chars"] = len(prompt)
    usage_file = Path(f"/tmp/cos-usage-{job.id}.json")
    t_agent = time.time()
    try:
        proc = subprocess.run(
            [str(HERMES_PY), "-m", "hermes_cli.main", "-z", prompt,
             "-t", TOOLSET, "--usage-file", str(usage_file)],
            cwd=str(HERMES_DIR), env=_env(),
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
        job.profile["agent"] = round(time.time() - t_agent, 2)
        job.profile.update(_usage(usage_file))
    except subprocess.TimeoutExpired:
        job.profile["agent"] = round(time.time() - t_agent, 2)
        job.profile["timed_out"] = True
        with _lock:
            job.status = "failed"
            job.error = f"No answer after {TIMEOUT_SECONDS}s. It may be stuck."
            job.finished = time.time()
        _record(job)     # or the question vanishes entirely on reload
        return
    except OSError as e:
        with _lock:
            job.status = "failed"
            job.error = f"Could not start the assistant: {e}"
            job.finished = time.time()
        _record(job)
        return

    out = (proc.stdout or "").strip()
    with _lock:
        # A non-zero exit is a failure whatever came out on stdout. The old
        # test was `returncode != 0 and not out`, so a crash that had already
        # printed something — a guardrail stop, an OOM, a truncated stream —
        # was marked done, cached, and written into the conversation as a real
        # answer, with stderr discarded. That is this project's documented
        # failure class, verbatim.
        if proc.returncode != 0:
            job.status = "failed"
            job.error = (proc.stderr or "no output").strip()[:400]
            if out:
                # Keep what arrived, but never as an answer and never cached.
                job.answer = ""
                job.error = f"Stopped after partial output. {job.error}"[:400]
        else:
            answer, follow = _split_follow_up(out[:ANSWER_MAX])
            job.status = "done"
            job.answer = answer
            job.follow_up = follow
        job.finished = time.time()
    if job.status == "done" and not job.screen:
        _remember(job.question, job.answer, job.hits, job.follow_up)
    _record(job)


def _record(job: Job) -> None:
    """File the finished exchange under its session."""
    if not job.session:
        return
    try:
        from . import chats

        chats.add_turn(job.session, {
            "id": job.id, "question": job.question, "answer": job.answer,
            "hits": job.hits, "follow_up": job.follow_up, "error": job.error,
            "status": job.status, "elapsed": round(job.elapsed, 1),
            "asked_at": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception:  # noqa: BLE001 — a failed write must not lose the answer
        pass


def start(question: str, fresh: bool = False, session: str = "",
          screen: str = "") -> Job:
    """Answer a question, reusing a valid cached answer unless `fresh`."""
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask something.")
    if len(question) > 2000:
        raise ValueError("That question is too long.")

    job = Job(id=uuid.uuid4().hex[:12], question=question, session=session,
              screen=(screen or "")[:6000])

    # A follow-up is not the same question twice: its answer depends on what
    # came before it, so the cache must not serve the standalone answer here.
    if session and _has_context(session):
        fresh = True

    # "Summarize" asked from the Prospects panel and "summarize" asked from
    # Tasks are different questions with the same words. Neither reads from
    # the cache nor writes to it.
    if job.screen:
        fresh = True

    # Questions the pipeline can answer on its own, answered on its own.
    #
    # "What is today's date?" measured 5.8 seconds, 1,305 tokens in and 55 out
    # — for a date the pipeline had already written into the prompt. Nearly all
    # of that was booting an agent and one round trip to OpenRouter, to have a
    # remote model read our own sentence and say it back.
    #
    # Only where the snapshot holds the whole answer, and only where nothing
    # else was asked; see `instant`. A follow-up inside a conversation always
    # goes to the assistant, because "and which should I do first?" is not this
    # kind of question.
    if not session:
        quick = instant.answer(question)
        if quick:
            job.status = "done"
            job.answer = quick
            job.finished = time.time()
            job.profile = {"instant": True, "agent": 0.0}
            with _lock:
                _jobs[job.id] = job
                _order.append(job.id)
            return job

    if fresh:
        forget(question)
    else:
        hit = cached_answer(question)
        if hit:
            job.status = "done"
            job.answer = hit["answer"]
            job.cached_age = hit["age_seconds"]
            job.hits = hit.get("hits", [])
            job.follow_up = hit.get("follow_up")
            job.finished = time.time()

    with _lock:
        _jobs[job.id] = job
        _order.append(job.id)
        # Bounded: this is a long-running server and questions accumulate.
        while len(_order) > HISTORY_MAX:
            _jobs.pop(_order.pop(0), None)

    # Write it down BEFORE the work starts. A running turn that lives only in
    # the browser dies the moment Wei clicks another chat — which, at 20-170
    # seconds an answer, is a routine gesture and not an edge case.
    _record(job)
    if job.status == "running":
        threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def _has_context(session: str) -> bool:
    try:
        from . import chats

        return bool(chats.context(session))
    except Exception:  # noqa: BLE001
        return False


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def history(limit: int = 10) -> list[dict]:
    """Most recent first, so the page can show what was asked before."""
    with _lock:
        return [_jobs[i].as_dict() for i in reversed(_order[-limit:]) if i in _jobs]
