# Zenith

Zenith is a local-first personal manager MVP: a responsive task list with a small HTTP API and an optional local Ollama boundary.

## Run it

Requires Node.js 20 or newer.

```bash
npm start
```

Open http://localhost:3000. Tasks persist in `data/tasks.json` (or `ZENITH_DATA_DIR` if configured).

## API surface

- `GET /api/tasks`, `POST /api/tasks`
- `PATCH /api/tasks/:id`, `DELETE /api/tasks/:id`
- `GET /api/health`
- `GET /api/assistant/status`

The app runs fully without Ollama. When Ollama is running at `OLLAMA_URL` (default `http://127.0.0.1:11434`), the status endpoint reports its availability without loading or invoking a model. Future calendar and voice clients can use this API as their stable integration boundary.
