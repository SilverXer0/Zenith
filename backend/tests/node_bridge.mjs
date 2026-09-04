// Compatibility fixture: boot the real Node app against a disposable database.
import assert from "node:assert/strict";
import { request } from "node:http";
import { app } from "../../server.js";

assert.ok(process.env.ZENITH_DATA_DIR, "An isolated test data directory is required");
assert.ok(Number(process.versions.node.split(".")[0]) >= 20, "Use Node 20+ (or set ZENITH_NODE)");
const address = await new Promise((resolve, reject) => {
  app.once("error", reject);
  app.listen(0, "127.0.0.1", () => resolve(app.address()));
});
const credentials = process.env.ZENITH_TEST_CREDENTIALS
  ? JSON.parse(process.env.ZENITH_TEST_CREDENTIALS)
  : { displayName: "Migration user", password: "Migration passphrase 2026!" };
function api(path, { method = "GET", payload, cookie } = {}) {
  return new Promise((resolve, reject) => {
    const req = request({ hostname: "127.0.0.1", port: address.port, path, method,
      headers: { Connection: "close", "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) } }, (response) => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { raw += chunk; });
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body: raw ? JSON.parse(raw) : null }));
    });
    req.on("error", reject);
    req.setTimeout(5000, () => req.destroy(new Error("Node fixture request timed out")));
    req.end(payload ? JSON.stringify(payload) : undefined);
  });
}
try {
  if (process.argv[2] === "seed") {
    const setup = await api("/api/auth/setup", { method: "POST", payload: credentials });
    assert.equal(setup.status, 201);
    const cookie = setup.headers["set-cookie"][0].split(";", 1)[0];
    const created = await api("/api/tasks", { method: "POST", cookie, payload: {
      title: "Node-created task", notes: "Keep this note", priority: "high", project: "Migration", dueDate: "2026-09-30", completed: true
    } });
    assert.equal(created.status, 201);
    const memory = await api("/api/memory", { method: "POST", cookie, payload: { category: "preference", content: "Preserve this context" } });
    assert.equal(memory.status, 201);
    console.log(JSON.stringify({ cookie, user: setup.body.user, task: created.body.task, memory: memory.body.memory }));
  } else if (process.argv[2] === "read") {
    const login = await api("/api/auth/session", { method: "POST", payload: credentials });
    assert.equal(login.status, 201, JSON.stringify(login.body));
    const cookie = process.env.ZENITH_TEST_COOKIE || login.headers["set-cookie"][0].split(";", 1)[0];
    const session = await api("/api/auth/session", { cookie });
    assert.equal(session.status, 200, "Node must accept the Python session cookie");
    const tasks = await api("/api/tasks", { cookie });
    const memory = await api("/api/memory", { cookie });
    const date = process.env.ZENITH_TEST_DATE || new Date().toISOString().slice(0, 10);
    const offset = process.env.ZENITH_TEST_OFFSET || "0";
    const summary = await api(`/api/summaries/daily?date=${encodeURIComponent(date)}&offset=${encodeURIComponent(offset)}`, { cookie });
    const briefing = await api(`/api/briefing?date=${encodeURIComponent(date)}`, { cookie });
    const morning = await api(`/api/briefing/morning?date=${encodeURIComponent(date)}`, { cookie });
    const weekly = await api(`/api/weekly-plan?start=${encodeURIComponent(date)}`, { cookie });
    const calendarStatus = await api("/api/calendar/status", { cookie });
    assert.equal(tasks.status, 200);
    assert.equal(memory.status, 200);
    assert.equal(summary.status, 200);
    assert.equal(briefing.status, 200);
    assert.equal(morning.status, 200);
    assert.equal(weekly.status, 200);
    assert.equal(calendarStatus.status, 200);
    console.log(JSON.stringify({ tasks: tasks.body.tasks, memories: memory.body.memories, summary: summary.body,
      briefing: briefing.body, morning: morning.body, weekly: weekly.body, calendarStatus: calendarStatus.body }));
  } else {
    throw new Error("Expected seed or read mode");
  }
} finally {
  app.closeAllConnections();
  await new Promise((resolve) => app.close(resolve));
}
