"""Development API foundation; not yet a replacement for the complete Node app."""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .auth import Auth, COOKIE_NAME, SESSION_SECONDS, token_hash
from .assistant import LocalAssistant
from .calendar import GoogleCalendar
from .database import Database
from .errors import ApiError
from .events import TaskEvents, TaskEventResponse
from .models import (AssistantActionsInput, AssistantChatInput, AssistantUnloadInput,
                     Credentials, MemoryPatch, TaskPatch, VoiceSpeakInput)
from .planning import Planning, planning_date, weekly_start
from .voice import LocalVoice, RECORDING_OPENAPI, WavResponse


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    # The default deliberately does not open the working Node app's data directory.
    directory = Path(data_dir or os.environ.get("ZENITH_DATA_DIR") or Path(__file__).resolve().parent.parent / "data-python-dev")
    database = Database(directory)
    auth = Auth(database)
    events = TaskEvents()
    google_calendar = GoogleCalendar(database)
    assistant = LocalAssistant(database, google_calendar)
    voice = LocalVoice()
    planning = Planning(database, google_calendar)
    secure_cookie = os.environ.get("ZENITH_COOKIE_SECURE", "").lower() in ("1", "true")

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(database.initialize)
        try:
            yield
        finally:
            events.close_all()

    app = FastAPI(title="Zenith Core", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.database = database
    app.state.task_events = events

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
        return JSONResponse({"error": "Local storage is temporarily unavailable."}, status_code=503)

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

    @app.get("/api/assistant/status")
    def assistant_status():
        return assistant.status()

    @app.get("/api/calendar/oauth/callback")
    def calendar_callback(state: str | None = None, code: str | None = None,
                          error: str | None = None):
        google_calendar.complete_callback(state, code, error)
        return RedirectResponse("/?calendar=connected", status_code=302)

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
        hashed_token = auth.logout(request.cookies.get(COOKIE_NAME))
        if hashed_token:
            events.close_session(hashed_token)
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=secure_cookie)
        return response

    @app.get("/api/events", response_class=TaskEventResponse)
    def task_events(request: Request, current_user: dict = Depends(user)):
        hashed_token = token_hash(request.cookies[COOKIE_NAME])
        return TaskEventResponse(events.stream(current_user["id"], hashed_token, auth))

    @app.get("/api/tasks")
    def tasks(current_user: dict = Depends(user)):
        return {"tasks": database.list_tasks(current_user["id"])}

    @app.post("/api/tasks", status_code=201)
    def create_task(patch: TaskPatch, current_user: dict = Depends(user)):
        task = database.save_task(current_user["id"], patch.model_dump(exclude_unset=True))
        events.publish(current_user["id"])
        return {"task": task}

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: str, patch: TaskPatch, current_user: dict = Depends(user)):
        task = database.save_task(current_user["id"], patch.model_dump(exclude_unset=True), task_id)
        events.publish(current_user["id"])
        return {"task": task}

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str, current_user: dict = Depends(user)):
        database.delete_task(current_user["id"], task_id)
        events.publish(current_user["id"])
        return Response(status_code=204)

    @app.get("/api/calendar/status")
    def calendar_status(current_user: dict = Depends(user)):
        return google_calendar.status(current_user["id"])

    @app.get("/api/calendar/connect")
    def calendar_connect(current_user: dict = Depends(user)):
        return RedirectResponse(google_calendar.connect_url(current_user["id"]), status_code=302)

    @app.get("/api/calendar/events")
    def calendar_events(start: str | None = None, end: str | None = None,
                        current_user: dict = Depends(user)):
        return {"events": google_calendar.events(current_user["id"], start, end)}

    @app.delete("/api/calendar/connection", status_code=204)
    def disconnect_calendar(current_user: dict = Depends(user)):
        google_calendar.disconnect(current_user["id"])
        return Response(status_code=204)

    @app.get("/api/voice/status")
    def voice_status(current_user: dict = Depends(user)):
        return voice.status()

    @app.post("/api/voice/transcribe", openapi_extra=RECORDING_OPENAPI)
    async def voice_transcribe(request: Request, current_user: dict = Depends(user)):
        recording, suffix = await voice.read_recording(request)
        text = await run_in_threadpool(voice.transcribe, recording, suffix)
        return {"text": text}

    @app.post("/api/voice/speak", response_class=WavResponse)
    async def voice_speak(body: VoiceSpeakInput, current_user: dict = Depends(user)):
        audio = await run_in_threadpool(voice.synthesize, body.text)
        return WavResponse(audio)

    @app.post("/api/assistant/chat")
    def assistant_chat(body: AssistantChatInput, current_user: dict = Depends(user)):
        return assistant.chat(current_user["id"], body)

    @app.post("/api/assistant/actions")
    def assistant_actions(body: AssistantActionsInput, current_user: dict = Depends(user)):
        tasks = assistant.apply_actions(current_user["id"], body.actions)
        events.publish(current_user["id"])
        return {"tasks": tasks}

    @app.post("/api/assistant/unload")
    def assistant_unload(body: AssistantUnloadInput, current_user: dict = Depends(user)):
        return assistant.unload(body)

    @app.get("/api/memory")
    def memories(current_user: dict = Depends(user)):
        return {"memories": database.list_memory(current_user["id"])}

    @app.post("/api/memory", status_code=201)
    def create_memory(patch: MemoryPatch, current_user: dict = Depends(user)):
        return {"memory": database.save_memory(current_user["id"], patch.model_dump(exclude_unset=True))}

    @app.patch("/api/memory/{memory_id}")
    def update_memory(memory_id: str, patch: MemoryPatch, current_user: dict = Depends(user)):
        return {"memory": database.save_memory(current_user["id"], patch.model_dump(exclude_unset=True), memory_id)}

    @app.delete("/api/memory/{memory_id}", status_code=204)
    def delete_memory(memory_id: str, current_user: dict = Depends(user)):
        database.delete_memory(current_user["id"], memory_id)
        return Response(status_code=204)

    @app.get("/api/briefing")
    def briefing(day: str | None = Query(default=None, alias="date"), current_user: dict = Depends(user)):
        return planning.briefing(current_user["id"], planning_date(day))

    @app.get("/api/briefing/morning")
    def morning_briefing(day: str | None = Query(default=None, alias="date"), current_user: dict = Depends(user)):
        return planning.morning(current_user["id"], planning_date(day))

    @app.get("/api/weekly-plan")
    def weekly_plan(start: str | None = None, current_user: dict = Depends(user)):
        return planning.weekly(current_user["id"], weekly_start(start))

    @app.get("/api/summaries/daily")
    def daily_summary(day: str | None = Query(default=None, alias="date"),
                      offset: int = Query(default=0, ge=-840, le=840), current_user: dict = Depends(user)):
        return planning.daily_summary(current_user["id"], planning_date(day), offset)

    return app
