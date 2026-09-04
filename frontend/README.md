# Zenith frontend

This is the target Next.js/TypeScript/Tailwind frontend for the Python API. The existing root Node app remains the working service and rollback path until the Windows cutover is complete.

## Run locally

From this directory:

```bash
npm install
npm run dev
```

Start the Python API separately, then set its trusted frontend origin. For example, from the repository root:

```bash
ZENITH_ALLOWED_ORIGINS=http://localhost:3000 \
ZENITH_DATA_DIR=/tmp/zenith-python-dev \
backend/.venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

The Next app proxies `/api/*` to `http://127.0.0.1:8000` by default. Set `ZENITH_API_ORIGIN` when the API uses another address. Keeping browser requests same-origin lets the HttpOnly session cookie and authenticated event stream work without frontend CORS code.

## Current slice

The shell includes local setup/sign-in, authenticated task capture and editing, completion/deletion, assistant chat with confirmation, Ollama availability/release controls, authenticated live task updates, optional read-only Calendar connection controls, task planning, weekly planning, daily completion history, persistent context notes, optional microphone transcription, spoken assistant replies, PWA installation metadata, a service worker, an install prompt, and browser-local due-task reminders.

Voice is deliberately optional. When the Python API reports no configured local adapters, the UI keeps text chat available and explains that voice is not configured. When adapters are configured, microphone recordings are sent only to the local Zenith server and assistant replies can be spoken on demand.

PWA installation and reminders require a secure browser context. `http://localhost` works for local testing; a raw HTTP Tailscale address does not meet browser requirements for installation or notifications. The service worker caches only the public Next app shell and static assets; it never caches `/api/` responses or private task data. Reminders work while Zenith is open and store one notification marker per task per browser day. Closed-app push delivery remains a later deployment decision.

## Checks

```bash
npm run typecheck
npm run lint
npm run build:webpack
```

The production check currently uses `next build --webpack`; the default Turbopack build hits an environment-specific process-permission panic in this workspace.
