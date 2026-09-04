import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import Auth, COOKIE_NAME, password_hash, token_hash
from backend.database import Database, timestamp
from backend.errors import ApiError


ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = {"displayName": "Migration user", "password": "Migration passphrase 2026!"}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zenith-python-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = create_app(self.directory)
        self.database = self.app.state.database
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def setup_account(self):
        response = self.client.post("/api/auth/setup", json=CREDENTIALS)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"]

    def test_health_and_auth_boundaries_without_ollama(self):
        with patch.dict(os.environ, {"OLLAMA_URL": "http://127.0.0.1:1"}):
            health = self.client.get("/api/health")
            self.assertEqual(health.json(), {"ok": True, "service": "zenith", "storage": "sqlite"})
            self.assertEqual(self.client.get("/api/auth/status").json(), {"setupRequired": True})
            for method, path, payload in [
                ("GET", "/api/tasks", None), ("GET", "/api/auth/session", None),
                ("POST", "/api/tasks", {"title": "No access"}), ("PATCH", "/api/tasks/missing", {"completed": True}),
                ("DELETE", "/api/tasks/missing", None),
            ]:
                response = self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Authentication required."})
                self.assertEqual(response.headers["cache-control"], "no-store")
            self.setup_account()
            self.assertEqual(self.client.post("/api/tasks", json={"title": "Core works without a model"}).status_code, 201)
            self.assertEqual(len(self.client.get("/api/tasks").json()["tasks"]), 1)

    def test_factory_defaults_to_separate_development_data(self):
        with patch.dict(os.environ):
            os.environ.pop("ZENITH_DATA_DIR", None)
            app = create_app()
        self.assertEqual(app.state.database.path, ROOT / "data-python-dev" / "zenith.sqlite")

    def test_openapi_describes_the_live_task_stream(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        stream = response.json()["paths"]["/api/events"]["get"]
        self.assertIn("text/event-stream", stream["responses"]["200"]["content"])

    def test_https_cookie_can_be_marked_secure(self):
        with patch.dict(os.environ, {"ZENITH_COOKIE_SECURE": "true"}):
            app = create_app(self.directory)
        with TestClient(app, base_url="https://testserver") as client:
            setup = client.post("/api/auth/setup", json=CREDENTIALS)
            self.assertEqual(setup.status_code, 201)
            self.assertIn("Secure", setup.headers["set-cookie"])
            self.assertEqual(client.get("/api/tasks").status_code, 200)

    def test_setup_cookie_multi_session_and_logout(self):
        first = self.client.post("/api/auth/setup", json=CREDENTIALS)
        self.assertEqual(first.status_code, 201)
        cookie_header = first.headers["set-cookie"].lower()
        for flag in ("httponly", "samesite=strict", "max-age=2592000", "path=/"):
            self.assertIn(flag, cookie_header)
        self.assertNotIn("password", first.text)
        self.assertEqual(self.client.get("/api/auth/status").json(), {"setupRequired": False})
        self.assertEqual(self.client.post("/api/auth/setup", json=CREDENTIALS).status_code, 409)
        other = TestClient(self.app)
        self.addCleanup(other.close)
        self.assertEqual(other.post("/api/auth/session", json={**CREDENTIALS, "password": "wrong password"}).status_code, 401)
        self.assertEqual(other.post("/api/auth/session", json=CREDENTIALS).status_code, 201)
        self.assertNotEqual(other.cookies[COOKIE_NAME], self.client.cookies[COOKIE_NAME])
        self.assertEqual(self.client.get("/api/auth/session").status_code, 200)
        self.assertEqual(other.delete("/api/auth/session").status_code, 204)
        self.assertEqual(other.get("/api/tasks").status_code, 401)
        self.assertEqual(self.client.get("/api/tasks").status_code, 200)

    def test_expired_and_malformed_sessions_are_rejected(self):
        self.setup_account()
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self.database.connection(write=True) as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (expired,))
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME, "x" * 129)
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)

    def test_crud_persistence_and_owned_fields(self):
        user = self.setup_account()
        title = "Plan café notes '); DROP TABLE users; --"
        created = self.client.post("/api/tasks", json={"title": f"  {title}  ", "project": "", "priority": "high", "dueDate": "2026-09-30", "notes": " Keep notes "})
        self.assertEqual(created.status_code, 201)
        task = created.json()["task"]
        self.assertEqual(task["title"], title)
        self.assertEqual(task["project"], "Inbox")
        self.assertEqual(task["notes"], "Keep notes")
        self.assertEqual(set(task), {"id", "title", "notes", "project", "priority", "dueDate", "completed", "completedAt", "createdAt", "updatedAt"})
        edited = self.client.patch(f'/api/tasks/{task["id"]}', json={"title": "Edited", "dueDate": ""}).json()["task"]
        self.assertEqual(edited["createdAt"], task["createdAt"])
        self.assertEqual(edited["priority"], "high")
        self.assertIsNone(edited["dueDate"])
        with TestClient(create_app(self.directory)) as restarted:
            restarted.cookies.set(COOKIE_NAME, self.client.cookies[COOKIE_NAME])
            self.assertEqual(restarted.get("/api/tasks").json()["tasks"], [edited])
        self.assertEqual(self.database.list_tasks(user["id"]), [edited])
        self.assertEqual(self.client.delete(f'/api/tasks/{task["id"]}').status_code, 204)
        self.assertEqual(self.client.delete(f'/api/tasks/{task["id"]}').status_code, 404)
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"], [])

    def test_user_isolation_including_guessed_task_ids(self):
        user = self.setup_account()
        with self.database.connection(write=True) as connection:
            connection.execute("INSERT INTO users (id, display_name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                               ("other", "Other account", password_hash("other passphrase"), timestamp()))
        foreign = self.database.save_task("other", {"title": "Not yours"})
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"], [])
        self.assertEqual(self.client.patch(f'/api/tasks/{foreign["id"]}', json={"title": "Hijacked"}).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/tasks/{foreign["id"]}').status_code, 404)
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Owned", "user_id": "other"}).status_code, 400)
        self.assertEqual(self.database.list_tasks("other")[0]["title"], "Not yours")
        self.assertEqual(self.database.list_tasks(user["id"]), [])

    def test_validation_and_cross_origin_rejection(self):
        for payload in ({"displayName": "  ", "password": "long passphrase"}, {"displayName": "User", "password": "short"}):
            self.assertEqual(self.client.post("/api/auth/setup", json=payload).status_code, 400)
        self.setup_account()
        for payload in ({}, {"title": " "}, {"title": 7}, {"title": "x" * 161}, {"title": "Task", "notes": "x" * 2001},
                        {"title": "Task", "priority": "urgent"}, {"title": "Task", "completed": "false"},
                        {"title": "Task", "dueDate": "2026-02-31"}, {"title": "Task", "dueDate": "2026-9-3"}):
            with self.subTest(payload=str(payload)[:80]):
                self.assertEqual(self.client.post("/api/tasks", json=payload).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks", content="{broken", headers={"Content-Type": "application/json"}).status_code, 400)
        for response in (self.client.get("/api/not-implemented"), self.client.put("/api/tasks")):
            self.assertIn(response.status_code, (404, 405))
            self.assertIn("error", response.json())
        rejected = self.client.post("/api/tasks", json={"title": "Cross site"}, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(rejected.status_code, 403)
        accepted = self.client.post("/api/tasks", json={"title": "Same origin"}, headers={"Origin": "http://testserver"})
        self.assertEqual(accepted.status_code, 201)

    def test_configured_frontend_origin_can_write_through_a_proxy(self):
        with patch.dict(os.environ, {"ZENITH_ALLOWED_ORIGINS": "http://localhost:3100"}):
            app = create_app(self.directory / "proxy")
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/auth/setup", json=CREDENTIALS,
                                             headers={"Origin": "http://localhost:3100"}).status_code, 201)
                self.assertEqual(client.post("/api/tasks", json={"title": "Proxy task"},
                                             headers={"Origin": "http://localhost:3100"}).status_code, 201)

    def test_completion_history_atomicity_and_reopening(self):
        user = self.setup_account()
        task = self.client.post("/api/tasks", json={"title": "Finish me"}).json()["task"]
        with patch.object(self.database, "_record_completion", side_effect=sqlite3.IntegrityError("injected failure")):
            response = self.client.patch(f'/api/tasks/{task["id"]}', json={"completed": True})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("injected", response.text)
        self.assertFalse(self.database.list_tasks(user["id"])[0]["completed"])
        finished = self.client.patch(f'/api/tasks/{task["id"]}', json={"completed": True}).json()["task"]
        self.assertIsNotNone(finished["completedAt"])
        repeat = self.client.patch(f'/api/tasks/{task["id"]}', json={"completed": True}).json()["task"]
        self.assertEqual(repeat["completedAt"], finished["completedAt"])
        reopened = self.client.patch(f'/api/tasks/{task["id"]}', json={"completed": False}).json()["task"]
        self.assertIsNone(reopened["completedAt"])
        self.client.delete(f'/api/tasks/{task["id"]}')
        with self.database.connection() as connection:
            history = connection.execute("SELECT * FROM task_completion_events").fetchall()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["title"], "Finish me")

    def test_concurrent_completions_record_one_transition(self):
        user = self.setup_account()
        task = self.database.save_task(user["id"], {"title": "Finish once"})
        with ThreadPoolExecutor(max_workers=4) as pool:
            completed = list(pool.map(lambda _: self.database.save_task(user["id"], {"completed": True}, task["id"]), range(4)))
        self.assertEqual(len({item["completedAt"] for item in completed}), 1)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_completion_events").fetchone()[0], 1)

    def test_setup_cannot_be_claimed_twice_concurrently(self):
        auth = Auth(self.database)
        def setup(index):
            try:
                auth.setup(f"User {index}", "test passphrase")
                return 201
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(setup, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zenith-migration-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def node(self, mode, cookie=None):
        env = {**os.environ, "ZENITH_DATA_DIR": str(self.directory), "OLLAMA_URL": "http://127.0.0.1:1"}
        if cookie:
            env["ZENITH_TEST_COOKIE"] = f"{COOKIE_NAME}={cookie}"
        result = subprocess.run([os.environ.get("ZENITH_NODE", "node"), str(ROOT / "backend/tests/node_bridge.mjs"), mode],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_node_to_python_and_back_preserves_tasks_sessions_and_context(self):
        seeded = self.node("seed")
        with TestClient(create_app(self.directory)) as client:
            client.cookies.set(COOKIE_NAME, seeded["cookie"].split("=", 1)[1])
            self.assertEqual(client.get("/api/auth/session").json()["user"], seeded["user"])
            self.assertEqual(client.get("/api/tasks").json()["tasks"], [seeded["task"]])
            self.assertEqual(client.get("/api/memory").json()["memories"], [seeded["memory"]])
            login = client.post("/api/auth/session", json=CREDENTIALS)
            self.assertEqual(login.status_code, 201)
            task_id = seeded["task"]["id"]
            updated = client.patch(f"/api/tasks/{task_id}", json={"title": "Edited in Python"}).json()["task"]
            self.assertEqual(updated["createdAt"], seeded["task"]["createdAt"])
            self.assertEqual(updated["completedAt"], seeded["task"]["completedAt"])
            self.assertEqual(client.post("/api/tasks", json={"title": "Python-created task", "completed": True}).status_code, 201)
            memory = client.patch(f'/api/memory/{seeded["memory"]["id"]}', json={"content": "Edited context in Python"})
            self.assertEqual(memory.status_code, 200)
            updated_memory = memory.json()["memory"]
            self.assertEqual(updated_memory["createdAt"], seeded["memory"]["createdAt"])
            cookie = login.cookies[COOKIE_NAME]
        returned = self.node("read", cookie)
        self.assertEqual({task["title"] for task in returned["tasks"]}, {"Edited in Python", "Python-created task"})
        self.assertEqual(returned["memories"], [updated_memory])
        self.assertEqual(returned["summary"]["counts"]["completed"], 2)

    def test_python_created_password_and_session_are_accepted_by_node(self):
        with TestClient(create_app(self.directory)) as client:
            setup = client.post("/api/auth/setup", json=CREDENTIALS)
            self.assertEqual(setup.status_code, 201)
            task = client.post("/api/tasks", json={"title": "Python first"}).json()["task"]
            cookie = client.cookies[COOKIE_NAME]
        returned = self.node("read", cookie)
        self.assertEqual(returned["tasks"], [task])

    def test_legacy_json_import_is_once_and_preserves_unknown_completion_time(self):
        legacy = [{"id": "legacy", "title": "Imported", "project": "Old project", "completed": True,
                   "createdAt": "2025-01-01T00:00:00.000Z", "updatedAt": "2025-02-01T00:00:00.000Z"}]
        path = self.directory / "tasks.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        with TestClient(create_app(self.directory)) as client:
            self.assertEqual(client.post("/api/auth/setup", json=CREDENTIALS).status_code, 201)
            imported = client.get("/api/tasks").json()["tasks"]
            cookie = client.cookies[COOKIE_NAME]
        self.assertEqual(imported[0]["id"], "legacy")
        self.assertEqual(imported[0]["createdAt"], legacy[0]["createdAt"])
        self.assertIsNone(imported[0]["completedAt"])
        self.assertEqual(json.loads(path.read_text()), legacy)
        path.write_text(json.dumps([{"id": "later", "title": "Do not reimport"}]), encoding="utf-8")
        with TestClient(create_app(self.directory)) as restarted:
            restarted.cookies.set(COOKIE_NAME, cookie)
            self.assertEqual(restarted.get("/api/tasks").json()["tasks"], imported)
        returned = self.node("read", cookie)
        self.assertEqual(returned["tasks"], imported)
        self.assertEqual(returned["summary"]["counts"]["completed"], 0)

    def test_malformed_legacy_file_is_not_marked_migrated(self):
        path = self.directory / "tasks.json"
        path.write_text("not JSON", encoding="utf-8")
        database = Database(self.directory)
        with self.assertRaises(json.JSONDecodeError):
            database.initialize()
        self.assertEqual(path.read_text(), "not JSON")
        with sqlite3.connect(database.path) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_migration'").fetchone())
        path.write_text('[{"id":"fixed","title":"Recovered"}]', encoding="utf-8")
        database.initialize()
        with database.connection() as connection:
            self.assertEqual(connection.execute("SELECT title FROM tasks").fetchone()[0], "Recovered")

    def test_pre_auth_schema_upgrade_invalidates_unsafe_sessions_and_keeps_data(self):
        database = Database(self.directory)
        with sqlite3.connect(database.path) as connection:
            connection.executescript("""
                CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, notes TEXT NOT NULL,
                    project TEXT NOT NULL, priority TEXT NOT NULL, due_date TEXT, completed INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE calendar_accounts (user_id TEXT PRIMARY KEY, refresh_token TEXT);
                INSERT INTO users VALUES ('old-user', 'Old name', '2025-01-01T00:00:00.000Z');
                INSERT INTO tasks VALUES ('old-task','old-user','Old task','','Inbox','medium',NULL,1,'2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z');
                INSERT INTO calendar_accounts VALUES ('old-user', 'test-refresh-token');
            """)
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token_hash("unsafe"), "old-user", timestamp(), "2099-01-01T00:00:00.000Z"))
        database.initialize()
        with database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT refresh_token FROM calendar_accounts").fetchone()[0], "test-refresh-token")
        self.assertIsNone(database.list_tasks("old-user")[0]["completedAt"])
        with TestClient(create_app(self.directory)) as client:
            self.assertTrue(client.get("/api/auth/status").json()["setupRequired"])
            user = client.post("/api/auth/setup", json=CREDENTIALS).json()["user"]
            self.assertEqual(user["id"], "old-user")
            self.assertEqual(client.get("/api/tasks").json()["tasks"][0]["title"], "Old task")


if __name__ == "__main__":
    unittest.main()
