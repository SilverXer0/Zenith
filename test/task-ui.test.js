import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { createContext, runInContext } from "node:vm";

// Exercise the real app script with a minimal DOM and controllable network/timers.
// No browser or extra packages are required for the default regression suite.
const source = await readFile(new URL("../public/app.js", import.meta.url), "utf8");
class Element {
  constructor(id = "") {
    this.id = id;
    this.children = [];
    this.selectors = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.classList = { toggle() {} };
  }
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  fire(type) { return this.listeners.get(type)?.({ currentTarget: this, preventDefault() {} }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  cloneNode() { return new Element(); }
  reset() { this.resetCalled = true; this.fields = {}; }
  focus() { this.focused = true; }
  showModal() {}
  close() {}
  remove() {}
}
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const task = (id, title = id, extra = {}) => ({ id, title, project: "Inbox", priority: "medium", dueDate: null, completed: false, ...extra });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
async function browser({ tasks = [], onRequest, live = true } = {}) {
  const elements = new Map();
  const node = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element(selector.slice(1)));
    return elements.get(selector);
  };
  node("#taskTemplate").content = { firstElementChild: new Element() };
  const document = new Element();
  document.querySelector = node;
  document.createElement = () => new Element();
  const window = new Element();
  const streams = [];
  class EventSource extends Element {
    constructor() { super(); this.readyState = 1; streams.push(this); }
    close() { this.readyState = 2; }
  }
  if (live) window.EventSource = EventSource;
  let timerId = 0;
  const timers = new Map();
  const intervals = new Map();
  const requests = [];
  const context = createContext({
    document, window, navigator: {}, EventSource,
    FormData: class { constructor(form) { return Object.entries(form.fields || {}); } },
    setTimeout: (callback) => { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    setInterval: (callback, delay) => { intervals.set(delay, callback); },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      const custom = onRequest?.(url, options);
      if (custom !== undefined) return custom;
      if (url === "/api/auth/session") return response({}, 401);
      if (url === "/api/auth/status") return response({ setupRequired: false });
      if (url === "/api/assistant/status") return response({ reachable: false });
      if (url === "/api/tasks") return response({ tasks });
      if (url.startsWith("/api/summaries/daily")) return response({ counts: { completed: 0, created: 0, open: tasks.length }, completedTasks: [] });
      throw new Error(`Unexpected request: ${url}`);
    }
  });
  const run = (code) => runInContext(code, context);
  run(source);
  await new Promise((resolve) => setImmediate(resolve)); // Finish the signed-out startup.
  run('state.user = { id: "test-user" };');
  return {
    node, run, streams, window, document, requests, intervals,
    titles: () => node("#taskList").children.map((item) => item.querySelector("strong").textContent),
    flush: async () => {
      const callbacks = [...timers.values()];
      timers.clear();
      await Promise.all(callbacks.map((callback) => callback()));
    }
  };
}

for (const formId of ["captureForm", "taskForm"]) {
  test(`${formId}: saved task renders even if the follow-up refresh fails`, async () => {
    const saved = task("new", "Saved immediately");
    const ui = await browser({ onRequest: (url, options) => {
      if (url !== "/api/tasks") return;
      return options.method === "POST" ? response({ task: saved }, 201) : response({ error: "Temporarily offline" }, 503);
    } });
    ui.node(`#${formId}`).fields = { title: saved.title };
    await ui.node(`#${formId}`).fire("submit");
    assert.deepEqual(ui.titles(), [saved.title]);
    assert.equal(ui.node("#openCount").textContent, 1);
    assert.equal(ui.node(`#${formId}`).resetCalled, true);
    assert.equal(ui.node(`#${formId}`).querySelector("button[type=submit]").disabled, false);
    assert.equal(ui.node("#taskError").textContent, "");
    await ui.flush();
    assert.deepEqual(ui.titles(), [saved.title]);
    assert.match(ui.node("#syncStatus").textContent, /retrying automatically/);
    assert.equal(ui.node("#taskError").textContent, "", "A saved task is not reported as an unsuccessful save");
  });
}

test("a rejected save preserves the draft and does not add a task", async () => {
  const ui = await browser({ onRequest: (url, options) => {
    if (url === "/api/tasks" && options.method === "POST") return response({ error: "Save failed" }, 503);
  } });
  const form = ui.node("#captureForm");
  form.fields = { title: "Keep this draft" };
  await form.fire("submit");
  assert.deepEqual(ui.titles(), []);
  assert.equal(form.fields.title, "Keep this draft");
  assert.equal(form.resetCalled, undefined);
  assert.equal(form.querySelector("button[type=submit]").disabled, false);
  assert.equal(ui.node("#taskError").textContent, "Save failed");
});

test("an old read cannot overwrite a saved task; a broadcast/save race cannot duplicate it", async () => {
  const pending = deferred();
  const ui = await browser({ onRequest: (url) => url === "/api/tasks" ? pending.promise : undefined });
  const read = ui.run("loadTasks()");
  ui.run(`taskSaved(${JSON.stringify(task("new"))})`);
  pending.resolve(response({ tasks: [] }));
  await read;
  assert.deepEqual(ui.titles(), ["new"]);
  ui.run(`taskSaved(${JSON.stringify(task("new"))})`);
  assert.deepEqual(ui.titles(), ["new"]);
});

