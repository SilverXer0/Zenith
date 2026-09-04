# Zenith

Zenith is a local-first personal manager MVP: a responsive task list with a small HTTP API and an optional local Ollama boundary.

The working app below still uses Node and the current responsive UI. An isolated [Python/FastAPI development backend](backend/README.md) now starts the migration to the [planned architecture](docs/architecture-migration.md); it is not yet a replacement for the Windows service.

## Run it

Requires Node.js 20 or newer and the `sqlite3` command-line runtime.

```bash
npm start
```

Open http://localhost:3000. Tasks persist in `data/zenith.sqlite` (or `ZENITH_DATA_DIR` if configured). On first start, an existing `data/tasks.json` is migrated into the initial local user and is never required again. Set `ZENITH_SQLITE` if the SQLite executable is not on your PATH.

### Private cross-device access

For the intended Windows home-server setup, install Tailscale on the Windows PC and each device that should access Zenith, then sign in to the same private tailnet. From PowerShell in the Zenith folder, run `scripts\start-zenith-tailscale.ps1`. The helper binds Zenith to the PC's Tailscale address and prints the private URL to open on your Mac or phone. This keeps Zenith off the public internet; do not port-forward port 3000. `ZENITH_HOST` can also be set manually when you need a different bind address.

### Live task updates

Open the same Windows-hosted Zenith URL and sign in to the same Zenith account on each device. Saved task changes appear immediately on the device making them; the authenticated event stream tells other open sessions to fetch the latest tasks. Create, edit, complete, reopen, delete, and confirmed assistant changes all use this path. Ollama is not required.

Zenith catches up when the live stream reconnects, when the page becomes visible again, and when the browser comes back online. A 30-second check while the page is visible provides a fallback if live updates are interrupted or unsupported. The header shows the connection status. Task reads bypass browser caches, and late responses cannot overwrite a newer save. Failed saves keep the draft; a successful save followed by a failed refresh is not reported as an unsuccessful save.

This updates the task list, counters, daily focus, and completion summary without reloading the page or clearing an unsaved capture draft. It is not offline editing or closed-app push: browsers may suspend background pages, and the Windows host must remain running and reachable. Calendar, memory, morning briefing, and weekly-plan panels still have their existing separate refresh behavior.

After updating the Windows checkout, restart Zenith and reload each device once to load the new client code. Verify on the real devices:

1. Open the same server URL on Windows and your Mac/phone; check for `Live sync connected`.
2. Capture a task on one device. It should appear on both without toggling completed tasks or refreshing.
3. Edit, complete, reopen, and delete it from the other device; check the first device each time.
4. Disconnect the phone temporarily, add a task on Windows, then reconnect and return to Zenith. The phone should catch up automatically.

### Verification

`npm test` runs the API integration and dependency-free client regression tests using disposable data. They cover separate authenticated live streams, immediate rendering, failed saves/refreshes, out-of-order responses, reconnects, fallback refresh, and logout cleanup.

For an optional real-browser check, run `node scripts/verify-task-sync.mjs` in a development environment with Playwright and Chrome installed. `ZENITH_PLAYWRIGHT_MODULE` can point to an existing Playwright module entry file; `ZENITH_BROWSER_CHANNEL` defaults to `chrome`. The check creates separate desktop and phone-sized sessions against a temporary SQLite database, tests bidirectional changes, interrupted connections and delayed/failed reads, and cleans up afterward. It keeps Ollama offline. Playwright is only a testing tool, not a Zenith runtime dependency. This check disables service workers for network fault injection and does not replace verification on actual devices over Tailscale.

### PWA installation

Zenith includes a web-app manifest and service worker. The static app shell can be installed as a PWA when served from a secure context. `http://localhost:3000` is suitable for local browser testing; a raw `http://100.x.x.x:3000` Tailscale address is usable across devices but browsers generally require HTTPS for PWA installation. Tailscale HTTPS/Serve setup is the next networking step. The service worker never caches `/api/` responses or private task data.

To use private HTTPS on the Windows home server, run `scripts\start-zenith-tailscale-https.ps1`. Tailscale Serve proxies the local Zenith port over the PC's private `https://...ts.net` address and leaves Zenith itself bound to localhost. The helper also sets the Google OAuth callback to that HTTPS address for the current session unless `GOOGLE_REDIRECT_URI` is already set. MagicDNS and HTTPS certificates must be enabled for the tailnet; Tailscale may open an approval page the first time. Use Tailscale Serve, not Funnel, for Zenith.

## API surface

- `GET /api/tasks`, `POST /api/tasks`
- `PATCH /api/tasks/:id`, `DELETE /api/tasks/:id`
- `GET /api/memory`, `POST /api/memory`
- `PATCH /api/memory/:id`, `DELETE /api/memory/:id`
- `GET /api/briefing` (read-only daily task focus)
- `GET /api/briefing/morning` (read-only morning summary)
- `GET /api/summaries/daily` (read-only completion summary)
- `GET /api/weekly-plan` (read-only seven-day task and calendar plan)
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
- `POST /api/voice/speak` (authenticated, optional local text-to-speech)
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

For local speech output, the included `scripts\pyttsx3-speak.py` adapter uses the Windows local speech engine and writes a temporary WAV file. Configure it alongside speech-to-text:

```powershell
$env:ZENITH_TTS_COMMAND=".venv\Scripts\python.exe"
$env:ZENITH_TTS_ARGS='["scripts\pyttsx3-speak.py","{text}","{output}"]'
```

Assistant replies show a `Speak reply` control when text-to-speech is configured. Audio is generated on demand and is not stored by Zenith.

### Persistent context

The `Help Zenith remember` panel stores short, user-entered preferences, routines, and project context in the local SQLite database. The signed-in assistant receives the latest context notes alongside tasks and Calendar events. Context is never created automatically, and each note can be edited or deleted from the panel.

### Daily briefing

The `Today’s focus` panel is generated locally from open tasks. It surfaces overdue and due-today counts and ranks up to five tasks by due date and priority. It does not require Ollama, so the basic planning view remains available while the local model is unloaded or offline.

The `Morning briefing` panel summarizes urgent work, today’s due tasks, the next few days of scheduled tasks, and today’s Calendar events when connected. It is generated on demand from local data and does not require Ollama; scheduled delivery and model-written narrative can be added later.

The `What got done` panel records completed-task events in SQLite and shows what was completed and captured during the current local day. Reopening a task does not erase earlier completion events, so the summary remains useful as a lightweight daily history.

The `Shape the week` panel groups open tasks by due date, separates unscheduled work, and reports Google Calendar events when Calendar is connected. It is read-only and can be refreshed without involving the local model.

### Local task reminders

When Zenith is open in a supported secure browser context, choose `Enable reminders` to allow browser notifications for open tasks due today or overdue. Each task is announced at most once per browser per day, and the reminder content stays on that device. This browser-local layer requires Zenith to remain open; background notifications while it is closed will need a later push-notification service.
