import assert from "node:assert/strict";
import { request as httpRequest } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

let app;
let baseUrl;
let dataDir;
let localCookie;
let migratedTask;

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
      response.on("end", () => resolve({ response: { status: response.statusCode, headers: response.headers }, body: raw ? JSON.parse(raw) : null }));
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

  const reLogin = await request("/api/auth/session", jsonOptions("POST", { displayName: "Alice", password: "correct horse" }));
  assert.equal(reLogin.response.status, 201);
  const reLoginCookie = reLogin.response.headers["set-cookie"][0].split(";", 1)[0];
  const rotatedSession = await request("/api/tasks", { headers: { Cookie: localCookie } });
  assert.equal(rotatedSession.response.status, 401);
  const reloadedTasks = await request("/api/tasks", { headers: { Cookie: reLoginCookie } });
  assert.equal(reloadedTasks.body.tasks.length, 2);

  const logout = await request("/api/auth/session", { method: "DELETE", headers: { Cookie: reLoginCookie } });
  assert.equal(logout.response.status, 204);
  const afterLogout = await request("/api/tasks", { headers: { Cookie: reLoginCookie } });
  assert.equal(afterLogout.response.status, 401);

  const ollama = await request("/api/assistant/status");
  assert.equal(ollama.response.status, 200);
  assert.equal(ollama.body.enabled, true);
  assert.equal(typeof ollama.body.reachable, "boolean");

  await new Promise((resolve) => app.close(resolve));
  await rm(dataDir, { recursive: true, force: true });
  delete process.env.ZENITH_DATA_DIR;
});
