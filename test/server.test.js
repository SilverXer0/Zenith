import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

let app;
let baseUrl;
let dataDir;
let localCookie;
let migratedTask;
let calendarMock;

const legacyTask = {
  id: "legacy-task",
  title: "Migrated task",
  notes: "Imported from the old store",
  project: "Inbox",
  priority: "high",
  dueDate: null,
  completed: false,
  createdAt: "2026-09-01T12:00:00.000Z",
  updatedAt: "2026-09-01T12:00:00.000Z"
};

async function request(path, options = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, baseUrl);
    const request = httpRequest(url, { method: options.method || "GET", headers: { Connection: "close", ...options.headers } }, (response) => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { raw += chunk; });
      response.on("end", () => resolve({ response: { status: response.statusCode, headers: response.headers }, body: options.raw ? raw : (raw ? JSON.parse(raw) : null) }));
    });
    request.on("error", reject);
    if (options.body) request.write(options.body);
    request.end();
  });
}

function jsonOptions(method, payload, cookie) {
  return {
    method,
    headers: { "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) },
    body: JSON.stringify(payload)
  };
}

async function openEventStream(cookie) {
  return new Promise((resolve, reject) => {
    const url = new URL("/api/events", baseUrl);
    const streamRequest = httpRequest(url, { headers: { Accept: "text/event-stream", Cookie: cookie } }, (response) => {
      response.setEncoding("utf8");
      let buffer = "";
      const waiters = [];
      response.on("data", (chunk) => {
        buffer += chunk;
        for (let index = waiters.length - 1; index >= 0; index -= 1) {
          if (!buffer.includes(waiters[index].text)) continue;
          waiters[index].resolve();
          waiters.splice(index, 1);
        }
      });
      response.on("error", reject);
      resolve({ request: streamRequest, response, waitFor: (text) => new Promise((waitResolve) => { if (buffer.includes(text)) waitResolve(); else waiters.push({ text, resolve: waitResolve }); }) });
    });
    streamRequest.on("error", reject);
    streamRequest.end();
  });
}

test("Zenith API integration", async () => {
  dataDir = await mkdtemp(join(tmpdir(), "zenith-test-"));
  await writeFile(join(dataDir, "tasks.json"), JSON.stringify([legacyTask]));
  process.env.ZENITH_DATA_DIR = dataDir;
  const module = await import(`../server.js?test=${Date.now()}`);
  app = module.app;
  const address = await new Promise((resolve, reject) => { app.once("error", reject); app.listen(0, "127.0.0.1", () => resolve(app.address())); });
  baseUrl = `http://127.0.0.1:${address.port}`;

  const { response, body } = await request("/api/health");
  assert.equal(response.status, 200);
  assert.deepEqual(body, { ok: true, service: "zenith", storage: "sqlite" });
  const manifest = await request("/manifest.webmanifest");
  assert.equal(manifest.response.status, 200);
  assert.equal(manifest.body.display, "standalone");
  assert.equal(manifest.body.start_url, "/");
  const page = await request("/", { raw: true });
  assert.match(page.body, /Capture to Inbox/);
  assert.match(page.body, /Add project, priority, date, or notes/);
  assert.match(page.body, /Speak/);
  assert.match(page.body, /Enable reminders/);
  assert.match(page.body, /Help Zenith remember/);
  assert.match(page.body, /Today’s focus/);
  assert.match(page.body, /Shape the week/);
  const serviceWorker = await request("/sw.js", { raw: true });
  assert.equal(serviceWorker.response.status, 200);
  assert.match(serviceWorker.body, /api/);

  const unauthenticated = await request("/api/tasks");
  const { response: unauthenticatedResponse, body: unauthenticatedBody } = unauthenticated;
  assert.equal(unauthenticatedResponse.status, 401);
  assert.equal(unauthenticatedBody.error, "Authentication required.");

  const authStatus = await request("/api/auth/status");
  assert.equal(authStatus.response.status, 200);
  assert.equal(authStatus.body.setupRequired, true);
  const missingPassword = await request("/api/auth/session", jsonOptions("POST", { displayName: "Alice" }));
  assert.equal(missingPassword.response.status, 400);

  const session = await request("/api/auth/setup", jsonOptions("POST", { displayName: "Alice", password: "correct horse" }));
  assert.equal(session.response.status, 201);
  localCookie = session.response.headers["set-cookie"][0].split(";", 1)[0];

  const first = await request("/api/tasks", { headers: { Cookie: localCookie } });
  assert.equal(first.response.status, 200);
  assert.equal(first.body.tasks.length, 1);
  migratedTask = first.body.tasks[0];
  assert.equal(migratedTask.id, legacyTask.id);

  await writeFile(join(dataDir, "tasks.json"), JSON.stringify([{ ...legacyTask, id: "should-not-import" }]));
  const second = await request("/api/tasks", { headers: { Cookie: localCookie } });
  assert.equal(second.body.tasks.some((task) => task.id === "should-not-import"), false);

  const configuredStatus = await request("/api/auth/status");
  assert.equal(configuredStatus.body.setupRequired, false);
  const wrongPassword = await request("/api/auth/session", jsonOptions("POST", { displayName: "Alice", password: "wrong pass" }));
  assert.equal(wrongPassword.response.status, 401);

  const created = await request("/api/tasks", jsonOptions("POST", { title: "Private task", priority: "medium" }, localCookie));
  assert.equal(created.response.status, 201);
  const edited = await request(`/api/tasks/${created.body.task.id}`, jsonOptions("PATCH", { title: "Edited private task", notes: "Remember the deadline", project: "Personal", dueDate: "2026-09-03" }, localCookie));
  assert.equal(edited.response.status, 200);
  assert.equal(edited.body.task.project, "Personal");
  assert.equal(edited.body.task.notes, "Remember the deadline");
  const briefing = await request("/api/briefing?date=2026-09-03", { headers: { Cookie: localCookie } });
  assert.equal(briefing.response.status, 200);
  assert.deepEqual(briefing.body.counts, { open: 2, overdue: 0, dueToday: 1 });
  assert.equal(briefing.body.focusTasks[0].title, "Edited private task");
  const invalidBriefing = await request("/api/briefing?date=not-a-date", { headers: { Cookie: localCookie } });
  assert.equal(invalidBriefing.response.status, 400);
  const weeklyPlan = await request("/api/weekly-plan?start=2026-09-01", { headers: { Cookie: localCookie } });
  assert.equal(weeklyPlan.response.status, 200);
  assert.deepEqual(weeklyPlan.body.counts, { open: 2, overdue: 0, scheduled: 1, unscheduled: 1 });
  assert.equal(weeklyPlan.body.days[2].tasks[0].title, "Edited private task");
  assert.equal(weeklyPlan.body.unscheduled[0].title, "Migrated task");
  const invalidWeeklyPlan = await request("/api/weekly-plan?start=2026-02-31", { headers: { Cookie: localCookie } });
  assert.equal(invalidWeeklyPlan.response.status, 400);

  const reLogin = await request("/api/auth/session", jsonOptions("POST", { displayName: "Alice", password: "correct horse" }));
  assert.equal(reLogin.response.status, 201);
  const reLoginCookie = reLogin.response.headers["set-cookie"][0].split(";", 1)[0];
  const previousDeviceSession = await request("/api/tasks", { headers: { Cookie: localCookie } });
  assert.equal(previousDeviceSession.response.status, 200);
  const reloadedTasks = await request("/api/tasks", { headers: { Cookie: reLoginCookie } });
  assert.equal(reloadedTasks.body.tasks.length, 2);

  const voiceUnconfiguredStatus = await request("/api/voice/status", { headers: { Cookie: reLoginCookie } });
  assert.deepEqual(voiceUnconfiguredStatus.body, { configured: false, ttsConfigured: false });
  const voiceUnconfigured = await request("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": "audio/webm", Cookie: reLoginCookie }, body: "test audio" });
  assert.equal(voiceUnconfigured.response.status, 409);
  process.env.ZENITH_STT_COMMAND = process.execPath;
  process.env.ZENITH_STT_ARGS = JSON.stringify(["-e", "console.log('local transcript')", "{input}"]);
  process.env.ZENITH_TTS_COMMAND = process.execPath;
  process.env.ZENITH_TTS_ARGS = JSON.stringify(["-e", "require('fs').writeFileSync(process.argv[2], 'audio')", "{text}", "{output}"]);
  const voiceConfiguredStatus = await request("/api/voice/status", { headers: { Cookie: reLoginCookie } });
  assert.deepEqual(voiceConfiguredStatus.body, { configured: true, ttsConfigured: true });
  const transcribed = await request("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": "audio/webm", Cookie: reLoginCookie }, body: "test audio" });
  assert.deepEqual(transcribed.body, { text: "local transcript" });
  const spoken = await request("/api/voice/speak", { ...jsonOptions("POST", { text: "Hello from Zenith" }, reLoginCookie), raw: true });
  assert.equal(spoken.response.status, 200);
  assert.equal(spoken.response.headers["content-type"], "audio/wav");
  assert.equal(spoken.body, "audio");

  const savedMemory = await request("/api/memory", jsonOptions("POST", { category: "preference", content: "I prefer quiet evenings for focused work." }, reLoginCookie));
  assert.equal(savedMemory.response.status, 201);
  const memories = await request("/api/memory", { headers: { Cookie: reLoginCookie } });
  assert.deepEqual(memories.body.memories.map(({ category, content }) => ({ category, content })), [{ category: "preference", content: "I prefer quiet evenings for focused work." }]);

  const calendarUnconfigured = await request("/api/calendar/status", { headers: { Cookie: reLoginCookie } });
  assert.deepEqual(calendarUnconfigured.body, { configured: false, connected: false, calendarName: null, connectedAt: null });
  const calendarEventsUnconfigured = await request("/api/calendar/events", { headers: { Cookie: reLoginCookie } });
  assert.equal(calendarEventsUnconfigured.response.status, 409);

  calendarMock = createServer(async (calendarRequest, calendarResponse) => {
    let raw = "";
    for await (const chunk of calendarRequest) raw += chunk;
    calendarResponse.setHeader("Content-Type", "application/json");
    if (calendarRequest.url === "/token") { calendarResponse.end(JSON.stringify({ access_token: "calendar-access", refresh_token: "calendar-refresh", expires_in: 3600 })); return; }
    if (calendarRequest.url === "/calendar/v3/calendars/primary") { calendarResponse.end(JSON.stringify({ summary: "Personal Calendar" })); return; }
    if (calendarRequest.url.startsWith("/calendar/v3/calendars/primary/events")) { calendarResponse.end(JSON.stringify({ items: [{ id: "event-1", summary: "Focus time", start: { dateTime: "2026-09-04T18:00:00-07:00" }, end: { dateTime: "2026-09-04T19:00:00-07:00" }, location: "Home", status: "confirmed" }] })); return; }
    calendarResponse.statusCode = 404;
    calendarResponse.end(JSON.stringify({ error: "not found" }));
  });
  const calendarAddress = await new Promise((resolve, reject) => { calendarMock.once("error", reject); calendarMock.listen(0, "127.0.0.1", () => resolve(calendarMock.address())); });
  process.env.GOOGLE_CLIENT_ID = "test-client";
  process.env.GOOGLE_CLIENT_SECRET = "test-secret";
  process.env.GOOGLE_REDIRECT_URI = `${baseUrl}/api/calendar/oauth/callback`;
  process.env.GOOGLE_TOKEN_URL = `http://127.0.0.1:${calendarAddress.port}/token`;
  process.env.GOOGLE_CALENDAR_URL = `http://127.0.0.1:${calendarAddress.port}/calendar/v3`;
  const calendarConnect = await request("/api/calendar/connect", { headers: { Cookie: reLoginCookie } });
  assert.equal(calendarConnect.response.status, 302);
  const authorization = new URL(calendarConnect.response.headers.location);
  assert.equal(authorization.searchParams.get("client_id"), "test-client");
  assert.equal(authorization.searchParams.get("scope"), "https://www.googleapis.com/auth/calendar.readonly");
  const calendarCallback = await request(`/api/calendar/oauth/callback?state=${authorization.searchParams.get("state")}&code=mock-code`);
  assert.equal(calendarCallback.response.status, 302);
  assert.equal(calendarCallback.response.headers.location, "/?calendar=connected");
  const calendarConnected = await request("/api/calendar/status", { headers: { Cookie: reLoginCookie } });
  assert.equal(calendarConnected.body.connected, true);
  assert.equal(calendarConnected.body.calendarName, "Personal Calendar");
  const calendarEvents = await request("/api/calendar/events?start=2026-09-04T00:00:00Z&end=2026-09-05T00:00:00Z", { headers: { Cookie: reLoginCookie } });
  assert.deepEqual(calendarEvents.body.events, [{ id: "event-1", title: "Focus time", start: "2026-09-04T18:00:00-07:00", end: "2026-09-04T19:00:00-07:00", allDay: false, location: "Home", status: "confirmed" }]);
  const connectedWeeklyPlan = await request("/api/weekly-plan?start=2026-09-01", { headers: { Cookie: reLoginCookie } });
  assert.equal(connectedWeeklyPlan.response.status, 200);
  assert.equal(connectedWeeklyPlan.body.calendar.connected, true);
  assert.equal(connectedWeeklyPlan.body.calendar.available, true);
  assert.equal(connectedWeeklyPlan.body.calendar.events[0].title, "Focus time");
  const unauthenticatedEvents = await request("/api/events");
  assert.equal(unauthenticatedEvents.response.status, 401);
  const eventStream = await openEventStream(reLoginCookie);
  assert.equal(eventStream.response.statusCode, 200);
  await eventStream.waitFor("event: ready");
  const liveCreated = await request("/api/tasks", jsonOptions("POST", { title: "Live sync task" }, reLoginCookie));
  assert.equal(liveCreated.response.status, 201);
  await eventStream.waitFor("event: tasks_changed");
  const liveRemoved = await request(`/api/tasks/${liveCreated.body.task.id}`, { method: "DELETE", headers: { Cookie: reLoginCookie } });
  assert.equal(liveRemoved.response.status, 204);
  await eventStream.waitFor("event: tasks_changed");
  eventStream.request.destroy();

  let receivedChat;
  let assistantActionsResponse = [{ type: "create_task", title: "Assistant-created task", project: "Inbox", priority: "low" }];
  let unloadRequest;
  const ollamaMock = createServer(async (ollamaRequest, ollamaResponse) => {
    let raw = "";
    for await (const chunk of ollamaRequest) raw += chunk;
    if (ollamaRequest.url === "/api/tags") { ollamaResponse.setHeader("Content-Type", "application/json"); ollamaResponse.end(JSON.stringify({ models: [{ name: "mock-model" }] })); return; }
    if (ollamaRequest.url === "/api/ps") { ollamaResponse.setHeader("Content-Type", "application/json"); ollamaResponse.end(JSON.stringify({ models: [{ name: "mock-model", model: "mock-model" }] })); return; }
    if (ollamaRequest.url === "/api/generate") { unloadRequest = JSON.parse(raw); ollamaResponse.setHeader("Content-Type", "application/json"); ollamaResponse.end(JSON.stringify({ done: true })); return; }
    receivedChat = JSON.parse(raw);
    ollamaResponse.setHeader("Content-Type", "application/json");
    ollamaResponse.end(JSON.stringify({ message: { content: JSON.stringify({ reply: "Focus on the private task first.", actions: assistantActionsResponse }) } }));
  });
  const ollamaAddress = await new Promise((resolve, reject) => { ollamaMock.once("error", reject); ollamaMock.listen(0, "127.0.0.1", () => resolve(ollamaMock.address())); });
  process.env.OLLAMA_URL = `http://127.0.0.1:${ollamaAddress.port}`;
  process.env.OLLAMA_MODEL = "mock-model";
  const assistant = await request("/api/assistant/chat", jsonOptions("POST", { message: "What should I focus on?" }, reLoginCookie));
  assert.equal(assistant.response.status, 200);
  assert.equal(assistant.body.message, "Focus on the private task first.");
  assert.equal(assistant.body.actions.length, 1);
  assert.match(receivedChat.messages[0].content, /private task/i);
  assert.match(receivedChat.messages[0].content, /Focus time/i);
  assert.match(receivedChat.messages[0].content, /quiet evenings/i);
  assert.equal(receivedChat.messages.at(-1).content, "What should I focus on?");
  assistantActionsResponse = [{ type: "complete_task", taskId: created.body.task.id }];
  const existingTaskAction = await request("/api/assistant/chat", jsonOptions("POST", { message: "Mark the edited task complete" }, reLoginCookie));
  assert.equal(existingTaskAction.response.status, 200);
  assert.deepEqual(existingTaskAction.body.actions, [{ type: "complete_task", taskId: created.body.task.id }]);
  assistantActionsResponse = [{ type: "create_task", title: "Assistant-created task", project: "Inbox", priority: "low" }];
  const invalidAction = await request("/api/assistant/actions", jsonOptions("POST", { actions: [{ type: "delete_task", taskId: "not-your-task" }] }, reLoginCookie));
  assert.equal(invalidAction.response.status, 409);
  const confirmed = await request("/api/assistant/actions", jsonOptions("POST", { actions: assistant.body.actions }, reLoginCookie));
  assert.equal(confirmed.response.status, 200);
  assert.equal(confirmed.body.tasks.length, 3);
  assert.equal(confirmed.body.tasks.some((task) => task.title === "Assistant-created task"), true);
  assert.equal(confirmed.body.tasks.some((task) => task.title === "Edited private task"), true);
  const unloaded = await request("/api/assistant/unload", jsonOptions("POST", {}, reLoginCookie));
  assert.equal(unloaded.response.status, 200);
  assert.deepEqual(unloaded.body, { unloaded: true, model: "mock-model" });
  assert.equal(unloadRequest.keep_alive, 0);
  const tasksAfterUnload = await request("/api/tasks", { headers: { Cookie: reLoginCookie } });
  assert.equal(tasksAfterUnload.response.status, 200);
  assert.equal(tasksAfterUnload.body.tasks.length, 3);
  const disconnected = await request("/api/calendar/connection", { method: "DELETE", headers: { Cookie: reLoginCookie } });
  assert.equal(disconnected.response.status, 204);
  const deletedMemory = await request(`/api/memory/${savedMemory.body.memory.id}`, { method: "DELETE", headers: { Cookie: reLoginCookie } });
  assert.equal(deletedMemory.response.status, 204);

  const logout = await request("/api/auth/session", { method: "DELETE", headers: { Cookie: reLoginCookie } });
  assert.equal(logout.response.status, 204);
  const afterLogout = await request("/api/tasks", { headers: { Cookie: reLoginCookie } });
  assert.equal(afterLogout.response.status, 401);
  const assistantAfterLogout = await request("/api/assistant/chat", jsonOptions("POST", { message: "Can you help?" }, reLoginCookie));
  assert.equal(assistantAfterLogout.response.status, 401);

  process.env.OLLAMA_URL = "http://127.0.0.1:1";
  const ollama = await request("/api/assistant/status");
  assert.equal(ollama.response.status, 200);
  assert.equal(ollama.body.enabled, true);
  assert.equal(typeof ollama.body.reachable, "boolean");

  await new Promise((resolve) => app.close(resolve));
  await new Promise((resolve) => ollamaMock.close(resolve));
  await new Promise((resolve) => calendarMock.close(resolve));
  await rm(dataDir, { recursive: true, force: true });
  delete process.env.ZENITH_DATA_DIR;
  delete process.env.OLLAMA_URL;
  delete process.env.OLLAMA_MODEL;
  delete process.env.GOOGLE_CLIENT_ID;
  delete process.env.GOOGLE_CLIENT_SECRET;
  delete process.env.GOOGLE_REDIRECT_URI;
  delete process.env.GOOGLE_TOKEN_URL;
  delete process.env.GOOGLE_CALENDAR_URL;
  delete process.env.ZENITH_STT_COMMAND;
  delete process.env.ZENITH_STT_ARGS;
  delete process.env.ZENITH_TTS_COMMAND;
  delete process.env.ZENITH_TTS_ARGS;
});
