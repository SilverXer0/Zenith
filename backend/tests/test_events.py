import asyncio
import sqlite3
import unittest

from backend.errors import ApiError
from backend.events import TaskEvents, TaskEventResponse


class SessionAuth:
    def __init__(self):
        self.sessions = {"session-a": "user-a", "session-b": "user-a", "session-c": "user-c"}
        self.error = None

    def user_for_session_hash(self, session_hash):
        if self.error:
            raise self.error
        if session_hash not in self.sessions:
            raise ApiError(401, "Authentication required.")
        return {"id": self.sessions[session_hash]}


class EventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = TaskEvents(heartbeat_interval=0.02)
        self.auth = SessionAuth()
        self.addCleanup(self.events.close_all)

    async def test_worker_thread_bursts_are_bounded_and_user_scoped(self):
        a = self.events.subscribe("user-a", "session-a")
        b = self.events.subscribe("user-a", "session-b")
        c = self.events.subscribe("user-c", "session-c")
        def burst():
            for _ in range(10000):
                self.events.publish("user-a")
        await asyncio.to_thread(burst)
        self.assertTrue(await self.events.wait(a, 0.1))
        self.assertTrue(await self.events.wait(b, 0.1))
        self.assertFalse(await self.events.wait(c, 0.01))
        self.assertFalse(await self.events.wait(a, 0.01), "A slow reader must not have a 10,000-event backlog")
        self.assertEqual(self.events.count, 3)

    async def test_every_connection_starts_with_ready_and_idle_heartbeat(self):
        for _ in range(2):
            stream = self.events.stream("user-a", "session-a", self.auth)
            self.assertEqual(await anext(stream), b"event: ready\ndata: {}\nretry: 1000\n\n")
            self.assertEqual(await anext(stream), b": heartbeat\n\n")
            await stream.aclose()
            self.assertEqual(self.events.count, 0)

    async def test_session_revocation_wakes_only_that_sessions_readers(self):
        a = self.events.stream("user-a", "session-a", self.auth)
        b = self.events.stream("user-a", "session-b", self.auth)
        await anext(a)
        await anext(b)
        waiting = asyncio.create_task(anext(a))
        await asyncio.to_thread(self.events.close_session, "session-a")
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(waiting, 0.5)
        self.assertEqual(self.events.count, 1)
        self.events.publish("user-a")
        self.assertEqual(await anext(b), b"event: tasks_changed\ndata: {}\n\n")
        await b.aclose()

    async def test_invalid_or_reassigned_session_never_receives_ready(self):
        for session in ("missing", "session-c"):
            stream = self.events.stream("user-a", session, self.auth)
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
        self.assertEqual(self.events.count, 0)

    async def test_expiry_or_storage_failure_closes_idle_streams(self):
        for failure in (ApiError(401, "Authentication required."), sqlite3.OperationalError("unavailable")):
            self.auth.error = None
            stream = self.events.stream("user-a", "session-a", self.auth)
            await anext(stream)
            self.auth.error = failure
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), 0.5)
            self.assertEqual(self.events.count, 0)

    async def test_shutdown_closes_all_pending_streams(self):
        streams = [self.events.stream("user-a", token, self.auth) for token in ("session-a", "session-b")]
        for stream in streams:
            await anext(stream)
        self.events.close_all()
        for stream in streams:
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
        self.assertEqual(self.events.count, 0)

    async def test_blocked_network_send_is_timed_out_and_releases_subscription(self):
        for spec in ("2.3", "2.4"):
            response = TaskEventResponse(self.events.stream("user-a", "session-a", self.auth), send_timeout=0.03)
            async def send(message):
                if message["type"] == "http.response.body":
                    await asyncio.Future()
            async def receive():
                await asyncio.Future()
            await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": spec}}, receive, send), 0.5)
            self.assertEqual(self.events.count, 0)

    async def test_disconnect_and_cancellation_release_generator_state(self):
        for disconnect in (True, False):
            ready = asyncio.Event()
            response = TaskEventResponse(self.events.stream("user-a", "session-a", self.auth))
            async def send(message):
                if message["type"] == "http.response.body":
                    ready.set()
            async def receive():
                await ready.wait()
                if disconnect:
                    return {"type": "http.disconnect"}
                await asyncio.Future()
            running = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))
            await asyncio.wait_for(ready.wait(), 0.5)
            if not disconnect:
                running.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await running
            else:
                await asyncio.wait_for(running, 0.5)
            self.assertEqual(self.events.count, 0)
