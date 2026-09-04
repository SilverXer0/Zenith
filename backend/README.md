# Python backend migration — development only

This is the first executable step toward Zenith's planned **Python/FastAPI backend**. The working application still starts with `npm start`; the Windows/Tailscale helpers still start Node. Do not replace that service with this backend yet.

## What this slice does

- Health and local account setup/sign-in/sign-out/session APIs.
- Authenticated, per-user task CRUD with the existing camel-case response format.
- Authenticated live task events, with per-user isolation and session-aware stream cleanup.
- User-managed persistent context CRUD, stored per account.
- Read-only daily focus, morning task view, seven-day plan, and completion-summary APIs.
- Optional read-only Google Calendar OAuth, event reads, refreshable sessions, and planning context.
- Optional local Ollama chat with owner-only task, context, and Calendar data; confirmation-gated task proposals and model unload.
- SQLite through Python's standard library, without the external SQLite CLI.
- Compatibility with the existing SQLite file, Node scrypt passphrases and hashed session tokens.
- One-time legacy `tasks.json` import and upgrades of older user/task tables.
- Atomic task writes and completion-event recording; concurrent completions record one transition.
- Same-origin write checks, HttpOnly/SameSite cookies, optional Secure cookies, and uncached API responses.

Core startup, tasks, context, Calendar, and deterministic planning neither connect to nor load Ollama. The assistant is optional and fails independently when Ollama is unavailable. This development backend does **not** yet implement voice endpoints or serve the current UI. These are missing capabilities, not disabled implementations or feature parity.

## Optional Google Calendar contract

The Python API now matches the existing read-only Calendar surface: authenticated status, connect, upcoming-event and disconnect routes, plus the public OAuth callback protected by a random, one-time state that expires after ten minutes. Authorization requests offline access and only `https://www.googleapis.com/auth/calendar.readonly`. Access tokens are refreshed when close to expiry and once after a Calendar 401 response. A failed or malformed remote response produces a generic local error and never exposes Google response bodies or credentials.

Primary-calendar event reads expand recurring instances, order by start time, normalize the requested range to UTC and follow pagination while returning at most 100 events. The default range is the current UTC day through seven days later. Explicit `start` and `end` values must be `YYYY-MM-DD` dates or timezone-aware timestamps, and the end must be later than the start. Morning and weekly planning include Calendar events when they are available; a Calendar outage does not prevent task planning.

Calendar remains optional. If client settings are absent, Core starts normally, status reports `configured: false`, no Google request is attempted, and explicit connect/event requests return 409. A retained account can still report `connected: true` while `available` is false in planning. OAuth states, access tokens and refresh tokens are account-scoped in the local SQLite database and remain compatible with the Node schema. The database is local but is not encrypted by the Zenith passphrase, so protect the Windows account and database backup like other private files.

For development, set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The callback defaults to `http://127.0.0.1:8000/api/calendar/oauth/callback`; set `GOOGLE_REDIRECT_URI` to the exact authorized HTTPS callback for Tailscale or a frontend proxy. `GOOGLE_TOKEN_URL` and `GOOGLE_CALENDAR_URL` exist only for isolated compatibility tests and should retain their defaults in normal use. The working Node deployment and its current port-3000 callback are unchanged by this migration slice.

## Local assistant contract

`GET /api/assistant/status` is a public readiness check. Authenticated `POST /api/assistant/chat` sends the signed-in owner's tasks, user-managed context notes, and available seven-day Calendar context to one installed Ollama model. Chat history is limited to the latest eight entries, individual inputs and replies are bounded, and context sections have fixed size budgets. The data sections are explicitly marked as untrusted in the system prompt so instructions embedded in a task, note, or Calendar title are not treated as commands.

The model receives a JSON schema and can return up to five task proposals. The server then discards unknown action types, unknown fields, invalid dates, and task IDs that do not belong to the signed-in owner. Chat itself is read-only. The client must show proposals and make a separate authenticated `POST /api/assistant/actions` request after the user confirms. The server validates them again against current data, applies the whole group in one SQLite transaction, records completion transitions, and emits one `tasks_changed` notice only after commit. Invalid, stale, mixed-owner, duplicate-target, or rolled-back groups change nothing.

