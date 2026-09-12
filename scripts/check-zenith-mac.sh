#!/bin/zsh

set -u

root="${0:A:h:h}"
backend="$root/backend"
frontend="$root/frontend"
python="$backend/.venv/bin/python"
label="com.zenith.manager"
domain="gui/$(id -u)"
passes=0
warnings=0
failures=0

cd "$root"

pass_check() {
  passes=$((passes + 1))
  print "PASS $1"
}

warn_check() {
  warnings=$((warnings + 1))
  print "WARN $1"
}

fail_check() {
  failures=$((failures + 1))
  print "FAIL $1"
}

print "Zenith Mac diagnostics"
print "Project: $root"
print ""

path_node="$(command -v node || true)"
node_path="$path_node"
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
if [[ "$node_supported" == true ]]; then
  export PATH="${node_path:h}:$PATH"
  pass_check "Node.js $node_version ($node_path)"
  if [[ -n "$path_node" && "$path_node" != "$node_path" ]]; then
    print "INFO Older Node.js was first on PATH; diagnostics selected the supported installation."
  fi
elif [[ -z "$node_path" ]]; then
  fail_check "Node.js is not on PATH. Install Node.js 20.9 or newer."
else
  fail_check "Node.js 20.9+ is required; found ${node_version:-an unknown version} ($node_path)"
fi

npm_path="$(command -v npm || true)"
if [[ -n "$npm_path" ]]; then
  pass_check "npm is available ($npm_path)"
else
  fail_check "npm is not on PATH. Install it with Node.js."
fi

if [[ -x "$python" ]]; then
  pass_check "Python environment is available ($python)"
else
  fail_check "Python environment is missing. Run the Mac setup commands from docs/mac-phone-runbook.md."
fi

if [[ -f "$frontend/.next/BUILD_ID" ]]; then
  pass_check "Frontend has a production build"
else
  warn_check "Frontend has not been built yet; the launcher will build it on startup."
fi

config_file="${ZENITH_CONFIG_FILE:-$root/.env}"
if [[ -f "$config_file" ]]; then
  set -a
  source "$config_file"
  set +a
  pass_check "Private configuration file is present"
else
  warn_check "No .env file found; Calendar and explicit local-service settings are unavailable."
fi

api_port="${ZENITH_API_PORT:-8000}"
frontend_port="${ZENITH_FRONTEND_PORT:-3000}"

if [[ -n "${GOOGLE_CLIENT_ID:-}" && -n "${GOOGLE_CLIENT_SECRET:-}" \
      && "${GOOGLE_CLIENT_ID}" != "your-oauth-client-id.apps.googleusercontent.com" \
      && "${GOOGLE_CLIENT_SECRET}" != "your-oauth-client-secret" ]]; then
  pass_check "Google Calendar credentials are configured"
else
  warn_check "Google Calendar credentials are not configured. Calendar remains optional."
fi

if [[ -n "$python" && -x "$python" ]] && curl --silent --fail --max-time 2 "http://127.0.0.1:$api_port/api/health" >/dev/null 2>&1; then
  pass_check "Python API is responding on 127.0.0.1:$api_port"
else
  warn_check "Python API is not responding on 127.0.0.1:$api_port. Start Zenith before testing runtime access."
fi

if curl --silent --fail --max-time 2 "http://127.0.0.1:$frontend_port/" >/dev/null 2>&1; then
  pass_check "Frontend is responding on 127.0.0.1:$frontend_port"
else
  warn_check "Frontend is not responding on 127.0.0.1:$frontend_port. Start Zenith before opening the phone URL."
fi

tailscale_path="$(command -v tailscale || true)"
if [[ -z "$tailscale_path" && -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
  tailscale_path="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi
if [[ -z "$tailscale_path" ]]; then
  warn_check "Tailscale is not installed or not on PATH. Phone access is unavailable."
else
  dns_name=""
  if [[ -x "$python" ]]; then
    dns_name="$($tailscale_path status --json 2>/dev/null | "$python" -c 'import json, sys; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))' 2>/dev/null || true)"
  fi
  if [[ -n "$dns_name" ]]; then
    pass_check "Tailscale is connected"
    print "INFO Private URL: https://$dns_name"
  else
    warn_check "Tailscale is installed but its connected DNS name could not be read."
  fi
  if "$tailscale_path" serve status >/dev/null 2>&1; then
    pass_check "Tailscale Serve status is readable"
  else
    warn_check "Tailscale Serve has no usable configuration yet."
  fi
fi

ollama_url="${OLLAMA_URL:-http://127.0.0.1:11434}"
if [[ "$ollama_url" != http://127.0.0.1:* && "$ollama_url" != http://localhost:* ]]; then
  warn_check "Ollama endpoint is not loopback-only and will be rejected by Zenith ($ollama_url)."
elif ollama_tags="$(curl --silent --fail --max-time 2 "$ollama_url/api/tags" 2>/dev/null)"; then
  pass_check "Ollama is responding locally"
  configured_model="${OLLAMA_MODEL:-qwen3:4b}"
  if [[ -x "$python" ]] && print -r -- "$ollama_tags" | ZENITH_CHECK_MODEL="$configured_model" "$python" -c 'import json, os, sys; selected = os.environ["ZENITH_CHECK_MODEL"]; names = {row.get("name") for row in json.load(sys.stdin).get("models", []) if isinstance(row, dict)}; raise SystemExit(0 if selected in names or (":" not in selected and selected + ":latest" in names) else 1)' 2>/dev/null; then
    pass_check "Configured Ollama model is installed ($configured_model)"
  else
    warn_check "Configured Ollama model is not installed ($configured_model). Zenith will run without the assistant."
  fi
else
  warn_check "Ollama is not responding locally. Tasks and Calendar can still work."
fi

if [[ -x "/usr/bin/say" && -x "/usr/bin/afconvert" ]]; then
  pass_check "Native Mac speech output is available"
else
  warn_check "Native Mac speech output tools are unavailable."
fi

voice_python="$backend/.venv-voice/bin/python"
if [[ -x "$voice_python" ]] && command -v ffmpeg >/dev/null 2>&1 \
   && "$voice_python" -c 'import mlx_whisper' >/dev/null 2>&1; then
  pass_check "MLX Whisper speech input environment is available"
else
  warn_check "MLX Whisper speech input is not installed; run: brew install ffmpeg && zsh ./scripts/setup-zenith-mac-voice.sh"
fi

if /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
  pass_check "Mac login agent is installed"
else
  warn_check "Mac login agent is not installed. Manual startup remains available."
fi

print ""
print "Summary: $passes passed, $warnings warnings, $failures failures"
if (( failures > 0 )); then
  exit 1
fi
