"""Questions this machine can answer without asking a model anything.

Wei: *"some of the simple questions, like what's today, can it be returned as a
local tool call?"* Yes, and the measurements say it is most of the wait for
those questions.

    hermes boot + one turn, nothing to do     4.3 s
    the real "what is today's date?"          5.8 s   (1,305 tokens in, 55 out)
    one OpenRouter round trip                ~2.0 s
    gbrain MCP handshake, 106 tools           0.3 s
    importing hermes                          0.08 s

The date is already *in the prompt* — the pipeline puts it there. So those 5.8
seconds bought a remote model reading a sentence we wrote and saying it back.

**This is not a shortcut around the assistant. It is the existing rule taken one
step further.** `_facts()` already computes the owed counts, the quiet deals and
the to-do list deterministically and hands them over as authoritative, because
asked the same question three times the model answered "50 days quiet", "50
days", and "roughly 45 days" for a number the pipeline had computed exactly.
Where the pipeline knows the whole answer and not merely the numbers inside it,
sending it to a model adds latency and subtracts certainty.

**The bar for answering here is deliberately high**, and it is the reason this
file stays short:

- The snapshot must actually hold the answer. No snapshot, no answer.
- The question must be *only* that question. "What is on my to-do list?" is
  answered here; "what is on my to-do list and which should I do first?"
  is not — the second clause is judgement, and judgement is the model's job.
- Anything not matched falls through, silently and completely.

Getting this wrong is worse than being slow, so a miss costs 5 seconds and a
false match costs trust.
"""

from __future__ import annotations

import re
from datetime import datetime

# Every pattern is anchored to the whole question, so a matched question is one
# where nothing else was asked. A trailing "?" and ordinary politeness are the
# only slack.
_TAIL = r"[\s?.!]*"
_LEAD = r"(?:hey |hi |ok |okay |so )?"


def _q(body: str) -> re.Pattern:
    """Anchor the WHOLE alternation, not just its ends.

    Without the group, `^a|b|c$` anchors `a` to the start and `c` to the end
    and leaves `b` floating — which matched "what is on my to-do list **and
    which should I do first?**" and answered only the first half. That is
    precisely the failure this module must never have.
    """
    return re.compile(rf"^{_LEAD}(?:{body}){_TAIL}$", re.I)


_DATE = _q(r"(?:what(?:'s| is) )?(?:the )?(?:today'?s? )?date"
           r"|what(?:'s| is) today(?:'s date)?|what day is it(?: today)?"
           r"|what(?:'s| is) the time|what time is it(?: now)?")

# After "who is waiting", English offers a dozen orderings — "longest for a
# reply from me", "on me the longest", "on a reply". Rather than enumerate
# them, allow any run of words from a CLOSED set. The set is what keeps this
# precise: "who is waiting on the Northwind proposal?" contains words that are
# not in it, so it falls through to the assistant where it belongs.
_OWED_TAIL = (r"(?:\s+(?:the|longest|for|a|an|my|reply|replies|response|"
              r"from|on|me|back|to|hear|answer))*")
_OWED = _q(r"(?:who|which people|how many people)"
           r"(?:\s+(?:is|are|has|have|'s|am i))?"
           r"(?:\s+been)?\s+(?:waiting|waited|owed)" + _OWED_TAIL)

_QUIET = _q(r"(?:which|what) deals? (?:have |has )?(?:gone )?"
            r"(?:quiet|cold|stale|silent|dark)"
            r"|(?:which|what) deals? (?:are|is) (?:quiet|cold|stalled)")

_TODO = _q(r"(?:what(?:'s| is) )?(?:on )?my (?:to.?do|task) list"
           r"(?: right now| today| currently)?"
           r"|what(?:'s| is) on my (?:to.?do|task) list(?: right now| today)?"
           r"|(?:show|list) (?:me )?my (?:to.?dos?|tasks)")


def answer(question: str, snapshot: dict | None = None) -> str | None:
    """A complete answer, or None to let the assistant handle it.

    None is the common case and the safe one.
    """
    q = (question or "").strip()
    if not q:
        return None

    if _DATE.match(q):
        return _date_line()

    if snapshot is None:
        try:
            from .webconfig import read_snapshot

            snapshot = read_snapshot()
        except Exception:  # noqa: BLE001
            return None
    if not snapshot or not snapshot.get("generated_at"):
        # Without a snapshot the pipeline does not know the answer either, and
        # guessing from a stale file is exactly the failure this avoids.
        return None

    if _OWED.match(q):
        return _owed_line(snapshot)
    if _QUIET.match(q):
        return _quiet_line(snapshot)
    if _TODO.match(q):
        return _todo_line(snapshot)
    return None


def _date_line() -> str:
    now = datetime.now()
    stamp = now.strftime("%A, %d %B %Y")
    clock = now.strftime("%H:%M").lstrip("0")
    tz = now.astimezone().tzname() or ""
    return f"Today is {stamp}. It's {clock} {tz}.".replace("  ", " ").strip()


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _owed_line(snap: dict) -> str:
    rows = snap.get("owed") or []
    if not rows:
        return "Nobody is waiting on a reply from you right now."
    top = rows[0]
    who, days = top.get("who") or "someone", top.get("days")
    subject = (top.get("subject") or "").strip()
    lead = f"{who} has been waiting longest"
    if isinstance(days, (int, float)):
        lead += f" — {int(days)} {_plural(int(days), 'day', 'days')}"
    if subject:
        lead += f", re {subject[:70]}"
    lead += "."

    total = snap.get("owed_total")
    if isinstance(total, int) and total > 1:
        others = [f"{r.get('who')} ({int(r['days'])}d)" for r in rows[1:5]
                  if isinstance(r.get("days"), (int, float))]
        if others:
            lead += (f" {total} {_plural(total, 'person is', 'people are')} waiting in "
                     f"total; next up: " + ", ".join(others) + ".")
        else:
            lead += f" {total} people are waiting in total."
    return lead


def _quiet_line(snap: dict) -> str:
    rows = snap.get("quiet") or []
    if not rows:
        return "No deals have gone quiet."
    bits = []
    for r in rows[:6]:
        name, days, ball = r.get("name"), r.get("days"), r.get("ball")
        piece = str(name)
        if isinstance(days, (int, float)):
            piece += f" ({int(days)}d"
            piece += f", ball with {ball})" if ball else ")"
        elif ball:
            piece += f" (ball with {ball})"
        bits.append(piece)
    n = len(rows)
    return (f"{n} {_plural(n, 'deal has', 'deals have')} gone quiet: "
            + "; ".join(bits) + ".")


def _todo_line(snap: dict) -> str:
    try:
        from . import agenda

        items = [i for i in agenda.build(snap)
                 if not i.done and i.kind in ("todo", "manual")]
    except Exception:  # noqa: BLE001
        return None  # type: ignore[return-value]
    if not items:
        return "Your to-do list is empty."

    by_bucket: dict[str, list] = {}
    for i in items:
        by_bucket.setdefault(i.bucket or "backlog", []).append(i)

    parts = [f"{len(items)} open {_plural(len(items), 'item', 'items')}."]
    for bucket, label in (("today", "Today"), ("soon", "Soon"), ("backlog", "Back list")):
        rows = by_bucket.get(bucket) or []
        if not rows:
            continue
        titles = "; ".join(r.title for r in rows[:6])
        more = f" (+{len(rows) - 6} more)" if len(rows) > 6 else ""
        parts.append(f"{label} — {titles}{more}.")
    return " ".join(parts)