`OLLAMA_URL` defaults to `http://127.0.0.1:11434` and this adapter rejects credentials, paths, and every non-loopback host. Ollama requests bypass environment proxies and reject redirects so private context cannot be forwarded away from that endpoint. `OLLAMA_MODEL` may select an installed model; otherwise the first installed non-cloud-tagged model is used. Explicit `:cloud` models are rejected. Because Ollama can otherwise proxy cloud models through its local API, set `OLLAMA_NO_CLOUD=1` for the Ollama service and restart it on the final Windows deployment. That service setting is the defense against custom aliases or future cloud routing that cannot be inferred from a model name alone.

`OLLAMA_KEEP_ALIVE` defaults to `5m`; set it to `0` to ask Ollama to unload after every answer. Authenticated `POST /api/assistant/unload` sends `keep_alive: 0` for a currently running model and never loads an idle model just to unload it. Task, authentication, planning, and Calendar routes stay operational when Ollama is stopped or the model is not loaded. The automated tests use a disposable local Ollama simulator; real Qwen output quality, GPU loading, and VRAM release still require a Windows-device check before cutover.

## Context and task planning contract

The authenticated context routes match the Node API: `GET/POST /api/memory` and `PATCH/DELETE /api/memory/{id}`. Notes are scoped to their owner, retain their ID and creation time across edits/restarts, and are never created automatically. Content is limited to 2,000 characters and categories to 40. Context changes do not emit `tasks_changed`; the future frontend must refresh that separate resource after context writes.

The read-only planning routes are `GET /api/briefing`, `GET /api/briefing/morning`, `GET /api/weekly-plan`, and `GET /api/summaries/daily`. They preserve the prototype response shapes and deterministic ordering. Daily focus prefers overdue tasks, then today's tasks, dates and priorities, with recent updates breaking ties. Morning includes overdue, due-today and the next three days. Weekly planning returns seven calendar dates plus undated tasks. Completed tasks are excluded from open-task projections.

Daily summaries use recorded completion events, so reopening, renaming or deleting the current task cannot rewrite what was completed. Start boundaries are inclusive and end boundaries exclusive. Current open/created counts are a current task snapshot; they are not a reconstructed historical end-of-day state. The history and task snapshot are read in one SQLite transaction.

Clients should pass a real `YYYY-MM-DD` date. If omitted, UTC today is used for compatibility; the current browser supplies its local date. Daily summary's `offset` is a whole number from -840 to 840 and follows `Date.getTimezoneOffset()` (positive west of UTC). This fixed 24-hour compatibility window is not a complete timezone/DST model. IANA-timezone and calendar-aware scheduling remain later planning gates.

## Live task event contract

`GET /api/events` requires the normal `zenith_session` cookie and returns `text/event-stream`. It matches the current client's event names:

- `ready` with `{}` data on every connection, including reconnects. Fetch `/api/tasks` to catch up on changes missed while disconnected.
- `tasks_changed` with `{}` data after a successful task create, edit, completion/reopening, or deletion commits. Fetch the latest task snapshot; the event is an invalidation notice, not a task payload.
- An idle heartbeat comment every 15 seconds and a one-second browser reconnect hint.

Streams only receive their user's notices. Signing out closes streams for that session without signing out another device. Before every event/heartbeat, the database session is rechecked; expiry, external revocation (including passphrase recovery), or unavailable auth storage closes the stream. Requests with invalid sessions receive 401 before opening a stream. No task text, cookie, or session hash is included in event data.

Each subscriber keeps at most one pending invalidation and one scheduled wake, so a slow device does not accumulate an event backlog. Blocked response sends have a ten-second timeout; disconnect/cancellation/shutdown releases subscriber state. Failed or rolled-back writes emit no change event. No Ollama or extra service is needed.

The event broker is **single-process**. Use the default single Uvicorn worker; do not add `--workers` or run a second backend against the same live database. External database edits do not publish task events. There is no durable event log or `Last-Event-ID` replay; recovery uses `ready` plus a fresh task read. Confirmed assistant mutations use the same post-commit invalidation path.

## Development setup

Run these commands from the repository root. Use Python 3.12 (the version tested locally). For tests, keep Node 20+ and the SQLite CLI available for the existing-backend compatibility checks.

Windows PowerShell, when you are ready to work on the Python development path:

