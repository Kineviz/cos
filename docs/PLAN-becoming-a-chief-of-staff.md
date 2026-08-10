# Becoming a chief of staff

Decided 2026-08-07. This supersedes the "what comes next" question in
`ROADMAP.md`, which was written when the tool was read-only and had no path
past reporting.

## Where this is going, and why it is not there yet

Today the tool is a **personal secretary**: it remembers, it suggests, it
drafts. The intent is for it to become a **chief of staff**: something that
also acts.

Those two words are not in tension. In Whitehall the Principal Private
Secretary to a minister is not a typist — they run the private office, control
what paper reaches the minister, draft in the minister's voice, and are the
channel to the whole department. "Private Secretary" at the top *is* chief of
staff. The word only reads as junior in American usage.

But there is a real difference between them, and it is worth naming precisely
because it determines the order of the work:

> **A secretary and a chief of staff both act toward other people.** A
> secretary schedules *with* someone, replies *to* someone, guards the door
> *against* someone. A chief of staff chases people, speaks with your
> authority, kills things on your behalf. Both have standing with third
> parties.

This tool has standing with exactly one person: its owner. Nobody else in
their world knows it exists. It cannot send an email, accept a meeting, or
tell a colleague anything.

The closest historical description is the Qing **师爷** — the privately hired
adviser who held the files, drafted the correspondence, tracked what was
overdue, and had no rank and no seal. He drafted; the magistrate signed. That
is exactly the shape of `draft_broker.py`, and it is a good description of the
product as it stands.

## The order of capabilities

Sorted by **blast radius**, not by how useful or clever each one is. This
ordering is the plan; the rungs are not interchangeable.

| # | Capability | Who sees it | Reversible |
|---|---|---|---|
| 1 | Read, report, draft into your own drafts folder | only you | n/a |
| 2 | Act on your own mailbox — label, file, archive | only you | fully |
| 3 | Send, with per-item approval | recipient | no |
| 4 | Send a narrow class unattended | recipient | no |
| 5 | Act outward on the calendar — decline, reschedule | attendees | partly |
| 6 | Speak in your name; chase people | anyone | no |

**Rung 1 is where the tool is now.** Nothing leaves the machine.

**Rung 2 is nearly free and worth doing next.** Labelling and filing are
invisible outside the account and undoable. It also buys the first real
signal about judgement quality at zero risk.

**Rung 3 adds no new risk.** The human still approves every message; only the
plumbing changes. It exists as a rung so that the send path is exercised and
audited *before* anything depends on it being correct.

**Rung 3 → 4 is the dangerous jump**, and it is the only one that needs a
gate rather than a decision. See below.

**Rungs 5 and 6 are not scheduled.** They should not be planned in detail
until 4 has run for a long time without incident.

## The gate on unattended sending

Do not build rung 4 on a judgement call about whether the drafts "seem good."
The measurement already exists and costs nothing to collect.

Every draft ends in one of three states:

* sent unedited,
* rewritten and sent,
* deleted.

That is an accuracy score that accumulates by itself, in the owner's own
mailbox, with no extra machinery and no labelling effort. `draft_broker.py`
already stamps each draft with a marker line, so the three states are
distinguishable after the fact.

**The gate: let drafts accumulate for months, then look at the ratio.** If
most go out untouched, unattended sending for a narrow class is an easy
decision backed by hundreds of real cases. If most get rewritten, that is a
far more valuable finding than any amount of caution in the abstract — and
the right response is to fix the drafting, not to add an approval dialog.

This is deliberately slow. The whole point is that by the time the decision
arrives, it is not really a decision.

## The invariant

At every rung, including the last:

> **The model supplies prose and intent. It never supplies targets.**

Every address on a draft is derived from the source message's headers by code
(`_reply_addresses`), never taken from model output. An email written by a
stranger therefore cannot redirect a reply, no matter what it says.

Extend that pattern rather than replacing it. When the tool can send, it
should be a **broker that executes with an approval step**, not an agent
holding a send tool. The model says *what*; the code decides *to whom*.

The reason to be strict about this is structural, not theoretical. This system
holds private data and reads content written by strangers. Adding an outbound
channel closes the triad. Right now safety is a property of the code — there
is no send function anywhere to call. Past rung 3 it becomes a property of
policy, which is a much weaker guarantee, and the invariant above is what
keeps the weaker guarantee honest.

## What must not happen

* No send function that takes a recipient as an argument.
* No capability added because it was easy, ahead of its rung.
* No unattended action whose failure is invisible. If the tool does something
  on its own, it must be discoverable afterwards — the draft audit log
  (`draft-audit.jsonl`) is the existing pattern.
* No scheduled report added without a reason it will not become the sixth
  dead artifact in the vault.
