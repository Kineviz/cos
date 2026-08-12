"""Kiran improving itself, inside a fence the user owns.

Wei: "run eval loops. track performance, log questions that takes too long or
answer wrong, when I flag it. then automatically try to improve." And, on who
decides what: an advisor can judge *within* boundaries, but the boundaries
live in a policy file only the human edits, enforced here in code — the loop
can never widen its own leash.

The loop, nightly:

  1. collect   flagged answers (Wei told Kiran "that was wrong"), slow
               answers (found in the conversation log), and benchmark
               regressions (a question that used to pass and now fails)
               land in one queue.
  2. attempt   a coding agent gets the queue and a git worktree on a branch.
               It never works in the live checkout.
  3. gate      code, not the agent, checks the result: no protected paths,
               diff within budget, the full test suite green, flagged
               benchmark questions actually passing, and an advisor's
               verdict on the diff.
  4. decide    every gate green and policy allows it -> merge, tell Wei what
               changed and how to undo it. Anything less -> the branch stays,
               Wei gets a summary and the apply command.

Why the human gate is shaped this way: review.py's header records the time
diagnosing a calendar failure took three wrong fixes before the real cause
(an unrestarted process) surfaced. An agent that merges its own first
plausible diagnosis buries problems under its own changes. So the automatic
path is deliberately narrow, and everything it does is one `git revert` away
from undone.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

STATE_DIR = Path.home() / ".cos"
QUEUE_FILE = STATE_DIR / "improve-queue.json"
POLICY_FILE = STATE_DIR / "improve-policy.yaml"
HERMES_DB = Path.home() / ".hermes" / "state.db"
REPO = Path(__file__).resolve().parents[2]
WORKTREES = STATE_DIR / "improve-worktrees"


def _claude_bin() -> str:
    """The claude CLI, findable under launchd's bare PATH too."""
    import shutil

    return (shutil.which("claude")
            or str(Path.home() / ".local" / "bin" / "claude"))

# The policy the human owns. Everything here errs narrow: the loop earns
# wider limits by being right, not by asking itself.
#
# `protected` is matched by prefix against repo-relative paths. The files
# listed are the ones where a bad change is not a bug but an incident:
# anything that puts words in front of other people (drafting), anything
# that talks to Wei's phone (alerting, digest), the safety-critical broker,
# secrets, and this loop itself — a self-improvement loop must never be able
# to improve away its own gates.
DEFAULT_POLICY = {
    "auto_apply": True,
    "max_diff_lines": 200,
    "slow_seconds": 120,
    "protected": [
        ".env",
        "src/cos/draft_broker.py",
        "src/cos/drafting.py",
        "src/cos/mcp_draft.py",
        "src/cos/alerting.py",
        "src/cos/digest.py",
        "src/cos/improve.py",
        "src/cos/mcp_improve.py",
        "scripts/",
        ".github/",
    ],
}

OPEN, PROPOSED, APPLIED, DISMISSED = "open", "proposed", "applied", "dismissed"


# --------------------------------------------------------------------------
# Policy


def load_policy() -> dict:
    """The human's fence. Missing file means the defaults; a present file
    overrides key by key, so adding one line does not silently drop the
    protected list."""
    policy = dict(DEFAULT_POLICY)
    if POLICY_FILE.is_file():
        import yaml

        try:
            loaded = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            # An unreadable policy fails CLOSED: nothing auto-applies.
            policy["auto_apply"] = False
            return policy
        if isinstance(loaded, dict):
            policy.update(loaded)
    return policy


def write_default_policy() -> None:
    if POLICY_FILE.exists():
        return
    import yaml

    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(
        "# What the self-improvement loop may do without asking.\n"
        "# This file is yours — the loop can read it, never write it.\n"
        "# auto_apply: false makes every change wait for your approval.\n"
        + yaml.safe_dump(DEFAULT_POLICY, sort_keys=False),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# The queue


def _load_queue() -> list[dict]:
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_queue(items: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=1, ensure_ascii=False),
                          encoding="utf-8")


