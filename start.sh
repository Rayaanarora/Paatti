#!/usr/bin/env bash
# Paatti one-click launcher (macOS / Linux). Run:  ./start.sh
# Installs everything, starts Paatti, opens a public HTTPS link (needed for the phone mic)
# and opens it in your browser. Keep this window open during the demo; Ctrl+C stops it.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
mkdir -p .tools

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m%s\033[0m\n' "$*"; exit 1; }

PY=$(command -v python3 || command -v python || true)
[ -n "$PY" ] || die "Python 3.11+ is missing. Install it from https://www.python.org/downloads/ and run again."
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is needed (found $("$PY" -V))."
command -v npm >/dev/null || die "Node.js is missing. Install the LTS version from https://nodejs.org/ and run again."

[ -f .env ] || { cp .env.example .env; say "Created .env (add GEMINI_API_KEY / SARVAM_API_KEY there for AI replies and real voice; optional)"; }

say "Installing the backend (first run takes a few minutes)"
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -e backend

say "Building the dashboard"
(cd frontend && npm install --silent --no-fund --no-audit && npm run build --silent)

CF=$(command -v cloudflared || true)
if [ -z "$CF" ]; then
  say "Downloading cloudflared (makes the public HTTPS link)"
  base=https://github.com/cloudflare/cloudflared/releases/latest/download
  arch=$(uname -m); case "$arch" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; esac
  if [ "$(uname)" = Darwin ]; then
    curl -fsSL "$base/cloudflared-darwin-$arch.tgz" | tar -xz -C .tools
  else
    curl -fsSL -o .tools/cloudflared "$base/cloudflared-linux-$arch"
  fi
  chmod +x .tools/cloudflared
  CF="$ROOT/.tools/cloudflared"
fi

say "Opening the public link"
: > .tools/tunnel.log
"$CF" tunnel --no-autoupdate --url http://localhost:8000 > .tools/tunnel.log 2>&1 &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null || true' EXIT
URL=""
for _ in $(seq 1 40); do
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' .tools/tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -n "$URL" ]; then
  export PAATTI_PUBLIC_URL="$URL"
  OPEN="$URL"
else
  OPEN="http://localhost:8000"
  printf '\nCould not create the public link (see .tools/tunnel.log). The laptop works on %s,\nbut the phone QR needs the public link.\n' "$OPEN"
fi

printf '\n\033[1;36m================================================================\n'
printf '  Paatti is starting. OPEN THIS LINK:\n\n    %s\n\n' "$OPEN"
printf '  Laptop: dashboard. Phone: click "Phone call via QR" and scan.\n'
printf '  Keep this window open. Press Ctrl+C to stop.\n'
printf '================================================================\033[0m\n\n'

( sleep 6; (command -v open >/dev/null && open "$OPEN") || (command -v xdg-open >/dev/null && xdg-open "$OPEN") || true ) >/dev/null 2>&1 &
cd backend
exec python -m uvicorn paatti.main:app --host 0.0.0.0 --port 8000
