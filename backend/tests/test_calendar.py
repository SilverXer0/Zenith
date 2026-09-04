import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import COOKIE_NAME, password_hash
from backend.calendar import CALENDAR_SCOPE, GoogleCalendar, calendar_range, instant
from backend.database import timestamp


ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = {"displayName": "Calendar user", "password": "Calendar passphrase 2026!"}
EVENTS = [
    {"id": "event-1", "summary": "Focus time",
     "start": {"dateTime": "2026-09-04T18:00:00-07:00"},
     "end": {"dateTime": "2026-09-04T19:00:00-07:00"},
     "location": "Home", "status": "confirmed"},
    {"id": "event-2", "start": {"date": "2026-09-04"}, "end": {"date": "2026-09-05"}},
]


class CalendarMock:
    def __init__(self):
        self.requests = []
        self.events_status = 200
        self.invalid_events_json = False
        self.paginated = False
        self.events = list(EVENTS)
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

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
                form = parse_qs(body)
                fixture.requests.append({"method": "POST", "path": self.path, "form": form,
                                         "authorization": self.headers.get("Authorization")})
                if self.path != "/token":
                    return self.send_json(404, {"error": "not_found"})
                if form.get("grant_type") == ["authorization_code"]:
                    if form.get("code") == ["bad-code"]:
                        return self.send_json(400, {"error": "invalid_grant"})
                    return self.send_json(200, {"access_token": "calendar-access",
                                                "refresh_token": "calendar-refresh", "expires_in": 3600})
                if form.get("refresh_token") == ["expired-refresh"]:
                    return self.send_json(400, {"error": "invalid_grant"})
                return self.send_json(200, {"access_token": "refreshed-access",
                                            "refresh_token": "rotated-refresh", "expires_in": 3600})

            def do_GET(self):
                fixture.requests.append({"method": "GET", "path": self.path,
                                         "authorization": self.headers.get("Authorization")})
                parsed = urlparse(self.path)
                if parsed.path == "/calendar/v3/calendars/primary":
                    return self.send_json(200, {"summary": "Personal Calendar"})
                if parsed.path != "/calendar/v3/calendars/primary/events":
                    return self.send_json(404, {"error": "not_found"})
                if self.headers.get("Authorization") == "Bearer stale-but-valid":
                    return self.send_json(401, {"error": "invalid_token"})
                if fixture.invalid_events_json:
                    body = b"not-json"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                query = parse_qs(parsed.query)
                if fixture.paginated and not query.get("pageToken"):
                    return self.send_json(200, {"items": fixture.events[:1], "nextPageToken": "second-page"})
                if fixture.paginated:
                    return self.send_json(200, {"items": fixture.events[1:]})
                return self.send_json(fixture.events_status,
                                      {"items": fixture.events} if fixture.events_status == 200 else {"error": "unavailable"})

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


class CalendarTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {})
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
                    "GOOGLE_TOKEN_URL", "GOOGLE_CALENDAR_URL"):
            os.environ.pop(key, None)
        temporary = tempfile.TemporaryDirectory(prefix="zenith-calendar-test-")
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
        self.mock = None

    def configure(self):
        self.mock = CalendarMock()
        self.addCleanup(self.mock.close)
        os.environ.update({
            "GOOGLE_CLIENT_ID": "test-client",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_REDIRECT_URI": "https://zenith.test/api/calendar/oauth/callback",
            "GOOGLE_TOKEN_URL": f"{self.mock.url}/token",
            "GOOGLE_CALENDAR_URL": f"{self.mock.url}/calendar/v3",
        })

    def connect(self, client=None, code="mock-code"):
        client = client or self.client
        response = client.get("/api/calendar/connect", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        authorization = urlparse(response.headers["location"])
        query = parse_qs(authorization.query)
        callback = client.get("/api/calendar/oauth/callback",
                              params={"state": query["state"][0], "code": code},
                              follow_redirects=False)
        return authorization, query, callback

    def node_snapshot(self):
        env = {**os.environ, "ZENITH_DATA_DIR": str(self.directory),
               "ZENITH_TEST_COOKIE": f"{COOKIE_NAME}={self.client.cookies[COOKIE_NAME]}",
               "ZENITH_TEST_CREDENTIALS": json.dumps(CREDENTIALS),
               "ZENITH_TEST_DATE": "2026-09-04", "ZENITH_TEST_OFFSET": "0",
               "OLLAMA_URL": "http://127.0.0.1:1"}
        result = subprocess.run([os.environ.get("ZENITH_NODE", "node"),
                                 str(ROOT / "backend/tests/node_bridge.mjs"), "read"],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_unconfigured_calendar_is_optional_and_all_private_routes_require_auth(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        for method, path in (("GET", "/api/calendar/status"), ("GET", "/api/calendar/connect"),
                             ("GET", "/api/calendar/events"), ("DELETE", "/api/calendar/connection")):
            self.assertEqual(anonymous.request(method, path).status_code, 401)
        self.assertEqual(anonymous.get("/api/calendar/oauth/callback").status_code, 400)
        self.assertEqual(self.client.get("/api/calendar/status").json(),
                         {"configured": False, "connected": False, "calendarName": None, "connectedAt": None})
        for path in ("/api/calendar/connect", "/api/calendar/events"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("secret", response.text.lower())
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Works without Calendar"}).status_code, 201)

    def test_oauth_events_planning_disconnect_and_node_compatibility(self):
        self.configure()
        self.mock.paginated = True
        authorization, query, callback = self.connect()
        self.assertEqual((authorization.scheme, authorization.netloc, authorization.path),
                         ("https", "accounts.google.com", "/o/oauth2/v2/auth"))
        self.assertEqual(query["client_id"], ["test-client"])
        self.assertEqual(query["redirect_uri"], [os.environ["GOOGLE_REDIRECT_URI"]])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertEqual(query["scope"], [CALENDAR_SCOPE])
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(callback.headers["location"], "/?calendar=connected")
        token_request = next(item for item in self.mock.requests if item["method"] == "POST")
        self.assertEqual(token_request["form"]["grant_type"], ["authorization_code"])
        self.assertEqual(token_request["form"]["redirect_uri"], [os.environ["GOOGLE_REDIRECT_URI"]])
        status = self.client.get("/api/calendar/status").json()
        self.assertEqual(status["configured"], True)
        self.assertEqual(status["connected"], True)
        self.assertEqual(status["calendarName"], "Personal Calendar")
        self.assertIsNotNone(status["connectedAt"])
        replay = self.client.get("/api/calendar/oauth/callback",
                                 params={"state": query["state"][0], "code": "mock-code"})
        self.assertEqual(replay.status_code, 400)
        expected = [
            {"id": "event-1", "title": "Focus time", "start": "2026-09-04T18:00:00-07:00",
             "end": "2026-09-04T19:00:00-07:00", "allDay": False,
             "location": "Home", "status": "confirmed"},
            {"id": "event-2", "title": "Untitled event", "start": "2026-09-04",
             "end": "2026-09-05", "allDay": True, "location": None, "status": "confirmed"},
        ]
        response = self.client.get("/api/calendar/events",
                                   params={"start": "2026-09-04T00:00:00Z", "end": "2026-09-11T00:00:00Z"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"], expected)
        event_requests = [item for item in self.mock.requests if "/events?" in item["path"]]
        event_request = event_requests[0]
        event_query = parse_qs(urlparse(event_request["path"]).query)
        self.assertEqual(event_query["singleEvents"], ["true"])
        self.assertEqual(event_query["orderBy"], ["startTime"])
        self.assertEqual(event_query["maxResults"], ["100"])
        self.assertEqual(event_query["timeMin"], ["2026-09-04T00:00:00.000Z"])
        self.assertEqual(event_query["timeMax"], ["2026-09-11T00:00:00.000Z"])
        self.assertEqual(parse_qs(urlparse(event_requests[1]["path"]).query)["pageToken"], ["second-page"])
        morning = self.client.get("/api/briefing/morning?date=2026-09-04").json()
        weekly = self.client.get("/api/weekly-plan?start=2026-09-04").json()
        self.assertEqual(morning["calendar"], {"connected": True, "available": True, "events": expected})
        self.assertEqual(morning["summary"], "2 calendar events")
        self.assertEqual(weekly["calendar"], {"connected": True, "available": True, "events": expected})
        assistant_context = GoogleCalendar(self.database).assistant_context(self.user_id)
        self.assertIn("Focus time", assistant_context)
        self.assertIn("2026-09-04T18:00:00-07:00", assistant_context)
        self.assertEqual(self.node_snapshot()["calendarStatus"], status)
        self.assertEqual(self.client.delete("/api/calendar/connection").status_code, 204)
        self.assertEqual(self.client.get("/api/calendar/status").json()["connected"], False)
        self.assertEqual(self.client.get("/api/calendar/events").status_code, 409)
        self.assertEqual(self.client.delete("/api/calendar/connection").status_code, 204)

    def test_unauthenticated_callback_uses_state_owner_not_browser_session(self):
        self.configure()
        with self.database.connection(write=True) as connection:
            connection.execute("INSERT INTO users (id,display_name,password_hash,created_at) VALUES (?,?,?,?)",
                               ("other-user", "Other", password_hash("other passphrase"), timestamp()))
        other = TestClient(self.app)
        other.__enter__()
        self.addCleanup(other.__exit__, None, None, None)
        self.assertEqual(other.post("/api/auth/session",
                                    json={"displayName": "Other", "password": "other passphrase"}).status_code, 201)
        response = other.get("/api/calendar/connect", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        callback = TestClient(self.app)
        self.addCleanup(callback.close)
        self.assertEqual(callback.get("/api/calendar/oauth/callback",
                                      params={"state": state, "code": "mock-code"},
                                      follow_redirects=False).status_code, 302)
        self.assertFalse(self.client.get("/api/calendar/status").json()["connected"])
        self.assertTrue(other.get("/api/calendar/status").json()["connected"])

    def test_expired_and_incomplete_states_are_consumed_without_remote_calls(self):
        self.configure()
        response = self.client.get("/api/calendar/connect", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        with self.database.connection(write=True) as connection:
            connection.execute("UPDATE calendar_oauth_states SET expires_at=? WHERE state=?",
                               ("2000-01-01T00:00:00.000Z", state))
        expired = self.client.get("/api/calendar/oauth/callback",
                                  params={"state": state, "code": "mock-code"})
        self.assertEqual(expired.status_code, 400)
        self.assertEqual([item for item in self.mock.requests if item["method"] == "POST"], [])
        response = self.client.get("/api/calendar/connect", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        incomplete = self.client.get("/api/calendar/oauth/callback", params={"state": state, "error": "access_denied"})
        self.assertEqual(incomplete.status_code, 400)
        retry = self.client.get("/api/calendar/oauth/callback", params={"state": state, "code": "mock-code"})
        self.assertEqual(retry.status_code, 400)

    def test_expired_token_refresh_and_unauthorized_retry_update_stored_tokens(self):
        self.configure()
        expires = instant(datetime.now(timezone.utc) + timedelta(hours=1))
        self.database.save_calendar_account(self.user_id, "stale-but-valid", "calendar-refresh",
                                            expires, "Personal", timestamp())
        response = self.client.get("/api/calendar/events",
                                   params={"start": "2026-09-04", "end": "2026-09-05"})
        self.assertEqual(response.status_code, 200)
        account = self.database.calendar_account(self.user_id)
        self.assertEqual(account["accessToken"], "refreshed-access")
        self.assertEqual(account["refreshToken"], "rotated-refresh")
        refresh = next(item for item in self.mock.requests
                       if item["method"] == "POST" and item["form"].get("grant_type") == ["refresh_token"])
        self.assertEqual(refresh["form"]["refresh_token"], ["calendar-refresh"])
        self.database.save_calendar_account(self.user_id, "", "expired-refresh",
                                            "2000-01-01T00:00:00.000Z", "Personal", timestamp())
        reauth = self.client.get("/api/calendar/events")
        self.assertEqual(reauth.status_code, 401)
        self.assertEqual(reauth.json(), {"error": "Google Calendar authorization has expired. Please reconnect it."})

    def test_bad_ranges_and_remote_failures_are_bounded_and_generic(self):
        self.configure()
        self.database.save_calendar_account(self.user_id, "calendar-access", "calendar-refresh",
                                            instant(datetime.now(timezone.utc) + timedelta(hours=1)),
                                            "Personal", timestamp())
        for start, end in (("bad", "2026-09-05T00:00:00Z"),
                           ("2026-W36-5", "2026-09-06"),
                           ("2026-09-05T00:00:00", "2026-09-06T00:00:00Z"),
                           ("2026-09-05", "2026-09-05")):
            self.assertEqual(self.client.get("/api/calendar/events",
                                             params={"start": start, "end": end}).status_code, 400)
        self.mock.events_status = 500
        failed = self.client.get("/api/calendar/events")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"error": "Google Calendar is temporarily unavailable."})
        self.mock.events_status = 200
        self.mock.invalid_events_json = True
        invalid = self.client.get("/api/calendar/events")
        self.assertEqual(invalid.status_code, 503)
        self.assertNotIn(self.mock.url, invalid.text)
        os.environ["GOOGLE_CALENDAR_URL"] = "://invalid"
        malformed = self.client.get("/api/calendar/events")
        self.assertEqual(malformed.status_code, 503)

    def test_event_results_are_capped_at_one_hundred(self):
        self.configure()
        self.mock.events = [{"id": f"event-{index}", "summary": f"Event {index}",
                             "start": {"dateTime": "2026-09-04T12:00:00Z"},
                             "end": {"dateTime": "2026-09-04T13:00:00Z"}}
                            for index in range(105)]
        self.database.save_calendar_account(self.user_id, "calendar-access", "calendar-refresh",
                                            instant(datetime.now(timezone.utc) + timedelta(hours=1)),
                                            "Personal", timestamp())
        events = self.client.get("/api/calendar/events").json()["events"]
        self.assertEqual(len(events), 100)
        self.assertEqual(events[0]["id"], "event-0")
        self.assertEqual(events[-1]["id"], "event-99")

    def test_schema_upgrade_preserves_old_refresh_token_and_adds_calendar_fields(self):
        temporary = tempfile.TemporaryDirectory(prefix="zenith-old-calendar-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        path = directory / "zenith.sqlite"
        with sqlite3.connect(path) as connection:
            connection.executescript("""
                CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, password_hash TEXT, created_at TEXT NOT NULL);
                CREATE TABLE calendar_accounts (user_id TEXT PRIMARY KEY, refresh_token TEXT);
                INSERT INTO users VALUES ('old-user','Old','hash','2025-01-01T00:00:00.000Z');
                INSERT INTO calendar_accounts VALUES ('old-user','preserved-refresh');
            """)
        migrated = create_app(directory)
        with TestClient(migrated):
            account = migrated.state.database.calendar_account("old-user")
        self.assertEqual(account["refreshToken"], "preserved-refresh")
        self.assertIsNotNone(account["connectedAt"])
        self.assertEqual(set(account), {"userId", "accessToken", "refreshToken", "tokenExpiresAt",
                                        "calendarName", "connectedAt"})

    def test_range_parser_normalizes_offsets_and_defaults_to_seven_days(self):
        start, end = calendar_range("2026-09-04T01:00:00-07:00", "2026-09-05T01:00:00-07:00")
        self.assertEqual(instant(start), "2026-09-04T08:00:00.000Z")
        self.assertEqual(instant(end), "2026-09-05T08:00:00.000Z")
        default_start, default_end = calendar_range(None, None)
        self.assertEqual(default_end - default_start, timedelta(days=7))


if __name__ == "__main__":
    unittest.main()
