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

log_dir="$HOME/Library/Logs/Bliss"
mkdir -p "$log_dir"
log_file="$log_dir/server.log"
label=local.bliss.server
launchctl remove "$label" 2>/dev/null || true
launchctl submit -l "$label" -o "$log_file" -e "$log_file" -- \
  /bin/bash -c 'cd -- "$1" && export PATH="$2" && exec /usr/bin/caffeinate -dis .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000' \
  bliss "$PWD" "$PATH"

for ((attempt = 0; attempt < 120; attempt++)); do
  if bliss_ready; then
    open "$url"
    exit
  fi
  sleep 0.5
done

launchctl remove "$label" 2>/dev/null || true
printf 'Bliss did not start. See %s for details.\n' "$log_file" >&2
exit 1
