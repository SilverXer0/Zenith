"""Bounded, per-user task invalidations for a single Zenith server process."""

import asyncio
import sqlite3
from dataclasses import dataclass, field
from threading import RLock

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from .errors import ApiError


@dataclass(eq=False)
class Subscription:
    user_id: str
    session_hash: str
    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    pending: bool = False
    wake_scheduled: bool = False
    closed: bool = False


class TaskEvents:
    def __init__(self, *, heartbeat_interval: float = 15):
        self._lock = RLock()
        self._users: dict[str, set[Subscription]] = {}
        self.heartbeat_interval = heartbeat_interval

    @property
    def count(self) -> int:
        with self._lock:
            return sum(len(clients) for clients in self._users.values())

    def subscribe(self, user_id: str, session_hash: str) -> Subscription:
        subscription = Subscription(user_id, session_hash, asyncio.get_running_loop())
        with self._lock:
            self._users.setdefault(user_id, set()).add(subscription)
        return subscription

    def _wake(self, subscription: Subscription):
        # Runs on the subscriber's event loop, never on a request worker thread.
        with self._lock:
            subscription.wake_scheduled = False
            if subscription.pending or subscription.closed:
                subscription.wake.set()

    def _schedule_wake(self, subscription: Subscription):
        if subscription.wake_scheduled:
            return
        subscription.wake_scheduled = True
        try:
            subscription.loop.call_soon_threadsafe(self._wake, subscription)
        except RuntimeError:
            subscription.wake_scheduled = False
            self.unsubscribe(subscription)

    def publish(self, user_id: str):
        with self._lock:
            for subscription in tuple(self._users.get(user_id, ())):
                if subscription.closed or subscription.pending:
                    continue
                # At most one queued invalidation AND one scheduled wake per client.
                # A slow client needs the latest snapshot, not a backlog of changes.
                subscription.pending = True
                self._schedule_wake(subscription)

    async def wait(self, subscription: Subscription, timeout: float) -> bool:
        if not subscription.closed:
            try:
                await asyncio.wait_for(subscription.wake.wait(), timeout)
            except TimeoutError:
                pass
        with self._lock:
            changed = subscription.pending
            subscription.pending = False
            subscription.wake.clear()
            return changed

    def unsubscribe(self, subscription: Subscription):
        with self._lock:
            subscription.closed = True
            clients = self._users.get(subscription.user_id)
            if clients is not None:
                clients.discard(subscription)
                if not clients:
                    self._users.pop(subscription.user_id, None)

    def close_session(self, session_hash: str):
        with self._lock:
            clients = [client for group in self._users.values() for client in group if client.session_hash == session_hash]
            for client in clients:
                self.unsubscribe(client)
                self._schedule_wake(client)

    def close_all(self):
        with self._lock:
            clients = [client for group in self._users.values() for client in group]
            for client in clients:
                self.unsubscribe(client)
                self._schedule_wake(client)

    async def stream(self, user_id: str, session_hash: str, auth):
        subscription = self.subscribe(user_id, session_hash)

        async def authorized():
            try:
                user = await run_in_threadpool(auth.user_for_session_hash, session_hash)
                return not subscription.closed and user["id"] == user_id
            except (ApiError, sqlite3.Error):
                return False  # Revocation, expiry, or unavailable storage: fail closed.

        try:
            if not await authorized():
                return
            # Every (re)connection prompts a fresh task read; no event replay is needed.
            yield b"event: ready\ndata: {}\nretry: 1000\n\n"
            while not subscription.closed:
                changed = await self.wait(subscription, self.heartbeat_interval)
                if not await authorized():
                    break
                yield b"event: tasks_changed\ndata: {}\n\n" if changed else b": heartbeat\n\n"
        finally:
            self.unsubscribe(subscription)


class TaskEventResponse(StreamingResponse):
    """Release stream state on disconnect/cancellation, including a blocked send."""

    media_type = "text/event-stream"

    def __init__(self, content, status_code: int = 200, *, send_timeout: float = 10):
        super().__init__(content, status_code=status_code, media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self.send_timeout = send_timeout

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
            with anyio.fail_after(self.send_timeout):
                await send(message)

        try:
            await super().__call__(scope, receive, bounded_send)
        except (TimeoutError, ClientDisconnect):
            pass
        finally:
            # A generator suspended at yield otherwise need not be closed promptly.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
