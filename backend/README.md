# Python backend migration — development only

This is the first executable step toward Zenith's planned **Python/FastAPI backend**. The working application still starts with `npm start`; the Windows/Tailscale helpers still start Node. Do not replace that service with this backend yet.

## What this slice does

- Health and local account setup/sign-in/sign-out/session APIs.
- Authenticated, per-user task CRUD with the existing camel-case response format.
- Authenticated live task events, with per-user isolation and session-aware stream cleanup.
- User-managed persistent context CRUD, stored per account.
- Read-only daily focus, morning task view, seven-day plan, and completion-summary APIs.
- SQLite through Python's standard library, without the external SQLite CLI.
- Compatibility with the existing SQLite file, Node scrypt passphrases and hashed session tokens.
- One-time legacy `tasks.json` import and upgrades of older user/task tables.
- Atomic task writes and completion-event recording; concurrent completions record one transition.
- Same-origin write checks, HttpOnly/SameSite cookies, optional Secure cookies, and uncached API responses.

Core startup, context and planning operations neither connect to nor load Ollama. This development backend does **not** yet implement assistant, Google Calendar event access/OAuth, or voice endpoints. It does not serve the current UI. These are missing capabilities, not disabled implementations or feature parity.

## Context and task planning contract

The authenticated context routes match the Node API: `GET/POST /api/memory` and `PATCH/DELETE /api/memory/{id}`. Notes are scoped to their owner, retain their ID and creation time across edits/restarts, and are never created automatically. Content is limited to 2,000 characters and categories to 40. Context changes do not emit `tasks_changed`; the future frontend must refresh that separate resource after context writes.

The read-only planning routes are `GET /api/briefing`, `GET /api/briefing/morning`, `GET /api/weekly-plan`, and `GET /api/summaries/daily`. They preserve the prototype response shapes and deterministic ordering. Daily focus prefers overdue tasks, then today's tasks, dates and priorities, with recent updates breaking ties. Morning includes overdue, due-today and the next three days. Weekly planning returns seven calendar dates plus undated tasks. Completed tasks are excluded from open-task projections.

Daily summaries use recorded completion events, so reopening, renaming or deleting the current task cannot rewrite what was completed. Start boundaries are inclusive and end boundaries exclusive. Current open/created counts are a current task snapshot; they are not a reconstructed historical end-of-day state. The history and task snapshot are read in one SQLite transaction.

Clients should pass a real `YYYY-MM-DD` date. If omitted, UTC today is used for compatibility; the current browser supplies its local date. Daily summary's `offset` is a whole number from -840 to 840 and follows `Date.getTimezoneOffset()` (positive west of UTC). This fixed 24-hour compatibility window is not a complete timezone/DST model. IANA-timezone and calendar-aware scheduling remain later planning gates.

Until the Calendar port lands, morning/weekly responses report preserved account state honestly: `connected` reflects a retained account row, while `available` is false and `events` is empty. No Google request is attempted. That is a deliberate migration boundary, not successful Calendar integration.

## Live task event contract

`GET /api/events` requires the normal `zenith_session` cookie and returns `text/event-stream`. It matches the current client's event names:

- `ready` with `{}` data on every connection, including reconnects. Fetch `/api/tasks` to catch up on changes missed while disconnected.
- `tasks_changed` with `{}` data after a successful task create, edit, completion/reopening, or deletion commits. Fetch the latest task snapshot; the event is an invalidation notice, not a task payload.
- An idle heartbeat comment every 15 seconds and a one-second browser reconnect hint.

Streams only receive their user's notices. Signing out closes streams for that session without signing out another device. Before every event/heartbeat, the database session is rechecked; expiry, external revocation (including passphrase recovery), or unavailable auth storage closes the stream. Requests with invalid sessions receive 401 before opening a stream. No task text, cookie, or session hash is included in event data.

Each subscriber keeps at most one pending invalidation and one scheduled wake, so a slow device does not accumulate an event backlog. Blocked response sends have a ten-second timeout; disconnect/cancellation/shutdown releases subscriber state. Failed or rolled-back writes emit no change event. No Ollama or extra service is needed.

The event broker is **single-process**. Use the default single Uvicorn worker; do not add `--workers` or run a second backend against the same live database. External database edits do not publish task events. There is no durable event log or `Last-Event-ID` replay; recovery uses `ready` plus a fresh task read. Confirmed assistant mutations will use this path when that API is ported.

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

The Python suite covers actual FastAPI requests, server startup, independent sessions, expiry, user isolation, validation, transactions, concurrency, restart persistence, legacy JSON, old SQLite schemas, and real Node → Python → Node compatibility. The live-event tests use real HTTP streams against temporary Uvicorn servers, verifying task changes across two sessions, user isolation, failed-write silence, reconnect snapshots, and logout/expiry/revocation. Separate broker/ASGI tests cover 10,000-change bursts, heartbeat checks, blocked sends, cancellation and cleanup. Context/planning tests cover CRUD, account isolation, restart persistence, ordering, failed-write rollback, date/offset boundaries, completion-history retention, calendar-unavailable state, read-only operation without Ollama, and exact Node API comparisons. All fixtures use disposable directories; real task data is not part of the tests. It also checks that pre-existing context and calendar-table data are not removed by migration. The Node suite remains `npm test`.

These checks do not prove a complete browser UI against Python, Windows packaging, real Tailscale access, actual Google OAuth/Calendar reads, real Qwen inference, microphone/speaker operation, timezone-aware schedule planning, or full API parity. The next migration gates are optional-service API parity and then the responsive Next.js/TypeScript/Tailwind frontend. See [the migration plan](../docs/architecture-migration.md) for the full scope.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), [SSE and the browser event contract](https://fastapi.tiangolo.com/tutorial/server-sent-events/), and [Python SQLite transactions](https://docs.python.org/3/library/sqlite3.html).
