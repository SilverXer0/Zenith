"""Optional read-only Google Calendar OAuth and event access."""

import json
import math
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from http.client import HTTPException as HttpClientError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import Database, timestamp
from .errors import ApiError


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3"
MAX_REMOTE_BYTES = 2 * 1024 * 1024


class RemoteError(Exception):
    def __init__(self, status: int | None = None):
        super().__init__("Remote Calendar request failed.")
        self.status = status


def instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError):
        raise ApiError(400, "Calendar start and end must be valid, and end must be after start.") from None


def calendar_range(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    today = datetime.combine(datetime.now(timezone.utc).date(), time(), timezone.utc)

    def boundary(value: str) -> datetime:
        if len(value) == 10:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            parsed_date = date.fromisoformat(value)
            if parsed_date.isoformat() != value:
                raise ValueError
            return datetime.combine(parsed_date, time(), timezone.utc)
        return parse_instant(value)

    try:
        range_start = boundary(start) if start else today
        range_end = boundary(end) if end else today + timedelta(days=7)
    except ValueError:
        raise ApiError(400, "Calendar start and end must be valid, and end must be after start.") from None
    if range_end <= range_start:
        raise ApiError(400, "Calendar start and end must be valid, and end must be after start.")
    return range_start, range_end


class GoogleCalendar:
    def __init__(self, database: Database):
        self.database = database

    def configured(self) -> bool:
        return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))

    def redirect_uri(self) -> str:
        return os.environ.get("GOOGLE_REDIRECT_URI") or "http://127.0.0.1:3000/api/calendar/oauth/callback"

    def status(self, user_id: str) -> dict:
        connections = self.database.list_calendar_connections(user_id)
        account = next((item for item in connections if item["enabled"]), connections[0] if connections else None)
        return {"configured": self.configured(), "connected": bool(account),
                "calendarName": account["calendarName"] if account else None,
                "connectedAt": account["connectedAt"] if account else None}

    def connections(self, user_id: str) -> list[dict]:
        return self.database.list_calendar_connections(user_id)

    def connect_url(self, user_id: str) -> str:
        self._require_configured()
        state = secrets.token_hex(24)
        expires = instant(datetime.now(timezone.utc) + timedelta(minutes=10))
        self.database.save_calendar_state(user_id, state, expires)
        query = urlencode({
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "scope": CALENDAR_SCOPE,
            "state": state,
        })
        return f"{AUTHORIZATION_URL}?{query}"

    def complete_callback(self, state: str | None, code: str | None, oauth_error: str | None = None):
        if not state or len(state) > 128:
            raise ApiError(400, "Google Calendar authorization was incomplete.")
        user_id = self.database.consume_calendar_state(state)
        if not user_id:
            raise ApiError(400, "That Google Calendar authorization link has expired. Please try again.")
        if oauth_error or not code or len(code) > 4096:
            raise ApiError(400, "Google Calendar authorization was incomplete.")
        self._require_configured()
        try:
            token = self._post_token({
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": self.redirect_uri(),
                "grant_type": "authorization_code",
            })
        except RemoteError:
            raise ApiError(502, "Google Calendar authorization could not be completed.") from None
        access_token = self._token_text(token, "access_token")
        refresh_token = self._token_text(token, "refresh_token")
        if not access_token or not refresh_token:
            raise ApiError(502, "Google did not provide a reusable Calendar authorization. Please try connecting again.")
        calendar_name = "Google Calendar"
        try:
            primary = self._remote_json(f"{self._calendar_base()}/calendars/primary",
                                        headers={"Authorization": f"Bearer {access_token}"})
            if isinstance(primary.get("summary"), str) and primary["summary"].strip():
                calendar_name = primary["summary"].strip()[:200]
        except RemoteError:
            pass
        self.database.save_calendar_connection(user_id, access_token, refresh_token,
                                               self._token_expiration(token), calendar_name, timestamp())
        return user_id

    def events(self, user_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
        self._require_configured()
        range_start, range_end = calendar_range(start, end)
        connections = self.database.list_calendar_connections(user_id, enabled_only=True, include_secrets=True)
        if not connections:
            raise ApiError(409, "No enabled Google Calendar connections are available.")
        events = []
        failures = 0
        errors = []
        for connection in connections:
            try:
                events.extend(self._list_events(user_id, connection, range_start, range_end))
            except (ApiError, RemoteError) as error:
                failures += 1
                errors.append(error)
        if failures and not events:
            auth_error = next((error for error in errors if isinstance(error, ApiError) and error.status == 401), None)
            if auth_error:
                raise auth_error
            raise ApiError(503, "Google Calendar is temporarily unavailable.")
        events.sort(key=lambda event: (event.get("start") or "9999", event.get("id") or ""))
        return events[:100]

    def projection(self, user_id: str, start: str, end: str) -> dict:
        connections = self.database.list_calendar_connections(user_id)
        result = {"connected": bool(connections), "available": False, "events": []}
        if not connections or not self.configured():
            return result
        try:
            result["events"] = self.events(user_id, start, end)
            result["available"] = True
        except (ApiError, RemoteError):
            pass
        return result

    def assistant_context(self, user_id: str) -> str:
        if not self.configured():
            return "Google Calendar is not configured."
        connections = self.database.list_calendar_connections(user_id, enabled_only=True, include_secrets=True)
        if not connections:
            if self.database.calendar_connected(user_id):
                return "Google Calendar connections are currently disabled."
            return "Google Calendar is not connected."
        events = []
        successful = False
        for connection in connections:
            try:
                fetched = self._list_events(user_id, connection, *calendar_range(None, None))
                successful = True
                events.extend((connection, event) for event in fetched)
            except (ApiError, RemoteError):
                continue
        if not successful:
            return "Google Calendar is connected but temporarily unavailable."
        if not events:
            return "No upcoming Google Calendar events in the next seven days."

        def clean(value, limit):
            return str(value).replace("\r", " ").replace("\n", " ")[:limit]

        lines = []
        events.sort(key=lambda item: (item[1].get("start") or "9999", item[1].get("id") or ""))
        for connection, event in events[:50]:
            title = clean(event.get("title") or "Untitled event", 200)
            start = clean(event.get("start") or "time unavailable", 80)
            end = f" to {clean(event['end'], 80)}" if event.get("end") else ""
            location = f" | {clean(event['location'], 200)}" if event.get("location") else ""
            source = clean(connection.get("displayName") or connection.get("calendarName") or "Google Calendar", 80)
            lines.append(f"- [{source}] {title} | {start}{end}{location}")
        return "\n".join(lines)

    def availability(self, user_id: str, day: str, timezone_name: str) -> dict:
        try:
            parsed_day = date.fromisoformat(day)
            if parsed_day.isoformat() != day:
                raise ValueError
            local_zone = ZoneInfo(timezone_name or "UTC")
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ApiError(400, "Calendar date and timezone must be valid.") from None

        workday_start = datetime.combine(parsed_day, time(8), local_zone)
        workday_end = datetime.combine(parsed_day, time(20), local_zone)
        result = {"date": day, "timezone": timezone_name or "UTC", "connected": False,
                  "available": False,
                  "workday": {"start": workday_start.isoformat(timespec="minutes"),
                              "end": workday_end.isoformat(timespec="minutes")},
                  "freeWindows": [], "conflicts": [], "events": []}
        connections = self.database.list_calendar_connections(user_id)
        result["connected"] = bool(connections)
        if not connections or not self.configured():
            return result

        events = self.events(user_id, instant(workday_start.astimezone(timezone.utc)),
                             instant(workday_end.astimezone(timezone.utc)))
        result["available"] = True
        result["events"] = events
        intervals = []
        for event in events:
            try:
                start = self._event_local_time(event.get("start"), local_zone, event.get("allDay"), False)
                end = self._event_local_time(event.get("end"), local_zone, event.get("allDay"), True)
            except (TypeError, ValueError):
                continue
            if end <= workday_start or start >= workday_end or end <= start:
                continue
            intervals.append((max(start, workday_start), min(end, workday_end), event))

        intervals.sort(key=lambda item: (item[0], item[1], item[2].get("id") or ""))
        merged = []
        for start, end, event in intervals:
            if merged and start < merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], end)
                merged[-1]["events"].append(event)
            else:
                merged.append({"start": start, "end": end, "events": [event]})

        cursor = workday_start
        for busy in merged:
            if busy["start"] > cursor:
                result["freeWindows"].append(self._free_window(cursor, busy["start"]))
            cursor = max(cursor, busy["end"])
        if cursor < workday_end:
            result["freeWindows"].append(self._free_window(cursor, workday_end))

        for busy in merged:
            if len(busy["events"]) < 2:
                continue
            result["conflicts"].append({
                "start": busy["start"].isoformat(timespec="minutes"),
                "end": busy["end"].isoformat(timespec="minutes"),
                "events": [{"id": event.get("id"), "title": event.get("title"),
                            "calendarName": event.get("calendarName")} for event in busy["events"]],
            })
        return result

    @staticmethod
    def _event_local_time(value, local_zone: ZoneInfo, all_day: bool, end: bool) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError
        if all_day:
            parsed = date.fromisoformat(value)
            return datetime.combine(parsed, time(), local_zone)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(local_zone)

    @staticmethod
    def _free_window(start: datetime, end: datetime) -> dict:
        return {"start": start.isoformat(timespec="minutes"),
                "end": end.isoformat(timespec="minutes"),
                "durationMinutes": int((end - start).total_seconds() // 60)}

    def disconnect(self, user_id: str, connection_id: str | None = None):
        self.database.delete_calendar_connection(user_id, connection_id)

    def _require_configured(self):
        if not self.configured():
            raise ApiError(409, "Google Calendar is not configured on this Zenith server.")

    def _calendar_base(self) -> str:
        return (os.environ.get("GOOGLE_CALENDAR_URL") or CALENDAR_URL).rstrip("/")

    def _token_url(self) -> str:
        return os.environ.get("GOOGLE_TOKEN_URL") or TOKEN_URL

    def _post_token(self, values: dict) -> dict:
        return self._remote_json(self._token_url(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 body=urlencode(values).encode("utf-8"))

    def _access_token(self, user_id: str, connection_id: str, force_refresh: bool = False) -> str:
        account = self.database.calendar_connection(user_id, connection_id)
        if not account:
            raise ApiError(409, "Google Calendar connection was not found.")
        expires_at = account.get("tokenExpiresAt")
        try:
            expires = parse_instant(expires_at) if expires_at else datetime.min.replace(tzinfo=timezone.utc)
        except ApiError:
            expires = datetime.min.replace(tzinfo=timezone.utc)
        if (not force_refresh and account.get("accessToken")
                and expires > datetime.now(timezone.utc) + timedelta(minutes=1)):
            return account["accessToken"]
        if not account.get("refreshToken"):
            raise ApiError(401, "Google Calendar authorization has expired. Please reconnect it.")
        try:
            token = self._post_token({
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "refresh_token": account["refreshToken"],
                "grant_type": "refresh_token",
            })
        except RemoteError as error:
            if error.status in (400, 401):
                raise ApiError(401, "Google Calendar authorization has expired. Please reconnect it.") from None
            raise
        access_token = self._token_text(token, "access_token")
        if not access_token:
            raise ApiError(401, "Google Calendar authorization has expired. Please reconnect it.")
        refresh_token = self._token_text(token, "refresh_token") or account["refreshToken"]
        self.database.update_calendar_tokens(connection_id, access_token, refresh_token, self._token_expiration(token))
        return access_token

    def _list_events(self, user_id: str, connection: dict,
                     range_start: datetime, range_end: datetime) -> list[dict]:
        try:
            access_token = self._access_token(user_id, connection["id"])
        except RemoteError:
            raise ApiError(503, "Google Calendar is temporarily unavailable.") from None

        def fetch(current_token: str) -> list[dict]:
            events = []
            page_token = None
            for _ in range(10):
                values = {"singleEvents": "true", "orderBy": "startTime",
                          "timeMin": instant(range_start), "timeMax": instant(range_end),
                          "maxResults": str(100 - len(events))}
                if page_token:
                    values["pageToken"] = page_token
                query = urlencode(values)
                payload = self._remote_json(f"{self._calendar_base()}/calendars/primary/events?{query}",
                                            headers={"Authorization": f"Bearer {current_token}"})
                items = payload.get("items", [])
                if not isinstance(items, list):
                    raise ApiError(503, "Google Calendar is temporarily unavailable.")
                for item in items:
                    if isinstance(item, dict):
                        event = self._event(item)
                        event["calendarId"] = connection["id"]
                        event["calendarName"] = connection.get("displayName") or connection.get("calendarName") or "Google Calendar"
                        events.append(event)
                if len(events) >= 100:
                    return events[:100]
                page_token = payload.get("nextPageToken")
                if not page_token:
                    return events
                if not isinstance(page_token, str) or len(page_token) > 4096:
                    raise ApiError(503, "Google Calendar is temporarily unavailable.")
            raise ApiError(503, "Google Calendar is temporarily unavailable.")

        try:
            events = fetch(access_token)
        except RemoteError as error:
            if error.status != 401:
                raise ApiError(503, "Google Calendar is temporarily unavailable.") from None
            try:
                events = fetch(self._access_token(user_id, connection["id"], force_refresh=True))
            except ApiError:
                raise
            except RemoteError:
                raise ApiError(503, "Google Calendar is temporarily unavailable.") from None
        return events

    def _remote_json(self, url: str, *, method: str = "GET", headers: dict | None = None,
                     body: bytes | None = None) -> dict:
        try:
            request = Request(url, data=body, method=method,
                              headers={"Accept": "application/json", **(headers or {})})
            with urlopen(request, timeout=10) as response:
                raw = response.read(MAX_REMOTE_BYTES + 1)
        except HTTPError as error:
            try:
                error.read(MAX_REMOTE_BYTES + 1)
            finally:
                error.close()
            raise RemoteError(error.code) from None
        except (URLError, OSError, TimeoutError, ValueError, HttpClientError):
            raise RemoteError() from None
        if len(raw) > MAX_REMOTE_BYTES:
            raise RemoteError()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteError() from None
        if not isinstance(payload, dict):
            raise RemoteError()
        return payload

    @staticmethod
    def _token_text(token: dict, key: str) -> str | None:
        value = token.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _token_expiration(token: dict) -> str:
        try:
            seconds = float(token.get("expires_in", 3600))
            if not math.isfinite(seconds):
                raise ValueError
            expires = datetime.now(timezone.utc) + timedelta(seconds=max(60, seconds))
        except (TypeError, ValueError, OverflowError):
            expires = datetime.now(timezone.utc) + timedelta(hours=1)
        return instant(expires)

    @staticmethod
    def _event(event: dict) -> dict:
        start = event.get("start") if isinstance(event.get("start"), dict) else {}
        end = event.get("end") if isinstance(event.get("end"), dict) else {}
        all_day = bool(isinstance(start.get("date"), str) and not start.get("dateTime"))
        title = event.get("summary") if isinstance(event.get("summary"), str) else None
        location = event.get("location") if isinstance(event.get("location"), str) else None
        status = event.get("status") if isinstance(event.get("status"), str) else None
        event_id = event.get("id") if isinstance(event.get("id"), str) else None
        start_value = start.get("dateTime") if isinstance(start.get("dateTime"), str) else start.get("date")
        end_value = end.get("dateTime") if isinstance(end.get("dateTime"), str) else end.get("date")
        return {"id": event_id, "title": title or "Untitled event",
                "start": start_value if isinstance(start_value, str) else None,
                "end": end_value if isinstance(end_value, str) else None, "allDay": all_day,
                "location": location, "status": status or "confirmed"}
