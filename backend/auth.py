"""Keep the Node account, passphrase and session formats across the cutover."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .database import Database, timestamp
from .errors import ApiError


COOKIE_NAME = "zenith_session"
SESSION_SECONDS = 30 * 86400


def derive(value: str, salt: str, length: int) -> bytes:
    # Node's scryptSync defaults. Salt is the UTF-8 hex string, not decoded bytes.
    return hashlib.scrypt(value.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=length)


def token_hash(token: str) -> str:
    return derive(token, "zenith-session", 32).hex()


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}:{derive(password, salt, 64).hex()}"


def password_matches(password: str, encoded: str | None) -> bool:
    try:
        salt, encoded_hash = encoded.split(":")
        expected = bytes.fromhex(encoded_hash)
        return len(expected) == 64 and hmac.compare_digest(derive(password, salt, 64), expected)
    except (AttributeError, ValueError, TypeError):
        return False


class Auth:
    def __init__(self, database: Database):
        self.database = database

    def setup_required(self) -> bool:
        with self.database.connection() as connection:
            user = connection.execute("SELECT password_hash FROM users ORDER BY created_at LIMIT 1").fetchone()
            return not user or not user["password_hash"]

    def _new_session(self, connection, user):
        token = secrets.token_urlsafe(32)
        now = timestamp()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)", (token_hash(token), user["id"], now, expires))
        return {"user": {"id": user["id"], "displayName": user["display_name"]}, "expires": expires}, token

    def setup(self, display_name: str, password: str):
        with self.database.connection(write=True) as connection:
            user = connection.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
            if user["password_hash"]:
                raise ApiError(409, "Zenith is already set up. Please log in.")
            connection.execute("UPDATE users SET display_name = ?, password_hash = ? WHERE id = ?", (display_name, password_hash(password), user["id"]))
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            return self._new_session(connection, {"id": user["id"], "display_name": display_name})

    def login(self, display_name: str, password: str):
        with self.database.connection(write=True) as connection:
            user = connection.execute("SELECT * FROM users WHERE display_name = ? LIMIT 1", (display_name,)).fetchone()
            if not user or not password_matches(password, user["password_hash"]):
                raise ApiError(401, "Invalid display name or passphrase.")
            return self._new_session(connection, user)

    def current_user(self, token: str | None) -> dict:
        if token and len(token) <= 128:
            return self.user_for_session_hash(token_hash(token))
        raise ApiError(401, "Authentication required.")

    def user_for_session_hash(self, hashed_token: str) -> dict:
        with self.database.connection() as connection:
            user = connection.execute("""SELECT u.id, u.display_name FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?""", (hashed_token, timestamp())).fetchone()
            if user:
                return {"id": user["id"], "displayName": user["display_name"]}
        raise ApiError(401, "Authentication required.")

    def logout(self, token: str | None):
        if token and len(token) <= 128:
            hashed_token = token_hash(token)
            with self.database.connection(write=True) as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashed_token,))
            return hashed_token
        return None
