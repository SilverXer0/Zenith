#!/bin/zsh

set -euo pipefail

root="${0:A:h:h}"
backend="$root/backend"
frontend="$root/frontend"
python="$backend/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  print -u2 "Python setup is missing. Create backend/.venv and install backend/requirements.txt first."
  exit 1
fi

npm_path="$(command -v npm || true)"
if [[ -z "$npm_path" ]]; then
  print -u2 "npm was not found. Install Node.js 20.9 or newer."
  exit 1
fi

data_dir="${ZENITH_DATA_DIR:-$root/data}"
if [[ "$data_dir" != /* ]]; then
  data_dir="$root/$data_dir"
fi
mkdir -p "$data_dir"

local_only=false
if [[ "${1:-}" == "--local-only" ]]; then
  local_only=true
fi

allowed_origins=("http://localhost:3000" "http://127.0.0.1:3000")
tailscale_path=""
public_url=""

if [[ "$local_only" == false ]]; then
  tailscale_path="$(command -v tailscale || true)"
  if [[ -z "$tailscale_path" && -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
    tailscale_path="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
  fi
  if [[ -z "$tailscale_path" ]]; then
    print -u2 "Tailscale was not found. Install it, sign in, and run this script again."
    exit 1
  fi

  dns_name="$($tailscale_path status --json | "$python" -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  if [[ -z "$dns_name" ]]; then
    print -u2 "Could not find this Mac's Tailscale DNS name. Enable MagicDNS and try again."
    exit 1
  fi
  public_url="https://$dns_name"
  allowed_origins=("$public_url" "${allowed_origins[@]}")

  print "Configuring private Tailscale HTTPS access..."
  "$tailscale_path" serve --bg --https=443 http://127.0.0.1:3000
  print "Zenith will be available privately at $public_url"
  if [[ -z "${GOOGLE_REDIRECT_URI:-}" ]]; then
    export GOOGLE_REDIRECT_URI="$public_url/api/calendar/oauth/callback"
  fi
  export ZENITH_COOKIE_SECURE=true
else
  unset ZENITH_COOKIE_SECURE
  print "Zenith will be available locally at http://localhost:3000"
fi

export ZENITH_DATA_DIR="$data_dir"
export ZENITH_ALLOWED_ORIGINS="${(j:,:)allowed_origins}"
export ZENITH_API_ORIGIN="http://127.0.0.1:8000"

api_log="$data_dir/zenith-python-api.log"
api_error_log="$data_dir/zenith-python-api.error.log"

print "Starting the Python API..."
"$python" -m uvicorn backend.app:create_app --factory \
  --host 127.0.0.1 --port 8000 \
  >"$api_log" 2>"$api_error_log" &
api_pid=$!

cleanup() {
  if kill -0 "$api_pid" 2>/dev/null; then
    kill "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ready=false
for attempt in {1..60}; do
  if curl --silent --fail --max-time 2 http://127.0.0.1:8000/api/health >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$api_pid" 2>/dev/null; then
    print -u2 "The Python API stopped during startup. See $api_error_log."
    exit 1
  fi
  sleep 0.5
done

if [[ "$ready" == false ]]; then
  print -u2 "The Python API did not become ready. See $api_error_log."
  exit 1
fi

print "Starting the Next frontend. Keep this window open while Zenith is running."
"$npm_path" --prefix "$frontend" run start -- --hostname 127.0.0.1 --port 3000
