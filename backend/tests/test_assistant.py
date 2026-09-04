import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.assistant import OllamaClient, OllamaError
from backend.auth import password_hash
from backend.database import timestamp


CREDENTIALS = {"displayName": "Assistant user", "password": "Assistant passphrase 2026!"}


class OllamaMock:
    def __init__(self):
        self.models = ["qwen3:8b"]
        self.running = ["qwen3:8b"]
        self.tags_status = 200
        self.ps_status = 200
        self.chat_status = 200
        self.generate_status = 200
        self.tags_redirect = None
        self.invalid_chat_json = False
        self.chat_content = json.dumps({"reply": "Focus on the private task first.", "actions": []})
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send_json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fixture.requests.append({"method": "GET", "path": self.path, "body": None})
                if self.path == "/api/tags":
                    if fixture.tags_redirect:
                        self.send_response(302)
                        self.send_header("Location", fixture.tags_redirect)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    return self.send_json(fixture.tags_status,
                                          {"models": [{"name": model} for model in fixture.models]})
                if self.path == "/api/ps":
                    return self.send_json(fixture.ps_status,
                                          {"models": [{"name": model, "model": model} for model in fixture.running]})
                return self.send_json(404, {"error": "not_found"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                fixture.requests.append({"method": "POST", "path": self.path, "body": body})
                if self.path == "/api/chat":
                    if fixture.invalid_chat_json:
                        invalid = b"not-json"
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(invalid)))
                        self.end_headers()
                        self.wfile.write(invalid)
                        return
                    return self.send_json(fixture.chat_status,
                                          {"message": {"role": "assistant", "content": fixture.chat_content}})
                if self.path == "/api/generate":
                    return self.send_json(fixture.generate_status, {"done": True})
                return self.send_json(404, {"error": "not_found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class AssistantTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {})
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("OLLAMA_URL", "OLLAMA_MODEL", "OLLAMA_KEEP_ALIVE",
                    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
                    "GOOGLE_TOKEN_URL", "GOOGLE_CALENDAR_URL"):
            os.environ.pop(key, None)
        os.environ["OLLAMA_URL"] = "http://127.0.0.1:1"
        temporary = tempfile.TemporaryDirectory(prefix="zenith-assistant-test-")
        self.addCleanup(temporary.cleanup)
        self.app = create_app(Path(temporary.name))
        self.database = self.app.state.database
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        setup = self.client.post("/api/auth/setup", json=CREDENTIALS)
        self.assertEqual(setup.status_code, 201)
        self.user_id = setup.json()["user"]["id"]
        self.mock = None

    def configure(self, *, selected=True):
        self.mock = OllamaMock()
        self.addCleanup(self.mock.close)
        os.environ["OLLAMA_URL"] = self.mock.url
        if selected:
            os.environ["OLLAMA_MODEL"] = "qwen3:8b"
        else:
            os.environ.pop("OLLAMA_MODEL", None)

    def task(self, title, **fields):
        response = self.client.post("/api/tasks", json={"title": title, **fields})
        self.assertEqual(response.status_code, 201)
        return response.json()["task"]

    def test_status_is_public_read_only_local_only_and_core_survives_offline(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get("/api/assistant/status").json(),
                         {"enabled": True, "reachable": False, "model": None,
                          "loaded": False, "loadedModel": None})
        for path, body in (("/api/assistant/chat", {"message": "Hello"}),
                           ("/api/assistant/actions", {"actions": [{"type": "create_task", "title": "No"}]}),
                           ("/api/assistant/unload", {})):
            self.assertEqual(anonymous.post(path, json=body).status_code, 401)
        os.environ["OLLAMA_URL"] = "https://example.com"
        os.environ["OLLAMA_MODEL"] = "qwen3:8b"
        self.assertEqual(self.client.get("/api/assistant/status").json()["reachable"], False)
        unavailable = self.client.post("/api/assistant/chat", json={"message": "Hello"})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json(), {"error": "Local assistant is unavailable."})
        self.assertEqual(self.task("Core remains independent")["title"], "Core remains independent")

        self.configure(selected=False)
        status = anonymous.get("/api/assistant/status").json()
        self.assertEqual(status, {"enabled": True, "reachable": True, "model": "qwen3:8b",
                                  "loaded": True, "loadedModel": "qwen3:8b"})
        self.mock.ps_status = 500
        status = anonymous.get("/api/assistant/status").json()
        self.assertTrue(status["reachable"])
        self.assertFalse(status["loaded"])
        self.mock.ps_status = 200
        self.mock.models = ["qwen3.5:cloud", "qwen3:8b"]
        self.assertEqual(anonymous.get("/api/assistant/status").json()["model"], "qwen3:8b")
        os.environ["OLLAMA_MODEL"] = "qwen3.5:cloud"
        self.assertIsNone(anonymous.get("/api/assistant/status").json()["model"])
        self.assertEqual(self.client.post("/api/assistant/chat", json={"message": "Never send this away"}).status_code, 503)
        self.mock.tags_redirect = "https://example.com/api/tags"
        self.assertFalse(anonymous.get("/api/assistant/status").json()["reachable"])

    def test_chat_uses_only_owner_context_and_never_writes_before_confirmation(self):
        self.configure()
        os.environ["OLLAMA_KEEP_ALIVE"] = "0"
        private = self.task("Private deadline", notes="Finish the report", priority="high", dueDate="2026-09-30")
        self.client.post("/api/memory", json={"category": "preference", "content": "Quiet evenings work best"})
        with self.database.connection(write=True) as connection:
            connection.execute("INSERT INTO users (id,display_name,password_hash,created_at) VALUES (?,?,?,?)",
                               ("foreign", "Foreign", password_hash("foreign password"), timestamp()))
        foreign = self.database.save_task("foreign", {"title": "Foreign secret"})
        self.database.save_memory("foreign", {"content": "Foreign memory"})
        self.mock.chat_content = json.dumps({
            "reply": "I can add the reminder and mark the report complete after you confirm.",
            "actions": [
                {"type": "create_task", "title": "Send reminder", "priority": "low", "dueDate": ""},
                {"type": "complete_task", "taskId": private["id"]},
                {"type": "delete_task", "taskId": foreign["id"]},
                {"type": "run_program", "command": "anything"},
            ],
        })
        history = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"history-{index}" + "x" * 2500}
                   for index in range(10)]
        before = self.client.get("/api/tasks").json()["tasks"]
        response = self.client.post("/api/assistant/chat",
                                    json={"message": "Add a reminder and finish the report", "history": history})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual([action["type"] for action in result["actions"]], ["create_task", "complete_task"])
        self.assertEqual(result["actions"][0], {"type": "create_task", "title": "Send reminder", "notes": "",
                                                "project": "Inbox", "priority": "low", "dueDate": None})
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"], before)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_completion_events WHERE user_id=?",
                                                (self.user_id,)).fetchone()[0], 0)
        chat = next(item["body"] for item in self.mock.requests if item["path"] == "/api/chat")
        self.assertEqual(chat["model"], "qwen3:8b")
        self.assertEqual(chat["stream"], False)
        self.assertIsInstance(chat["format"], dict)
        self.assertEqual(chat["keep_alive"], 0)
        self.assertEqual(chat["options"], {"temperature": 0})
        self.assertEqual(len(chat["messages"]), 10)
        self.assertTrue(chat["messages"][1]["content"].startswith("history-2"))
        self.assertEqual(len(chat["messages"][1]["content"]), 2000)
        system = chat["messages"][0]["content"]
        self.assertIn("Private deadline", system)
        self.assertIn("Quiet evenings work best", system)
        self.assertIn("Google Calendar is not configured.", system)
        self.assertNotIn("Foreign secret", system)
        self.assertNotIn("Foreign memory", system)

        confirmed = self.client.post("/api/assistant/actions", json={"actions": result["actions"]})
        self.assertEqual(confirmed.status_code, 200)
        tasks = confirmed.json()["tasks"]
        self.assertTrue(next(task for task in tasks if task["id"] == private["id"])["completed"])
        self.assertIn("Send reminder", {task["title"] for task in tasks})
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_completion_events WHERE user_id=?",
                                                (self.user_id,)).fetchone()[0], 1)

    def test_confirmed_actions_are_strict_current_owned_and_atomic(self):
        first = self.task("Original")
        second = self.task("Second")
        valid = self.client.post("/api/assistant/actions", json={"actions": [
            {"type": "update_task", "taskId": first["id"], "title": "Updated", "dueDate": "2026-10-01"},
            {"type": "delete_task", "taskId": second["id"]},
        ]})
        self.assertEqual(valid.status_code, 200)
        self.assertEqual([(task["title"], task["dueDate"]) for task in valid.json()["tasks"]],
                         [("Updated", "2026-10-01")])

        for actions, expected in (([], 400),
                                  ([{"type": "create_task", "title": str(index)} for index in range(6)], 400),
                                  ([{"type": "unknown"}], 409),
                                  ([{"type": "create_task", "title": "Bad date", "dueDate": "2026-02-31"}], 409),
                                  ([{"type": "delete_task", "taskId": "foreign"}], 409),
                                  ([{"type": "update_task", "taskId": first["id"]},], 409),
                                  ([{"type": "complete_task", "taskId": first["id"]},
                                    {"type": "delete_task", "taskId": first["id"]}], 409)):
            self.assertEqual(self.client.post("/api/assistant/actions", json={"actions": actions}).status_code, expected)
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"][0]["title"], "Updated")

        with self.database.connection(write=True) as connection:
            connection.execute("""CREATE TRIGGER reject_assistant_insert BEFORE INSERT ON tasks
                WHEN NEW.title='Fail transaction' BEGIN SELECT RAISE(ABORT, 'private detail'); END""")
        failed = self.client.post("/api/assistant/actions", json={"actions": [
            {"type": "update_task", "taskId": first["id"], "title": "Must roll back"},
            {"type": "create_task", "title": "Fail transaction"},
        ]})
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private detail", failed.text)
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"][0]["title"], "Updated")

    def test_plain_responses_and_model_failures_are_bounded(self):
        self.configure(selected=False)
        self.mock.models = []
        missing = self.client.post("/api/assistant/chat", json={"message": "Hello"})
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json(), {"error": "No Ollama model is available."})
        self.mock.models = ["qwen3:8b"]
        os.environ["OLLAMA_MODEL"] = "qwen3:8b"
        self.mock.chat_content = "Plain local response"
        plain = self.client.post("/api/assistant/chat", json={"message": "Hello"}).json()
        self.assertEqual(plain, {"message": "Plain local response", "actions": [], "model": "qwen3:8b"})
        self.mock.chat_content = "x" * 5000
        self.assertEqual(len(self.client.post("/api/assistant/chat", json={"message": "Hello"}).json()["message"]), 4000)
        self.mock.chat_content = ""
        self.assertEqual(self.client.post("/api/assistant/chat", json={"message": "Hello"}).status_code, 503)
        self.mock.invalid_chat_json = True
        self.assertEqual(self.client.post("/api/assistant/chat", json={"message": "Hello"}).status_code, 503)
        self.mock.invalid_chat_json = False
        self.mock.chat_status = 500
        failed = self.client.post("/api/assistant/chat", json={"message": "Hello"})
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"error": "Local assistant is unavailable."})
        for body in ({}, {"message": " "}, {"message": "x" * 4001}, {"message": 42},
                     {"message": "ok", "history": "not a list"},
                     {"message": "ok", "extra": "no"}):
            self.assertEqual(self.client.post("/api/assistant/chat", json=body).status_code, 400)

    def test_unload_discovers_running_model_and_never_loads_an_idle_one(self):
        self.configure(selected=False)
        unloaded = self.client.post("/api/assistant/unload", json={})
        self.assertEqual(unloaded.json(), {"unloaded": True, "model": "qwen3:8b"})
        generate = [item["body"] for item in self.mock.requests if item["path"] == "/api/generate"]
        self.assertEqual(generate, [{"model": "qwen3:8b", "prompt": "", "stream": False, "keep_alive": 0}])
        self.mock.running = []
        idle = self.client.post("/api/assistant/unload", json={})
        self.assertEqual(idle.json(), {"unloaded": False, "model": "qwen3:8b", "reason": "not_loaded"})
        self.assertEqual(len([item for item in self.mock.requests if item["path"] == "/api/generate"]), 1)
        self.mock.models = []
        self.assertEqual(self.client.post("/api/assistant/unload", json={}).status_code, 409)
        self.mock.running = ["qwen3:8b"]
        self.mock.generate_status = 500
        failed = self.client.post("/api/assistant/unload", json={})
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"error": "Local assistant is unavailable."})
        self.assertEqual(self.client.post("/api/assistant/unload", json={"model": "x" * 121}).status_code, 400)

    def test_loopback_guard_accepts_local_ollama_addresses_only(self):
        client = OllamaClient()
        for value in ("http://localhost:11434", "http://127.0.0.1:11434/", "http://[::1]:11434"):
            os.environ["OLLAMA_URL"] = value
            self.assertEqual(client.base_url(), value.rstrip("/"))
        for value in ("https://ollama.com", "http://192.168.1.2:11434", "file:///tmp/model",
                      "http://user:password@localhost:11434", "http://localhost:99999",
                      "http://localhost:11434/api"):
            os.environ["OLLAMA_URL"] = value
            with self.assertRaises(OllamaError):
                client.base_url()


if __name__ == "__main__":
    unittest.main()
