# What I changed, and why

## The short version

Yesterday's change — "not every question deserves the same six pages" — was
correct in intent and broken in practice. It asked the brain for more pages,
and the brain silently returned **none**.

So the questions it was meant to help arrived at Kiran with **no sources at
all**, plus an instruction to go and search. Kiran searched for five minutes
and got killed by the clock. That is the entire slowdown.

## What was actually happening

The search tool cuts its own reply off at exactly 65,536 bytes. Nobody knew.

Asking for six pages fits under that. Asking for ten, fourteen or sixteen —
which is what the new routing did for counts, timelines and "did we ever"
questions — does not. The reply came back chopped in half, our code could not
read it, and the error handling threw away the whole thing rather than
keeping the thirty pages that had arrived intact.

I reproduced it on the real mailbox. Every wide question returned **zero**
pages:

| Question | Pages before | Pages now |
|---|---|---|
| "Is there anything I promised someone and have not delivered?" | 0 | 14 |
| "Catch me up on HKJC — what has happened since June?" | 0 | 16 |
| "How much did we invoice Constella last quarter?" | 0 | 16 |
| "When is the CDL talk due and who has the draft?" | 0 | 10 |
| "Who is Dienert and what does he work on?" | 0 | 10 |

Ordinary six-page questions were never far from the same cliff: one of them
came back 64,778 bytes long — 758 bytes short of being cut in half too. One
longer email in the mailbox and it would have been.

Worse, when the cut landed in the middle of a curly quote or an em dash, the
whole question crashed rather than losing one page. That is the "never
returned at all" symptom from the notes.

## The five fixes

1. **Keep what arrived.** When the reply is cut off, read the pages that came
   through whole instead of throwing all of them away. Nothing is silent any
   more.
2. **Don't ask for more than fits.** Capped at forty pages per request, which
   is where the reply stops being deliverable. Ordinary questions still ask
   for thirty, exactly as before — that path is untouched.
3. **Hand over every page we retrieved.** A separate bug: even when retrieval
   worked, only the first six pages were ever put in front of Kiran. For a
   timeline that was the six *oldest*, because timeline pages get sorted into
   date order first — so "what has happened since June" was answered from
   pages that stopped before June.
4. **Cap the searching on honesty questions.** "Did we ever…" told Kiran to
   search repeatedly with no ceiling. Now: the pages provided are already a
   wide search, try at most twice more, then answer. Finding nothing is still
   a correct answer, and it should not cost five minutes.
5. **Stop searching for "catch me up".** It is a request to be told, not a
   subject. See the ✗ in the evidence table below — this one only surfaced
   because of fix 3.

## Two things I found on the way

**The benchmark was measuring the wrong search.** It ran its own separate
search at the old fixed width, and reported *that* profile — so every report
said "6 pages kept" no matter what the question actually retrieved. That is
precisely the number that would have shown this bug on day one. It now
reports the search the answer was really built from.

**`python -m cos.cli bench` did not work at all.** A stray line two thirds of
the way down the file made the program start early, with only nine of its
twenty-eight commands loaded. Nineteen commands — bench, serve, health,
digest, alert and more — answered "no such command" when run that way. Moved
to the bottom of the file where it belongs. (Typing `cos bench` was never
affected, which is why it went unnoticed.)

## Evidence

The nine affected questions, before and after. "Timeout" means it ran the
full five minutes and was killed with no answer.

| | before (Aug 12) | before (last night) | after, run 1 | after, run 2 | after, run 3 |
|---|---|---|---|---|---|
| "anything I promised and haven't delivered?" | **timeout** | **timeout** | 72s ✓ | 95s ✓ | 70s ✓ |
| "how much did we invoice Constella?" | **timeout** | 201s ✓ | 28s ✓ | 46s ✓ | 155s ✓ |
| "catch me up on HKJC since June" | 67s ✓ | **timeout** | 26s ✗ | 30s ✓ | 29s ✓ |
| "which prospects are on my panel?" | 37s ✓ | 81s ✓ | 19s ✓ | 18s ✓ | 52s ✓ |
| "when is the CDL talk due, who has the draft?" | 36s ✓ | 73s ✓ | 61s ✓ | 29s ✓ | 16s ✓ |
| "who is Dienert and what does he work on?" | 36s ✓ | 52s ✓ | 28s ✓ | — | 30s ✓ |
| "what did I agree with Acme about Zephyr?" | 29s ✓ | 132s ✓ | 40s ✓ | — | 40s ✓ |
| "most overdue thing to deal with today?" | 25s ✓ | 22s ✓ | 13s ✓ | — | 11s ✓ |
| "who is the real decision maker at Constella?" | 25s ✓ | 24s ✓ | 26s ✓ | — | 44s ✓ |

**Three timeouts in the two runs before. None in twenty-three question-runs
after.** Every question correct in the last two runs; no invented facts in
any run.

The one ✗ is the HKJC question in the first run after the fix, and it is why
there are three runs. Handing over sixteen pages instead of six exposed a
second, older problem: "catch me up" was being searched for *literally*, so
eight of the sixteen slots went to unrelated "catch-up" emails about other
people. Six pages had hidden it; sixteen could not. I stopped the phrase
being searched for — the routing already recognises it — and the August HKJC
calendar entry and the case-management proposal took those slots. Correct in
both runs since, and roughly twice as fast as it ever was.

523 tests pass. I added seven covering exactly what broke: a reply cut
mid-page, a reply cut through a curly quote, the forty-page cap, every
retrieved page reaching the prompt, the bounded honesty playbook, and "catch
me up" not reaching the search.

## What I did not fix

**The 176-second answer to your note about building a self-improving
application.** That question takes the ordinary six-page path, so none of the
routing above touched it. The truncation fix may help it — it was one long
email away from the same cliff — but I have no measurement proving that, and
the honest answer is that a long strategic statement with no question in it
is a question-shaped thing Kiran has no good playbook for. Deciding what
Kiran *should* do with "here is my strategy, react to it" is a product call,
not a bug fix, so I left it.
