#!/usr/bin/env bash
# The nightly self-improvement run: benchmark, collect flagged/slow/regressed
# questions, attempt fixes on a branch, merge only what passes every gate.
# See src/cos/improve.py for the design and ~/.cos/improve-policy.yaml for
# what may merge without asking. Logs to ~/.cos/improve.log.
set -uo pipefail

COS=~/projects/chief-of-staff
LOG=~/.cos/improve.log
mkdir -p ~/.cos

exec >>"$LOG" 2>&1
echo "── $(date '+%Y-%m-%d %H:%M:%S') ─────────────────────────────"

# launchd gives a bare PATH; the improver needs the claude CLI and bun.
export PATH="$HOME/.local/bin:$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f ~/.zshenv ] && . ~/.zshenv 2>/dev/null

cd "$COS" || exit 1
.venv/bin/cos improve nightly || echo "  ! nightly improvement run failed"
echo "  done $(date '+%H:%M:%S')"