```powershell
py -3.12 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-test.txt
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
.\backend\.venv\Scripts\python.exe -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

macOS/Linux:

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-test.txt
backend/.venv/bin/python -m unittest discover -s backend/tests -v
backend/.venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

There is no virtual-environment activation step, avoiding PowerShell script-policy problems. `requirements.txt` contains runtime dependencies; `requirements-test.txt` also installs the test client. Dependencies are pinned to the versions verified for this slice. `ZENITH_NODE` can specify a Node executable for compatibility tests if the default `node` is older than version 20.

Open `http://127.0.0.1:8000/api/health` to verify startup. `/` has no frontend yet. The machine-readable API description is `/openapi.json`; externally hosted documentation assets are not loaded. This is loopback-only development startup, not a Tailscale deployment instruction.

## Data and rollback precautions

The **default** Python directory is `data-python-dev/`, not the existing Node `data/`. `ZENITH_DATA_DIR` overrides that default. Check or clear any inherited `ZENITH_DATA_DIR` before starting: an override can point at real data. The app factory does not open a database until server startup.

For migration testing, stop Node first, back up the database using SQLite's backup facility (including any journal/WAL considerations), and use a **copy** in a separate test directory. Do not run both servers against the live database: their event streams are not shared. This slice does not create an automatic backup or switch the production service.

Existing rows, IDs, timestamps, unrelated tables, passphrase hashes and valid sessions are retained. Upgrading a pre-passphrase users table invalidates its unsafe old sessions, as the Node migration does. Completion times that were never recorded stay unknown; no historical completion events are fabricated. Legacy JSON is not deleted. Malformed JSON stops startup without a migration marker, allowing correction and retry.

Task input is intentionally stricter than the prototype: dates must be real `YYYY-MM-DD` values, completion must be boolean, unknown fields are rejected, and title/notes/project limits match the UI. Display names are limited to 80 characters and passphrases require at least eight characters, as in the Node account setup. Errors remain `{ "error": "..." }` responses and do not echo credentials. `ZENITH_COOKIE_SECURE=true` enables Secure cookies for a future verified HTTPS deployment; leave it unset for loopback HTTP development.

## Verification and remaining gates

The Python suite covers actual FastAPI requests, server startup, independent sessions, expiry, user isolation, validation, transactions, concurrency, restart persistence, legacy JSON, old SQLite schemas, and real Node → Python → Node compatibility. The live-event tests use real HTTP streams against temporary Uvicorn servers, verifying task changes across two sessions, user isolation, failed-write silence, reconnect snapshots, confirmed assistant changes, and logout/expiry/revocation. Separate broker/ASGI tests cover 10,000-change bursts, heartbeat checks, blocked sends, cancellation and cleanup. Context/planning tests cover CRUD, account isolation, restart persistence, ordering, failed-write rollback, date/offset boundaries, completion-history retention, calendar-unavailable state, read-only operation without Ollama, and exact Node API comparisons. Calendar tests use a disposable local service to verify OAuth state ownership/expiry/replay protection, code exchange, token refresh and retry, pagination, event projection, remote failures, schema upgrades, disconnect, assistant/planning context, and Python → Node compatibility. Assistant tests use a disposable loopback Ollama simulator to verify owner-only context, bounded inputs/outputs, structured proposal filtering, no pre-confirmation writes, atomic confirmation, task-event publication, offline Core independence, local-endpoint restrictions, explicit-cloud rejection, model discovery, failures, and unload behavior. All fixtures use disposable directories; real task data and credentials are not part of the tests. The Node suite remains `npm test`.

These checks do not prove a complete browser UI against Python, Windows packaging, real Tailscale access, actual Google OAuth/Calendar reads, real Qwen inference or VRAM release, microphone/speaker operation, timezone-aware schedule planning, or full API parity. The next backend parity gate is the local voice API, followed by the responsive Next.js/TypeScript/Tailwind frontend and real-device cutover checks. See [the migration plan](../docs/architecture-migration.md) for the full scope.

Implementation references: [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Google Calendar scopes](https://developers.google.com/workspace/calendar/api/auth), [Google event listing](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [Ollama local API](https://docs.ollama.com/api/introduction), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [Ollama model unloading and local-only setting](https://docs.ollama.com/faq), [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [SSE and the browser event contract](https://fastapi.tiangolo.com/tutorial/server-sent-events/), and [Python SQLite transactions](https://docs.python.org/3/library/sqlite3.html).
