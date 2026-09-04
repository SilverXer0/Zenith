import http.client
import json
import queue
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sqlite3
import uvicorn

from backend.app import create_app
from backend.auth import password_hash, token_hash
from backend.database import timestamp


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for server state")


class EventStream:
    def __init__(self, port, cookie):
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.connection.request("GET", "/api/events", headers={"Cookie": cookie, "Accept": "text/event-stream"})
        self.response = self.connection.getresponse()
        self.frames = queue.Queue()
        self.closing = False
        self.error = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            frame = []
            while line := self.response.readline():
                text = line.decode("utf-8").rstrip("\r\n")
                if text:
                    frame.append(text)
                elif frame:
                    self.frames.put("\n".join(frame))
                    frame = []
        except Exception as error:
            if not self.closing or not isinstance(error, (OSError, http.client.HTTPException)):
                self.error = error
                self.frames.put(error)
        finally:
            self.frames.put(None)

    def event(self, name, timeout=3):
        deadline = time.monotonic() + timeout
        while True:
            frame = self.frames.get(timeout=max(0.001, deadline - time.monotonic()))
            if frame is None or isinstance(frame, Exception):
                raise AssertionError(f"Stream ended before {name}: {frame}")
            if frame.startswith(":"):
                continue
            if not frame.startswith(f"event: {name}\n"):
                raise AssertionError(f"Unexpected event: {frame}")
            return frame

    def no_changes(self, duration=0.1):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty:
                return
            if frame is None or isinstance(frame, Exception) or not frame.startswith(":"):
                raise AssertionError(f"Unexpected event or closure: {frame}")

    def closed(self):
        deadline = time.monotonic() + 3
        while True:
            frame = self.frames.get(timeout=max(0.001, deadline - time.monotonic()))
            if frame is None:
                return
            if isinstance(frame, Exception) or not frame.startswith(":"):
                raise AssertionError(f"Unexpected frame before closure: {frame}")

    def close(self):
        self.closing = True
        if self.connection.sock:
            try:
                self.connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=3)
        # HTTPConnection.close() also closes its active response. Let the reader
        # finish first, so two threads cannot close the same buffered reader.
        self.connection.close()
        self.response.close()
        if self.thread.is_alive():
            raise AssertionError("Event reader did not stop")
        if self.error:
            raise AssertionError(f"Event reader failed: {self.error}")


class LiveEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zenith-live-python-")
        self.addCleanup(temporary.cleanup)
        self.app = create_app(Path(temporary.name))
        self.app.state.task_events.heartbeat_interval = 0.05  # Exercise idle expiry without a 15-second test wait.
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", timeout_graceful_shutdown=1))
        self.server_thread = threading.Thread(target=lambda: self.server.run(sockets=[self.listener]), daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        eventually(lambda: self.server.started)
        self.credentials = {"displayName": "Live user", "password": "temporary live password"}
        status, body, headers = self.api("POST", "/api/auth/setup", self.credentials)
        self.assertEqual(status, 201)
        self.user_id = body["user"]["id"]
        self.cookie = headers["set-cookie"].split(";", 1)[0]

    def stop_server(self):
        self.server.should_exit = True
        self.server_thread.join(timeout=4)
        if self.server_thread.is_alive():
            self.server.force_exit = True
            self.server_thread.join(timeout=2)
        self.listener.close()
        self.assertFalse(self.server_thread.is_alive(), "Test server did not stop")
        self.assertEqual(self.app.state.task_events.count, 0)

    def api(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, dict(response.getheaders())
        finally:
            connection.close()

    def stream(self, cookie):
        stream = EventStream(self.port, cookie)
        self.addCleanup(stream.close)
        self.assertEqual(stream.response.status, 200)
        self.assertIn("text/event-stream", stream.response.getheader("content-type"))
        self.assertEqual(stream.response.getheader("cache-control"), "no-store")
        self.assertEqual(stream.response.getheader("x-accel-buffering"), "no")
        self.assertIn("data: {}", stream.event("ready"))
        return stream

    def second_session(self):
        status, _, headers = self.api("POST", "/api/auth/session", self.credentials)
        self.assertEqual(status, 201)
        return headers["set-cookie"].split(";", 1)[0]

    def test_all_task_mutations_reach_both_sessions_after_commit(self):
        self.assertEqual(self.api("GET", "/api/events")[0], 401)
        second_cookie = self.second_session()
        first, second = self.stream(self.cookie), self.stream(second_cookie)
        status, body, _ = self.api("POST", "/api/tasks", {"title": "Live capture"}, self.cookie)
        self.assertEqual(status, 201)
        task_id = body["task"]["id"]
        for stream in (first, second):
            self.assertEqual(stream.event("tasks_changed"), "event: tasks_changed\ndata: {}")
        self.assertEqual(self.api("GET", "/api/tasks", cookie=second_cookie)[1]["tasks"][0]["title"], "Live capture")
        for changes in ({"title": "Phone edit"}, {"completed": True}, {"completed": False}):
            self.assertEqual(self.api("PATCH", f"/api/tasks/{task_id}", changes, second_cookie)[0], 200)
            for stream in (first, second):
                stream.event("tasks_changed")
            task = self.api("GET", "/api/tasks", cookie=self.cookie)[1]["tasks"][0]
            for key, expected in changes.items():
                self.assertEqual(task[key], expected)
        self.assertEqual(self.api("DELETE", f"/api/tasks/{task_id}", cookie=self.cookie)[0], 204)
        for stream in (first, second):
            stream.event("tasks_changed")
        self.assertEqual(self.api("GET", "/api/tasks", cookie=second_cookie)[1]["tasks"], [])

    def test_changes_are_private_to_the_owner(self):
        with self.app.state.database.connection(write=True) as connection:
            connection.execute("INSERT INTO users (id,display_name,password_hash,created_at) VALUES (?,?,?,?)",
                               ("foreign-user", "Other user", password_hash("other password"), timestamp()))
        status, _, headers = self.api("POST", "/api/auth/session", {"displayName": "Other user", "password": "other password"})
        self.assertEqual(status, 201)
        other_cookie = headers["set-cookie"].split(";", 1)[0]
        mine, other = self.stream(self.cookie), self.stream(other_cookie)
        self.api("POST", "/api/tasks", {"title": "Private capture"}, self.cookie)
        mine.event("tasks_changed")
        other.no_changes()
        status, body, _ = self.api("POST", "/api/tasks", {"title": "Other capture"}, other_cookie)
        self.assertEqual(status, 201)
        other.event("tasks_changed")
        mine.no_changes()
        self.assertEqual(self.api("PATCH", f'/api/tasks/{body["task"]["id"]}', {"completed": True}, self.cookie)[0], 404)
        mine.no_changes()
        other.no_changes()

    def test_failed_writes_emit_no_change_event(self):
        stream = self.stream(self.cookie)
        self.assertEqual(self.api("POST", "/api/tasks", {"title": ""}, self.cookie)[0], 400)
        self.assertEqual(self.api("DELETE", "/api/tasks/missing", cookie=self.cookie)[0], 404)
        with patch.object(self.app.state.database, "_record_completion", side_effect=sqlite3.IntegrityError("test failure")):
            self.assertEqual(self.api("POST", "/api/tasks", {"title": "Rolled back", "completed": True}, self.cookie)[0], 503)
        stream.no_changes()
        self.assertEqual(self.api("GET", "/api/tasks", cookie=self.cookie)[1]["tasks"], [])

    def test_disconnect_cleanup_and_reconnect_snapshot(self):
        stream = self.stream(self.cookie)
        stream.close()
        eventually(lambda: self.app.state.task_events.count == 0)
        self.assertEqual(self.api("POST", "/api/tasks", {"title": "Missed while offline"}, self.cookie)[0], 201)
        reconnected = self.stream(self.cookie)
        self.assertEqual(self.api("GET", "/api/tasks", cookie=self.cookie)[1]["tasks"][0]["title"], "Missed while offline")
        reconnected.no_changes()  # Recovery is a fresh snapshot, not stale event replay.

    def test_logout_closes_only_the_logged_out_sessions_streams(self):
        second_cookie = self.second_session()
        first, second = self.stream(self.cookie), self.stream(second_cookie)
        self.assertEqual(self.api("DELETE", "/api/auth/session", cookie=self.cookie)[0], 204)
        first.closed()
        self.assertEqual(self.api("GET", "/api/events", cookie=self.cookie)[0], 401)
        self.assertEqual(self.app.state.task_events.count, 1)
        self.assertEqual(self.api("POST", "/api/tasks", {"title": "Still signed in"}, second_cookie)[0], 201)
        second.event("tasks_changed")

    def test_idle_expiry_and_external_revocation_close_streams(self):
        for revoke in (False, True):
            cookie = self.second_session()
            stream = self.stream(cookie)
            hashed = token_hash(cookie.split("=", 1)[1])
            with self.app.state.database.connection(write=True) as connection:
                if revoke:
                    connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
                else:
                    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    connection.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (expired, hashed))
            stream.closed()
            self.assertEqual(self.api("GET", "/api/events", cookie=cookie)[0], 401)
        self.assertEqual(self.app.state.task_events.count, 0)
