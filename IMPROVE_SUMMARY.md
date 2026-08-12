# What changed

## Kiran now tells an instruction from a question before it acts

**The problem.** You typed "archive Insight2" and got a summary of Insight2.
You typed "add these prospects" and got a description of the panel. The ask
pipeline had one route for everything — search the brain, write prose, cite
the pages — so anything you typed came back as an answer, even when it was an
order.

**The fix.** Every input is now sorted into one of two kinds before anything
else happens:

- **A question** — "what's open with Constella?" — goes exactly where it went
  before. Nothing about that path changed.
- **An instruction** — "archive Insight2", "mark Europol unblocked", "add task
  to hand the dashboard to Jacob" — is told, in the first line of the prompt,
  to carry it out with the panel tools and then say in one line what changed.
  It is explicitly told not to answer with a summary or a promise.

Three smaller things fall out of that:

- **Politeness no longer hides the verb.** "Can you archive Insight2?" has a
  question mark and is still an instruction. That is how most people actually
  type, so it was the biggest single source of missed actions.
- **Instructions are never answered from the cache.** Before, typing the same
  instruction twice could return the earlier "Archived: Insight2" without
  running anything — a receipt for work that never happened. Instructions now
  always run, and their confirmations are never saved.
- **Deleting asks first.** "Delete the Northwind row" is flagged as
  irreversible, and Kiran states what it is about to do and waits. Archiving
  and marking done are *not* in that class — both are undoable in the
  dashboard, and a confirmation prompt in front of the commonest action would
  make the panel chat annoying.

**On the panel specifically**, the shorthand "Constella: ball with Alberto" is
read as a write, because with those rows on screen it cannot mean anything
else. Typed into the ordinary chat box, the same words are treated as you
thinking out loud — there is no field there to write them to.

**When it cannot tell, it says question.** A question wrongly treated as an
instruction wastes a tool call. An instruction wrongly treated as a question
silently does nothing, and you find out days later that the deal never moved.

## Evidence it works

- 33 new tests for the sorting itself, covering the instructions you reported
  ("archive Insight2", "add these prospects"), the polite forms, the panel
  shorthand, and 18 questions that must *not* flip into instructions.
- 10 new tests for the routing: an instruction reaches the prompt marked as
  one, never reads or writes the cache, and cannot be swallowed by the
  instant-answer shortcut; a question behaves exactly as before.
- The whole suite passes: 491 tests, 2 seconds.
- All 17 benchmark questions still sort as questions, so the answer path they
  measure is unchanged — same prompt, same cache, same shortcuts. There was
  nothing for a benchmark run to move.

# What I did not fix

**The 176-second answer.** That one was your note about building a
self-improving application, and the time went into the assistant reading and
weighing a lot of pages to capture a strategic brief properly. It is not a bug
with a fix in the code — it is a question about how long a "capture this" turn
is allowed to take, and what it should be allowed to skip. That is your call,
not mine, so I left it alone.
