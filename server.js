import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { dirname, extname, join, normalize } from "node:path";
import { randomBytes, randomUUID, scryptSync, timingSafeEqual } from "node:crypto";

const execFileAsync = promisify(execFile);
const root = dirname(fileURLToPath(import.meta.url));
const publicDir = join(root, "public");
const dataDir = process.env.ZENITH_DATA_DIR || join(root, "data");
const databaseFile = join(dataDir, "zenith.sqlite");
const legacyTaskFile = join(dataDir, "tasks.json");
const sqlite = process.env.ZENITH_SQLITE || "sqlite3";
const port = Number(process.env.PORT || 3000);
const host = process.env.ZENITH_HOST || process.env.HOST || "127.0.0.1";
const contentTypes = { ".css": "text/css", ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml" };
let databaseReady;
const liveClients = new Map();

const sqlQuote = (value) => `'${String(value).replaceAll("'", "''")}'`;
async function runSql(sql, json = false) {
  await mkdir(dataDir, { recursive: true });
  const args = json ? ["-json", databaseFile, sql] : [databaseFile, sql];
  const { stdout } = await execFileAsync(sqlite, args, { maxBuffer: 8 * 1024 * 1024 });
  return json ? (stdout.trim() ? JSON.parse(stdout) : []) : stdout;
}

async function initializeDatabase() {
  await runSql(`PRAGMA busy_timeout = 5000;
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, password_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT 'Inbox', priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tasks_user_updated ON tasks(user_id, updated_at);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);`);
  const userColumns = await runSql("PRAGMA table_info(users)", true);
  if (!userColumns.some((column) => column.name === "password_hash")) {
    await runSql("ALTER TABLE users ADD COLUMN password_hash TEXT");
    await runSql("DELETE FROM sessions");
  }
  const users = await runSql("SELECT id FROM users ORDER BY created_at LIMIT 1", true);
  let userId = users[0]?.id;
  if (!userId) {
    userId = randomUUID();
    await runSql(`INSERT INTO users (id, display_name, created_at) VALUES (${sqlQuote(userId)}, 'Local user', ${sqlQuote(new Date().toISOString())})`);
  }
  if (existsSync(legacyTaskFile)) {
    const marker = await runSql("SELECT 1 AS migrated FROM sqlite_master WHERE type='table' AND name='legacy_migration'", true);
    if (!marker.length) {
      let tasks = [];
      try { tasks = JSON.parse(await readFile(legacyTaskFile, "utf8")); } catch { /* malformed legacy data is ignored */ }
      for (const task of Array.isArray(tasks) ? tasks : []) {
        if (!task?.id || !task?.title) continue;
        await runSql(`INSERT OR IGNORE INTO tasks (id, user_id, title, notes, project, priority, due_date, completed, created_at, updated_at) VALUES (${sqlQuote(task.id)}, ${sqlQuote(userId)}, ${sqlQuote(task.title)}, ${sqlQuote(task.notes || "")}, ${sqlQuote(task.project || "Inbox")}, ${sqlQuote(["low", "medium", "high"].includes(task.priority) ? task.priority : "medium")}, ${task.dueDate ? sqlQuote(task.dueDate) : "NULL"}, ${task.completed ? 1 : 0}, ${sqlQuote(task.createdAt || new Date().toISOString())}, ${sqlQuote(task.updatedAt || task.createdAt || new Date().toISOString())})`);
      }
      await runSql("CREATE TABLE legacy_migration (migrated_at TEXT NOT NULL)");
      await runSql(`INSERT INTO legacy_migration VALUES (${sqlQuote(new Date().toISOString())})`);
    }
  }
  return userId;
}
function ready() { return databaseReady ||= initializeDatabase(); }

function send(response, status, payload, headers = {}) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", ...headers });
  response.end(status === 204 ? "" : JSON.stringify(payload));
}
async function body(request) { let raw = ""; for await (const chunk of request) raw += chunk; try { return JSON.parse(raw || "{}"); } catch { throw new Error("Please send valid JSON."); } }
function taskInput(input, existing = {}) {
  const title = (input.title ?? existing.title ?? "").trim();
  if (!title) throw new Error("A task needs a title.");
  return { ...existing, title, notes: (input.notes ?? existing.notes ?? "").trim(), project: (input.project ?? existing.project ?? "Inbox").trim() || "Inbox", priority: ["low", "medium", "high"].includes(input.priority) ? input.priority : (existing.priority || "medium"), dueDate: input.dueDate === undefined ? (existing.dueDate || null) : (input.dueDate || null), completed: input.completed === undefined ? Boolean(existing.completed) : Boolean(input.completed), updatedAt: new Date().toISOString() };
}
function tokenHash(token) { return scryptSync(token, "zenith-session", 32).toString("hex"); }
function passwordHash(password) { const salt = randomBytes(16).toString("hex"); return `${salt}:${scryptSync(password, salt, 64).toString("hex")}`; }
function passwordMatches(password, encoded) {
  try { const [salt, expectedHex] = String(encoded).split(":"); const expected = Buffer.from(expectedHex, "hex"); const actual = scryptSync(password, salt, expected.length); return expected.length > 0 && timingSafeEqual(actual, expected); } catch { return false; }
}
function credentials(input) {
  const displayName = String(input.displayName || "").trim().slice(0, 80);
  const password = String(input.password || "");
  if (!displayName) throw new Error("A display name is required.");
  if (password.length < 8) throw new Error("Use a passphrase with at least 8 characters.");
  return { displayName, password };
}
function cookieToken(request) { return request.headers.cookie?.match(/(?:^|;\s*)zenith_session=([^;]+)/)?.[1]; }
async function createSession(userId) {
  const token = randomBytes(32).toString("base64url"); const now = new Date(); const expires = new Date(now.getTime() + 30 * 86400000).toISOString();
  await runSql(`DELETE FROM sessions WHERE expires_at <= datetime('now'); INSERT INTO sessions VALUES (${sqlQuote(tokenHash(token))}, ${sqlQuote(userId)}, ${sqlQuote(now.toISOString())}, ${sqlQuote(expires)})`); return { token, expires };
}
function sessionCookie(session) { return `zenith_session=${session.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000`; }
async function currentUser(request) {
  const token = cookieToken(request); if (!token) return null;
  const rows = await runSql(`SELECT u.id, u.display_name AS displayName FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=${sqlQuote(tokenHash(token))} AND s.expires_at > datetime('now')`, true); return rows[0] || null;
}
async function requireUser(request, response) { const user = await currentUser(request); if (!user) { send(response, 401, { error: "Authentication required." }); return null; } return user; }
function addLiveClient(userId, request, response) {
  const client = { response, closed: false };
  let clients = liveClients.get(userId);
  if (!clients) { clients = new Set(); liveClients.set(userId, clients); }
  clients.add(client);
  const cleanup = () => {
    if (client.closed) return;
    client.closed = true;
    clearInterval(client.heartbeat);
    clients.delete(client);
    if (!clients.size) liveClients.delete(userId);
  };
  client.heartbeat = setInterval(() => {
    try { response.write(": heartbeat\n\n"); } catch { cleanup(); }
  }, 25000);
  client.heartbeat.unref?.();
  request.on("close", cleanup);
  response.on("close", cleanup);
  response.on("error", cleanup);
}
function broadcastTasksChanged(userId) {
  const clients = liveClients.get(userId);
  if (!clients) return;
  for (const client of clients) {
    try { client.response.write(`event: tasks_changed\ndata: {}\n\n`); } catch { /* cleanup handles closed streams */ }
  }
}
function taskFromRow(row) { return { id: row.id, title: row.title, notes: row.notes, project: row.project, priority: row.priority, dueDate: row.dueDate, completed: Boolean(row.completed), createdAt: row.createdAt, updatedAt: row.updatedAt }; }
async function listTasks(userId) { const rows = await runSql(`SELECT id,title,notes,project,priority,due_date AS dueDate,completed,created_at AS createdAt,updated_at AS updatedAt FROM tasks WHERE user_id=${sqlQuote(userId)} ORDER BY completed, due_date IS NULL, due_date, updated_at DESC`, true); return rows.map(taskFromRow); }
function assistantActions(actions, tasks) {
  if (!Array.isArray(actions)) return [];
  const taskIds = new Set(tasks.map((task) => task.id));
  return actions.slice(0, 5).flatMap((action) => {
    if (!action || typeof action !== "object") return [];
    if (action.type === "create_task" && typeof action.title === "string" && action.title.trim()) return [{ type: "create_task", title: action.title.trim().slice(0, 160), notes: String(action.notes || "").trim().slice(0, 2000), project: String(action.project || "Inbox").trim().slice(0, 80) || "Inbox", priority: ["low", "medium", "high"].includes(action.priority) ? action.priority : "medium", dueDate: action.dueDate ? String(action.dueDate).slice(0, 10) : null }];
    if (["update_task", "complete_task", "delete_task"].includes(action.type) && taskIds.has(action.taskId)) {
      if (action.type === "complete_task") return [{ type: action.type, taskId: action.taskId }];
      if (action.type === "delete_task") return [{ type: action.type, taskId: action.taskId }];
      const update = { type: action.type, taskId: action.taskId };
      for (const field of ["title", "notes", "project", "priority", "dueDate", "completed"]) if (action[field] !== undefined) update[field] = action[field];
      return [update];
    }
    return [];
  });
}
async function ollamaJson(path, options = {}) {
  const baseUrl = process.env.OLLAMA_URL || "http://127.0.0.1:11434"; const target = new URL(`${baseUrl}${path}`); const transport = target.protocol === "https:" ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => { const request = transport(target, { method: options.method || "GET", headers: options.headers || {} }, (response) => { let raw = ""; response.setEncoding("utf8"); response.on("data", (chunk) => { raw += chunk; }); response.on("end", () => { if (response.statusCode < 200 || response.statusCode >= 300) return reject(new Error(`Ollama returned ${response.statusCode}.`)); try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error("Ollama returned invalid JSON.")); } }); }); request.setTimeout(options.timeout || 1000, () => request.destroy(new Error("Ollama request timed out."))); request.on("error", reject); if (options.body) request.write(options.body); request.end(); });
}
async function ollamaStatus() { try { const { models = [] } = await ollamaJson("/api/tags", { timeout: 800 }); let running = []; try { running = (await ollamaJson("/api/ps", { timeout: 800 })).models || []; } catch { /* model listing is optional */ } const model = process.env.OLLAMA_MODEL || models[0]?.name || null; const loadedModel = running[0]?.name || running[0]?.model || null; return { enabled: true, reachable: true, model, loaded: Boolean(model && running.some((item) => item.name === model || item.model === model)), loadedModel }; } catch { return { enabled: true, reachable: false, model: process.env.OLLAMA_MODEL || null, loaded: false, loadedModel: null }; } }
async function assistantChat(userId, input) {
  const message = String(input.message || "").trim();
  if (!message) throw new Error("Ask the local assistant a question.");
  if (message.length > 4000) throw new Error("Keep assistant messages under 4,000 characters.");
  const rows = await runSql(`SELECT title,notes,project,priority,due_date AS dueDate,completed FROM tasks WHERE user_id=${sqlQuote(userId)} ORDER BY completed, due_date IS NULL, due_date, updated_at DESC LIMIT 100`, true);
  const context = rows.length ? rows.map((task, index) => `${index + 1}. (${task.id}) [${task.completed ? "done" : "open"}] ${task.title} — ${task.project}, ${task.priority} priority${task.dueDate ? `, due ${task.dueDate}` : ""}${task.notes ? ` — ${task.notes.slice(0, 500)}` : ""}`).join("\n") : "No tasks are currently saved.";
  const history = Array.isArray(input.history) ? input.history.filter((item) => ["user", "assistant"].includes(item?.role) && typeof item.content === "string").slice(-8).map((item) => ({ role: item.role, content: item.content.slice(0, 2000) })) : [];
  let model = process.env.OLLAMA_MODEL || null;
  if (!model) { const tags = await ollamaJson("/api/tags", { timeout: 1500 }); model = tags.models?.[0]?.name || null; }
  if (!model) throw new Error("No Ollama model is available.");
  const result = await ollamaJson("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, timeout: 30000, body: JSON.stringify({ model, stream: false, format: "json", keep_alive: process.env.OLLAMA_KEEP_ALIVE || "5m", messages: [{ role: "system", content: `You are Zenith, a calm local personal manager. Use the user's task list below. Return one valid JSON object with exactly these keys: reply (a concise helpful string) and actions (an array). Actions are proposals only and are never applied automatically. Use create_task with title, optional notes/project/priority/dueDate; update_task with taskId and optional fields; complete_task with taskId; or delete_task with taskId. Only use task IDs from the list. If no change is requested, return an empty actions array.\n\nUser task list:\n${context}` }, ...history, { role: "user", content: message }] }) });
  const content = result.message?.content?.trim();
  if (!content) throw new Error("Ollama returned an empty response.");
  let parsed; try { parsed = JSON.parse(content); } catch { parsed = { reply: content, actions: [] }; }
  const tasks = rows.map(taskFromRow);
  return { message: String(parsed.reply || content).trim(), actions: assistantActions(parsed.actions, tasks), model };
}
async function unloadAssistantModel(input = {}) {
  const model = String(input.model || process.env.OLLAMA_MODEL || "").trim().slice(0, 120); if (!model) throw new Error("No Ollama model is configured.");
  const running = (await ollamaJson("/api/ps", { timeout: 1500 })).models || []; if (!running.some((item) => item.name === model || item.model === model)) return { unloaded: false, model, reason: "not_loaded" };
  await ollamaJson("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, timeout: 10000, body: JSON.stringify({ model, prompt: "", stream: false, keep_alive: 0 }) }); return { unloaded: true, model };
}
async function applyAssistantActions(userId, actions) {
  const tasks = await listTasks(userId); const valid = assistantActions(actions, tasks); if (!Array.isArray(actions) || valid.length !== actions.length) throw new Error("The proposed task changes are no longer valid. Ask the assistant again.");
  for (const action of valid) {
    if (action.type === "create_task") { const task = taskInput(action, { id: randomUUID(), createdAt: new Date().toISOString() }); await runSql(`INSERT INTO tasks VALUES (${sqlQuote(task.id)},${sqlQuote(userId)},${sqlQuote(task.title)},${sqlQuote(task.notes)},${sqlQuote(task.project)},${sqlQuote(task.priority)},${task.dueDate ? sqlQuote(task.dueDate) : "NULL"},${task.completed ? 1 : 0},${sqlQuote(task.createdAt)},${sqlQuote(task.updatedAt)})`); continue; }
    const existing = tasks.find((task) => task.id === action.taskId); if (!existing) throw new Error("The proposed task changes are no longer valid. Ask the assistant again.");
    if (action.type === "delete_task") { await runSql(`DELETE FROM tasks WHERE id=${sqlQuote(action.taskId)} AND user_id=${sqlQuote(userId)}`); continue; }
    const updated = taskInput(action.type === "complete_task" ? { completed: true } : action, existing); await runSql(`UPDATE tasks SET title=${sqlQuote(updated.title)},notes=${sqlQuote(updated.notes)},project=${sqlQuote(updated.project)},priority=${sqlQuote(updated.priority)},due_date=${updated.dueDate ? sqlQuote(updated.dueDate) : "NULL"},completed=${updated.completed ? 1 : 0},updated_at=${sqlQuote(updated.updatedAt)} WHERE id=${sqlQuote(action.taskId)} AND user_id=${sqlQuote(userId)}`);
  }
  return listTasks(userId);
}

async function api(request, response, url) {
  const path = url.pathname;
  if (request.method === "GET" && path === "/api/health") return send(response, 200, { ok: true, service: "zenith", storage: "sqlite" });
  if (request.method === "GET" && path === "/api/assistant/status") return send(response, 200, await ollamaStatus());
  if (request.method === "GET" && path === "/api/auth/status") {
    const rows = await runSql("SELECT password_hash AS passwordHash FROM users ORDER BY created_at LIMIT 1", true);
    return send(response, 200, { setupRequired: !rows[0]?.passwordHash });
  }
  if (request.method === "POST" && path === "/api/auth/setup") {
    const { displayName, password } = credentials(await body(request));
    const rows = await runSql("SELECT id, password_hash AS passwordHash FROM users ORDER BY created_at LIMIT 1", true);
    if (rows[0]?.passwordHash) return send(response, 409, { error: "Zenith is already set up. Please log in." });
    const userId = rows[0]?.id || randomUUID();
    if (!rows[0]) await runSql(`INSERT INTO users (id, display_name, password_hash, created_at) VALUES (${sqlQuote(userId)}, ${sqlQuote(displayName)}, ${sqlQuote(passwordHash(password))}, ${sqlQuote(new Date().toISOString())})`);
    else await runSql(`UPDATE users SET display_name=${sqlQuote(displayName)}, password_hash=${sqlQuote(passwordHash(password))} WHERE id=${sqlQuote(userId)}`);
    await runSql(`DELETE FROM sessions WHERE user_id=${sqlQuote(userId)}`);
    const session = await createSession(userId); return send(response, 201, { user: { id: userId, displayName }, expires: session.expires }, { "Set-Cookie": sessionCookie(session) });
  }
  if (request.method === "POST" && path === "/api/auth/session") {
    const { displayName, password } = credentials(await body(request));
    const rows = await runSql(`SELECT id, display_name AS displayName, password_hash AS passwordHash FROM users WHERE display_name=${sqlQuote(displayName)} LIMIT 1`, true);
    if (!rows[0] || !passwordMatches(password, rows[0].passwordHash)) return send(response, 401, { error: "Invalid display name or passphrase." });
    const session = await createSession(rows[0].id); return send(response, 201, { user: { id: rows[0].id, displayName: rows[0].displayName }, expires: session.expires }, { "Set-Cookie": sessionCookie(session) });
  }
  if (request.method === "GET" && path === "/api/auth/session") { const user = await requireUser(request, response); return user && send(response, 200, { user }); }
  if (request.method === "DELETE" && path === "/api/auth/session") { const token = cookieToken(request); if (token) await runSql(`DELETE FROM sessions WHERE token_hash=${sqlQuote(tokenHash(token))}`); return send(response, 204, {}); }
  const user = await requireUser(request, response); if (!user) return;
  if (request.method === "GET" && path === "/api/events") {
    response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache", Connection: "keep-alive", "X-Accel-Buffering": "no" });
    response.write("event: ready\ndata: {}\n\n");
    addLiveClient(user.id, request, response);
    return;
  }
  if (request.method === "POST" && path === "/api/assistant/chat") { try { return send(response, 200, await assistantChat(user.id, await body(request))); } catch (error) { return send(response, error.message.startsWith("Ask") || error.message.startsWith("Keep") ? 400 : 503, { error: error.message === "No Ollama model is available." ? error.message : "Local assistant is unavailable." }); } }
  if (request.method === "POST" && path === "/api/assistant/unload") { try { return send(response, 200, await unloadAssistantModel(await body(request))); } catch (error) { return send(response, error.message === "No Ollama model is configured." ? 409 : 503, { error: error.message === "No Ollama model is configured." ? error.message : "Local assistant is unavailable." }); } }
  if (request.method === "POST" && path === "/api/assistant/actions") { try { const tasks = await applyAssistantActions(user.id, (await body(request)).actions); broadcastTasksChanged(user.id); return send(response, 200, { tasks }); } catch (error) { return send(response, 409, { error: error.message }); } }
  if (request.method === "GET" && path === "/api/tasks") return send(response, 200, { tasks: await listTasks(user.id) });
  if (request.method === "POST" && path === "/api/tasks") { const input = await body(request); const task = taskInput(input, { id: randomUUID(), createdAt: new Date().toISOString() }); await runSql(`INSERT INTO tasks VALUES (${sqlQuote(task.id)},${sqlQuote(user.id)},${sqlQuote(task.title)},${sqlQuote(task.notes)},${sqlQuote(task.project)},${sqlQuote(task.priority)},${task.dueDate ? sqlQuote(task.dueDate) : "NULL"},${task.completed ? 1 : 0},${sqlQuote(task.createdAt)},${sqlQuote(task.updatedAt)})`); broadcastTasksChanged(user.id); return send(response, 201, { task }); }
  const match = path.match(/^\/api\/tasks\/([\w-]+)$/);
  if (match && request.method === "PATCH") { const rows = await runSql(`SELECT id,title,notes,project,priority,due_date AS dueDate,completed,created_at AS createdAt,updated_at AS updatedAt FROM tasks WHERE id=${sqlQuote(match[1])} AND user_id=${sqlQuote(user.id)}`, true); if (!rows[0]) return send(response, 404, { error: "Task not found." }); const task = taskInput(await body(request), taskFromRow(rows[0])); await runSql(`UPDATE tasks SET title=${sqlQuote(task.title)},notes=${sqlQuote(task.notes)},project=${sqlQuote(task.project)},priority=${sqlQuote(task.priority)},due_date=${task.dueDate ? sqlQuote(task.dueDate) : "NULL"},completed=${task.completed ? 1 : 0},updated_at=${sqlQuote(task.updatedAt)} WHERE id=${sqlQuote(task.id)} AND user_id=${sqlQuote(user.id)}`); broadcastTasksChanged(user.id); return send(response, 200, { task }); }
  if (match && request.method === "DELETE") { const result = await runSql(`DELETE FROM tasks WHERE id=${sqlQuote(match[1])} AND user_id=${sqlQuote(user.id)}; SELECT changes() AS changes`, true); if (!result.at(-1)?.changes) return send(response, 404, { error: "Task not found." }); broadcastTasksChanged(user.id); return send(response, 204, {}); }
  return send(response, 404, { error: "Route not found." });
}
async function staticFile(request, response, pathname) { const requested = pathname === "/" ? "/index.html" : pathname; const filePath = normalize(join(publicDir, requested)); if (!filePath.startsWith(publicDir)) { response.writeHead(403); return response.end(); } try { const file = await readFile(filePath); response.writeHead(200, { "Content-Type": `${contentTypes[extname(filePath)] || "application/octet-stream"}; charset=utf-8`, "Cache-Control": "no-cache" }); response.end(file); } catch { response.writeHead(404, { "Content-Type": "text/plain" }); response.end("Not found"); } }

export const app = createServer(async (request, response) => { const url = new URL(request.url, `http://${request.headers.host || "localhost"}`); try { await ready(); if (url.pathname.startsWith("/api/")) await api(request, response, url); else await staticFile(request, response, url.pathname); } catch (error) { send(response, error.code === "ENOENT" ? 503 : 400, { error: error.message || "Request failed." }); } });
if (process.argv[1] === fileURLToPath(import.meta.url)) app.listen(port, host, () => console.log(`Zenith is ready at http://${host}:${port}`));
