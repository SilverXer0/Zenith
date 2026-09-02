import { createServer } from "node:http";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, extname, join, normalize } from "node:path";
import { randomUUID } from "node:crypto";

const root = dirname(fileURLToPath(import.meta.url));
const publicDir = join(root, "public");
const dataDir = process.env.ZENITH_DATA_DIR || join(root, "data");
const taskFile = join(dataDir, "tasks.json");
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || "127.0.0.1";

const contentTypes = { ".css": "text/css", ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml" };

async function readTasks() {
  if (!existsSync(taskFile)) return [];
  try { return JSON.parse(await readFile(taskFile, "utf8")); }
  catch { return []; }
}

async function saveTasks(tasks) {
  await mkdir(dataDir, { recursive: true });
  const temporaryFile = `${taskFile}.tmp`;
  await writeFile(temporaryFile, `${JSON.stringify(tasks, null, 2)}\n`);
  await rename(temporaryFile, taskFile);
}

function send(response, status, payload) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

async function body(request) {
  let raw = "";
  for await (const chunk of request) raw += chunk;
  try { return JSON.parse(raw || "{}"); }
  catch { throw new Error("Please send valid JSON."); }
}

function taskInput(input, existing = {}) {
  const title = (input.title ?? existing.title ?? "").trim();
  if (!title) throw new Error("A task needs a title.");
  const dueDate = input.dueDate === undefined ? existing.dueDate : input.dueDate || null;
  return {
    ...existing,
    title,
    notes: (input.notes ?? existing.notes ?? "").trim(),
    project: (input.project ?? existing.project ?? "Inbox").trim() || "Inbox",
    priority: ["low", "medium", "high"].includes(input.priority) ? input.priority : (existing.priority || "medium"),
    dueDate,
    completed: input.completed === undefined ? Boolean(existing.completed) : Boolean(input.completed),
    updatedAt: new Date().toISOString()
  };
}

async function ollamaStatus() {
  const baseUrl = process.env.OLLAMA_URL || "http://127.0.0.1:11434";
  try {
    const response = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(800) });
    if (!response.ok) throw new Error("unavailable");
    const { models = [] } = await response.json();
    return { enabled: true, reachable: true, model: process.env.OLLAMA_MODEL || models[0]?.name || null };
  } catch {
    return { enabled: true, reachable: false, model: process.env.OLLAMA_MODEL || null };
  }
}

async function api(request, response, url) {
  const path = url.pathname;
  if (request.method === "GET" && path === "/api/health") return send(response, 200, { ok: true, service: "zenith" });
  if (request.method === "GET" && path === "/api/assistant/status") return send(response, 200, await ollamaStatus());
  if (request.method === "GET" && path === "/api/tasks") return send(response, 200, { tasks: await readTasks() });
  if (request.method === "POST" && path === "/api/tasks") {
    const input = await body(request);
    const task = taskInput(input, { id: randomUUID(), createdAt: new Date().toISOString() });
    const tasks = await readTasks(); tasks.unshift(task); await saveTasks(tasks);
    return send(response, 201, { task });
  }
  const match = path.match(/^\/api\/tasks\/([\w-]+)$/);
  if (match && request.method === "PATCH") {
    const input = await body(request); const tasks = await readTasks(); const index = tasks.findIndex((task) => task.id === match[1]);
    if (index < 0) return send(response, 404, { error: "Task not found." });
    tasks[index] = taskInput(input, tasks[index]); await saveTasks(tasks);
    return send(response, 200, { task: tasks[index] });
  }
  if (match && request.method === "DELETE") {
    const tasks = await readTasks(); const remaining = tasks.filter((task) => task.id !== match[1]);
    if (remaining.length === tasks.length) return send(response, 404, { error: "Task not found." });
    await saveTasks(remaining); return send(response, 204, {});
  }
  return send(response, 404, { error: "Route not found." });
}

async function staticFile(request, response, pathname) {
  const requested = pathname === "/" ? "/index.html" : pathname;
  const filePath = normalize(join(publicDir, requested));
  if (!filePath.startsWith(publicDir)) { response.writeHead(403); return response.end(); }
  try {
    const file = await readFile(filePath);
    response.writeHead(200, { "Content-Type": `${contentTypes[extname(filePath)] || "application/octet-stream"}; charset=utf-8` }); response.end(file);
  } catch { response.writeHead(404, { "Content-Type": "text/plain" }); response.end("Not found"); }
}

export const app = createServer(async (request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);
  try { if (url.pathname.startsWith("/api/")) await api(request, response, url); else await staticFile(request, response, url.pathname); }
  catch (error) { send(response, 400, { error: error.message || "Request failed." }); }
});

if (process.argv[1] === fileURLToPath(import.meta.url)) app.listen(port, host, () => console.log(`Zenith is ready at http://${host}:${port}`));
