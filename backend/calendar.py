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
        return os.environ.get("GOOGLE_REDIRECT_URI") or "http://127.0.0.1:8000/api/calendar/oauth/callback"

    def status(self, user_id: str) -> dict:
        account = self.database.calendar_account(user_id)
        return {"configured": self.configured(), "connected": bool(account),
                "calendarName": account["calendarName"] if account else None,
                "connectedAt": account["connectedAt"] if account else None}

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
        existing = self.database.calendar_account(user_id)
        refresh_token = self._token_text(token, "refresh_token") or (existing or {}).get("refreshToken")
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
        self.database.save_calendar_account(user_id, access_token, refresh_token,
                                            self._token_expiration(token), calendar_name, timestamp())

    def events(self, user_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
        self._require_configured()
        range_start, range_end = calendar_range(start, end)
        return self._list_events(user_id, range_start, range_end)

    def projection(self, user_id: str, start: str, end: str) -> dict:
        account = self.database.calendar_account(user_id)
        result = {"connected": bool(account), "available": False, "events": []}
        if not account or not self.configured():
            return result
        try:
            result["events"] = self.events(user_id, start, end)
            result["available"] = True
        except (ApiError, RemoteError):
            pass
        return result

    def disconnect(self, user_id: str):
        self.database.delete_calendar_connection(user_id)

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

    def _access_token(self, user_id: str, force_refresh: bool = False) -> str:
        account = self.database.calendar_account(user_id)
        if not account:
            raise ApiError(409, "Google Calendar is not connected.")
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
        self.database.update_calendar_tokens(user_id, access_token, refresh_token, self._token_expiration(token))
        return access_token

    def _list_events(self, user_id: str, range_start: datetime, range_end: datetime) -> list[dict]:
        try:
            access_token = self._access_token(user_id)
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
                events.extend(self._event(item) for item in items if isinstance(item, dict))
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
                events = fetch(self._access_token(user_id, force_refresh=True))
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
