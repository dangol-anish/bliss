#!/bin/bash
set -euo pipefail

cd -- "$(dirname -- "$0")"
export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"

url=http://127.0.0.1:8000/
bliss_ready() {
  curl -fs --max-time 1 "$url" | grep -Fq '<title>Bliss</title>'
}

if bliss_ready; then
  open "$url"
  exit
fi

if [[ ! -x .venv/bin/uvicorn ]]; then
  if ! command -v uv >/dev/null; then
    echo "Install uv first, then open this launcher again." >&2
    exit 1
  fi
  uv sync --locked --extra detection
fi

if [[ ! -s frontend/dist/downloader.js || ! -s frontend/dist/app.css ]]; then
  npm ci
  npm run build
fi

.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
trap 'exit 0' INT TERM HUP

for ((attempt = 0; attempt < 120; attempt++)); do
  if ! kill -0 "$server" 2>/dev/null; then
    wait "$server"
    exit 1
  fi
  if bliss_ready; then
    open "$url"
    echo "Bliss is running. Keep this Terminal window open while using it."
    wait "$server"
    exit
  fi
  sleep 0.5
done

echo "Bliss did not start. Check the server output above." >&2
exit 1
