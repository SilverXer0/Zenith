# Python backend migration — development only

This is the first executable step toward Zenith's planned **Python/FastAPI backend**. The working application still starts with `npm start`; the Windows/Tailscale helpers still start Node. Do not replace that service with this backend yet.

## What this slice does

- Health and local account setup/sign-in/sign-out/session APIs.
- Authenticated, per-user task CRUD with the existing camel-case response format.
- SQLite through Python's standard library, without the external SQLite CLI.
- Compatibility with the existing SQLite file, Node scrypt passphrases and hashed session tokens.
- One-time legacy `tasks.json` import and upgrades of older user/task tables.
- Atomic task writes and completion-event recording; concurrent completions record one transition.
- Same-origin write checks, HttpOnly/SameSite cookies, optional Secure cookies, and uncached API responses.

Core startup and task operations neither connect to nor load Ollama. This slice does **not** yet implement assistant, live event stream, calendar, memory, planning, summary, or voice endpoints. It does not serve the current UI. These are missing capabilities, not disabled implementations or feature parity.

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

The Python suite covers actual FastAPI requests, server startup, independent sessions, expiry, user isolation, validation, transactions, concurrency, restart persistence, legacy JSON, old SQLite schemas, and real Node → Python → Node compatibility. All fixtures use disposable directories; real task data is not part of the tests. It also checks that pre-existing context and calendar-table data are not removed by migration. The Node suite remains `npm test`.

These checks do not prove Windows packaging, real Tailscale access, actual Google OAuth, real Qwen inference, microphone/speaker operation, or full API parity. The next migration gate is Python live task events, followed by the remaining existing API surfaces and the responsive Next.js/TypeScript/Tailwind frontend. See [the migration plan](../docs/architecture-migration.md) for the full scope.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), and [Python SQLite transactions](https://docs.python.org/3/library/sqlite3.html).
