# Design note — email drafting

**Status:** design only. Not built, not scheduled. Written 2026-08-01 while the
reasoning was fresh, so the access model gets decided once rather than
retrofitted.

**Target:** Stage 3.5 — after bodies and retrieval exist (`docs/ROADMAP.md`),
before any autonomous behaviour.

---

## 1. Why this is the risky one

Kiran today has four properties that make it defensible:

| Property | Today |
|---|---|
| No model in the loop | every number is header-derived by rule |
| No writes anywhere | Kuzu client refuses mutations; vault is read-only |
| No network beyond localhost | `:7001` only |
| Headers only, never bodies | 156k message bodies untouched |

**Drafting spends three of the four at once.** It needs bodies (to write
something relevant), a model (to write it), and a write path (to place it). Only
"no network beyond localhost" partially survives, and only because bodies come
from disk rather than from Google.

That is not an argument against building it. It is an argument for deciding the
access model deliberately, because every later feature will inherit it.

## 2. Why drafts are nonetheless the right first write

Spec §8.1 classes *create draft* as **internal reversible — automatic with
audit**, distinct from *send email*, which is mandatory-approval. A Gmail draft
is inert: it does nothing until a human presses send. That is the correct first
crossing of the write boundary.

There is a second reason, and it is the stronger one.

The product review's sharpest finding was that `90_agent/` would become a second
unread inbox stacked on the first — the fate already suffered by the weekly lint
report, `Dashboard.md`, the Monday digest, and the ingest-backlog notifier. Five
for five.

**A Gmail draft is the one output surface in this design that lands where Wei
already works.** It sits in the thread it belongs to. It is on the phone without
anything being built. Reviewing it is the same gesture as replying. And
**deleting it is the dismiss action** — which yields the dismissal-rate metric
the review asked for, for free, with no UI and no new habit.

## 3. Access model

### The ask is narrower than "Gmail access"

Two separable needs, and only one of them touches Google:

| Need | Source | Requires Gmail? |
|---|---|---|
| Read message bodies | local maildir on `/Volumes/FAST` | **no** |
| Place a draft in a thread | Gmail API | yes — one scope |

Kiran can read every word of every message without a Google credential, because
the mirror already exists. That fact is what makes the following possible.

### Request exactly one scope: `gmail.compose`

```
https://www.googleapis.com/auth/gmail.compose
```

It creates and modifies drafts, and it **cannot read your mailbox** — reading a
thread needs `gmail.readonly` or `gmail.modify`, neither of which we request.

> **Correction, 2026-08-04.** This section previously claimed the token
> "cannot send." **That is false**, and it was the load-bearing claim of the
> whole access model. Google's published description of `gmail.compose` is
> *"Manage drafts and send emails."* Every scope that can create a draft can
> also send: `compose`, `modify`, and `mail.google.com` alike. **There is no
> draft-only Gmail scope.**
>
> The guarantee therefore cannot come from Google. It has to be built — see
> *The broker* below. Verified against
> `developers.google.com/workspace/gmail/api/auth/scopes`.

### The broker: where the no-send guarantee actually comes from

The token does not go to the agent. It goes to a small local service that
exposes one operation, and Kiran talks to that service over localhost:

```
Kiran (Hermes)  ──localhost──>  draft-broker  ──>  Gmail API
   holds no token               holds the token
                                createDraft()   ✅
                                updateDraft()   ✅
                                listDrafts()    ✅
                                readThread()    ❌  — the mirror has every body
                                sendEmail()     ❌  — no such function exists
```

The distinction that matters: if Kiran held the token, "never send" would be a
promise the agent could be argued out of — and once web research is enabled, a
poisoned page will try exactly that. With the broker, a fully compromised agent
still cannot send, because the only process holding the credential has no code
that sends.

`readThread()` is deliberately absent. Most implementations of this pattern
include it; we can leave it out because the maildir already holds every body
locally, which keeps the credential to `compose` alone.

Additional properties:

- **Rate limit** — a few drafts per hour, so a loop or a manipulated agent
  produces noise you notice rather than a flood.
- **Append-only call log.** Any method other than `drafts.create` /
  `drafts.update` appearing in it is an alarm, not a warning.
- **Independent OAuth client**, revocable without touching mbsync.

Residual risk, stated plainly: anyone with local access as Wei can read the
token and call Gmail directly. That is already true of the mbsync IMAP
password. What the broker removes is *the agent* as a path to sending, which is
the path this feature would otherwise add.

### Why not write drafts into the maildir instead

It would need no Google credential at all, and it does not work: the mirror is
configured `Create Near / Remove Near / Expunge Near`, so it is strictly
download-only and nothing written locally propagates to Gmail. Making it
bidirectional would give this pipeline the ability to mutate the real mailbox,
which costs the read-only property the whole design has relied on. Rejected.

### Do not request

| Scope | Why not |
|---|---|
| `gmail.readonly` | bodies already come from the maildir; asking for it converts a local-only read into a network-capable one for zero benefit |
| `gmail.send` | Kiran must have no send path *in code*, not merely a disabled one |
| `gmail.modify` | labels, archive, delete — none of it needed, all of it destructive. Note this is also what applying a Gmail label to a draft would cost (`users.messages.modify` requires `gmail.modify` or `mail.google.com`), so "tag it AI-Draft" is not the cheap convenience it appears to be. Put the marker in the subject line instead — no permission required. |
| `mail.google.com` | full control; never |

### Credential handling

- Desktop OAuth client, refresh token in the macOS keychain (or
  `~/.config/kiran/`, mode 600) — **never** in the repo, never in `.env`.
- One account: `weidong@kineviz.com`.
- The token grants no read, so it is not a data-exfiltration credential. It is
  still a write credential and belongs in the audit trail.
- Revocable independently at myaccount.google.com without touching mbsync's
  IMAP credentials.

## 4. Hard dependencies

### 4.1 P2 threading — this is a prerequisite, not a nice-to-have

You cannot draft a reply without knowing which thread it belongs to.
`drafts.create` takes `message.threadId`, which is Gmail's thread id — i.e.
`X-GM-THRID` — and that header is **absent on every message synced since
February 2026** (8,868 of 11,526 emails from 2026 have a NULL `thread_id`;
sampled `maildir-live` = 0/60 carry it).

The fallback works: Gmail threads a submitted message correctly when
`In-Reply-To` and `References` are set properly, and those headers are present
on ~60% of live mail. Either way, **P2 in `docs/gmail-repo-request.md` is on the
critical path for drafting**, not just for thread summaries.

### 4.2 Body handling

Quote-stripping before anything reaches a model. Only **17.9%** of body text
survives quote/signature/disclaimer removal, and the entity-resolution review
traced **2 of 7** extraction errors directly to quoted blocks being read as
current statements. A `>`-only stripper misses the 11.4% of mail using Outlook
block quoting — which is most of the top counterparties in the corpus it was
measured on.

## 5. The draft contract

This is the part that makes drafting safe, and it is a structural rule rather
than a prompt instruction.

### The model writes prose. It does not choose recipients.

| Field | Source |
|---|---|
| `To`, `Cc` | derived from the source message headers, deterministically |
| `Subject` | `Re: ` + source subject, normalized |
| `threadId` / `In-Reply-To` / `References` | source message |
| **body** | **the only field the model produces** |

The single most valuable property here: **an injected email cannot redirect a
draft to an attacker's address**, because no addressee ever originates from
model output. The security review's exfiltration chains mostly assume the model
can influence a destination. Here it structurally cannot.

### A draft may quote the thread it is replying to, and nothing else

No synthesized history. No "as you agreed in March." No commitments pulled from
the wider corpus.

This is the direct mitigation for the measured failure mode: qwen3:14b scored
**1/7** on commitment extraction on this corpus, and two of its errors inverted
who owed what. A badly-worded draft is harmless — you rewrite it. A draft that
confidently references *a commitment that was never made*, to the counterparty
who would know, is the case that costs something real.

