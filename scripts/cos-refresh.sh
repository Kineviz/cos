#!/usr/bin/env bash
# Everything that has to happen for Kiran to be current, in one place.
#
# Each of these was a separate way for Kiran to be confidently wrong:
#
#   date        SOUL.md and today.md carried a stale date, so "what do I have
#               tomorrow" was answered from a two-month-old email.
#   calendar    meetings existed only in Google, so Kiran did not know they
#               were happening.
#   mail        new threads sat in Gmail unexported; Kiran answered from mail
#               that stopped days ago and said nothing about it.
#   vault       notes written in Obsidian are untracked in git, and gbrain
#               syncs by commit range — so an uncommitted note is invisible.
#
# Safe to run repeatedly; every step is idempotent. Logs to ~/.cos/refresh.log.
set -uo pipefail

COS=~/projects/chief-of-staff
VAULT="${COS_VAULT_ROOT:-$HOME/vault}"
BRAIN=~/brain
LOG=~/.cos/refresh.log
mkdir -p ~/.cos

exec >>"$LOG" 2>&1
echo "── $(date '+%Y-%m-%d %H:%M:%S') ─────────────────────────────"

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f ~/.zshenv ] && . ~/.zshenv 2>/dev/null

cd "$COS" || exit 1

# 1. Date anchor + upcoming meetings, into today.md AND the agent's prompt.
.venv/bin/cos brief || echo "  ! brief failed"

# 2. Calendar -> brain pages.
.venv/bin/python - <<'PY' || echo "  ! calendar refresh failed"
from pathlib import Path
from cos.config import Config
from cos.calendar_source import load_events, write_pages
cfg = Config.load()
P = {p.lower() for p in cfg.principal_addresses}
w, s = write_pages(load_events(days_back=180, days_forward=120),
                   Path.home()/"brain"/"calendar", P)
print(f"  calendar: {w} pages")
PY

# 3. New mail -> brain pages. A short window; older mail is already exported.
.venv/bin/cos export-brain --since "$(date -v-7d '+%Y-%m-%d')" 2>&1 \
  | grep -E "page\(s\) written|card\(s\)" || echo "  ! export-brain produced nothing"

# 4. Commit both repos. gbrain syncs by COMMIT RANGE, so anything uncommitted
#    is invisible to it — this is the step whose absence hid Wei's meeting
#    notes from Kiran entirely.
#
#    Run through PYTHON, not bash, and this is not a style choice. The vault
#    lives under ~/Documents, which macOS protects with TCC, and TCC grants are
#    per-executable and NOT inherited by children. Under launchd, /bin/bash has
#    no grant: `ls` returns "Operation not permitted" and git run directly from
#    here dies with "fatal: Unable to read current working directory". The venv
#    python and gbrain both hold the grant, so git launched as a subprocess of
#    python works fine.
#
#    That difference was invisible for two days. `git status --porcelain`
#    failed, printed to stderr, and returned EMPTY STDOUT — which the old
#    `if [ -n "$(...)" ]` read as "nothing to commit". 168 runs, one line of
#    fatal each, and the vault was never once committed by the unattended job.
#    Every vault commit in the history came from a hand-run. Kiran therefore
#    could not see anything written in Obsidian unless someone ran this by
#    hand — the exact failure this step was added to prevent.
#
#    So: check the exit status, never infer "clean" from empty output, and make
#    a permission failure LOUD.
.venv/bin/python - "$VAULT" "$BRAIN" <<'PY' || echo "  ! commit step failed"
import subprocess, sys, time
from pathlib import Path

stamp = time.strftime("%Y-%m-%d %H:%M")

def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, **kw)

for repo in (Path(p) for p in sys.argv[1:]):
    st = git(repo, "status", "--porcelain")
    if st.returncode != 0:
        # Loud. An unreadable repo is not a clean repo.
        print(f"  ! {repo.name}: cannot read git status — "
              f"{(st.stderr or '').strip().splitlines()[0][:90] if st.stderr else 'unknown'}")
        continue
    if not st.stdout.strip():
        continue

    # New and modified files only. Never -A: a deletion should be deliberate,
    # and this runs unattended.
    untracked = git(repo, "ls-files", "-o", "--exclude-standard", "-z").stdout
    paths = [p for p in untracked.split("\0") if p]
    if paths:
        git(repo, "add", "--", *paths)
    git(repo, "add", "-u")

    # Authored as the agent, not as Wei. These commits sweep up whatever
    # autopilot happened to edit; if they carried Wei's name there would be no
    # way to ask later "what did the machine change?" — git log --author=Kiran
    # answers it, and `git log --author=Weidong` stays honest.
    c = git(repo, "-c", "user.name=Kiran (agent)", "-c", "user.email=kiran@localhost",
            "commit", "-q", "-m", f"cos refresh {stamp}")
    if c.returncode == 0:
        print(f"  committed {repo.name}")
    else:
        print(f"  ! {repo.name}: commit failed — "
              f"{(c.stderr or c.stdout or '').strip()[:90]}")