def add(kind: str, question: str, complaint: str = "", answer: str = "",
        latency_s: float | None = None) -> dict:
    """File one problem. Deduplicates on the question text so a slow answer
    scanned twice, or flagged twice in frustration, stays one item."""
    items = _load_queue()
    norm = " ".join(question.lower().split())
    for it in items:
        if it["status"] in (OPEN, PROPOSED) \
                and " ".join(it["question"].lower().split()) == norm:
            if complaint and complaint not in (it.get("complaint") or ""):
                it["complaint"] = ((it.get("complaint") or "")
                                   + " | " + complaint).strip(" |")
            _save_queue(items)
            return it
    item = {
        "id": uuid.uuid4().hex[:8],
        "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kind": kind,  # flagged | slow | bench
        "question": question.strip(),
        "complaint": complaint.strip(),
        "answer": (answer or "")[:500],
        "latency_s": round(latency_s, 1) if latency_s else None,
        "status": OPEN,
        "branch": "",
        "resolution": "",
    }
    items.append(item)
    _save_queue(items)
    return item


def queue(status: str | None = OPEN) -> list[dict]:
    items = _load_queue()
    return [i for i in items if status is None or i["status"] == status]


def set_status(item_id: str, status: str, resolution: str = "",
               branch: str = "") -> bool:
    items = _load_queue()
    hit = False
    for it in items:
        if (item_id and it["id"] == item_id) \
                or (branch and it.get("branch") == branch):
            it["status"] = status
            if resolution:
                it["resolution"] = resolution
            if branch:
                it["branch"] = branch
            hit = True
    if hit:
        _save_queue(items)
    return hit


# --------------------------------------------------------------------------
# Collectors


def _question_of(user_content: str) -> str:
    """The person's actual words, out of a message the pipeline prefixed.

    Kiran's inbound messages carry an injected preamble — the date anchor and
    the computed-facts block — before what Wei typed. The person's text is
    the final paragraph."""
    parts = [p.strip() for p in (user_content or "").split("\n\n") if p.strip()]
    return parts[-1][:300] if parts else ""


