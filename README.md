# Zenith

Zenith is a local-first personal manager MVP: a responsive task list with a small HTTP API and an optional local Ollama boundary.

## Run it

Requires Node.js 20 or newer and the `sqlite3` command-line runtime.

```bash
npm start
```

Open http://localhost:3000. Tasks persist in `data/zenith.sqlite` (or `ZENITH_DATA_DIR` if configured). On first start, an existing `data/tasks.json` is migrated into the initial local user and is never required again. Set `ZENITH_SQLITE` if the SQLite executable is not on your PATH.

### Private cross-device access

For the intended Windows home-server setup, install Tailscale on the Windows PC and each device that should access Zenith, then sign in to the same private tailnet. From PowerShell in the Zenith folder, run `scripts\start-zenith-tailscale.ps1`. The helper binds Zenith to the PC's Tailscale address and prints the private URL to open on your Mac or phone. This keeps Zenith off the public internet; do not port-forward port 3000. `ZENITH_HOST` can also be set manually when you need a different bind address.

### PWA installation

Zenith includes a web-app manifest and service worker. The static app shell can be installed as a PWA when served from a secure context. `http://localhost:3000` is suitable for local browser testing; a raw `http://100.x.x.x:3000` Tailscale address is usable across devices but browsers generally require HTTPS for PWA installation. Tailscale HTTPS/Serve setup is the next networking step. The service worker never caches `/api/` responses or private task data.

To use private HTTPS on the Windows home server, run `scripts\start-zenith-tailscale-https.ps1`. Tailscale Serve proxies the local Zenith port over the PC's private `https://...ts.net` address and leaves Zenith itself bound to localhost. The helper also sets the Google OAuth callback to that HTTPS address for the current session unless `GOOGLE_REDIRECT_URI` is already set. MagicDNS and HTTPS certificates must be enabled for the tailnet; Tailscale may open an approval page the first time. Use Tailscale Serve, not Funnel, for Zenith.

## API surface

- `GET /api/tasks`, `POST /api/tasks`
- `PATCH /api/tasks/:id`, `DELETE /api/tasks/:id`
- `GET /api/auth/status`
- `POST /api/auth/setup` (first launch), `POST /api/auth/session`, `GET /api/auth/session`, `DELETE /api/auth/session`
- `GET /api/health`
- `GET /api/events` (authenticated task-change stream)
- `GET /api/calendar/status`, `GET /api/calendar/connect`
- `GET /api/calendar/oauth/callback` (Google OAuth callback)
- `GET /api/calendar/events` (authenticated, read-only upcoming events)
- `DELETE /api/calendar/connection`
- `GET /api/voice/status`
- `POST /api/voice/transcribe` (authenticated, optional local speech-to-text)
- `GET /api/assistant/status`
- `POST /api/assistant/chat` (authenticated, read-only task context)
- `POST /api/assistant/actions` (authenticated, applies user-confirmed proposals)
- `POST /api/assistant/unload` (authenticated, releases the configured model from VRAM)

On first launch, set a local display name and passphrase (at least 8 characters). The passphrase is stored only as a salted scrypt hash. Subsequent access requires the credentials and creates a 30-day HttpOnly `zenith_session` cookie; each device can keep its own session. Signing out removes only the current session, while changing the passphrase invalidates all sessions. Task endpoints require this cookie and remain scoped to the account. When Ollama is running at `OLLAMA_URL` (default `http://127.0.0.1:11434`), the assistant chat endpoint uses `OLLAMA_MODEL` or the first installed model. It sends only the signed-in user's task context and returns read-only replies plus optional structured task proposals. Proposals are never applied automatically; the user must confirm them, and the server validates them again before writing. Use `OLLAMA_KEEP_ALIVE=0` for automatic unload after each response, or the authenticated release control to free the currently loaded model on demand. The rest of the app runs fully without Ollama.

### Forgotten passphrase

If you forget the local account credentials, stop Zenith and run this from the project folder on the Windows PC:

```powershell
node scripts\reset-zenith-passphrase.js
```

The local tool displays the existing account name, asks for an explicit `RESET` confirmation, changes only the passphrase, and invalidates old sessions. It does not modify tasks. The new passphrase is entered visibly in the PowerShell window, so close the window afterward.

### Google Calendar

Calendar is optional. To enable it, create a Google Cloud OAuth web application with the Google Calendar API enabled, then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on the Windows Zenith server. `GOOGLE_REDIRECT_URI` can be set when the callback must use a specific address; otherwise the local callback defaults to `http://127.0.0.1:3000/api/calendar/oauth/callback`, so start authorization from the Windows PC. Keep the client secret outside the repository. Zenith requests the read-only Calendar scope, stores the connection in SQLite, refreshes access tokens as needed, and exposes only upcoming event details to the UI. If these settings are absent, tasks and the rest of Zenith continue working normally.

### Local voice input

Voice input is optional and stays on the Zenith server. The included `scripts\whisper-transcribe.py` adapter uses `faster-whisper`; it accepts the browser's audio file and prints only the transcript. The server invokes an external command using `ZENITH_STT_COMMAND` and a JSON array in `ZENITH_STT_ARGS`; the array must contain `{input}` where the temporary recording path should be inserted. For the included adapter on Windows, create a Python virtual environment, install `faster-whisper`, then set:

```powershell
$env:ZENITH_STT_COMMAND=".venv\Scripts\python.exe"
$env:ZENITH_STT_ARGS='["scripts\whisper-transcribe.py","{input}"]'
```

The adapter defaults to the `base.en` model on CUDA with FP16. Set `ZENITH_WHISPER_MODEL`, `ZENITH_WHISPER_DEVICE`, `ZENITH_WHISPER_COMPUTE_TYPE`, or `ZENITH_WHISPER_LANGUAGE` to adjust it. The first transcription downloads the selected model; subsequent transcription is local. `faster-whisper` supports GPU execution with `device="cuda"` and `compute_type="float16"`; its current Windows GPU requirements are documented in the project's [official README](https://github.com/SYSTRAN/faster-whisper#requirements).
