"""What KIND of question this is, so it gets the right treatment.

Wei: "The way we answer is overly simplistic. Get top 6 matching, and
summarize... using different approach [for] different question or ask."
He is right about the failure and about where it bites: six best pages is
the correct move for "who is the decision maker at Northwind?" and the
wrong move for "how many deals mention pricing?" — counting needs
everything, not the best. One treatment for every question was the
simplification, and this module removes it.

Each kind maps to a playbook (ask.py): how wide to retrieve, in what
order, and a short set of marching orders in the prompt. The agent stays
singular — it already knows how to search again and walk the brain's
links when told to; what was missing was anything that told it when.

Same design rules as intent.py, for the same reasons:

- **Cheap string tests, not a model.** A model call to decide how to make
  a model call taxes every question, including the easy majority that the
  11-second median depends on.
- **Every decision carries its reason.** Misroutes must be findable in
  the log the way misfiled actions are.
- **When it cannot tell, it says lookup.** The default path answers most
  things acceptably; a wrong specialised route is stranger than a plain
  one. Rules are ordered so the kinds with the most distinctive wording
  claim the question first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Retrieval width per kind. Lookup keeps the tuned default (6); the wide
# kinds get more because their failure mode is a missing page, not a noisy
# one. 16 is not a magic number — it is where the prompt stays under control
# with 1,200-character excerpts; the eval loop owns tuning it.
WIDTH = {"lookup": 6, "timeline": 16, "sweep": 16, "multihop": 10,
         "absence": 14, "compare": 12, "multipart": 10}


@dataclass(frozen=True)
class QType:
    kind: str        # lookup | timeline | sweep | multihop | absence | compare | multipart
    reason: str = ""

    @property
    def width(self) -> int:
        return WIDTH.get(self.kind, 6)


# Two asks joined into one sentence. The second clause is what silently
# drops today — "when is the talk due, and who has the draft?" came back
# with a date and no name.
_MULTIPART = re.compile(
    r"[?;].+\?|,?\s+and\s+(?:who|what|when|where|which|how|why|did|is|are|does|has)\b",
    re.I)

# Counting and enumerating. "Best six" is the wrong idea entirely here —
# these need a sweep of the structured data, then prose.
_SWEEP = re.compile(
    r"\b(?:how many|how much|how often|count|total|list (?:all|every)|"
    r"all (?:the|of the|my|our)\s|每|which (?:deals|people|clients|meetings|threads|emails)|"
    r"every(?:one|thing)? (?:who|that|i|we)|breakdown of)\b",
    re.I)

# A chain: the answer to the first hop is the search term for the second.
_MULTIHOP = re.compile(
    r"\b(?:who introduced|introduced (?:me|us)|through whom|via whom|"
    r"who connected|how (?:do|did) i know|who else (?:at|from|in)|"
    r"in common|know each other|met through|whose (?:colleague|boss|report))\b",
    re.I)

# Asking whether something ever happened. The dangerous kind: thin search
# plus confidence invents facts, so these search wide and must say what was
# searched before saying no.
_ABSENCE = re.compile(
    r"\b(?:did (?:i|we|they) ever|have (?:i|we) ever|was there ever|"
    r"is there any (?:record|note|email|mention)|any record of|"
    r"ever (?:sent|signed|invoiced|paid|agreed|promised)|"
    r"(?:promised|committed|agreed).{0,30}(?:not|never|haven'?t|didn'?t)|"
    r"never (?:replied|answered|followed))\b",
    re.I)

# The story of something over time. Needs many pages in date order; six
# unordered pages cannot hold three months.
_TIMELINE = re.compile(
    r"\b(?:what happened (?:with|to|on)|history (?:of|with)|timeline|"
    r"over the (?:last|past)|since (?:january|february|march|april|may|june|"
    r"july|august|september|october|november|december|20\d\d)|"
    r"how (?:has|did) .{3,40}(?:evolve|progress|develop|change|go)|"
    r"catch me up|bring me up to (?:speed|date)|the story (?:of|with)|"
    r"summar(?:y|ise|ize) (?:of )?(?:the |our |my )?(?:relationship|deal|thread|work))\b",
    re.I)

# Judging across candidates. Facts must be gathered per candidate first, or
# the judgement skews toward whoever emails the most.
_COMPARE = re.compile(
    r"\b(?:compare|versus|\bvs\.?\b|which .{0,40}(?:should|better|best|first|"
    r"more promising|prioriti[sz]e)|prioriti[sz]e|rank (?:the|my|our)|"
    r"in line with|compared to|stack up)\b",
    re.I)


def classify(question: str) -> QType:
    """The kind, with the rule that decided it.

    Order is by distinctiveness of wording, not importance: sweep and
    multihop phrasing is nearly unmistakable; timeline and compare words
    appear in looser talk, so they judge later; multipart runs last because
    any kind can be compound — a compound sweep is still a sweep, and the
    sweep playbook already enumerates.
    """
    q = " ".join((question or "").split())
    if not q:
        return QType("lookup", "empty")
    for kind, rx in (("sweep", _SWEEP), ("multihop", _MULTIHOP),
                     ("absence", _ABSENCE), ("timeline", _TIMELINE),
                     ("compare", _COMPARE)):
        m = rx.search(q)
        if m:
            return QType(kind, f"matched {m.group(0)!r}")
    m = _MULTIPART.search(q)
    if m:
        return QType("multipart", f"matched {m.group(0)!r}")
    return QType("lookup", "default")


# The marching orders each kind adds to the prompt. Short on purpose: the
# agent already has the tools and the sources; what it needs is the shape of
# a good answer for THIS kind of question.
PLAYBOOK = {
    "timeline": (
        "This asks for a story over time. The sources are ordered oldest "
        "to newest — keep that order in your answer. If the sources have "
        "gaps in the period asked about, search once more for the missing "
        "stretch before writing. Dates on every beat."),
    "sweep": (
        "This asks for a count or a complete list, so 'the best few pages' "
        "is the wrong standard — completeness is. Enumerate every item "
        "first (search again with different words if the sources look "
        "partial), give the list or the number plainly, and say what set "
        "you counted over. Never estimate from a sample."),
    "multihop": (
        "This needs two steps: the answer to the first part is the search "
        "term for the second. Resolve the first hop, then search again or "
        "follow the brain's links (get_links, get_backlinks, "
        "traverse_graph) with what you learned. Do not guess the second "
        "hop from wording."),
    "absence": (
        "This asks whether something ever happened. The pages below are "
        "already a wide search; if they do not settle it, search at most "
        "twice more with different words, then stop and answer. If you find "
        "nothing, say 'no record' and name exactly where you looked — never "
        "soften into a guess, and never invent a plausible answer. Finding "
        "nothing, stated honestly, is a correct answer, and it is not worth "
        "five minutes of looking."),
    "compare": (
        "This asks for a judgement across several things. First gather the "
        "facts for EACH candidate — one search per candidate if the "
        "sources cover them unevenly — then compare. Name the criteria you "
        "used, and say if one candidate's information is too thin to "
        "judge."),
    "multipart": (
        "This question has more than one part. Answer every part. Before "
        "replying, re-read the question and check each part is addressed — "
        "the usual failure is answering the first and dropping the rest."),
}
