# The self-improvement loop

The assistant collects its own failures and tries to fix them overnight.
You stay in charge through a policy file it can read but never write.

## What gets collected

Three signals, one queue (`~/.cos/improve-queue.json`):

- **You flag it.** Tell the assistant "that answer was wrong" (or slow, or
  incomplete) in any chat. It files the question, your complaint, and the
  answer it gave.
- **Too slow.** Answers that took longer than `slow_seconds` (default 120)
  are found in the conversation log automatically.
- **Benchmark regressions.** The nightly run executes `cos bench`; a
  question that used to pass and now fails is queued. One bad run is not a
  regression — the benchmark is noisy, so "used to pass" means a majority of
  recent runs.

`cos improve` shows the queue.

## What happens at night

A scheduled job (3:30am) runs the benchmark, collects new problems, and — if
anything is queued — hands the list to a coding agent working in a **git
worktree on a branch**, never in your live checkout. When the agent
finishes, code (not the agent) checks the result:

1. No protected file was touched.
2. The diff is within budget (`max_diff_lines`).
3. The full test suite passes — run by the gate, not taken on trust.
4. An independent advisor model reads the diff and answers one question:
   safe to merge without a human?

All gates green and `auto_apply: true` → the branch merges and you get a
message saying what changed and how to undo it (`git revert`). Any gate
short → the branch waits, and the message tells you why and gives the
command: `cos improve apply <branch>`.

## The policy file — `~/.cos/improve-policy.yaml`

Yours. The loop reads it, never writes it, and an unreadable file fails
closed (nothing auto-applies). The default `protected` list covers
everything where a bad change is an incident rather than a bug: drafting
(words in front of other people), alerting and the digest (your phone),
secrets, the schedule scripts — and the loop itself, so it can never widen
its own limits.

Set `auto_apply: false` to make every change wait for your OK.

## Commands

```
cos improve                    the queue and what happened to each item
cos improve nightly            run the loop now (what the schedule runs)
cos improve nightly --no-bench collect and attempt, skip the benchmark
cos improve apply <branch>     merge a proposed fix
cos improve dismiss <id>       drop a queue item
```

Everything the loop does is logged to `~/.cos/improve.log`, and every
applied change is an ordinary merge commit — `git log` is the audit trail.
