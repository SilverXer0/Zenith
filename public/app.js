const state = { tasks: [], showCompleted: false, user: null, assistantHistory: [] };
let assistantModel = null;
let liveEvents = null;
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
    toggle.addEventListener("change", () => update(task.id, { completed: toggle.checked })); item.querySelector(".edit").addEventListener("click", () => openEditor(task)); item.querySelector(".delete").addEventListener("click", () => remove(task.id)); list.append(item);
  }
}
async function loadTasks() { const { tasks } = await api("/api/tasks"); state.tasks = tasks; render(); $("#logout").hidden = false; $("#unloadModel").hidden = !assistantModel; }
function closeLiveUpdates() { if (liveEvents) { liveEvents.close(); liveEvents = null; } }
function connectLiveUpdates() {
  closeLiveUpdates();
  if (!state.user || !window.EventSource) { $("#syncStatus").textContent = window.EventSource ? "Live sync unavailable" : "Live sync not supported"; return; }
  $("#syncStatus").textContent = "Live sync connecting…";
  liveEvents = new EventSource("/api/events");
  liveEvents.addEventListener("ready", () => { $("#syncStatus").textContent = "Live sync connected"; });
  liveEvents.addEventListener("tasks_changed", () => { loadTasks().catch(() => { $("#syncStatus").textContent = "Live sync reconnecting…"; }); });
  liveEvents.onerror = () => { $("#syncStatus").textContent = "Live sync reconnecting…"; };
}
function formatCalendarStart(event) {
  if (!event.start) return "Time unavailable";
  const value = event.allDay ? new Date(`${event.start}T12:00:00`) : new Date(event.start);
  const options = { weekday: "short", month: "short", day: "numeric" };
  if (!event.allDay) { options.hour = "numeric"; options.minute = "2-digit"; }
  return new Intl.DateTimeFormat(undefined, options).format(value);
}
function renderCalendarEvents(events) {
  const list = $("#calendarEvents"); list.replaceChildren();
  if (!events.length) { const item = document.createElement("li"); item.className = "calendar-empty"; item.textContent = "No upcoming events."; list.append(item); return; }
  for (const event of events) { const item = document.createElement("li"); const title = document.createElement("strong"); title.textContent = event.title; const details = document.createElement("span"); details.textContent = `${formatCalendarStart(event)}${event.location ? ` · ${event.location}` : ""}`; item.append(title, details); list.append(item); }
}
async function loadCalendar() {
  $("#calendarError").textContent = "";
  try {
    const status = await api("/api/calendar/status");
    const connect = $("#connectCalendar"); const disconnect = $("#disconnectCalendar");
    if (!status.configured) { $("#calendarTitle").textContent = "Calendar optional"; $("#calendarDescription").textContent = "Add Google Calendar settings on the Zenith server to connect it."; connect.hidden = true; disconnect.hidden = true; renderCalendarEvents([]); return; }
    connect.hidden = false; connect.textContent = status.connected ? "Reconnect calendar" : "Connect calendar"; disconnect.hidden = !status.connected;
    if (!status.connected) { $("#calendarTitle").textContent = "Calendar not connected"; $("#calendarDescription").textContent = "Connect Google Calendar to see upcoming events alongside your tasks."; renderCalendarEvents([]); return; }
    $("#calendarTitle").textContent = status.calendarName || "Google Calendar"; $("#calendarDescription").textContent = "Upcoming events for the next seven days.";
    const { events } = await api("/api/calendar/events"); renderCalendarEvents(events);
  } catch (error) { $("#calendarError").textContent = error.message; }
}
function actionLabel(action) { if (action.type === "create_task") return `Create “${action.title}”`; if (action.type === "complete_task") return "Mark task complete"; if (action.type === "delete_task") return "Delete task"; return `Update “${action.title || "task"}”`; }
function addActionProposal(actions) { if (!actions?.length) return; const chat = $("#assistantChat"); const proposal = document.createElement("div"); proposal.className = "assistant-proposal"; const title = document.createElement("strong"); title.textContent = "Suggested changes"; proposal.append(title); const list = document.createElement("ul"); for (const action of actions) { const item = document.createElement("li"); item.textContent = actionLabel(action); list.append(item); } proposal.append(list); const confirm = document.createElement("button"); confirm.type = "button"; confirm.textContent = "Confirm changes"; confirm.addEventListener("click", async () => { confirm.disabled = true; $("#assistantError").textContent = "Applying changes…"; try { const result = await api("/api/assistant/actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actions }) }); state.tasks = result.tasks; render(); proposal.remove(); $("#assistantError").textContent = "Changes applied."; } catch (error) { confirm.disabled = false; $("#assistantError").textContent = error.message; } }); proposal.append(confirm); chat.append(proposal); chat.scrollTop = chat.scrollHeight; }
async function update(id, patch) { const { task } = await api(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); state.tasks = state.tasks.map((existing) => existing.id === id ? task : existing); render(); }
async function remove(id) { await api(`/api/tasks/${id}`, { method: "DELETE" }); state.tasks = state.tasks.filter((task) => task.id !== id); render(); }
function openEditor(task) { const form = $("#editForm"); form.dataset.taskId = task.id; $("#editTitle").value = task.title; $("#editProject").value = task.project; $("#editNotes").value = task.notes; $("#editPriority").value = task.priority; $("#editDueDate").value = task.dueDate || ""; $("#editError").textContent = ""; $("#editDialog").showModal(); }
$("#taskForm").addEventListener("submit", async (event) => { event.preventDefault(); const button = event.currentTarget.querySelector("button[type=submit]"); button.disabled = true; $("#taskError").textContent = ""; try { const form = new FormData(event.currentTarget); await api("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) }); await loadTasks(); event.currentTarget.reset(); $("#priority").value = "medium"; $("#title").focus(); } catch (error) { $("#taskError").textContent = error.message; } finally { button.disabled = false; } });
$("#cancelEdit").addEventListener("click", () => $("#editDialog").close());
$("#editForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { await update(event.currentTarget.dataset.taskId, Object.fromEntries(form)); $("#editDialog").close(); } catch (error) { $("#editError").textContent = error.message; } });
$("#showCompleted").addEventListener("click", (event) => { state.showCompleted = !state.showCompleted; event.currentTarget.textContent = state.showCompleted ? "Hide completed" : "Show completed"; render(); });
$("#connectCalendar").addEventListener("click", () => { window.location.href = "/api/calendar/connect"; });
$("#disconnectCalendar").addEventListener("click", async () => { try { await api("/api/calendar/connection", { method: "DELETE" }); await loadCalendar(); } catch (error) { $("#calendarError").textContent = error.message; } });
$("#today").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());
api("/api/assistant/status").then(({ reachable, model }) => { $("#assistantStatus").textContent = reachable ? `Local assistant ready${model ? ` · ${model}` : ""}` : "Local assistant offline · tasks stay available"; }).catch(() => { $("#assistantStatus").textContent = "Local assistant unavailable"; });
api("/api/assistant/status").then(({ model }) => { assistantModel = model; if (state.user && model) $("#unloadModel").hidden = false; });
function showAuth(setupRequired) { $("#authPanel").hidden = false; $("#manager").hidden = true; $("#authEyebrow").textContent = setupRequired ? "WELCOME TO ZENITH" : "WELCOME BACK"; $("#authTitle").textContent = setupRequired ? "Set up your local account." : "Sign in to Zenith."; $("#authIntro").textContent = setupRequired ? "Choose a passphrase. It stays local and protects access to your tasks." : "Use your local account to continue."; $("#authSubmit").textContent = setupRequired ? "Create account" : "Sign in"; $("#authForm").dataset.mode = setupRequired ? "setup" : "login"; $("#password").autocomplete = setupRequired ? "new-password" : "current-password"; }
$("#authForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const mode = event.currentTarget.dataset.mode; $("#authError").textContent = ""; try { const session = await api(mode === "setup" ? "/api/auth/setup" : "/api/auth/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) }); state.user = session.user; $("#authPanel").hidden = true; $("#manager").hidden = false; await loadTasks(); connectLiveUpdates(); await loadCalendar(); } catch (error) { $("#authError").textContent = error.message; } });
$("#logout").addEventListener("click", async () => { closeLiveUpdates(); await api("/api/auth/session", { method: "DELETE" }); state.user = null; state.tasks = []; $("#logout").hidden = true; $("#unloadModel").hidden = true; showAuth(false); $("#password").value = ""; });
$("#unloadModel").addEventListener("click", async () => { const button = $("#unloadModel"); button.disabled = true; try { const result = await api("/api/assistant/unload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }); $("#assistantStatus").textContent = result.unloaded ? "Local assistant released · VRAM available" : "Local assistant is not loaded"; } catch (error) { $("#assistantStatus").textContent = error.message; } finally { button.disabled = false; } });
$("#assistantForm").addEventListener("submit", async (event) => { event.preventDefault(); const input = $("#assistantInput"); const message = input.value.trim(); if (!message) return; const chat = $("#assistantChat"); const userMessage = document.createElement("p"); userMessage.className = "assistant-message user-message"; userMessage.textContent = message; chat.append(userMessage); input.value = ""; $("#assistantError").textContent = "Thinking…"; try { const result = await api("/api/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history: state.assistantHistory }) }); const answer = document.createElement("p"); answer.className = "assistant-message"; answer.textContent = result.message; chat.append(answer); addActionProposal(result.actions); state.assistantHistory.push({ role: "user", content: message }, { role: "assistant", content: result.message }); $("#assistantError").textContent = result.model ? `Using ${result.model}` : ""; chat.scrollTop = chat.scrollHeight; } catch (error) { $("#assistantError").textContent = error.message === "Local assistant is unavailable." ? "Ollama is offline. Your tasks are still available." : error.message; } });
async function start() { const existing = await fetch("/api/auth/session"); if (existing.ok) { const session = await existing.json(); state.user = session.user; $("#manager").hidden = false; await loadTasks(); connectLiveUpdates(); await loadCalendar(); return; } const { setupRequired } = await api("/api/auth/status"); showAuth(setupRequired); }
start().catch((error) => { showAuth(false); $("#authError").textContent = error.message; });