If a future version cites history, every cited claim must carry a verbatim quote
and a message link, and must be visually subordinate to the quote — you cannot
launder a sentence the reader is looking at.

### Machine authorship must be visible in the artifact

Spec §9.11 requires generated content to be distinguishable from human-authored.
In Gmail there is no metadata channel for that, so it goes in the body:

```
[KIRAN DRAFT — delete this line before sending]
```

Deliberately something that must be removed by hand. A draft sent by accident
with that line intact is embarrassing; a draft sent by accident *without* any
marker is worse.

### No send path exists in code

Not a flag, not a config option, not a disabled branch. There is no function
that calls `messages.send` or `drafts.send`, in the broker or anywhere else.

This rule used to be the second of two layers, behind a scope that was believed
to forbid sending. That scope does not exist (see §3), so **this is now the only
layer** — which is precisely why the token lives in the broker rather than in
the agent. A rule the agent cannot reach is worth more than a rule it is asked
to follow.

## 6. Context budget

The first place Kiran makes an external model call. Spec §4.3 says the model
gets "the current task, retrieved evidence snippets, relevant entities" — not a
mailbox.

Per draft, send: the target thread quote-stripped (typically a few hundred
tokens after cleaning — median cleaned body is ~60 tokens), the counterparty's
name and organization, and the deal's stated next step from `Pipeline.md` if the
domain maps. **Nothing else.** Cap at 32k tokens, not the 200k originally
configured.

At `deepseek-v4-flash-0731` rates this is fractions of a cent per draft.

## 7. Command shape

```
kiran draft <person|address>     # reply to their most recent message
    --dry-run                    # print only; do not touch Gmail  [default]
    --place                      # after review, create the Gmail draft
```

Rules, consistent with the rest of Kiran:

- **Pull-triggered.** Never scheduled, never batched, one at a time.
- **Terminal first.** The draft is printed and, by default, goes nowhere. `--place`
  is a second, deliberate act.
- **Audit row per draft**: timestamp, source message id, recipient set, model
  and resolved provider, `sha256` of the body. Not the body itself — spec §9.5.
- **Allowed to decline.** If the thread is too thin to draft from, say so rather
  than producing filler. Same discipline as an empty report.

## 8. What this does not become

- No autonomous drafting on new mail arrival.
- No `--send`, ever, at this stage. Sending is spec §8.1 mandatory-approval and
  belongs behind the workflow machinery in Stage 5, if at all.
- No draft-and-forget. If drafts accumulate unread, that is the same failure as
  the lint report and should be read as the feature failing, not as a backlog.

## 9. Open questions

1. **Does a draft need Kiran at all, or is this a Claude-in-the-loop task?**
   Drafting one reply is something Wei can already get from any assistant with
   the thread pasted in. Kiran's edge is knowing *which* reply is overdue and
   having the thread already. If `kiran owed` output plus a manual paste turns
   out to be enough, this feature may not need to exist — worth testing that way
   first, at zero cost.
2. **Tone.** Drafts written in a voice that is not Wei's get rewritten from
   scratch, which is worse than no draft. Might need a handful of real sent
   messages as few-shot examples — which is more corpus reaching the model, and
   should be a deliberate decision.
3. **Where does the marker line go** if the reply is top-posted vs. inline?
4. **Threading fallback correctness** when `References` is absent (~40% of live
   mail): draft as a new message in the thread, or decline?

## 10. Prerequisites, in order

1. **P0** — `serve.py` lifecycle (`docs/gmail-repo-request.md`)
2. **P2** — RFC-5322 threading fallback. Blocking.
3. Stage 0 substrate repairs
4. Stage 3 — bodies, quote-stripping, retrieval
5. OAuth `gmail.compose` setup, one time
6. This feature

Nothing here should start before `kiran owed` has proven it gets used. If the
three-Mondays gate fails, drafting is moot — a draft of a reply you were never
going to send is not worth building.
