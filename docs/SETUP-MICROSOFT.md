# Testing the Microsoft backend — on a second computer

The point of the second computer: the machine running Kiran stays on Gmail
and is never touched. Backend selection enforces the same promise in code —
Gmail wins whenever a Google token exists — but the cleanest test is a
machine with no Google token at all.

## What works today, and what does not yet

Works through Microsoft Graph:

- **Sign-in** (`cos ms-auth`) — device code, works for outlook.com and
  corporate accounts alike.
- **Connection check** (`cos ms-check`) — profile, a week of mail counts,
  today's calendar. Read-only.
- **The ledger** — who wrote when, who is waiting. Feeds owed, quiet deals
  and the prospects overlay unchanged.
- **Draft replies** — through Graph's `createReply`, which builds
  recipients and threading server-side. The malformed-Cc failure the Gmail
  path hit cannot happen here.

Not built yet:

- **Brain export** — mail as searchable pages. The test machine can run the
  dashboard's ledger-driven parts; the ask-anything search needs this.
- **Calendar pages and the 15-minute refresh loop** are untested against
  Graph data.

## One-time: register the app (any Entra tenant, free)

Someone does this once; every user afterwards just signs in.

1. Sign in at <https://entra.microsoft.com> — a free tenant is fine. A
   personal outlook.com account cannot register apps by itself; creating the
   free tenant during first sign-in solves it.
2. **App registrations → New registration.** Name it `Kiran`. Supported
   account types: **Accounts in any organizational directory and personal
   Microsoft accounts** (that is what lets an outlook.com test account and a
   corporate mailbox both sign in).
3. No redirect URI. After creating: **Authentication → Advanced settings →
   Allow public client flows → Yes.**
4. **API permissions → Add → Microsoft Graph → Delegated:**
   `Mail.ReadWrite`, `Calendars.Read`, `User.Read`, `offline_access`.
5. Copy the **Application (client) ID** from the overview page.

The client id is not a secret. It only names the app on the consent screen.

## On the test computer

```bash
git clone <repo> && cd chief-of-staff
python3 -m venv .venv && .venv/bin/pip install -e .
echo 'COS_MS_CLIENT_ID=<the-application-id>' >> .env
echo 'COS_PRINCIPAL_ADDRESSES=<the-test-mailbox-address>' >> .env

.venv/bin/cos ms-auth     # prints a URL and a code; enter in any browser
.venv/bin/cos ms-check    # profile, mail counts, calendar — read-only
```

For a test mailbox: a free **outlook.com** account exercises everything
above. A corporate tenant additionally exercises the admin-consent screen —
what a corporate user would face — for which a one-month Microsoft 365 Business trial
works.

## Seed the test mailbox before judging results

A fresh outlook.com account has no history, so `owed` and the ledger will
be honestly empty. Send it a few messages from another account, reply to
one and leave one unanswered — that gives the ledger something true to say.
