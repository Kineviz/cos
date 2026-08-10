#!/usr/bin/env bash
# The daily summary to Telegram. Once each morning, from com.cos.digest.
#
# Separate from cos-refresh.sh on purpose. The refresh is a 15-minute
# maintenance loop whose failures are expected and recoverable; this sends a
# message to a human's phone. Mixing them would mean a bug in either one can
# either silence the digest or spam it.
set -uo pipefail

COS=~/projects/chief-of-staff
LOG=~/.cos/digest.log
mkdir -p ~/.cos

exec >>"$LOG" 2>&1
echo "── $(date '+%Y-%m-%d %H:%M:%S') ─────────────────────────────"

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f ~/.zshenv ] && . ~/.zshenv 2>/dev/null

cd "$COS" || exit 1

# Not --quiet, and the result is logged either way. A delivery failure that
# leaves no trace is the same silent-success bug this monitoring exists to
# catch, one level up.
if .venv/bin/cos digest --send; then
  echo "  digest delivered"
else
  echo "  ! digest NOT delivered — see above"
fi