PY

# 5. Index it.
cd "$BRAIN" || exit 1
gbrain sync --source vault   2>&1 | grep -E "^Synced|added" | sed 's/^/  vault /'
gbrain sync --source default --repo "$BRAIN" 2>&1 | grep -E "^Synced|added" | sed 's/^/  brain /'
gbrain embed --stale 2>&1 | grep -E "^Embedded" | sed 's/^/  /'

# 6. The agent reads SOUL.md once, at start. It had been up for three days,
#    which meant every prompt fix — including the date block written in step 1 —
#    was sitting on disk unread while Kiran confidently answered with the wrong
#    day. Restart when the stamped date changes, and only then, so a restart
#    never lands in the middle of a conversation for no reason.
STAMP=~/.cos/soul-date
TODAY=$(date '+%Y-%m-%d')
if [ "$(cat "$STAMP" 2>/dev/null)" != "$TODAY" ]; then
  cd ~/.hermes && ./hermes-agent/venv/bin/python -m hermes_cli.main gateway restart \
    >/dev/null 2>&1 && echo "  gateway restarted (date rolled to $TODAY)"
  echo "$TODAY" > "$STAMP"
fi

# 7. Watch how Kiran is actually behaving. Mechanical faults (a hung run, a dead
#    gateway) are fixed here because there is no judgement in them — the
#    40-minute silence needed nothing but a restart, and nobody was watching.
#    Anything requiring judgement is written to the report and left alone: three
#    wrong diagnoses of the calendar failure in one afternoon is the argument
#    against letting this rewrite its own instructions.
cd "$COS" || exit 1
.venv/bin/cos review --fix > ~/.cos/last-review.md 2>&1
if grep -q "Needs your judgement" ~/.cos/last-review.md; then
  echo "  review: findings need attention — ~/.cos/last-review.md"
  cat ~/.cos/last-review.md >> ~/.cos/review-history.md
else
  echo "  review: clean"
fi

# 8. Did the machinery above actually work? `review` reads what the agent SAID;
#    this asserts that each step left evidence behind — a commit that reached
#    the index, a message newer than N hours, an MCP server with tools. Every
#    failure this project has had looked like success from the log alone.
.venv/bin/cos health --quiet > ~/.cos/last-health.md 2>&1 \
  && echo "  health: all checks pass" \
  || { echo "  health: NEEDS ATTENTION"; sed 's/^/    /' ~/.cos/last-health.md; }

# 9. Page Wei only on the ok->broken transition, at most once a day per problem.
#    Unguarded this would send 96 messages a day for one unfixed fault, and a
#    muted bot is worse than no monitoring: it looks like coverage.
ALERT=$(.venv/bin/cos alert 2>&1)
[ -n "$ALERT" ] && echo "  alert sent: $(echo "$ALERT" | head -1)"

# 10a. Restart the dashboard if it is serving stale code. It loads its modules
#      once and launchd only restarts it on a crash, so an edit can sit
#      unserved for hours — /api/page did exactly that.
V=$(curl -s --max-time 4 http://127.0.0.1:8787/api/version 2>/dev/null)
if [ -n "$V" ]; then
  RUNNING=$(echo "$V" | sed -n 's/.*"running": *"\([^"]*\)".*/\1/p')
  HEADC=$(echo "$V" | sed -n 's/.*"head": *"\([^"]*\)".*/\1/p')
  if [ -n "$RUNNING" ] && [ -n "$HEADC" ] && [ "$RUNNING" != "$HEADC" ]; then
    launchctl kickstart -k "gui/$(id -u)/com.cos.serve" 2>/dev/null \
      && echo "  dashboard restarted ($RUNNING -> $HEADC)"
  fi
fi

# 10. Cache the dashboard's numbers for the web page. Computing them per
#     request took over five minutes — a full ledger build and a Gmail round
#     trip — so the page reads this file instead. A run that cannot read the
#     mail keeps the previous numbers rather than showing blank, because blank
#     reads as "nothing to do".
.venv/bin/cos snapshot >/dev/null 2>&1 \
  && echo "  snapshot: written" \
  || echo "  ! snapshot failed"

# 11. Mirror today's list into the vault. The dashboard already writes this on
#     every change; doing it here as well keeps the page current as new mail
#     arrives, and means the markdown is committed and indexed even on a day
#     nobody opens the dashboard.
.venv/bin/python -c "
from cos import agenda
from cos.config import Config
from cos.webconfig import read_snapshot
agenda.write_page(Config.load().vault_root, agenda.build(read_snapshot()))
" >/dev/null 2>&1 && echo "  list: mirrored" || echo "  ! list mirror failed"

echo "  done $(date '+%H:%M:%S')"
