"""Development API foundation; not yet a replacement for the complete Node app."""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .auth import Auth, COOKIE_NAME, SESSION_SECONDS
from .database import Database
from .errors import ApiError
from .models import Credentials, TaskPatch


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    # The default deliberately does not open the working Node app's data directory.
    directory = Path(data_dir or os.environ.get("ZENITH_DATA_DIR") or Path(__file__).resolve().parent.parent / "data-python-dev")
    database = Database(directory)
    auth = Auth(database)
    secure_cookie = os.environ.get("ZENITH_COOKIE_SECURE", "").lower() in ("1", "true")

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(database.initialize)
        yield

    app = FastAPI(title="Zenith Core", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.database = database

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Cross-origin writes are not allowed."}, status_code=403, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        return JSONResponse({"error": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Keep the existing {error} contract and never echo passphrases or inputs.
        return JSONResponse({"error": "Invalid request. Check field types, lengths, and dates."}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"error": str(error.detail)}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        return JSONResponse({"error": "Task storage is temporarily unavailable."}, status_code=503)

    def user(request: Request):
        return auth.current_user(request.cookies.get(COOKIE_NAME))

    def session_response(result):
        payload, token = result
        response = JSONResponse(payload, status_code=201)
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure_cookie, path="/")
        return response

    @app.get("/api/health")
    def health():
        with database.connection() as connection:
            connection.execute("SELECT 1")
        return {"ok": True, "service": "zenith", "storage": "sqlite"}

    @app.get("/api/auth/status")
    def auth_status():
        return {"setupRequired": auth.setup_required()}

    @app.post("/api/auth/setup")
    def setup(credentials: Credentials):
        return session_response(auth.setup(credentials.displayName, credentials.password))

    @app.post("/api/auth/session")
    def login(credentials: Credentials):
        return session_response(auth.login(credentials.displayName, credentials.password))

    @app.get("/api/auth/session")
    def session(current_user: dict = Depends(user)):
        return {"user": current_user}

    @app.delete("/api/auth/session")
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE_NAME))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=secure_cookie)
        return response

    @app.get("/api/tasks")
    def tasks(current_user: dict = Depends(user)):
        return {"tasks": database.list_tasks(current_user["id"])}

    @app.post("/api/tasks", status_code=201)
    def create_task(patch: TaskPatch, current_user: dict = Depends(user)):
        return {"task": database.save_task(current_user["id"], patch.model_dump(exclude_unset=True))}

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: str, patch: TaskPatch, current_user: dict = Depends(user)):
        return {"task": database.save_task(current_user["id"], patch.model_dump(exclude_unset=True), task_id)}

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str, current_user: dict = Depends(user)):
        database.delete_task(current_user["id"], task_id)
        return Response(status_code=204)

    return app
