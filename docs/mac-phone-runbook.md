# Mac host and phone runbook

Mac is the active Zenith host while feature work and real-device verification are in progress. Windows packaging and automatic startup are intentionally postponed until the final deployment step.

## Prerequisites

- macOS with Node.js 20.9 or newer and npm.
- Python 3.12 or newer with a working `python3` command.
- Tailscale installed and signed in on the Mac and phone.
- MagicDNS and Tailscale HTTPS enabled for the tailnet when using phone access.

Ollama/Qwen, Google Calendar credentials, Whisper, and TTS remain optional. Zenith Core and the web app can run without them.

## First setup

From the Zenith folder:

~~~zsh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend install
npm --prefix frontend run build:webpack
~~~

The setup only installs Zenith dependencies. It does not install or download Ollama, Qwen, Whisper, or TTS.

## Start Zenith for Mac and phone access

Stop any other Zenith server first. Run:

~~~zsh
zsh ./scripts/start-zenith-mac.sh
~~~

The launcher automatically loads private settings from `.env`, starts the Python API and Next frontend on localhost, configures private Tailscale HTTPS, and prints the exact URL. Keep the terminal open. On first use, copy `.env.example` to `.env` and fill in the Google OAuth values.

On the Mac and phone:

1. Open Tailscale and confirm both devices are connected to the same tailnet.
2. Open the exact `https://...ts.net` URL printed by the launcher.
3. Sign in with the same Zenith account.
4. Create a task on one device and confirm it appears on the other without a refresh.

Do not add `:3000` to the HTTPS URL. Tailscale Serve forwards the private HTTPS address to Zenith's local port 3000.

For local-only Mac development without Tailscale:

~~~zsh
zsh ./scripts/start-zenith-mac.sh --local-only
~~~

Then open http://localhost:3000.

## Optional services

Start Ollama separately when testing the assistant. The Mac launcher defaults to `qwen3:4b`. If more than one model is installed, set the exact model tag before launching Zenith:

~~~zsh
export OLLAMA_URL="http://127.0.0.1:11434"
export OLLAMA_MODEL="another-installed-model"
zsh ./scripts/start-zenith-mac.sh
~~~

For Google Calendar, put the OAuth values in `.env` before launching Zenith. The redirect URI is the printed Tailscale URL plus `/api/calendar/oauth/callback`; leave `GOOGLE_REDIRECT_URI` commented so the launcher can derive it automatically.

One-command Ollama startup remains planned. For now, the launcher reads `OLLAMA_MODEL="qwen3:4b"` from `.env`, but Ollama itself must already be running. The OAuth secret stays outside Git, and Zenith still starts normally when Ollama is unavailable.

The API logs are written to `data/zenith-python-api.log` and `data/zenith-python-api.error.log`.

## Verification order

Verify the must-have flows on the Mac and phone first:

1. Setup/sign-in, task capture, editing, completion, reopening, and deletion.
2. Live task updates in both directions and reconnect catch-up.
3. Calendar connection and upcoming event display.
4. Local Qwen chat, confirmation-gated task changes, and model unload.
5. Planning, memory, PWA installation, reminders, and optional voice controls.

Only after these flows are useful on the real devices should we return to Windows-specific packaging, automatic startup, and service hardening.