test("overlapping task refreshes ignore out-of-order responses", async () => {
  const pending = [deferred(), deferred()];
  let requestIndex = 0;
  const ui = await browser({ onRequest: (url) => url === "/api/tasks" ? pending[requestIndex++].promise : undefined });
  const oldRead = ui.run("loadTasks()");
  const newRead = ui.run("loadTasks()");
  pending[1].resolve(response({ tasks: [task("latest")] }));
  await newRead;
  pending[0].resolve(response({ tasks: [task("stale")] }));
  await oldRead;
  assert.deepEqual(ui.titles(), ["latest"]);
  assert.ok(ui.requests.filter(({ url }) => url === "/api/tasks").every(({ options }) => options.cache === "no-store"));
});

test("live changes and reconnects reload without toggling completed or reloading the page", async () => {
  let tasks = [task("first")];
  const ui = await browser({ onRequest: (url) => url === "/api/tasks" ? response({ tasks }) : undefined });
  ui.run("connectLiveUpdates()");
  const stream = ui.streams[0];
  stream.fire("ready");
  await ui.flush();
  assert.deepEqual(ui.titles(), ["first"]);
  assert.equal(ui.node("#syncStatus").textContent, "Live sync connected");
  tasks = [task("from-other-device")];
  stream.fire("tasks_changed");
  stream.fire("tasks_changed");
  stream.fire("tasks_changed");
  await ui.flush();
  assert.deepEqual(ui.titles(), ["from-other-device"]);
  assert.equal(ui.requests.filter(({ url }) => url === "/api/tasks").length, 2, "Bursts should use one refresh");
  stream.onerror();
  tasks = [task("missed-while-offline")];
  stream.fire("ready");
  await ui.flush();
  assert.deepEqual(ui.titles(), ["missed-while-offline"]);
});

test("returning to the app and periodic fallback fetch missed changes", async () => {
  let tasks = [];
  const ui = await browser({ live: false, onRequest: (url) => url === "/api/tasks" ? response({ tasks }) : undefined });
  ui.run("connectLiveUpdates()");
  tasks = [task("resume")];
  ui.document.fire("visibilitychange");
  await ui.flush();
  assert.deepEqual(ui.titles(), ["resume"]);
  assert.equal(ui.node("#syncStatus").textContent, "Auto-refresh on (30s)");
  tasks = [task("online")];
  ui.window.fire("online");
  await ui.flush();
  assert.deepEqual(ui.titles(), ["online"]);
  tasks = [task("fallback")];
  ui.intervals.get(30000)();
  await ui.flush();
  assert.deepEqual(ui.titles(), ["fallback"]);
  ui.document.hidden = true;
  const count = ui.requests.length;
  ui.intervals.get(30000)();
  await ui.flush();
  assert.equal(ui.requests.length, count, "Do not poll background tabs");
});

test("edit, completion, deletion and assistant confirmation share immediate rendering", async () => {
  const ui = await browser({ onRequest: (url, options) => {
    if (url === "/api/tasks/local" && options.method === "PATCH") return response({ task: task("local", "Edited", JSON.parse(options.body)) });
    if (url === "/api/tasks/local" && options.method === "DELETE") return response(null, 204);
    if (url === "/api/assistant/actions") return response({ tasks: [task("confirmed")] });
  } });
  ui.run(`setTasks(${JSON.stringify([task("local")])})`);
  await ui.run('update("local", { title: "Edited" })');
  assert.deepEqual(ui.titles(), ["Edited"]);
  await ui.run('update("local", { completed: true })');
  assert.deepEqual(ui.titles(), []);
  assert.equal(ui.node("#doneCount").textContent, 1);
  await ui.node("#showCompleted").fire("click");
  assert.deepEqual(ui.titles(), ["Edited"]);
  await ui.run('remove("local")');
  assert.deepEqual(ui.titles(), []);
  assert.equal(ui.node("#doneCount").textContent, 0);
  ui.run('addActionProposal([{ type: "create_task", title: "confirmed" }])');
  const proposal = ui.node("#assistantChat").children.at(-1);
  await proposal.children.at(-1).fire("click");
  assert.deepEqual(ui.titles(), ["confirmed"]);
});

test("failed task actions restore controls and show a visible error", async () => {
  const ui = await browser({ onRequest: (url) => url === "/api/tasks/local" ? response({ error: "Could not save" }, 503) : undefined });
  ui.run(`setTasks(${JSON.stringify([task("local")])})`);
  await ui.run('taskAction(() => update("local", { completed: true }))');
  assert.deepEqual(ui.titles(), ["local"]);
  assert.equal(ui.node("#taskList").children[0].querySelector(".toggle").checked, false);
  assert.equal(ui.node("#taskList").children[0].querySelector(".toggle").disabled, false);
  assert.equal(ui.node("#taskError").textContent, "Could not save");
});

test("logout ignores in-flight reads and closes the live stream", async () => {
  const pending = deferred();
  const ui = await browser({ onRequest: (url, options) => {
    if (url === "/api/tasks") return pending.promise;
    if (url === "/api/auth/session" && options.method === "DELETE") return response(null, 204);
  } });
  ui.run("connectLiveUpdates()");
  const read = ui.run("loadTasks()");
  await ui.node("#logout").fire("click");
  pending.resolve(response({ tasks: [task("private")] }));
  await read;
  assert.equal(ui.run("state.tasks.length"), 0);
  assert.equal(ui.streams[0].readyState, 2);
  assert.equal(ui.node("#manager").hidden, true);
  assert.equal(ui.node("#logout").hidden, true);
});
