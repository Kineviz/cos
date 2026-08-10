"""A benchmark for the assistant: how fast, and how right.

Built because "it seems slow" and "the sources looked wrong" are not things you
can improve against. Two failures found by hand in one evening — a toolset that
made the model loop until timeout, and a search pointed at the wrong brain —
were both invisible until someone happened to ask the right question. This runs
the right questions on purpose, every time.

**Accuracy is graded against facts, not by a model.** Each question carries
`must` — strings that a correct answer has to contain, in any of several
accepted forms — drawn from your real data and verified against the brain when
the set is written. An LLM grader would have been quicker to write and would
have made the benchmark's own accuracy the thing you have to trust; a claim
like "Morgan, not Taylor, is the decision maker" is either in the answer or
it is not.

`forbid` matters as much as `must`. Several questions have a specific wrong
answer that a plausible-sounding response would give, and catching those is how
you tell a system that knows something from one that is guessing well.

**Source relevance is measured separately from the answer.** They fail
independently: one recall question returned four unrelated people's wiki
pages and still answered correctly, because the model went and searched again. That
looks fine from the outside and is one unlucky corpus away from being wrong.

Run: `cos bench` — see `docs/BENCHMARK.md`.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path.home() / ".cos" / "bench"


@dataclass
class Question:
    id: str
    category: str
    text: str
    # Each entry is a list of alternatives; the answer must contain at least
    # one of them. Alternatives exist because "Aug 31" and "end of August" are
    # the same fact, and pinning the phrasing would grade the writing style.
    must: list[list[str]] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)
    # Slug fragments a good retrieval should surface. Scored separately from
    # the answer because they fail independently.
    want_sources: list[str] = field(default_factory=list)
    # Questions whose right answer depends on knowing what "today" is.
    temporal: bool = False
    notes: str = ""


def _last_week_spellings() -> list[str]:
    """Any day of last week, in any of the ways a person writes one.

    Derived rather than written down, because a fact list with dates in it
    quietly stops testing anything the following Monday.

    Both readings count, because on a Saturday they are both right. The ISO
    week before this one is Monday-to-Sunday and ended six days ago; the week
    a person means when they say it on a Saturday is the one that just
    finished. The model picked the second and was marked wrong for it, which
    grades an opinion about English rather than whether it found the week.
    """
    from datetime import date, timedelta

    today = date.today()
    out: list[str] = []
    for back in (7, 0):
        monday = today - timedelta(days=today.weekday() + back)
        for n in range(7):
            d = monday + timedelta(days=n)
            # Today is not last week under either reading, and accepting it
            # would let "here is what you did this morning" pass.
            if d >= today:
                continue
            out += [f"{d.month}/{d.day}", d.isoformat(),
                    d.strftime("%b %-d").lower(), d.strftime("%B %-d").lower()]
    return out


# The questions ship in two layers, because the real ones are private.
#
# Real questions grade against real facts — "who is the decision maker at
# <client>" with the actual name — which is exactly what makes them useful
# and exactly what must not be in a public repository. So:
#
#   ~/.cos/bench-questions.yaml   your questions, about your data. Private,
#                                 outside the repo, loaded when present.
#   the built-in set below        fictional company, same shape, so a fresh
#                                 install can exercise the harness end to end
#                                 before writing its own.
#
# Write your own early. A benchmark over fictional data measures the
# machinery; only your questions measure the assistant.

QUESTIONS_FILE = Path.home() / ".cos" / "bench-questions.yaml"


def _load_private() -> list[Question]:
    if not QUESTIONS_FILE.is_file():
        return []
    import yaml

    try:
        rows = yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise SystemExit(f"Could not read {QUESTIONS_FILE}: {e}") from e
    out = []
    for r in rows or []:
        out.append(Question(
            id=r["id"], category=r["category"], text=r["text"],
            must=[[str(a) for a in g] for g in r.get("must", [])],
            forbid=list(r.get("forbid", [])),
            want_sources=list(r.get("want_sources", [])),
            temporal=bool(r.get("temporal")), notes=r.get("notes", "")))
    return out


_BUILTIN: list[Question] = [
    # ---- temporal: does it know when "now" is? -------------------------
    Question(
        "t1", "temporal", "What is today's date?",
        must=[[str(datetime.now().year)]],
        temporal=True,
        notes="The floor. It has a clock tool; this proves it reaches for it.",
    ),
    Question(
        "t4", "temporal", "What did I do last week?",
        must=[_last_week_spellings()],
        temporal=True,
        notes="Accepts any day of last week, in any common spelling.",
    ),
    # ---- recall: specific facts, with a specific wrong answer ----------
    # Northwind Analytics is fictional. Replace these with questions about
    # your own deals and colleagues in ~/.cos/bench-questions.yaml.
    Question(
        "r1", "recall", "Who is the decision maker at Northwind Analytics?",
        must=[["morgan"]],
        forbid=["taylor is the decision"],
        want_sources=["northwind"],
        notes="Works only after you have mail about Northwind — i.e. never. "
              "A template for the shape: the obvious wrong answer is listed "
              "as a forbidden string.",
    ),
    # ---- honesty: the right answer is \"I do not know\" ------------------
    Question(
        "n1", "honesty",
        "What did I agree with Acme Dynamics about the Zephyr contract?",
        must=[["no ", "not", "nothing", "cannot", "can't", "don't", "unable"]],
        forbid=["zephyr contract states", "we agreed to"],
        notes="Acme Dynamics and Zephyr do not exist. An invented answer here "
              "is the most damaging failure this system can have.",
    ),
]

QUESTIONS: list[Question] = _load_private() or _BUILTIN

# t4's facts are date-derived and cannot live in a YAML file; make sure the
# private set always has it.
if _load_private() and not any(q.id == "t4" for q in QUESTIONS):
    QUESTIONS.append(Question("t4", "temporal", "What did I do last week?",
                              must=[_last_week_spellings()], temporal=True))


# --------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def grade(q: Question, answer: str, hits: list[dict]) -> dict:
    """Score one answer. Deterministic, so two runs are comparable."""
    a = _norm(answer)
    met = [any(alt.lower() in a for alt in group) for group in q.must]
    banned = [f for f in q.forbid if f.lower() in a]

    slugs = " ".join(h.get("slug", "") for h in hits).lower()
    if q.want_sources:
        src = sum(1 for w in q.want_sources if w.lower() in slugs) / len(q.want_sources)
    else:
        src = None

    return {
        "answered": bool(a.strip()),
        "facts_hit": sum(met),
        "facts_total": len(q.must),
        "accuracy": (sum(met) / len(met)) if met else (1.0 if a.strip() else 0.0),
        "forbidden": banned,
        "source_relevance": src,
        "chars": len(answer or ""),
    }


def run_one(q: Question, timeout: int = 300) -> dict:
    """Ask one question through the same path the dashboard uses."""
    from . import ask

    t0 = time.time()
    hits, clock = ask.search_profiled(q.text)
    t_search = time.time() - t0

    job = ask.start(q.text, fresh=True)
    while job.status == "running" and time.time() - t0 < timeout:
        time.sleep(1)
        job = ask.get(job.id) or job
    total = time.time() - t0

    # Where the time went. "It seems slow" is not something you can improve
    # against, and the whole-question number hides which half is the problem:
    # retrieval is under two seconds of a run that can take five minutes.
    profile = dict(job.profile or {})
    profile["retrieval"] = {**clock}
    agent = profile.get("agent")
    if isinstance(agent, (int, float)):
        # Everything the pipeline does that is neither retrieval nor the agent:
        # prompt assembly, the queue, the poll interval.
        profile["overhead"] = round(max(total - t_search - agent, 0.0), 2)

    row = {
        "id": q.id, "category": q.category, "question": q.text,
        "status": job.status, "search_seconds": round(t_search, 2),
        "total_seconds": round(total, 1),
        "profile": profile,
        "answer": job.answer, "error": job.error,
        "hits": [h.get("slug") for h in (job.hits or hits)],
    }
    row.update(grade(q, job.answer, job.hits or hits))
    return row


def run(ids: list[str] | None = None, label: str = "") -> dict:
    rows = []
    picked = [q for q in QUESTIONS if not ids or q.id in ids]
    for n, q in enumerate(picked, 1):
        print(f"  [{n}/{len(picked)}] {q.id} {q.text[:52]}", flush=True)
        try:
            row = run_one(q)
        except Exception as e:  # noqa: BLE001 — one bad question must not end the run
            row = {"id": q.id, "category": q.category, "question": q.text,
                   "status": "error", "error": f"{type(e).__name__}: {e}",
                   "accuracy": 0.0, "total_seconds": 0, "search_seconds": 0,
                   "forbidden": [], "source_relevance": None, "answered": False}
        print(f"        {row['status']:8} {row['total_seconds']:>6}s  "
              f"acc {row.get('accuracy', 0):.0%}"
              + (f"  FORBIDDEN {row['forbidden']}" if row.get("forbidden") else ""),
              flush=True)
        rows.append(row)
    return summarise(rows, label)


def summarise(rows: list[dict], label: str = "") -> dict:
    done = [r for r in rows if r["status"] == "done"]
    times = [r["total_seconds"] for r in done] or [0]
    srcs = [r["source_relevance"] for r in rows if r.get("source_relevance") is not None]
    per_cat: dict[str, list[float]] = {}
    for r in rows:
        per_cat.setdefault(r["category"], []).append(r.get("accuracy", 0.0))

    return {
        "label": label,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n": len(rows),
        "completed": len(done),
        "failed": len(rows) - len(done),
        "accuracy": round(statistics.mean([r.get("accuracy", 0.0) for r in rows]), 3),
        "accuracy_by_category": {
            k: round(statistics.mean(v), 3) for k, v in sorted(per_cat.items())
        },
        "hallucinations": sum(1 for r in rows if r.get("forbidden")),
        "source_relevance": round(statistics.mean(srcs), 3) if srcs else None,
        "median_seconds": round(statistics.median(times), 1),
        "p90_seconds": round(sorted(times)[int(len(times) * 0.9) - 1], 1) if times else 0,
        "slowest": round(max(times), 1),
        "search_seconds": round(
            statistics.mean([r["search_seconds"] for r in rows] or [0]), 2),
        "rows": rows,
    }


def save(result: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{result.get('label') or 'run'}.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return path


def history() -> list[dict]:
    """Every past run, oldest first, without the per-question rows."""
    if not RESULTS_DIR.is_dir():
        return []
    out = []
    for p in sorted(RESULTS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: v for k, v in d.items() if k != "rows"})
    return out


def render(result: dict) -> str:
    L = [f"# {result.get('label') or 'benchmark'} — {result['at']}", ""]
    L.append(f"{result['completed']}/{result['n']} completed  ·  "
             f"accuracy {result['accuracy']:.0%}  ·  "
             f"median {result['median_seconds']}s  ·  p90 {result['p90_seconds']}s")
    if result.get("source_relevance") is not None:
        L.append(f"source relevance {result['source_relevance']:.0%}")
    if result["hallucinations"]:
        L.append(f"**{result['hallucinations']} forbidden claim(s)**")
    L.append("")
    L.append("| id | category | acc | sec | status |")
    L.append("|---|---|---|---|---|")
    for r in result["rows"]:
        L.append(f"| {r['id']} | {r['category']} | {r.get('accuracy', 0):.0%} | "
                 f"{r['total_seconds']} | {r['status']} |")
    return "\n".join(L) + "\n"


def compare() -> str:
    """Every run so far, oldest first, so the movement is the point.

    A single run tells you where you are; only the sequence tells you whether
    the last change helped. Written for the overnight loop, where each row is
    "the fix I made before this one".
    """
    runs = history()
    if not runs:
        return "No runs yet."
    L = ["| run | acc | temporal | list | recall | reason | honesty | halluc | src | med s | p90 s | done |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        c = r.get("accuracy_by_category", {})
        pct = lambda k: f"{c[k]:.0%}" if k in c else "—"  # noqa: E731
        src = f"{r['source_relevance']:.0%}" if r.get("source_relevance") is not None else "—"
        L.append(
            f"| {r.get('label') or r['at']} | **{r['accuracy']:.0%}** | "
            f"{pct('temporal')} | {pct('list')} | {pct('recall')} | "
            f"{pct('reasoning')} | {pct('honesty')} | {r['hallucinations']} | "
            f"{src} | {r['median_seconds']} | {r['p90_seconds']} | "
            f"{r['completed']}/{r['n']} |")

    first, last = runs[0], runs[-1]
    L += ["", "**Movement, first run to last**", ""]
    def delta(name, key, fmt="{:.0%}", better="up"):
        a, b = first.get(key), last.get(key)
        if a is None or b is None:
            return
        arrow = "→" if a == b else ("↑" if b > a else "↓")
        good = "" if a == b else (
            "  ✓" if ((b > a) == (better == "up")) else "  ✗")
        L.append(f"- {name}: {fmt.format(a)} {arrow} {fmt.format(b)}{good}")
    delta("accuracy", "accuracy")
    delta("hallucinations", "hallucinations", "{}", "down")
    delta("source relevance", "source_relevance")
    delta("median seconds", "median_seconds", "{}", "down")
    delta("p90 seconds", "p90_seconds", "{}", "down")
    delta("completed", "completed", "{}", "up")
    return "\n".join(L) + "\n"
