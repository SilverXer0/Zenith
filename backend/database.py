"""SQLite storage compatible with the existing Node schema and timestamps."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import ApiError


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, display_name TEXT NOT NULL, password_hash TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT 'Inbox',
        priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
        completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_completion_events (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        task_id TEXT NOT NULL, title TEXT NOT NULL, completed_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS memory_items (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category TEXT NOT NULL DEFAULT 'general', content TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS calendar_accounts (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        access_token TEXT, refresh_token TEXT NOT NULL, token_expires_at TEXT,
        calendar_name TEXT, connected_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS calendar_oauth_states (
        state TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS tasks_user_updated ON tasks(user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS task_completion_user_time ON task_completion_events(user_id, completed_at)",
    "CREATE INDEX IF NOT EXISTS memory_user_updated ON memory_items(user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS calendar_oauth_expiry ON calendar_oauth_states(expires_at)",
)


def task_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "title": row["title"], "notes": row["notes"], "project": row["project"],
        "priority": row["priority"], "dueDate": row["due_date"], "completed": bool(row["completed"]),
        "completedAt": row["completed_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def memory_from_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "category": row["category"], "content": row["content"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


class Database:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "zenith.sqlite"

    @contextmanager
    def connection(self, *, write: bool = False):
        # Connections are never shared between request worker threads.
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as connection:
            # Avoid executescript: its implicit commit would break migration atomicity.
            for statement in SCHEMA:
                connection.execute(statement)
            user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
            if "password_hash" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
                connection.execute("DELETE FROM sessions")
            task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "completed_at" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
            calendar_columns = {row["name"] for row in connection.execute("PRAGMA table_info(calendar_accounts)")}
            for name in ("access_token", "token_expires_at", "calendar_name", "connected_at"):
                if name not in calendar_columns:
                    connection.execute(f"ALTER TABLE calendar_accounts ADD COLUMN {name} TEXT")
            connection.execute("UPDATE calendar_accounts SET connected_at=? WHERE connected_at IS NULL", (timestamp(),))
            user = connection.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            user_id = user["id"] if user else str(uuid4())
            if not user:
                connection.execute("INSERT INTO users (id, display_name, created_at) VALUES (?, ?, ?)", (user_id, "Local user", timestamp()))
            self._migrate_legacy(connection, user_id)

    def _migrate_legacy(self, connection, user_id):
        legacy = self.data_dir / "tasks.json"
        migrated = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_migration'").fetchone()
        if not legacy.exists() or migrated:
            return
        # Never mark a malformed file as successfully migrated; leave it untouched.
        tasks = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            raise ValueError("Legacy tasks.json must contain a list of tasks.")
        for task in tasks:
            if not isinstance(task, dict) or not task.get("id") or not task.get("title"):
                continue
            created = task.get("createdAt") or timestamp()
            connection.execute("""INSERT OR IGNORE INTO tasks
                (id, user_id, title, notes, project, priority, due_date, completed, completed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""", (
                str(task["id"]), user_id, str(task["title"]), str(task.get("notes") or ""),
                str(task.get("project") or "Inbox"), task.get("priority") if task.get("priority") in ("low", "medium", "high") else "medium",
                task.get("dueDate") or None, int(bool(task.get("completed"))), created, task.get("updatedAt") or created,
            ))
        connection.execute("CREATE TABLE legacy_migration (migrated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_migration VALUES (?)", (timestamp(),))

    def list_tasks(self, user_id: str) -> list[dict]:
        with self.connection() as connection:
            return self._list_tasks(connection, user_id)

    def _list_tasks(self, connection, user_id: str) -> list[dict]:
        rows = connection.execute("""SELECT * FROM tasks WHERE user_id = ?
            ORDER BY completed, due_date IS NULL, due_date, updated_at DESC""", (user_id,))
        return [task_from_row(row) for row in rows]

    def summary_data(self, user_id: str, start: str, end: str) -> tuple[list[dict], list[dict]]:
        # One read transaction keeps history and current tasks at the same snapshot.
        with self.connection() as connection:
            rows = connection.execute("""SELECT task_id AS taskId, title, completed_at AS completedAt
                FROM task_completion_events WHERE user_id=? AND completed_at>=? AND completed_at<?
                ORDER BY completed_at""", (user_id, start, end))
            completed = [dict(row) for row in rows]
            return self._list_tasks(connection, user_id), completed

    def save_task(self, user_id: str, patch: dict, task_id: str | None = None) -> dict:
        with self.connection(write=True) as connection:
            if task_id:
                row = connection.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
                if not row:
                    raise ApiError(404, "Task not found.")
                existing = task_from_row(row)
            else:
                existing = {"id": str(uuid4()), "title": "", "notes": "", "project": "Inbox", "priority": "medium",
                            "dueDate": None, "completed": False, "completedAt": None, "createdAt": timestamp()}
            task = {**existing}
            for key in ("title", "notes", "project", "priority"):
                if patch.get(key) is not None:
                    task[key] = patch[key]
            if not task["title"]:
                raise ApiError(400, "A task needs a title.")
            task["project"] = task["project"] or "Inbox"
            for key in ("dueDate", "completed"):
                if key in patch:
                    task[key] = patch[key]
            task["updatedAt"] = timestamp()
            completion = task["completed"] and not existing["completed"]
            task["completedAt"] = (task["updatedAt"] if completion else existing["completedAt"]) if task["completed"] else None
            values = (task["title"], task["notes"], task["project"], task["priority"], task["dueDate"], int(task["completed"]), task["completedAt"], task["updatedAt"], task["id"], user_id)
            if task_id:
                connection.execute("""UPDATE tasks SET title=?, notes=?, project=?, priority=?, due_date=?,
                    completed=?, completed_at=?, updated_at=? WHERE id=? AND user_id=?""", values)
            else:
                connection.execute("""INSERT INTO tasks (title, notes, project, priority, due_date, completed,
                    completed_at, updated_at, id, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*values, task["createdAt"]))
            if completion:
                self._record_completion(connection, user_id, task)
            return task

    def _record_completion(self, connection, user_id, task):
        connection.execute("""INSERT INTO task_completion_events
            (id, user_id, task_id, title, completed_at) VALUES (?, ?, ?, ?, ?)""",
            (str(uuid4()), user_id, task["id"], task["title"], task["completedAt"]))

    def delete_task(self, user_id: str, task_id: str):
        with self.connection(write=True) as connection:
            result = connection.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
            if not result.rowcount:
                raise ApiError(404, "Task not found.")

    def list_memory(self, user_id: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM memory_items WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
            return [memory_from_row(row) for row in rows]

    def save_memory(self, user_id: str, patch: dict, memory_id: str | None = None) -> dict:
        with self.connection(write=True) as connection:
            if memory_id:
                row = connection.execute("SELECT * FROM memory_items WHERE id=? AND user_id=?", (memory_id, user_id)).fetchone()
                if not row:
                    raise ApiError(404, "Context note not found.")
                memory = memory_from_row(row)
            else:
                memory = {"id": str(uuid4()), "content": "", "category": "general", "createdAt": timestamp()}
            for key in ("content", "category"):
                if patch.get(key) is not None:
                    memory[key] = patch[key]
            if not memory["content"]:
                raise ApiError(400, "Context needs some text.")
            memory["category"] = memory["category"] or "general"
            memory["updatedAt"] = timestamp()
            if memory_id:
                connection.execute("""UPDATE memory_items SET category=?, content=?, updated_at=? WHERE id=? AND user_id=?""",
                                   (memory["category"], memory["content"], memory["updatedAt"], memory_id, user_id))
            else:
                connection.execute("""INSERT INTO memory_items (id, user_id, category, content, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""", (memory["id"], user_id, memory["category"], memory["content"], memory["createdAt"], memory["updatedAt"]))
            return memory

    def delete_memory(self, user_id: str, memory_id: str):
        with self.connection(write=True) as connection:
            result = connection.execute("DELETE FROM memory_items WHERE id=? AND user_id=?", (memory_id, user_id))
            if not result.rowcount:
                raise ApiError(404, "Context note not found.")

    def calendar_connected(self, user_id: str) -> bool:
        return self.calendar_account(user_id) is not None

    def calendar_account(self, user_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("""SELECT user_id AS userId, access_token AS accessToken,
                refresh_token AS refreshToken, token_expires_at AS tokenExpiresAt,
                calendar_name AS calendarName, connected_at AS connectedAt
                FROM calendar_accounts WHERE user_id=?""", (user_id,)).fetchone()
            return dict(row) if row else None

    def save_calendar_state(self, user_id: str, state: str, expires_at: str):
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM calendar_oauth_states WHERE expires_at<=?", (timestamp(),))
            connection.execute("INSERT INTO calendar_oauth_states (state,user_id,expires_at) VALUES (?,?,?)",
                               (state, user_id, expires_at))

    def consume_calendar_state(self, state: str) -> str | None:
        with self.connection(write=True) as connection:
            now = timestamp()
            connection.execute("DELETE FROM calendar_oauth_states WHERE expires_at<=?", (now,))
            row = connection.execute("SELECT user_id FROM calendar_oauth_states WHERE state=?", (state,)).fetchone()
            if not row:
                return None
            connection.execute("DELETE FROM calendar_oauth_states WHERE state=?", (state,))
            return row["user_id"]

    def save_calendar_account(self, user_id: str, access_token: str, refresh_token: str,
                              expires_at: str, calendar_name: str, connected_at: str):
        with self.connection(write=True) as connection:
            connection.execute("""INSERT INTO calendar_accounts
                (user_id,access_token,refresh_token,token_expires_at,calendar_name,connected_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                access_token=excluded.access_token, refresh_token=excluded.refresh_token,
                token_expires_at=excluded.token_expires_at, calendar_name=excluded.calendar_name,
                connected_at=excluded.connected_at""",
                (user_id, access_token, refresh_token, expires_at, calendar_name, connected_at))

    def update_calendar_tokens(self, user_id: str, access_token: str, refresh_token: str, expires_at: str):
        with self.connection(write=True) as connection:
            result = connection.execute("""UPDATE calendar_accounts SET access_token=?, refresh_token=?,
                token_expires_at=? WHERE user_id=?""", (access_token, refresh_token, expires_at, user_id))
            if not result.rowcount:
                raise ApiError(409, "Google Calendar is not connected.")

    def delete_calendar_connection(self, user_id: str):
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM calendar_accounts WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM calendar_oauth_states WHERE user_id=?", (user_id,))
