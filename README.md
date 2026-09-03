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

## API surface

- `GET /api/tasks`, `POST /api/tasks`
- `PATCH /api/tasks/:id`, `DELETE /api/tasks/:id`
- `GET /api/auth/status`
- `POST /api/auth/setup` (first launch), `POST /api/auth/session`, `GET /api/auth/session`, `DELETE /api/auth/session`
- `GET /api/health`
- `GET /api/assistant/status`
- `POST /api/assistant/chat` (authenticated, read-only task context)
- `POST /api/assistant/actions` (authenticated, applies user-confirmed proposals)
- `POST /api/assistant/unload` (authenticated, releases the configured model from VRAM)

On first launch, set a local display name and passphrase (at least 8 characters). The passphrase is stored only as a salted scrypt hash. Subsequent access requires the credentials and creates a 30-day HttpOnly `zenith_session` cookie; signing in rotates prior sessions for that account. Task endpoints require this cookie and remain scoped to the account. When Ollama is running at `OLLAMA_URL` (default `http://127.0.0.1:11434`), the assistant chat endpoint uses `OLLAMA_MODEL` or the first installed model. It sends only the signed-in user's task context and returns read-only replies plus optional structured task proposals. Proposals are never applied automatically; the user must confirm them, and the server validates them again before writing. Use `OLLAMA_KEEP_ALIVE=0` for automatic unload after each response, or the authenticated release control to free the currently loaded model on demand. The rest of the app runs fully without Ollama.
