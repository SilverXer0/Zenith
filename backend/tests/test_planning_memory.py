import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import COOKIE_NAME, password_hash
from backend.database import timestamp
from backend.planning import summary_window


ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = {"displayName": "Migration user", "password": "Migration passphrase 2026!"}


class PlanningMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zenith-planning-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.app = create_app(self.directory)
        self.database = self.app.state.database
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        setup = self.client.post("/api/auth/setup", json=CREDENTIALS)
        self.assertEqual(setup.status_code, 201)
        self.user_id = setup.json()["user"]["id"]

    def create_task(self, title, **fields):
        response = self.client.post("/api/tasks", json={"title": title, **fields})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["task"]

    def create_memory(self, content, **fields):
        response = self.client.post("/api/memory", json={"content": content, **fields})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["memory"]

    def fixture_times(self, task_id, created_at, completed_at=None):
        with self.database.connection(write=True) as connection:
            connection.execute("UPDATE tasks SET created_at=? WHERE id=?", (created_at, task_id))
            if completed_at:
                connection.execute("""INSERT INTO task_completion_events (id,user_id,task_id,title,completed_at)
                    SELECT ?,user_id,id,title,? FROM tasks WHERE id=?""", (f"event-{task_id}", completed_at, task_id))

    def node_snapshot(self, day, offset=0):
        env = {**os.environ, "ZENITH_DATA_DIR": str(self.directory), "OLLAMA_URL": "http://127.0.0.1:1",
               "ZENITH_TEST_COOKIE": f"{COOKIE_NAME}={self.client.cookies[COOKIE_NAME]}",
               "ZENITH_TEST_DATE": day, "ZENITH_TEST_OFFSET": str(offset)}
        result = subprocess.run([os.environ.get("ZENITH_NODE", "node"), str(ROOT / "backend/tests/node_bridge.mjs"), "read"],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_memory_crud_preserves_fields_and_survives_restart(self):
        content = "Café notes '); DROP TABLE memory_items; --"
        note = self.create_memory(f"  {content}  ")
        self.assertEqual(note["content"], content)
        self.assertEqual(note["category"], "general")
        self.assertEqual(set(note), {"id", "category", "content", "createdAt", "updatedAt"})
        changed = self.client.patch(f'/api/memory/{note["id"]}', json={"category": "project"})
        self.assertEqual(changed.status_code, 200)
        note = changed.json()["memory"]
        changed = self.client.patch(f'/api/memory/{note["id"]}', json={"content": "  Updated details  "}).json()["memory"]
        self.assertEqual(changed["category"], "project")
        self.assertEqual(changed["content"], "Updated details")
        self.assertEqual(changed["createdAt"], note["createdAt"])
        with TestClient(create_app(self.directory)) as restarted:
            restarted.cookies.set(COOKIE_NAME, self.client.cookies[COOKIE_NAME])
            self.assertEqual(restarted.get("/api/memory").json()["memories"], [changed])
        self.assertEqual(self.client.delete(f'/api/memory/{note["id"]}').status_code, 204)
        self.assertEqual(self.client.delete(f'/api/memory/{note["id"]}').status_code, 404)
        self.assertEqual(self.client.get("/api/memory").json(), {"memories": []})

    def test_memory_validation_and_failed_update_leave_existing_note_intact(self):
        for body in ({}, {"content": " "}, {"content": 42}, {"content": "x" * 2001},
                     {"content": "Valid", "category": "x" * 41}, {"content": "Valid", "user_id": "other"}):
            self.assertEqual(self.client.post("/api/memory", json=body).status_code, 400)
        note = self.create_memory("Original", category="preference")
        self.assertEqual(self.client.patch(f'/api/memory/{note["id"]}', json={"content": ""}).status_code, 400)
        with self.database.connection(write=True) as connection:
            connection.execute("""CREATE TRIGGER reject_memory_update BEFORE UPDATE ON memory_items
                BEGIN SELECT RAISE(ABORT, 'private database detail'); END""")
        rejected = self.client.patch(f'/api/memory/{note["id"]}', json={"content": "Should roll back"})
        self.assertEqual(rejected.status_code, 503)
        self.assertNotIn("private database detail", rejected.text)
        self.assertEqual(self.client.get("/api/memory").json()["memories"], [note])

    def test_memory_order_and_blank_category_default(self):
        first = self.create_memory("Earlier", category="routine")
        second = self.create_memory("Later", category="project")
        with self.database.connection(write=True) as connection:
            connection.execute("UPDATE memory_items SET updated_at=? WHERE id=?", ("2025-01-01T00:00:00.000Z", first["id"]))
            connection.execute("UPDATE memory_items SET updated_at=? WHERE id=?", ("2025-01-02T00:00:00.000Z", second["id"]))
        self.assertEqual([note["id"] for note in self.client.get("/api/memory").json()["memories"]], [second["id"], first["id"]])
        changed = self.client.patch(f'/api/memory/{first["id"]}', json={"category": " "}).json()["memory"]
        self.assertEqual(changed["category"], "general")
        self.assertEqual(changed["content"], "Earlier")

    def test_authentication_and_account_isolation_cover_all_new_routes(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        for method, path, body in [("GET", "/api/memory", None), ("POST", "/api/memory", {"content": "Blocked"}),
                                   ("PATCH", "/api/memory/guess", {"content": "Blocked"}), ("DELETE", "/api/memory/guess", None),
                                   ("GET", "/api/briefing", None), ("GET", "/api/briefing/morning", None),
                                   ("GET", "/api/weekly-plan", None), ("GET", "/api/summaries/daily", None)]:
            response = anonymous.request(method, path, json=body)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["cache-control"], "no-store")
        with self.database.connection(write=True) as connection:
            connection.execute("INSERT INTO users (id,display_name,password_hash,created_at) VALUES (?,?,?,?)",
                               ("other", "Other", password_hash("other passphrase"), timestamp()))
        foreign = self.database.save_memory("other", {"content": "Private preference"})
        self.database.save_task("other", {"title": "Private task", "completed": True})
        self.assertEqual(self.client.get("/api/memory").json()["memories"], [])
        for method in ("PATCH", "DELETE"):
            response = self.client.request(method, f'/api/memory/{foreign["id"]}', json={"content": "Hijacked"} if method == "PATCH" else None)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/briefing").json()["counts"], {"open": 0, "overdue": 0, "dueToday": 0})
        self.assertEqual(self.client.get("/api/briefing/morning").json()["overdue"], [])
        self.assertEqual(self.client.get("/api/weekly-plan").json()["counts"]["open"], 0)
        self.assertEqual(self.client.get("/api/summaries/daily").json()["counts"], {"completed": 0, "created": 0, "open": 0})
        self.assertEqual(self.database.list_memory("other"), [foreign])

    def test_focus_prioritizes_overdue_then_today_then_future_with_priority_and_recency(self):
        day = "2026-09-04"
        self.create_task("Undated high", priority="high")
        self.create_task("Future high", priority="high", dueDate="2026-09-05")
        self.create_task("Today low", priority="low", dueDate=day)
        high_old = self.create_task("Today high older", priority="high", dueDate=day)
        high_new = self.create_task("Today high newer", priority="high", dueDate=day)
        self.create_task("Overdue low", priority="low", dueDate="2026-09-03")
        self.create_task("Earlier overdue", priority="medium", dueDate="2026-09-01")
        self.create_task("Completed excluded", completed=True, dueDate="2026-01-01")
        with self.database.connection(write=True) as connection:
            connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", ("2026-09-01T00:00:00.000Z", high_old["id"]))
            connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", ("2026-09-02T00:00:00.000Z", high_new["id"]))
        response = self.client.get(f"/api/briefing?date={day}")
        self.assertEqual(response.status_code, 200)
        briefing = response.json()
        self.assertEqual(briefing["counts"], {"open": 7, "overdue": 2, "dueToday": 3})
        self.assertEqual([task["title"] for task in briefing["focusTasks"]],
                         ["Earlier overdue", "Overdue low", "Today high newer", "Today high older", "Today low"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.node_snapshot(day)["briefing"], briefing)

    def test_summary_uses_inclusive_start_exclusive_end_and_current_open_count(self):
        points = [("Before", "2026-09-04T06:59:59.999Z"), ("At start", "2026-09-04T07:00:00.000Z"),
                  ("Before end", "2026-09-05T06:59:59.999Z"), ("At end", "2026-09-05T07:00:00.000Z")]
        for title, instant in points:
            task = self.create_task(title)
            self.fixture_times(task["id"], instant, instant)
        response = self.client.get("/api/summaries/daily?date=2026-09-04&offset=420")
        self.assertEqual(response.status_code, 200)
        summary = response.json()
        self.assertEqual(summary["counts"], {"completed": 2, "created": 2, "open": 4})
        self.assertEqual([task["title"] for task in summary["completedTasks"]], ["At start", "Before end"])
        self.assertEqual({task["title"] for task in summary["createdTasks"]}, {"At start", "Before end"})
        self.assertEqual(summary["summary"], "2 tasks completed today.")
        self.assertEqual(self.node_snapshot("2026-09-04", 420)["summary"], summary)

    def test_negative_offset_includes_the_previous_utc_date(self):
        task = self.create_task("New Year in Tokyo")
        self.fixture_times(task["id"], "2025-12-31T15:00:00.000Z", "2025-12-31T15:00:00.000Z")
        summary = self.client.get("/api/summaries/daily?date=2026-01-01&offset=-540").json()
        self.assertEqual(summary["counts"], {"completed": 1, "created": 1, "open": 1})
        self.assertEqual(summary["summary"], "1 task completed today.")
        self.assertEqual(self.node_snapshot("2026-01-01", -540)["summary"], summary)

    def test_morning_and_weekly_task_plans_match_node_without_calendar(self):
        day = "2026-09-04"
        task_data = [("Overdue", "2026-09-03"), ("Today", day), ("Tomorrow", "2026-09-05"),
                     ("Third day", "2026-09-07"), ("Outside morning", "2026-09-08"), ("Undated", None),
                     ("After week", "2026-09-11")]
        for title, due_date in task_data:
            self.create_task(title, dueDate=due_date)
        self.create_task("Completed", dueDate=day, completed=True)
        morning = self.client.get(f"/api/briefing/morning?date={day}").json()
        weekly = self.client.get(f"/api/weekly-plan?start={day}").json()
        self.assertEqual(morning["summary"], "1 overdue · 1 due today")
        self.assertEqual([task["title"] for task in morning["overdue"]], ["Overdue"])
        self.assertEqual([task["title"] for task in morning["dueToday"]], ["Today"])
        self.assertEqual([task["title"] for task in morning["upcoming"]], ["Tomorrow", "Third day"])
        self.assertEqual(morning["calendar"], {"connected": False, "available": False, "events": []})
        self.assertEqual(weekly["counts"], {"open": 7, "overdue": 1, "scheduled": 4, "unscheduled": 1})
        self.assertEqual(weekly["end"], "2026-09-11")
        self.assertEqual(weekly["days"][0]["tasks"][0]["title"], "Today")
        self.assertEqual(weekly["days"][4]["tasks"][0]["title"], "Outside morning")
        self.assertEqual(weekly["unscheduled"][0]["title"], "Undated")
        node = self.node_snapshot(day)
        self.assertEqual(node["morning"], morning)
        self.assertEqual(node["weekly"], weekly)

    def test_connected_calendar_is_explicitly_unavailable_until_calendar_port(self):
        with self.database.connection(write=True) as connection:
            connection.execute("""CREATE TABLE calendar_accounts (user_id TEXT PRIMARY KEY, access_token TEXT,
                refresh_token TEXT NOT NULL, token_expires_at TEXT, calendar_name TEXT, connected_at TEXT NOT NULL)""")
            connection.execute("INSERT INTO calendar_accounts VALUES (?,NULL,?,?,?,?)",
                               (self.user_id, "preserved-refresh-token", "2099-01-01T00:00:00.000Z", "Personal", timestamp()))
        for path in ("/api/briefing/morning?date=2026-09-04", "/api/weekly-plan?start=2026-09-04"):
            self.assertEqual(self.client.get(path).json()["calendar"], {"connected": True, "available": False, "events": []})

    def test_deleted_and_reopened_tasks_retain_completion_history(self):
        task = self.create_task("Finished once", completed=True)
        day = task["completedAt"][:10]
        path = f"/api/summaries/daily?date={day}&offset=0"
        before = self.client.get(path).json()["completedTasks"]
        self.assertEqual(len(before), 1)
        self.client.patch(f'/api/tasks/{task["id"]}', json={"title": "Renamed and reopened", "completed": False})
        self.assertEqual(self.client.get(path).json()["completedTasks"], before)
        self.client.delete(f'/api/tasks/{task["id"]}')
        summary = self.client.get(path).json()
        self.assertEqual(summary["completedTasks"], before)
        self.assertEqual(summary["completedTasks"][0]["title"], "Finished once")
        self.assertEqual(summary["counts"], {"completed": 1, "created": 0, "open": 0})

    def test_invalid_dates_offsets_and_extreme_boundaries_return_400(self):
        for route in ("/api/briefing", "/api/briefing/morning", "/api/summaries/daily"):
            for day in ("2026-02-31", "2026-2-03", "not-a-date", "0000-01-01"):
                self.assertEqual(self.client.get(route, params={"date": day}).status_code, 400)
        for offset in ("NaN", "0.5", "841", "-841", "true"):
            self.assertEqual(self.client.get("/api/summaries/daily", params={"offset": offset}).status_code, 400)
        for day, offset in (("0001-01-01", -840), ("9999-12-31", 840)):
            self.assertEqual(self.client.get("/api/summaries/daily", params={"date": day, "offset": offset}).status_code, 400)
        self.assertEqual(self.client.get("/api/briefing?date=2024-02-29").status_code, 200)
        for day in ("2026-02-31", "2026-2-03", "not-a-date", "0000-01-01"):
            self.assertEqual(self.client.get("/api/weekly-plan", params={"start": day}).status_code, 400)
        self.assertEqual(self.client.get("/api/briefing/morning?date=9999-12-31").status_code, 400)
        self.assertEqual(self.client.get("/api/weekly-plan?start=9999-12-31").status_code, 400)
        self.assertEqual(summary_window("2026-01-01", -840), ("2025-12-31T10:00:00.000Z", "2026-01-01T10:00:00.000Z"))
        self.assertEqual(summary_window("2026-01-01", 840), ("2026-01-01T14:00:00.000Z", "2026-01-02T14:00:00.000Z"))

    def test_empty_defaults_and_read_only_operation_without_ollama(self):
        def snapshot():
            with self.database.connection() as connection:
                return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                        for table in ("users", "sessions", "tasks", "memory_items", "task_completion_events")}
        before = snapshot()
        with patch.dict(os.environ, {"OLLAMA_URL": "http://127.0.0.1:1"}):
            briefing = self.client.get("/api/briefing").json()
            morning = self.client.get("/api/briefing/morning").json()
            weekly = self.client.get("/api/weekly-plan").json()
            summary = self.client.get("/api/summaries/daily").json()
            self.client.get("/api/memory")
        self.assertEqual(briefing["date"], datetime.now(timezone.utc).date().isoformat())
        self.assertEqual(briefing["focusTasks"], [])
        self.assertEqual(morning["calendar"], {"connected": False, "available": False, "events": []})
        self.assertEqual(weekly["calendar"], {"connected": False, "available": False, "events": []})
        self.assertEqual(summary["summary"], "No tasks completed today.")
        self.assertEqual(summary["counts"], {"completed": 0, "created": 0, "open": 0})
        self.assertEqual(snapshot(), before)

    def test_python_created_context_is_readable_by_node(self):
        notes = [self.create_memory("Quiet evenings", category="preference"), self.create_memory("Morning walks", category="routine")]
        expected = self.client.get("/api/memory").json()["memories"]
        returned = self.node_snapshot("2026-09-04")
        self.assertEqual(returned["memories"], expected)
        self.assertEqual({note["id"] for note in returned["memories"]}, {note["id"] for note in notes})


if __name__ == "__main__":
    unittest.main()
