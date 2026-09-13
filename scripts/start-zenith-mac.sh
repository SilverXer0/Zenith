#!/bin/zsh

set -euo pipefail

root="${0:A:h:h}"
backend="$root/backend"
frontend="$root/frontend"
python="$backend/.venv/bin/python"

# LaunchAgent and manual launches should share the same project-relative paths.
cd "$root"

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
node_version=""
node_supported=false
for candidate in "$node_path" "/opt/homebrew/bin/node" "/usr/local/bin/node"; do
  if [[ -z "$candidate" || ! -x "$candidate" ]]; then
    continue
  fi
  if "$candidate" -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major >= 21 || (major === 20 && minor >= 9) ? 0 : 1)' 2>/dev/null; then
    node_path="$candidate"
    node_version="$($candidate --version 2>/dev/null || true)"
    node_supported=true
    break
  fi
  if [[ -z "$node_version" ]]; then
    node_version="$($candidate --version 2>/dev/null || true)"
  fi
done

if [[ "$node_supported" == false ]]; then
  if [[ -z "$node_path" ]]; then
    print -u2 "Node.js was not found. Install Node.js 20.9 or newer."
  else
    print -u2 "Node.js 20.9 or newer is required. Found ${node_version:-an unknown version} at $node_path."
    print -u2 "Install a newer Node.js version or place it earlier in PATH."
  fi
  exit 1
fi

# Keep npm's /usr/bin/env node shebang aligned with the selected runtime.
export PATH="${node_path:h}:$PATH"
npm_path="$(command -v npm || true)"
if [[ -z "$npm_path" ]]; then
  print -u2 "npm was not found beside the active Node.js installation."
  exit 1
fi

api_port="${ZENITH_API_PORT:-8000}"
frontend_port="${ZENITH_FRONTEND_PORT:-3000}"
for port in "$api_port" "$frontend_port"; do
  if [[ "$port" != <-> ]] || (( port < 1 || port > 65535 )); then
    print -u2 "Zenith ports must be whole numbers between 1 and 65535."
    exit 1
  fi
done
if [[ "$api_port" == "$frontend_port" ]]; then
  print -u2 "Zenith API and frontend ports must be different."
  exit 1
fi

