#!/bin/zsh

set -euo pipefail

root="${0:A:h:h}"
backend="$root/backend"
frontend="$root/frontend"
python="$backend/.venv/bin/python"

# Load private Mac settings once per launch. The default file is ignored by
# Git; ZENITH_CONFIG_FILE can point to a different local file when needed.
config_file="${ZENITH_CONFIG_FILE:-$root/.env}"
if [[ -f "$config_file" ]]; then
  set -a
  source "$config_file"
  set +a
fi

if [[ ! -x "$python" ]]; then
  print -u2 "Python setup is missing. Create backend/.venv and install backend/requirements.txt first."
  exit 1
fi

node_path="$(command -v node || true)"
if [[ -z "$node_path" ]]; then
  print -u2 "Node.js was not found. Install Node.js 20.9 or newer."
  exit 1
fi

node_version="$($node_path --version 2>/dev/null || true)"
if ! "$node_path" -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major >= 21 || (major === 20 && minor >= 9) ? 0 : 1)' 2>/dev/null; then
  print -u2 "Node.js 20.9 or newer is required. Found ${node_version:-an unknown version} at $node_path."
  print -u2 "Put a newer Node.js installation earlier in PATH, then run Zenith again."
  exit 1
fi

npm_path="$(command -v npm || true)"
if [[ -z "$npm_path" ]]; then
  print -u2 "npm was not found beside the active Node.js installation."
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

# The Mac host uses a small local Qwen model by default. Keep an explicit
# environment override available for testing another installed model.
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:4b}"

ollama_pid=""
ollama_started=false
ollama_path="$(command -v ollama || true)"
if [[ -z "$ollama_path" && -x "/Applications/Ollama.app/Contents/Resources/ollama" ]]; then
  ollama_path="/Applications/Ollama.app/Contents/Resources/ollama"
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
ollama_log="$data_dir/ollama.log"
ollama_error_log="$data_dir/ollama.error.log"

if [[ "${OLLAMA_AUTOSTART:-true}" != "false" && "${OLLAMA_URL:-http://127.0.0.1:11434}" == "http://127.0.0.1:11434" ]]; then
  if curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    print "Ollama is already running."
  elif [[ -z "$ollama_path" ]]; then
    print "Ollama was not found; Zenith will run without the assistant."
  else
    print "Starting Ollama for the local assistant..."
    "$ollama_path" serve >"$ollama_log" 2>"$ollama_error_log" &
    ollama_pid=$!
    for attempt in {1..30}; do
      if curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ollama_started=true
        break
      fi
      if ! kill -0 "$ollama_pid" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
    if [[ "$ollama_started" == false ]]; then
      print "Ollama did not become ready; Zenith will run without the assistant. See $ollama_error_log."
    fi
  fi
elif [[ "${OLLAMA_AUTOSTART:-true}" == "false" ]]; then
  print "Ollama autostart is disabled; Zenith will use the assistant only if Ollama is already running."
fi

ollama_tags="$(curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags 2>/dev/null || true)"
if [[ -n "$ollama_tags" ]]; then
  if print -r -- "$ollama_tags" | "$python" -c 'import json, os, sys; model = os.environ["OLLAMA_MODEL"]; payload = json.load(sys.stdin); raise SystemExit(0 if any(item.get("name") == model for item in payload.get("models", [])) else 1)' 2>/dev/null; then
    print "Ollama is ready with $OLLAMA_MODEL."
  else
    print "Ollama is running, but $OLLAMA_MODEL is not installed; Zenith will run without the assistant."
    print "Install it separately with: ollama pull $OLLAMA_MODEL"
  fi
fi

print "Starting the Python API..."
"$python" -m uvicorn backend.app:create_app --factory \
  --host 127.0.0.1 --port 8000 \
  >"$api_log" 2>"$api_error_log" &
api_pid=$!

cleanup() {
  if kill -0 "$api_pid" 2>/dev/null; then
    kill "$api_pid" 2>/dev/null || true
  fi
  if [[ "$ollama_started" == true && -n "$ollama_pid" ]] && kill -0 "$ollama_pid" 2>/dev/null; then
    kill "$ollama_pid" 2>/dev/null || true
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

print "Building the current Next frontend..."
"$npm_path" --prefix "$frontend" run build:webpack
print "Starting the Next frontend. Keep this window open while Zenith is running."
"$npm_path" --prefix "$frontend" run start -- --hostname 127.0.0.1 --port 3000
