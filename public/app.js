const state = { tasks: [], showCompleted: false };
const $ = (selector) => document.querySelector(selector);
const api = async (path, options) => { const response = await fetch(path, options); if (!response.ok && response.status !== 204) throw new Error((await response.json()).error); return response.status === 204 ? null : response.json(); };

function isToday(date) { return date === new Date().toISOString().slice(0, 10); }
function formatDue(date) { if (!date) return "No due date"; return isToday(date) ? "Due today" : `Due ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${date}T12:00:00`))}`; }
function render() {
  const all = state.tasks; const visible = all.filter((task) => state.showCompleted || !task.completed);
  const open = all.filter((task) => !task.completed); $("#openCount").textContent = open.length; $("#todayCount").textContent = open.filter((task) => isToday(task.dueDate)).length; $("#doneCount").textContent = all.filter((task) => task.completed).length;
  $("#emptyState").hidden = visible.length > 0; const list = $("#taskList"); list.replaceChildren();
  for (const task of visible.sort((a, b) => Number(a.completed) - Number(b.completed) || (a.dueDate || "9999").localeCompare(b.dueDate || "9999"))) {
    const item = $("#taskTemplate").content.firstElementChild.cloneNode(true); item.classList.toggle("is-done", task.completed); item.querySelector("strong").textContent = task.title; item.querySelector("span").textContent = `${task.project} · ${formatDue(task.dueDate)} · ${task.priority}`; const toggle = item.querySelector(".toggle"); toggle.checked = task.completed;
    toggle.addEventListener("change", () => update(task.id, { completed: toggle.checked })); item.querySelector(".delete").addEventListener("click", () => remove(task.id)); list.append(item);
  }
}
async function load() { const { tasks } = await api("/api/tasks"); state.tasks = tasks; render(); }
async function update(id, patch) { const { task } = await api(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); state.tasks = state.tasks.map((existing) => existing.id === id ? task : existing); render(); }
async function remove(id) { await api(`/api/tasks/${id}`, { method: "DELETE" }); state.tasks = state.tasks.filter((task) => task.id !== id); render(); }
$("#taskForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const { task } = await api("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) }); state.tasks.unshift(task); event.currentTarget.reset(); $("#priority").value = "medium"; render(); $("#title").focus(); });
$("#showCompleted").addEventListener("click", (event) => { state.showCompleted = !state.showCompleted; event.currentTarget.textContent = state.showCompleted ? "Hide completed" : "Show completed"; render(); });
$("#today").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());
api("/api/assistant/status").then(({ reachable, model }) => { $("#assistantStatus").textContent = reachable ? `Local assistant ready${model ? ` · ${model}` : ""}` : "Local assistant offline · tasks stay available"; }).catch(() => { $("#assistantStatus").textContent = "Local assistant unavailable"; });
load().catch((error) => { $("#emptyState").textContent = `Could not load tasks: ${error.message}`; });