data_dir="${ZENITH_DATA_DIR:-$root/data}"
if [[ "$data_dir" != /* ]]; then
  data_dir="$root/$data_dir"
fi
mkdir -p "$data_dir"

api_pid=""
frontend_pid=""
ollama_pid=""
ollama_started=false
lock_dir="$data_dir/.zenith-launcher.lock"
if [[ -d "$lock_dir" ]]; then
  lock_pid="$(<"$lock_dir/pid" 2>/dev/null || true)"
  lock_command=""
  if [[ "$lock_pid" == <-> ]]; then
    lock_command="$(ps -o command= -p "$lock_pid" 2>/dev/null || true)"
  fi
  if [[ "$lock_command" == *start-zenith-mac.sh* ]]; then
    print -u2 "Zenith is already running (launcher PID $lock_pid). Stop that launcher before starting another one."
    exit 1
  fi
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
fi
if ! mkdir "$lock_dir" 2>/dev/null; then
  print -u2 "Zenith could not acquire its launcher lock. Stop any existing Zenith launcher and try again."
  exit 1
fi
print -r -- "$$" >"$lock_dir/pid"

stop_process_tree() {
  local process_id="$1"
  local child_ids=""
  [[ -z "$process_id" ]] && return
  child_ids="$(pgrep -P "$process_id" 2>/dev/null || true)"
  for child_id in ${(f)child_ids}; do
    stop_process_tree "$child_id"
  done
  kill "$process_id" 2>/dev/null || true
}

cleanup() {
  stop_process_tree "$frontend_pid"
  stop_process_tree "$api_pid"
  if [[ "$ollama_started" == true ]]; then
    stop_process_tree "$ollama_pid"
  fi
  if [[ -f "$lock_dir/pid" ]] && [[ "$(<"$lock_dir/pid")" == "$$" ]]; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

local_only=false
if [[ "${1:-}" == "--local-only" ]]; then
  local_only=true
fi

# The Mac host uses a small local Qwen model by default. Keep an explicit
# environment override available for testing another installed model.
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:4b}"

# macOS already ships local speech output. Expose it automatically unless the
# user has explicitly configured another adapter in .env.
if [[ -z "${ZENITH_TTS_COMMAND:-}" && -x "/usr/bin/say" && -x "/usr/bin/afconvert" ]]; then
  export ZENITH_TTS_COMMAND="$python"
  export ZENITH_TTS_ARGS='["scripts/macos-say-speak.py","{text}","{output}"]'
fi

# Speech input remains optional. Only advertise it when the separate MLX
# environment and its decoder dependency are actually installed.
voice_python="$backend/.venv-voice/bin/python"
if [[ -z "${ZENITH_STT_COMMAND:-}" && -x "$voice_python" ]] \
   && command -v ffmpeg >/dev/null 2>&1 \
   && "$voice_python" -c 'import mlx_whisper' >/dev/null 2>&1; then
  export ZENITH_STT_COMMAND="$voice_python"
  export ZENITH_STT_ARGS='["scripts/mlx-whisper-transcribe.py","{input}"]'
fi

ollama_path="$(command -v ollama || true)"
if [[ -z "$ollama_path" && -x "/Applications/Ollama.app/Contents/Resources/ollama" ]]; then
  ollama_path="/Applications/Ollama.app/Contents/Resources/ollama"
fi
ollama_url="${OLLAMA_URL:-http://127.0.0.1:11434}"

allowed_origins=("http://localhost:$frontend_port" "http://127.0.0.1:$frontend_port")
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

  tailscale_status="$($tailscale_path status --json 2>/dev/null || true)"
  dns_name="$(print -r -- "$tailscale_status" | "$python" -c 'import json, sys; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))' 2>/dev/null || true)"
  if [[ -z "$dns_name" ]]; then
    print -u2 "Could not find this Mac's Tailscale DNS name. Confirm Tailscale is connected and MagicDNS is enabled, then try again."
    exit 1
  fi
  public_url="https://$dns_name"
  allowed_origins=("$public_url" "${allowed_origins[@]}")

  print "Configuring private Tailscale HTTPS access..."
  if ! "$tailscale_path" serve --bg --https=443 "http://127.0.0.1:$frontend_port"; then
    print -u2 "Tailscale Serve could not be configured. Enable HTTPS certificates for this tailnet, then run Zenith again."
    exit 1
  fi
  print "Zenith will be available privately at $public_url"
  if [[ -z "${GOOGLE_REDIRECT_URI:-}" ]]; then
    export GOOGLE_REDIRECT_URI="$public_url/api/calendar/oauth/callback"
  fi
  export ZENITH_COOKIE_SECURE=true
else
  unset ZENITH_COOKIE_SECURE
  print "Zenith will be available locally at http://localhost:$frontend_port"
fi

export ZENITH_DATA_DIR="$data_dir"
export ZENITH_ALLOWED_ORIGINS="${(j:,:)allowed_origins}"
export ZENITH_API_ORIGIN="http://127.0.0.1:$api_port"

api_log="$data_dir/zenith-python-api.log"
api_error_log="$data_dir/zenith-python-api.error.log"
ollama_log="$data_dir/ollama.log"
ollama_error_log="$data_dir/ollama.error.log"

if [[ "${OLLAMA_AUTOSTART:-true}" != "false" && "$ollama_url" == "http://127.0.0.1:11434" ]]; then
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

ollama_tags="$(curl --silent --fail --max-time 2 "$ollama_url/api/tags" 2>/dev/null || true)"
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
  --host 127.0.0.1 --port "$api_port" \
  >"$api_log" 2>"$api_error_log" &
api_pid=$!

ready=false
for attempt in {1..60}; do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:$api_port/api/health" >/dev/null; then
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
"$npm_path" --prefix "$frontend" run start -- --hostname 127.0.0.1 --port "$frontend_port" &
frontend_pid=$!
set +e
wait "$frontend_pid"
frontend_exit=$?
set -e
exit "$frontend_exit"
