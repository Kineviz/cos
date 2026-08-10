# Benchmark

`cos bench` measures two things about asking the assistant a question: **how
long it takes** and **whether the answer is right**.

It exists because "it seems slow" and "the sources looked wrong" are not things
you can improve against. Two real failures — a toolset that made the model loop
until it was killed at 300 seconds, and a search pointed at the wrong brain —
were both invisible until someone happened to ask the right question by hand.
This asks the right questions on purpose, every time.

```bash
cos bench --label baseline      # the whole set
cos bench --only r1,r3,n1       # a few
```

Results land in `~/.cos/bench/` as JSON, one file per run, so runs can be
compared.

## How accuracy is graded

**Against facts, not by a model.** Every question carries a list of facts a
correct answer has to contain, each with accepted alternative phrasings —
"Aug 31" and "end of August" are the same fact, and pinning the wording would
grade prose style rather than correctness.

An LLM grader would have been faster to write and would have made the
benchmark's own judgement the thing you have to trust. "Morgan, not Taylor, is
the decision maker at Northwind" is either in the answer or it is not.

Some questions also carry **forbidden** strings: the specific wrong answer a
plausible-sounding response would give. `r1` fails if the answer says Andres
decides. That is how you tell a system that knows something from one that
guesses well.

## What is measured

| metric | meaning |
|---|---|
| `accuracy` | share of required facts present, averaged over questions |
| `hallucinations` | answers containing a forbidden claim — the number that should always be zero |
| `source_relevance` | share of expected pages that retrieval actually surfaced |
| `median_seconds` / `p90_seconds` | end to end, question typed to answer complete |
| `search_seconds` | just the retrieval step, which should stay near one second |
| `failed` | ran past the ceiling or errored |

**Source relevance is scored separately from the answer, because they fail
independently.** One recall question once returned four unrelated people's wiki
pages *and still answered correctly*, because the model went and searched again
on its own. From the outside that looks fine. It is one unlucky corpus away
from being wrong, and only a separate measurement shows it.

## The question set

Sixteen questions in five categories. Every fact was verified against the brain
when the set was written.

**temporal** — does it know when "now" is?
`t1` today's date · `t2` today's meetings (correct answer: none) · `t3` "how was
the response to my talk at JPMC this morning" · `t4` last week.

`t2` is a trap worth keeping: the right answer is that there is nothing, and a
system that invents a meeting to be helpful is worse than one that says so.

**recall** — a specific fact with a specific wrong answer.
`r1` who really decides at a client (with the plausible wrong name forbidden)
· `r2` why a deal stalled · `r3` recent project activity · `r4` who a colleague
is · `r5` a deadline
and who holds the draft · `r6` Spanner Graph status.

**list** — derived by rule, so exactly checkable.
`l1` longest wait for a reply · `l2` quiet deals · `l3` the current to-do list.

**reasoning** — needs more than one page.
`x1` the most overdue thing · `x2` anything promised and not delivered.

**honesty** — the right answer is "I don't know".
`n1` a contract with a company that does not exist · `n2` invoicing figures
that are not indexed anywhere.

The honesty questions matter most. Every other failure costs time; a confident
invented answer costs trust, and this system's whole value is that its answers
can be acted on without checking. `hallucinations` should be zero in every run,
and a run that improves speed while moving that number off zero is a
regression.

## Using it

The loop is: run, read the worst finding, fix one thing, run again. Keep the
label meaningful (`baseline`, `after-toolset-pin`) — the file name carries it,
and the point is to see the metric move.

Two cautions.

**The corpus moves under you.** The 15-minute refresh adds mail and rewrites
the dashboard snapshot, so two runs a day apart are not perfectly comparable.
Runs close together are.

**Answers are cached, so the benchmark forces a recompute** (`fresh=True`).
Without that it would measure the cache and report a system that had become
instantly accurate overnight.
