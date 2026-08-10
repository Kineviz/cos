#!/usr/bin/env bash
# The dashboard and settings page, kept running by com.cos.serve.
#
# Safe to leave up permanently: the socket accepts connections from anywhere,
# but every request whose peer is not loopback or Tailscale (100.64.0.0/10) is
# refused before it reaches a handler, and the refusal is logged. So a probe
# from the local network shows up here as evidence rather than as access.
set -uo pipefail

COS=~/projects/chief-of-staff
LOG=~/.cos/serve.log
mkdir -p ~/.cos

# Rotate rather than grow without bound — this logs every refused request, and
# an unattended log that only ever grows is a disk-space bug waiting to happen.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 5000000 ]; then
  mv "$LOG" "$LOG.1"
fi

exec >>"$LOG" 2>&1
echo "── $(date '+%Y-%m-%d %H:%M:%S') starting ──────────────────"

export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f ~/.zshenv ] && . ~/.zshenv 2>/dev/null

cd "$COS" || exit 1
# exec so launchd supervises python directly; otherwise it watches a shell that
# has already handed off, and KeepAlive restarts the wrong process.
exec .venv/bin/cos serve --port "${COS_SERVE_PORT:-8787}"
