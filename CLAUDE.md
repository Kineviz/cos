# Instructions for Claude Code in this repository

You are probably here for one of two reasons: helping someone **install** COS,
or helping someone **develop** it. Both sections matter; installation first
because that is the harder session to get right.

## Helping someone install

The person you are helping wants a working chief-of-staff assistant, not a
tour of the code. Drive with commands, verify each step before the next, and
explain what things mean in plain words.

**The one command that orients every session:**

```bash
cos setup
```

It checks every prerequisite and prints the fix for anything missing. Run it
first, run it after each step, and trust its order. When it prints "Ready",
the core install is done.

**The steps, and who does what:**

1. `uv venv && uv pip install -e ".[dev,gmail,clock]"` — you run this.
2. `cos setup` — creates `.env` on first run. Then edit `.env` with the
   user: `COS_PRINCIPAL_ADDRESSES` is their own email address(es). Everything
   else can wait.
3. **Google connection — the human does the console part.** They create a
   Google Cloud project, enable the Gmail and Calendar APIs, create an OAuth
   client ID (Desktop app), and download the JSON to
   `~/.config/cos/oauth_client.json`. Walk them through it; do not try to do
   it for them. Then `cos check` opens the consent screen — they approve it
   in their own browser.
   (Microsoft/Outlook instead: `docs/SETUP-MICROSOFT.md`.)
4. `cos brief` and `cos owed` — the first real output. If `owed` shows
   people waiting, the install works.
5. Optional, in order of value: the deal list
   (`config/deal_domains.example.yaml` → `deal_domains.yaml`), the dashboard
   (`cos serve`), the scheduled refresh, and finally the brain + assistant
   (`docs/SETUP-gbrain-hermes.md` — long; it has a numbered path and a
   symptom→cause→fix list at the end. Follow it exactly, especially step 7,
   the security lockdown).

**Rules that protect the person you are helping:**

- Never put real values in `.env.example`; real config goes in `.env`, which
  is gitignored. Never commit `.env`, tokens, or anything in `~/.cos` or
  `~/.config/cos`.
- Never run `hermes -z` yourself; it bypasses approval checks. (The
  dashboard's pipeline uses it deliberately with a pinned read-only toolset;
  that is the only sanctioned use.)
- Credentials are typed by the human into real consent screens. You never
  ask for, read, or paste passwords, API keys, or OAuth secrets.
- This software cannot send email, and nothing you do while installing
  should change that. If a task seems to need a send capability, stop and
  say so.

**When something fails:** check `docs/SETUP-gbrain-hermes.md` →
"Problems you may hit" before theorising. Every entry there is a real
failure with its symptom, cause, and fix.

## Helping someone develop

- Run `pytest` before and after changes — the suite is fast (~2s, no
  network) and green means green.
- Comments in this codebase explain *why*, often with the measured failure
  that motivated the code. Keep that style: when you fix a real bug, record
  what broke and how it was found.
- The example data is fictional on purpose (Northwind, Acme, Morgan, Pat).
  Never introduce real people, companies, or mailbox contents into code,
  tests, docs, or commit messages — this repo is public, and its history is
  forever.
- `docs/BENCHMARK.md` explains how answer quality is measured. If you touch
  retrieval or prompting, run `cos bench` and compare before claiming an
  improvement; single runs are noisy, so repeat the run before trusting a
  small difference.
