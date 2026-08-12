"""Is this an instruction to do something, or a question to answer?

The dashboard's ask pipeline treated every input the same way: retrieve from
the brain, synthesise prose, cite the pages. That is right for "what's open
with Northwind?" and wrong for "archive Insight2" — Wei typed an instruction
and got a summary of the thing he had asked to change, which reads as though
the work were done when nothing moved.

It bites hardest on the Tasks and Prospects panels, where most of what a
person types is a write: *"add these prospects"*, *"mark Europol unblocked"*,
*"Constella: ball with Alberto"*. The panel tools to do all three already
exist; nothing was deciding to reach for them.

So the input is classified BEFORE anything else happens, and the two intents
are routed differently:

- **question** — retrieve, answer, cite. Unchanged.
- **action** — execute with the tools, then confirm what changed. Never served
  from cache, and never written to it: a cached "Archived: Insight2" replayed
  for the same words a second time is a confirmation of work that never ran.

The rules are cheap string tests on purpose. A second model call to decide
whether to make a model call would cost more than the mistake it prevents, and
this runs on every keystroke-to-Enter path including the ones that must answer
in under a second.

**When it cannot tell, it says question.** A question wrongly routed as an
action wastes a tool call; an action wrongly routed as a question silently
does nothing, and the user only finds out later that the deal never moved.
The one exception is a panel, where the spec asks for action-tolerance — and
even there only for the `name: value` shape that is unambiguously a write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    kind: str                  # "action" | "question"
    reason: str = ""           # what decided it — logged, so misroutes are findable
    destructive: bool = False

    @property
    def is_action(self) -> bool:
        return self.kind == "action"


# Openings that are not part of the instruction. Stripped before anything is
# read, so "Kiran, please archive Insight2" is the same input as "archive
# Insight2" — the politeness was hiding the verb.
_LEAD = re.compile(
    r"^\s*(?:hey|hi|ok|okay|so|and|also|then|kiran|please|pls)\b[\s,:-]*",
    re.I)
# "Can you add a task" is an instruction wearing a question mark. This is the
# single biggest source of missed actions in real typing, so the request
# wrapper is removed and what is left is judged on its own.
_REQUEST = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+(?:you|u|we)\s*(?:please\s*)?"
    r"|i(?:'d| would) like you to\s+|i want you to\s+|(?:let'?s)\s+"
    r"|(?:go ahead and)\s+)",
    re.I)

# Verbs that change something. From the spec, plus the ones the panel tools
# actually implement.
_ACTION_VERBS = {
    "add", "append", "archive", "assign", "bump", "cancel", "change", "clear",
    "close", "complete", "create", "delegate", "delete", "do", "draft",
    "drop", "file", "finish", "flag", "handle", "hand", "log", "make", "mark",
    "merge", "move", "note", "pause", "put", "rename", "remind",
    "remove", "reopen", "reply", "reschedule", "resume", "schedule", "send",
    "set", "snooze", "star", "start", "stop", "tag", "track", "unarchive",
    "unflag", "unstar", "update",
}

# Imperatives that only READ. "show me my tasks" is typed like a command and
# is a question in every way that matters: it wants an answer, it is safe to
# cache, and routing it as an action would have the assistant hunting for a
# write tool that does not exist.
_READ_VERBS = {
    "brief", "check", "compare", "count", "describe", "explain", "find",
    "give", "list", "look", "recap", "review", "search", "show", "summarise",
    "summarize", "tell", "walk",
}

# Words that open a question.
_INTERROGATIVE = {
    "am", "any", "anybody", "anyone", "anything", "are", "can", "could",
    "did", "do", "does", "has", "have", "how", "is", "should", "was", "were",
    "what", "whats", "what's", "when", "where", "which", "who", "whom",
    "whos", "who's", "whose", "why", "will", "would",
}

# "do" is the one verb on both lists. "do I owe Bob a reply?" is a question;
# "do the Northwind follow-up" is not. The pronoun after it decides.
_DO_PRONOUNS = {"i", "we", "you", "they", "he", "she", "it"}

# Irreversible, and therefore not to be done without asking. Archiving and
# marking done are NOT here: both are reversible in the dashboard, and
# treating them as dangerous would put a confirmation prompt in front of the
# most common action there is.
_DESTRUCTIVE = re.compile(
    r"\b(delete|erase|wipe|purge|permanently|overwrite|unsubscribe)\b", re.I)

# The panel shorthand: "Constella: ball with Alberto", "Europol — unblocked".
# A colon or dash with a short left-hand side is how a person writes a field
# assignment, and on a panel it can only mean a write.
_ASSIGNMENT = re.compile(r"^[^?\n]{1,60}?\s*(?::|—|->|=>)\s*\S")

_WORD = re.compile(r"[A-Za-z'’]+")


def _strip(text: str) -> str:
    """The instruction, with the politeness and the request wrapper removed."""
    out = (text or "").strip()
    for _ in range(4):          # "hey Kiran, can you please …" nests
        before = out
        out = _REQUEST.sub("", _LEAD.sub("", out)).lstrip()
        if out == before:
            break
    return out


def _first_words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD.findall(text)[:3]]


def classify(text: str, screen: str = "") -> Intent:
    """Question or action, and why.

    `screen` is the panel the input was typed under, as text — empty for the
    ordinary chat box. On a panel the classifier is slightly action-tolerant,
    because that is where the writes are.
    """
    raw = (text or "").strip()
    if not raw:
        return Intent("question", "empty")

    core = _strip(raw)
    words = _first_words(core)
    first = words[0] if words else ""

    if first in _READ_VERBS:
        return Intent("question", f"read verb {first!r}")

    if first in _ACTION_VERBS and not (
            first == "do" and len(words) > 1 and words[1] in _DO_PRONOUNS):
        return Intent("action", f"imperative {first!r}",
                      destructive=bool(_DESTRUCTIVE.search(raw)))

    if "?" in raw or first in _INTERROGATIVE:
        return Intent("question", "interrogative")

    if screen.strip() and _ASSIGNMENT.match(core):
        # Only on a panel. In the general chat box "Northwind: still stuck"
        # is a person thinking out loud, and there is no field to write it to.
        return Intent("action", "panel field assignment",
                      destructive=bool(_DESTRUCTIVE.search(raw)))

    return Intent("question", "no action signal")
