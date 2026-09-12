# Mac host and phone runbook

Mac is the active Zenith host while feature work and real-device verification are in progress. Windows packaging and automatic startup are intentionally postponed until the final deployment step. Optional macOS login startup is available for the active host.

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

The setup only installs Zenith dependencies. It does not install or download Ollama, Qwen, Whisper, or TTS. macOS's built-in `say` and `afconvert` tools can provide local speech output without another package.

## Start Zenith for Mac and phone access

Stop any other Zenith server first. Run:

~~~zsh
zsh ./scripts/start-zenith-mac.sh
~~~

The launcher automatically loads private settings from `.env`, checks that Node.js 20.9+ is active, starts the Python API and Next frontend on localhost, rebuilds the current frontend, configures private Tailscale HTTPS, enables built-in Mac speech output, and prints the exact URL. Keep the terminal open. On first use, copy `.env.example` to `.env` and fill in the Google OAuth values.

On the Mac and phone:

1. Open Tailscale and confirm both devices are connected to the same tailnet.
2. Open the exact `https://...ts.net` URL printed by the launcher.
3. Sign in with the same Zenith account.
4. Create a task on one device and confirm it appears on the other without a refresh.

Do not add `:3000` to the HTTPS URL. Tailscale Serve forwards the private HTTPS address to Zenith's local port 3000.

## Optional start at Mac login

After manually stopping any running Zenith launcher, install the optional macOS LaunchAgent:

~~~zsh
zsh ./scripts/install-zenith-mac-agent.sh
~~~

It starts Zenith at login, restarts it after an unexpected exit, and uses the same `.env`, Tailscale HTTPS, and optional Ollama settings as the manual launcher. The launcher and API logs remain in `data/`. Do not also run the manual launcher while the agent is active, because both would compete for Zenith's ports.

To remove automatic startup:

~~~zsh
zsh ./scripts/install-zenith-mac-agent.sh --remove
~~~

For local-only Mac development without Tailscale:

~~~zsh
zsh ./scripts/start-zenith-mac.sh --local-only
~~~

Then open http://localhost:3000.

## Optional services

The Mac launcher now reuses Ollama if it is running, or starts its local service automatically when the `ollama` command is installed. It defaults to `qwen3:4b` and checks that model is installed without downloading anything. The model loads into memory on the first assistant request. If Ollama or the model is unavailable, Zenith Core still starts normally. When you stop Zenith, the launcher stops only the Ollama process it started itself; an already-running Ollama app/service is left alone.

If more than one model is installed, set the exact model tag before launching Zenith:

~~~zsh
export OLLAMA_URL="http://127.0.0.1:11434"
export OLLAMA_MODEL="another-installed-model"
zsh ./scripts/start-zenith-mac.sh
~~~

Set `OLLAMA_AUTOSTART=false` in `.env` to disable this launcher behavior. To install a missing model, run `ollama pull qwen3:4b` separately once.

For Google Calendar, put the OAuth values in `.env` before launching Zenith. The redirect URI is the printed Tailscale URL plus `/api/calendar/oauth/callback`; leave `GOOGLE_REDIRECT_URI` commented so the launcher can derive it automatically.

Mac speech output is enabled automatically with the built-in `say` and `afconvert` tools. To override it with another local adapter, add these lines to `.env`:

~~~text
ZENITH_TTS_COMMAND=/absolute/path/to/Zenith/backend/.venv/bin/python
ZENITH_TTS_ARGS=["scripts/macos-say-speak.py","{text}","{output}"]
~~~

Replace the path with the absolute path to your Zenith folder. Restart the launcher afterward. Zenith will show a `Speak reply` control when the adapter is available.

For speech-to-text on the M1 Mac, install the Apple-Silicon MLX Whisper adapter in its own environment:

~~~zsh
python3 -m venv backend/.venv-voice
backend/.venv-voice/bin/python -m pip install mlx-whisper
brew install ffmpeg
~~~

Then add `ZENITH_STT_COMMAND=/Users/sharan/Documents/Zenith/backend/.venv-voice/bin/python`, `ZENITH_STT_ARGS=["scripts/mlx-whisper-transcribe.py","{input}"]`, and `ZENITH_WHISPER_MODEL=mlx-community/whisper-base-mlx` to `.env`. The launcher detects this setup automatically; the first transcription downloads the model and transcription afterward remains local. Restart the launcher after changing `.env`.

The launcher’s Ollama startup is intentionally local-only. The OAuth secret stays outside Git, and Zenith still starts normally when Ollama is unavailable.

The API logs are written to `data/zenith-python-api.log` and `data/zenith-python-api.error.log`.

## Verification order

Verify the must-have flows on the Mac and phone first:

1. Setup/sign-in, task capture, editing, completion, reopening, and deletion.
2. Live task updates in both directions and reconnect catch-up.
3. Calendar connection and upcoming event display. Connect a second Google account, rename it, pause/include it, and confirm events from enabled calendars appear together.
4. Local Qwen chat, confirmation-gated task changes, and model unload.
5. Planning, memory, PWA installation, reminders, and optional voice controls.

Only after these flows are useful on the real devices should we return to Windows-specific packaging, automatic startup, and service hardening.