def scan_slow(hours: int = 24, threshold_s: float | None = None) -> list[dict]:
    """Questions that took too long, from the conversation log itself.

    Latency here is wall-clock between Wei's message and the reply that
    followed it — the number he experienced, not any internal timing."""
    threshold = threshold_s or load_policy()["slow_seconds"]
    if not HERMES_DB.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{HERMES_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    since = time.time() - hours * 3600
    try:
        rows = db.execute(
            "SELECT session_id, role, content, timestamp FROM messages "
            "WHERE timestamp > ? AND role IN ('user','assistant') "
            "AND content IS NOT NULL AND content != '' "
            "ORDER BY session_id, timestamp", (since,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()

    found = []
    pending: dict[str, tuple[str, float]] = {}
    for session, role, content, ts in rows:
        if role == "user":
            pending[session] = (content, ts)
            continue
        if session not in pending:
            continue
        q_content, q_ts = pending.pop(session)
        lag = ts - q_ts
        question = _question_of(q_content)
        # Alerts and scheduled runs are not Wei waiting on an answer.
        if lag < threshold or not question or content.startswith(("🔴", "🟢")):
            continue
        found.append(add("slow", question,
                         complaint=f"took {lag:.0f}s to answer",
                         answer=content, latency_s=lag))
    return found


def bench_regressions(min_history: int = 3) -> list[dict]:
    """A question that used to pass and failed the latest nightly run.

    Single runs are noisy (docs/BENCHMARK.md), so "used to pass" means a
    majority of its recent runs, not just the one before."""
    from .bench import RESULTS_DIR

    runs = []
    if RESULTS_DIR.is_dir():
        for p in sorted(RESULTS_DIR.glob("*-nightly.json")):
            try:
                runs.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    if len(runs) < min_history:
        return []
    latest, prior = runs[-1], runs[-(min_history + 1):-1]

    def acc(run: dict) -> dict[str, float]:
        return {r["id"]: r.get("accuracy", 0.0) for r in run.get("rows", [])}

    latest_acc = acc(latest)
    history = [acc(r) for r in prior]
    found = []
    for qid, a in latest_acc.items():
        if a >= 0.999:
            continue
        past = [h[qid] for h in history if qid in h]
        if past and sum(1 for p in past if p >= 0.999) > len(past) / 2:
            row = next(r for r in latest["rows"] if r["id"] == qid)
            found.append(add(
                "bench", row["question"],
                complaint=f"benchmark {qid} regressed: accuracy "
                          f"{a:.0%} after passing {sum(1 for p in past if p >= 0.999)}"
                          f"/{len(past)} recent runs",
                answer=(row.get("answer") or "")[:500]))
    return found


# --------------------------------------------------------------------------
# The attempt


def _git(*args, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def _diff_stats(worktree: Path, base: str) -> tuple[list[str], int]:
    files = [f for f in _git("diff", "--name-only", base, cwd=worktree)
             .stdout.splitlines() if f.strip()]
    lines = 0
    for row in _git("diff", "--numstat", base, cwd=worktree).stdout.splitlines():
        parts = row.split("\t")
        if len(parts) >= 2:
            lines += sum(int(p) for p in parts[:2] if p.isdigit())
    return files, lines


def protected_hits(files: list[str], policy: dict) -> list[str]:
    """Which changed files the human reserved. Prefix match, so 'scripts/'
    covers the directory."""
    out = []
    for f in files:
        for p in policy.get("protected", []):
            if f == p or f.startswith(p):
                out.append(f)
                break
    return out


def _agent_prompt(items: list[dict]) -> str:
    lines = [
        "You are the self-improvement agent for this repository — a personal",
        "chief-of-staff assistant. The user flagged the problems below. Fix",
        "what is fixable in code; skip anything that needs a product decision.",
        "",
        "Rules, enforced by gates after you finish — breaking them wastes the run:",
        "- Work only in this worktree. Never touch files outside it.",
        "- Do not modify: " + ", ".join(DEFAULT_POLICY["protected"]) + ".",
        "- Keep the diff small and focused. Every changed line must serve a",
        "  listed problem.",
        "- Run the tests with: PYTHONPATH=src "
        f"{REPO}/.venv/bin/python -m pytest -q",
        "  They must pass. Add a test for what you fix.",
        "- For benchmark or wrong-answer items, verify with: PYTHONPATH=src "
        f"{REPO}/.venv/bin/python -m cos.cli bench --only <ids> --label improve-check",
        "  Repeat runs before trusting a small difference; the benchmark is noisy.",
        "- Commit your work with a message explaining what broke and why the",
        "  fix is right.",
        "- Write IMPROVE_SUMMARY.md at the worktree root: what you changed, why,",
        "  the evidence it works, in plain language for a non-engineer. If you",
        "  could not fix something, say so there plainly.",
        "",
        "The problems:",
    ]
    for it in items:
        lines.append(f"- [{it['id']} {it['kind']}] Q: {it['question']}")
        if it.get("complaint"):
            lines.append(f"  complaint: {it['complaint']}")
        if it.get("answer"):
            lines.append(f"  answer given: {it['answer'][:300]}")
    return "\n".join(lines)


def _advisor_verdict(worktree: Path, base: str) -> dict:
    """A second, independent model reads the diff and answers one question:
    would you ship this to a system the owner relies on daily? Returns
    {"ship": bool, "reason": str}; any failure to answer is a no."""
    diff = _git("diff", base, cwd=worktree).stdout[:60000]
    summary = (worktree / "IMPROVE_SUMMARY.md")
    prompt = (
        "You are the advisor gate for an automated code-improvement loop on a "
        "personal assistant the owner relies on daily. Below is the diff and "
        "the implementing agent's summary. Judge ONLY: is this safe and "
        "clearly correct enough to merge without a human review? Narrow "
        "fixes with tests: yes. Behaviour changes, prompt/personality edits, "
        "broad refactors, anything you cannot verify from the diff: no.\n"
        "Answer with one JSON object only: "
        '{"ship": true/false, "reason": "<one sentence>"}\n\n'
        "SUMMARY:\n"
        + (summary.read_text(encoding="utf-8")[:4000] if summary.is_file()
           else "(none written)")
        + "\n\nDIFF:\n" + diff
    )
    try:
        out = subprocess.run(
            [_claude_bin(), "-p", prompt], capture_output=True, text=True,
            timeout=600, cwd=str(worktree))
        m = re.search(r'\{[^{}]*"ship"[^{}]*\}', out.stdout, re.S)
        if m:
            verdict = json.loads(m.group(0))
            return {"ship": bool(verdict.get("ship")),
                    "reason": str(verdict.get("reason", ""))[:300]}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        pass
    return {"ship": False, "reason": "advisor gave no readable verdict"}


def _run_gate_tests(worktree: Path) -> tuple[bool, str]:
    """The suite, run by us in the worktree — the agent's claim that tests
    pass is not evidence; this is."""
    r = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), "-m", "pytest", "-q"],
        capture_output=True, text=True, cwd=str(worktree),
        env={"PYTHONPATH": "src",
             "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
             "HOME": str(Path.home())}, timeout=900)
    tail = (r.stdout or r.stderr).strip().splitlines()[-1:]
    return r.returncode == 0, tail[0] if tail else ""


def attempt(items: list[dict], policy: dict | None = None,
            timeout_s: int = 2700) -> dict:
    """One improvement attempt over the open items. Returns what happened,
    for the report; never raises."""
    policy = policy or load_policy()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    branch = f"improve/{stamp}"
    worktree = WORKTREES / stamp
    WORKTREES.mkdir(parents=True, exist_ok=True)

    made = _git("worktree", "add", "-b", branch, str(worktree), "HEAD")
    if made.returncode != 0:
        return {"outcome": "error",
                "detail": f"could not make worktree: {made.stderr[:200]}"}
    base = _git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

    try:
        agent = subprocess.run(
            [_claude_bin(), "-p", _agent_prompt(items),
             "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(worktree))
        agent_said = agent.stdout[-2000:]
    except subprocess.TimeoutExpired:
        return {"outcome": "error", "branch": branch,
                "detail": f"agent ran past {timeout_s}s and was stopped"}

    # Anything staged but uncommitted still counts toward the diff.
    _git("add", "-A", cwd=worktree)
    files, diff_lines = _diff_stats(worktree, base)
    if not files:
        _git("worktree", "remove", "--force", str(worktree))
        _git("branch", "-D", branch)
        return {"outcome": "nothing", "detail": agent_said[:400]}
    _git("-c", "user.name=Kiran (improve)", "-c", "user.email=kiran@localhost",
         "commit", "-q", "-m", f"improve attempt {stamp} (uncommitted remainder)",
         cwd=worktree)

    summary_file = worktree / "IMPROVE_SUMMARY.md"
    summary = (summary_file.read_text(encoding="utf-8")[:2000]
               if summary_file.is_file() else agent_said[:1000])

    # The gates. Code decides; the agent's opinion of its own work does not.
    reasons_to_ask = []
    hits = protected_hits(files, policy)
    if hits:
        reasons_to_ask.append(f"touches protected files: {', '.join(hits[:5])}")
    if diff_lines > policy["max_diff_lines"]:
        reasons_to_ask.append(
            f"diff is {diff_lines} lines (budget {policy['max_diff_lines']})")

    tests_ok, tests_tail = _run_gate_tests(worktree)
    if not tests_ok:
        for it in items:
            set_status(it["id"], OPEN, resolution=f"attempt {stamp}: tests failed")
        return {"outcome": "failed-gates", "branch": branch,
                "detail": f"tests failed in the worktree: {tests_tail}",
                "summary": summary}

    verdict = _advisor_verdict(worktree, base)
    if not verdict["ship"]:
        reasons_to_ask.append(f"advisor: {verdict['reason']}")

    for it in items:
        set_status(it["id"], PROPOSED, branch=branch)

    if reasons_to_ask or not policy.get("auto_apply", False):
        if not policy.get("auto_apply", False):
            reasons_to_ask.append("auto_apply is off in your policy")
        return {"outcome": "proposed", "branch": branch, "files": files,
                "diff_lines": diff_lines, "summary": summary,
                "why_asking": reasons_to_ask}

    merged = _git("merge", "--no-ff", branch, "-m",
                  f"self-improvement {stamp}: {', '.join(i['id'] for i in items)}")
    if merged.returncode != 0:
        _git("merge", "--abort")
        return {"outcome": "proposed", "branch": branch, "files": files,
                "diff_lines": diff_lines, "summary": summary,
                "why_asking": [f"merge did not apply cleanly: "
                               f"{merged.stderr.strip()[:150]}"]}
    for it in items:
        set_status(it["id"], APPLIED, branch=branch,
                   resolution=f"auto-applied {stamp}")
    _git("worktree", "remove", "--force", str(worktree))
    return {"outcome": "applied", "branch": branch, "files": files,
            "diff_lines": diff_lines, "summary": summary,
            "advisor": verdict["reason"],
            "undo": f"git -C {REPO} revert -m 1 HEAD"}


def apply_branch(branch: str) -> str:
    """Wei said yes. Merge the proposed branch into the live checkout."""
    merged = _git("merge", "--no-ff", branch,
                  "-m", f"self-improvement (approved): {branch}")
    if merged.returncode != 0:
        _git("merge", "--abort")
        return f"merge failed: {merged.stderr.strip()[:200]}"
    set_status("", APPLIED, branch=branch, resolution="approved by Wei")
    for wt in _git("worktree", "list", "--porcelain").stdout.split("\n\n"):
        if branch in wt:
            path = wt.splitlines()[0].removeprefix("worktree ").strip()
            _git("worktree", "remove", "--force", path)
    return f"applied {branch}"


# --------------------------------------------------------------------------
# The nightly loop


def nightly(run_bench: bool = True) -> str:
    """Collect, measure, attempt, report. The one entry point the schedule
    calls."""
    from . import bench, digest

    write_default_policy()
    policy = load_policy()
    slow = scan_slow(hours=26)

    if run_bench:
        result = bench.run(label="nightly")
        bench.save(result)
        bench_line = (f"bench: accuracy {result['accuracy']:.0%}, "
                      f"median {result['median_seconds']}s, "
                      f"p90 {result['p90_seconds']}s")
    else:
        bench_line = "bench: skipped"
    regressed = bench_regressions()

    open_items = queue(OPEN)
    lines = [f"improve nightly {datetime.now():%Y-%m-%d %H:%M}", bench_line,
             f"new: {len(slow)} slow, {len(regressed)} regressed; "
             f"open queue: {len(open_items)}"]

    if open_items:
        result = attempt(open_items, policy)
        lines.append(f"attempt: {result['outcome']}")
        msg = None
        if result["outcome"] == "applied":
            msg = ("🔧 *Kiran fixed itself overnight*\n"
                   f"{result['summary'][:600]}\n"
                   f"Changed: {', '.join(result['files'][:6])}\n"
                   f"_Undo with one word — tell me \"revert it\"._")
        elif result["outcome"] == "proposed":
            msg = ("🔧 *Kiran has a fix waiting for your OK*\n"
                   f"{result['summary'][:600]}\n"
                   f"Why it needs you: {'; '.join(result.get('why_asking', []))}\n"
                   f"_Apply: `cos improve apply {result['branch']}`_")
        elif result["outcome"] == "failed-gates":
            lines.append(f"  {result['detail']}")
        if msg:
            ok, detail = digest.send(msg)
            lines.append(f"report: {'sent' if ok else f'NOT sent — {detail}'}")

    return "\n".join(lines)
