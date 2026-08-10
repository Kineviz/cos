# COS — the Chief-of-staff Operation System

Thirty years ago your computer booted into DOS: a disk operating system, one
prompt, and everything on the machine at your command. **COS is that prompt
for your working life.** It watches your email, calendar, and notes, and
tells you what needs your attention: who is waiting on a reply, which deals have gone quiet, what you
promised and have not delivered. It can draft replies into your Drafts folder.
**It cannot send email.** There is no send function in the code.

Everything runs on your own computer. Your mail never goes to anyone's server,
including ours. Only the chat assistant uses a hosted model, and you choose
which one.

## What you get

**A command line:**

```
C:\> cos brief      # today: date, meetings, who is waiting on you
C:\> cos owed       # people who wrote to you and got no reply
C:\> cos quiet      # deals with no recent contact
C:\> cos drafts     # drafts it wrote: sent as-is, rewritten, or deleted
C:\> cos health     # is everything running? exits non-zero if not
```

(The `C:\>` is a joke. It runs on a Mac.)

**A dashboard** (`cos serve`) with three things: your task list, your prospect
list, and a chat box. You can drag tasks between Today / Soon / Backlog, drag
prospects between pipeline stages, and flag deals as "needs attention now".
The chat can edit both lists for you: "move Northwind to Engaged and add a
note" works.

**A phone assistant** (optional). Connect Telegram and you can ask the same
questions and give the same instructions from your phone.

## The three parts

| Part | What it is | Required? |
|---|---|---|
| `cos` | This repo. A Python program that reads Gmail and Google Calendar and writes reports. | Yes |
| The brain | A local search index of your mail and notes (`gbrain`, PostgreSQL). Lets the assistant answer "what did we discuss with X". | Optional |
| The assistant | A chat agent (`hermes`) on Telegram and the dashboard. | Optional |

You can install just `cos` and get the reports. Add the brain and the
assistant when you want to ask questions in plain language.

## Safety, in plain terms

- **It cannot send email.** The permission it asks Google for is read-only.
  Drafting uses a second, separate permission — and even that code path has
  no send call in it.
- **It cannot invent a recipient.** Every draft goes to an address taken from
  a real message in your own mailbox. A stranger's email cannot redirect a
  reply, no matter what it says.
- **It writes only where you allow.** Your notes are read-only to it, except
  inside clearly marked blocks in files it maintains.

## What you need

1. A Mac (this is what it is tested on) with Python 3.11+ and
   [uv](https://github.com/astral-sh/uv).
2. A Google account. (Microsoft/Outlook also works for the core reports —
   see `docs/SETUP-MICROSOFT.md`.)
3. About 30 minutes for the basic install. The brain and assistant take
   longer — see `docs/SETUP-gbrain-hermes.md`.

## Install

**Step 1 — get the code:**

```bash
git clone git@github.com:Kineviz/cos.git
cd cos
uv venv && uv pip install -e ".[dev,gmail,clock]"
```

**Step 2 — connect Google.** Create a Google Cloud project, enable the Gmail
and Calendar APIs, create an OAuth client ID of type "Desktop app", and save
the downloaded JSON as `~/.config/cos/oauth_client.json`. Then run:

```bash
cos google-auth
```

It opens Google's consent screen in your browser. Approve it once and you
are connected; `cos check` then confirms the mailbox is reachable. (Google requires no app review below 100 users, which
is one reason this is self-hosted software rather than a service.)

**A shortcut for the whole install:** run `cos setup` at any point — it
checks every step below and prints the fix for whatever is missing. If you
use Claude Code, open this folder and ask it to help you install; the repo
carries instructions for it.

**Step 3 — tell it who you are.** Copy `.env.example` to `.env` and set:

| Setting | What it means |
|---|---|
| `COS_PRINCIPAL_ADDRESSES` | your own email addresses, comma-separated |
| `COS_VAULT_ROOT` | your Obsidian vault, if you have one (optional) |
| `COS_QUIET_DAYS` | days of silence before a deal counts as quiet (default 30) |
| `COS_OWED_WINDOW_DAYS` | how far back to look for unanswered mail (default 90) |

**Step 4 — try it:**

```bash
cos brief
cos owed
```

If `cos owed` shows people waiting on you, it works.

**Step 5 (optional) — deals.** List your deals in `config/deal_domains.yaml`,
mapping each deal name to the email domains of the people in it. This file is
maintained by hand on purpose: it connects what *you* call a deal to what
your *mailbox* sees, and no model should guess that. Deals without a mapping
are shown as unmapped, never silently dropped.

**Step 6 (optional) — the brain and the assistant.** Follow
`docs/SETUP-gbrain-hermes.md`. This is the longest part of setup, and the
guide includes every problem we hit so you do not hit them cold.

## Keeping it running

Two scheduled jobs do the work: a refresh every 15 minutes and a morning
digest at 07:30. The dashboard restarts itself if it dies.

`cos health` is the honest check. Every test in it looks for positive
evidence with a time bound — "mail is newer than N hours", "the index matches
the current code" — never "no errors in the log". Every failure this system
ever had looked like success from the outside; the checks are written to
catch exactly that. A check that cannot tell reports `unknown`, and unknown
counts as needing attention.

Alerts fire once, when something breaks — not 96 times a day while it stays
broken. A still-broken check reminds you once a day. Recovery gets one line.

## Design rules, and where they came from

Each rule exists because we measured the failure it prevents.

- **The model never decides structure.** A local model, tested on real mail,
  extracted 1 of 7 commitments correctly and reversed who-owes-whom on two.
  So "who is waiting" comes from message headers — who wrote last, how long
  ago — and the model only writes prose.
- **Reports may be empty.** `cos quiet` with nothing quiet prints one green
  line. A report that is sometimes empty gets believed when it is not.
- **Your text is never destroyed.** Generated files are rewritten only inside
  `<!-- cos:begin -->` / `<!-- cos:end -->` markers. Everything you write is
  preserved byte-for-byte. If you type inside a generated block, your text is
  moved out below it rather than lost.

## Tests

```bash
pytest      # 417 tests, no network or database needed
```

## More documentation

| File | What it covers |
|---|---|
| `docs/SETUP-gbrain-hermes.md` | installing the brain and the assistant, step by step |
| `docs/SETUP-MICROSOFT.md` | using an Outlook/Exchange mailbox instead of Gmail |
| `docs/DESIGN-email-drafting.md` | why drafting is built the way it is |
| `docs/BENCHMARK.md` | how the assistant's answers are scored |
| `docs/PLAN-becoming-a-chief-of-staff.md` | where this is going |

## License and credit

MIT — see [LICENSE](LICENSE). Built by [Kineviz](https://www.kineviz.com),
the graph-visualization company behind GraphXR, as the operating layer for
its own founder's working day, and open-sourced so it can be yours too.

## A note on the name

COS — Chief-of-staff Operation System, with a nod to MS-DOS. Like DOS, it is
the layer underneath: it keeps the machinery current and honest, and does
nothing glamorous itself. The assistant that runs on top has whatever name
you give it — the original install calls its assistant Kiran. The name lives
in the assistant's prompt, not in the code.
