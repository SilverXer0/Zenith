const state = { tasks: [], showCompleted: false, user: null, assistantHistory: [] };
let assistantModel = null;
let liveEvents = null;
const $ = (selector) => document.querySelector(selector);
const api = async (path, options) => { const response = await fetch(path, options); if (!response.ok && response.status !== 204) throw new Error((await response.json()).error); return response.status === 204 ? null : response.json(); };
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

function localDateString(date = new Date()) { const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return offsetDate.toISOString().slice(0, 10); }
function isToday(date) { return date === localDateString(); }
function formatDue(date) { if (!date) return "No due date"; return isToday(date) ? "Due today" : `Due ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${date}T12:00:00`))}`; }
function renderBriefing(briefing) { const { counts, focusTasks } = briefing; const summary = []; if (counts.overdue) summary.push(`${counts.overdue} overdue`); if (counts.dueToday) summary.push(`${counts.dueToday} due today`); $("#briefingSummary").textContent = summary.length ? `${summary.join(" · ")} · ${counts.open} open total` : counts.open ? `${counts.open} open task${counts.open === 1 ? "" : "s"} to work through.` : "No open tasks. Enjoy the clear space."; const list = $("#briefingTasks"); list.replaceChildren(); for (const task of focusTasks) { const item = document.createElement("li"); const title = document.createElement("strong"); title.textContent = task.title; const details = document.createElement("span"); details.textContent = `${task.project} · ${formatDue(task.dueDate)} · ${task.priority} priority`; item.append(title, details); list.append(item); } }
async function loadBriefing() { $("#briefingError").textContent = ""; try { const briefing = await api(`/api/briefing?date=${encodeURIComponent(localDateString())}`); renderBriefing(briefing); } catch (error) { $("#briefingError").textContent = error.message; } }
function renderMorningBriefing(briefing) { $("#morningSummary").textContent = briefing.summary; const renderTaskList = (selector, tasks, emptyText) => { const list = $(selector); list.replaceChildren(); if (!tasks.length) { const empty = document.createElement("li"); empty.className = "morning-empty"; empty.textContent = emptyText; list.append(empty); return; } for (const task of tasks) { const item = document.createElement("li"); const title = document.createElement("strong"); title.textContent = task.title; const details = document.createElement("span"); details.textContent = `${task.project} · ${formatDue(task.dueDate)} · ${task.priority} priority`; item.append(title, details); list.append(item); } }; renderTaskList("#morningUrgent", [...briefing.overdue, ...briefing.dueToday], "Nothing urgent today."); renderTaskList("#morningUpcoming", briefing.upcoming, "No dated tasks in the next few days."); $("#morningCalendar").textContent = !briefing.calendar.connected ? "Google Calendar is not connected." : briefing.calendar.available ? `${briefing.calendar.events.length} calendar event${briefing.calendar.events.length === 1 ? "" : "s"} today.` : "Google Calendar is temporarily unavailable."; }
async function loadMorningBriefing() { $("#morningError").textContent = ""; try { const briefing = await api(`/api/briefing/morning?date=${encodeURIComponent(localDateString())}`); renderMorningBriefing(briefing); } catch (error) { $("#morningError").textContent = error.message; } }
function mondayDate(date = new Date()) { const monday = new Date(date); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7)); return localDateString(monday); }
function renderWeeklyPlan(plan) { $("#weeklySummary").textContent = `${plan.counts.open} open · ${plan.counts.scheduled} scheduled this week · ${plan.counts.unscheduled} unscheduled${plan.counts.overdue ? ` · ${plan.counts.overdue} overdue` : ""}`; const days = $("#weeklyDays"); days.replaceChildren(); for (const day of plan.days) { const section = document.createElement("section"); section.className = "weekly-day"; const heading = document.createElement("h3"); heading.textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" }).format(new Date(`${day.date}T12:00:00`)); const list = document.createElement("ul"); if (!day.tasks.length) { const empty = document.createElement("li"); empty.className = "weekly-empty"; empty.textContent = "No tasks scheduled"; list.append(empty); } for (const task of day.tasks) { const item = document.createElement("li"); const title = document.createElement("strong"); title.textContent = task.title; item.append(title, ` · ${task.project} · ${task.priority}`); list.append(item); } section.append(heading, list); days.append(section); } const unscheduled = $("#weeklyUnscheduled"); unscheduled.replaceChildren(); if (!plan.unscheduled.length) { const empty = document.createElement("li"); empty.className = "weekly-empty"; empty.textContent = "Nothing waiting for a date."; unscheduled.append(empty); } for (const task of plan.unscheduled) { const item = document.createElement("li"); const title = document.createElement("strong"); title.textContent = task.title; item.append(title, ` · ${task.project} · ${task.priority}`); unscheduled.append(item); } $("#weeklyCalendar").textContent = !plan.calendar.connected ? "Google Calendar is not connected." : plan.calendar.available ? `${plan.calendar.events.length} calendar event${plan.calendar.events.length === 1 ? "" : "s"} this week.` : "Google Calendar is temporarily unavailable."; }
async function loadWeeklyPlan() { $("#weeklyError").textContent = ""; try { const plan = await api(`/api/weekly-plan?start=${encodeURIComponent(mondayDate())}`); renderWeeklyPlan(plan); } catch (error) { $("#weeklyError").textContent = error.message; } }
function updateNotificationUI() {
  const button = $("#enableNotifications"); const status = $("#notificationStatus");
  if (!("Notification" in window)) { button.hidden = true; status.textContent = ""; return; }
  if (!window.isSecureContext) { button.hidden = true; status.textContent = "Reminders need a secure connection."; return; }
  if (Notification.permission === "granted") { button.hidden = true; status.textContent = "Task reminders on"; return; }
  button.hidden = false; button.disabled = false;
  status.textContent = Notification.permission === "denied" ? "Reminders are blocked in browser settings." : "";
}
function notifyDueTasks(tasks) {
  if (!("Notification" in window) || !window.isSecureContext || Notification.permission !== "granted") return;
  const today = localDateString(); const storageKey = `zenith-notified-${today}`; let notified = new Set();
  try { notified = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); } catch {}
  for (const task of tasks.filter((candidate) => !candidate.completed && candidate.dueDate && candidate.dueDate <= today && !notified.has(candidate.id))) {
    try { new Notification(`Due: ${task.title}`, { body: task.dueDate === today ? "Due today" : `Overdue · ${task.dueDate}`, tag: `zenith-task-${task.id}` }); notified.add(task.id); } catch {}
  }
  try { localStorage.setItem(storageKey, JSON.stringify([...notified])); } catch {}
}
async function loadNotificationStatus() { updateNotificationUI(); }
function render() {
  const all = state.tasks; const visible = all.filter((task) => state.showCompleted || !task.completed);
  const open = all.filter((task) => !task.completed); $("#openCount").textContent = open.length; $("#todayCount").textContent = open.filter((task) => isToday(task.dueDate)).length; $("#doneCount").textContent = all.filter((task) => task.completed).length;
  $("#emptyState").hidden = visible.length > 0; const list = $("#taskList"); list.replaceChildren();
  for (const task of visible.sort((a, b) => Number(a.completed) - Number(b.completed) || (a.dueDate || "9999").localeCompare(b.dueDate || "9999"))) {
    const item = $("#taskTemplate").content.firstElementChild.cloneNode(true); item.classList.toggle("is-done", task.completed); item.querySelector("strong").textContent = task.title; item.querySelector("span").textContent = `${task.project} · ${formatDue(task.dueDate)} · ${task.priority}`; const toggle = item.querySelector(".toggle"); toggle.checked = task.completed;
    toggle.addEventListener("change", () => update(task.id, { completed: toggle.checked })); item.querySelector(".edit").addEventListener("click", () => openEditor(task)); item.querySelector(".delete").addEventListener("click", () => remove(task.id)); list.append(item);
  }
}
async function loadTasks() { const { tasks } = await api("/api/tasks"); state.tasks = tasks; render(); renderBriefing({ counts: { open: tasks.filter((task) => !task.completed).length, overdue: tasks.filter((task) => !task.completed && task.dueDate && task.dueDate < localDateString()).length, dueToday: tasks.filter((task) => !task.completed && isToday(task.dueDate)).length }, focusTasks: tasks.filter((task) => !task.completed).sort((a, b) => (a.dueDate || "9999").localeCompare(b.dueDate || "9999") || ({ high: 0, medium: 1, low: 2 }[a.priority] - { high: 0, medium: 1, low: 2 }[b.priority])).slice(0, 5) }); notifyDueTasks(tasks); $("#logout").hidden = false; $("#unloadModel").hidden = !assistantModel; }
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
let editingMemoryId = null;
function resetMemoryForm() { editingMemoryId = null; $("#memoryForm").reset(); $("#memoryForm button[type=submit]").textContent = "Save context"; $("#cancelMemory").hidden = true; }
function renderMemory(memories) {
  const list = $("#memoryList"); list.replaceChildren();
  for (const memory of memories) { const item = document.createElement("li"); const head = document.createElement("div"); head.className = "memory-item-head"; const category = document.createElement("span"); category.className = "memory-item-category"; category.textContent = memory.category; const actions = document.createElement("div"); actions.className = "memory-item-actions"; const edit = document.createElement("button"); edit.type = "button"; edit.className = "text-button"; edit.textContent = "Edit"; edit.addEventListener("click", () => { editingMemoryId = memory.id; $("#memoryContent").value = memory.content; $("#memoryCategory").value = memory.category; $("#memoryForm button[type=submit]").textContent = "Update context"; $("#cancelMemory").hidden = false; $("#memoryContent").focus(); }); const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "Delete"; remove.addEventListener("click", async () => { if (!window.confirm("Delete this context note?")) return; try { await api(`/api/memory/${memory.id}`, { method: "DELETE" }); if (editingMemoryId === memory.id) resetMemoryForm(); await loadMemory(); } catch (error) { $("#memoryError").textContent = error.message; } }); actions.append(edit, remove); head.append(category, actions); const content = document.createElement("span"); content.className = "memory-item-content"; content.textContent = memory.content; item.append(head, content); list.append(item); }
}
async function loadMemory() { $("#memoryError").textContent = ""; try { const { memories } = await api("/api/memory"); renderMemory(memories); } catch (error) { $("#memoryError").textContent = error.message; } }
let voiceRecorder = null;
let voiceStream = null;
let ttsReady = false;
async function loadVoiceStatus() {
  try {
    const { configured, ttsConfigured } = await api("/api/voice/status");
    ttsReady = ttsConfigured;
    $("#voiceButton").hidden = !configured;
    $("#voiceStatus").textContent = configured || ttsConfigured ? `${configured ? "Local voice input" : "Local voice replies"} is ready.` : "";
  } catch { $("#voiceButton").hidden = true; $("#voiceStatus").textContent = ""; }
}
async function toggleVoiceRecording() {
  const button = $("#voiceButton");
  if (voiceRecorder?.state === "recording") { voiceRecorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { $("#voiceStatus").textContent = "This browser cannot record audio."; return; }
  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = []; voiceRecorder = new MediaRecorder(voiceStream); const mimeType = voiceRecorder.mimeType || "audio/webm";
    voiceRecorder.addEventListener("dataavailable", (event) => { if (event.data.size) chunks.push(event.data); });
    voiceRecorder.addEventListener("stop", async () => {
      voiceStream?.getTracks().forEach((track) => track.stop()); voiceStream = null; button.disabled = true; button.textContent = "Transcribing…"; $("#voiceStatus").textContent = "Sending audio to local Zenith…";
      try { const result = await api("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": mimeType }, body: new Blob(chunks, { type: mimeType }) }); $("#assistantInput").value = result.text; $("#assistantInput").focus(); $("#voiceStatus").textContent = "Transcription ready — review it before sending."; } catch (error) { $("#voiceStatus").textContent = error.message; } finally { button.disabled = false; button.textContent = "Speak"; voiceRecorder = null; }
    });
    voiceRecorder.start(); button.textContent = "Stop recording"; $("#voiceStatus").textContent = "Recording… tap again when finished.";
  } catch { $("#voiceStatus").textContent = "Microphone access was not available."; }
}
async function speakAssistantReply(text, button) {
  button.disabled = true; button.textContent = "Speaking…";
  try {
    const response = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) throw new Error((await response.json()).error);
    const audioUrl = URL.createObjectURL(await response.blob()); const audio = new Audio(audioUrl);
    await new Promise((resolve, reject) => { audio.addEventListener("ended", resolve, { once: true }); audio.addEventListener("error", () => reject(new Error("The audio could not be played.")), { once: true }); audio.play().catch(reject); });
    URL.revokeObjectURL(audioUrl);
  } catch (error) { $("#voiceStatus").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "Speak reply"; }
}
function addSpeechControl(chat, text) { if (!ttsReady) return; const button = document.createElement("button"); button.type = "button"; button.className = "text-button voice-replay"; button.textContent = "Speak reply"; button.addEventListener("click", () => speakAssistantReply(text, button)); chat.append(button); }
function actionLabel(action) { if (action.type === "create_task") return `Create “${action.title}”`; if (action.type === "complete_task") return "Mark task complete"; if (action.type === "delete_task") return "Delete task"; return `Update “${action.title || "task"}”`; }
function addActionProposal(actions) { if (!actions?.length) return; const chat = $("#assistantChat"); const proposal = document.createElement("div"); proposal.className = "assistant-proposal"; const title = document.createElement("strong"); title.textContent = "Suggested changes"; proposal.append(title); const list = document.createElement("ul"); for (const action of actions) { const item = document.createElement("li"); item.textContent = actionLabel(action); list.append(item); } proposal.append(list); const confirm = document.createElement("button"); confirm.type = "button"; confirm.textContent = "Confirm changes"; confirm.addEventListener("click", async () => { confirm.disabled = true; $("#assistantError").textContent = "Applying changes…"; try { const result = await api("/api/assistant/actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actions }) }); state.tasks = result.tasks; render(); proposal.remove(); $("#assistantError").textContent = "Changes applied."; } catch (error) { confirm.disabled = false; $("#assistantError").textContent = error.message; } }); proposal.append(confirm); chat.append(proposal); chat.scrollTop = chat.scrollHeight; }
async function update(id, patch) { const { task } = await api(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); state.tasks = state.tasks.map((existing) => existing.id === id ? task : existing); render(); }
async function remove(id) { await api(`/api/tasks/${id}`, { method: "DELETE" }); state.tasks = state.tasks.filter((task) => task.id !== id); render(); }
function openEditor(task) { const form = $("#editForm"); form.dataset.taskId = task.id; $("#editTitle").value = task.title; $("#editProject").value = task.project; $("#editNotes").value = task.notes; $("#editPriority").value = task.priority; $("#editDueDate").value = task.dueDate || ""; $("#editError").textContent = ""; $("#editDialog").showModal(); }
async function createTaskFromForm(event, defaults = {}) { event.preventDefault(); const formElement = event.currentTarget; const button = formElement.querySelector("button[type=submit]"); button.disabled = true; $("#taskError").textContent = ""; try { const form = new FormData(formElement); await api("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...defaults, ...Object.fromEntries(form) }) }); await loadTasks(); formElement.reset(); if (formElement.id === "taskForm") $("#priority").value = "medium"; $(formElement.querySelector("input")?.id === "captureTitle" ? "#captureTitle" : "#title").focus(); } catch (error) { $("#taskError").textContent = error.message; } finally { button.disabled = false; } }
$("#captureForm").addEventListener("submit", (event) => createTaskFromForm(event, { project: "Inbox", priority: "medium" }));
$("#taskForm").addEventListener("submit", (event) => createTaskFromForm(event));
$("#cancelEdit").addEventListener("click", () => $("#editDialog").close());
$("#editForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { await update(event.currentTarget.dataset.taskId, Object.fromEntries(form)); $("#editDialog").close(); } catch (error) { $("#editError").textContent = error.message; } });
$("#showCompleted").addEventListener("click", (event) => { state.showCompleted = !state.showCompleted; event.currentTarget.textContent = state.showCompleted ? "Hide completed" : "Show completed"; render(); });
$("#connectCalendar").addEventListener("click", () => { window.location.href = "/api/calendar/connect"; });
$("#disconnectCalendar").addEventListener("click", async () => { try { await api("/api/calendar/connection", { method: "DELETE" }); await loadCalendar(); } catch (error) { $("#calendarError").textContent = error.message; } });
$("#memoryForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const path = editingMemoryId ? `/api/memory/${editingMemoryId}` : "/api/memory"; try { await api(path, { method: editingMemoryId ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) }); resetMemoryForm(); await loadMemory(); } catch (error) { $("#memoryError").textContent = error.message; } });
$("#cancelMemory").addEventListener("click", resetMemoryForm);
$("#refreshBriefing").addEventListener("click", loadBriefing);
$("#refreshMorning").addEventListener("click", loadMorningBriefing);
$("#refreshWeeklyPlan").addEventListener("click", loadWeeklyPlan);
$("#voiceButton").addEventListener("click", toggleVoiceRecording);
$("#enableNotifications").addEventListener("click", async () => { const button = $("#enableNotifications"); button.disabled = true; try { await Notification.requestPermission(); updateNotificationUI(); notifyDueTasks(state.tasks); } finally { button.disabled = false; } });
$("#today").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());
setInterval(() => notifyDueTasks(state.tasks), 60000);
api("/api/assistant/status").then(({ reachable, model }) => { $("#assistantStatus").textContent = reachable ? `Local assistant ready${model ? ` · ${model}` : ""}` : "Local assistant offline · tasks stay available"; }).catch(() => { $("#assistantStatus").textContent = "Local assistant unavailable"; });
api("/api/assistant/status").then(({ model }) => { assistantModel = model; if (state.user && model) $("#unloadModel").hidden = false; });
function showAuth(setupRequired) { $("#authPanel").hidden = false; $("#manager").hidden = true; $("#authEyebrow").textContent = setupRequired ? "WELCOME TO ZENITH" : "WELCOME BACK"; $("#authTitle").textContent = setupRequired ? "Set up your local account." : "Sign in to Zenith."; $("#authIntro").textContent = setupRequired ? "Choose a passphrase. It stays local and protects access to your tasks." : "Use your local account to continue."; $("#authSubmit").textContent = setupRequired ? "Create account" : "Sign in"; $("#authForm").dataset.mode = setupRequired ? "setup" : "login"; $("#password").autocomplete = setupRequired ? "new-password" : "current-password"; }
$("#authForm").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const mode = event.currentTarget.dataset.mode; $("#authError").textContent = ""; try { const session = await api(mode === "setup" ? "/api/auth/setup" : "/api/auth/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) }); state.user = session.user; $("#authPanel").hidden = true; $("#manager").hidden = false; await loadTasks(); connectLiveUpdates(); await loadCalendar(); await loadMemory(); await loadBriefing(); await loadMorningBriefing(); await loadWeeklyPlan(); await loadVoiceStatus(); await loadNotificationStatus(); } catch (error) { $("#authError").textContent = error.message; } });
$("#logout").addEventListener("click", async () => { closeLiveUpdates(); await api("/api/auth/session", { method: "DELETE" }); state.user = null; state.tasks = []; $("#logout").hidden = true; $("#unloadModel").hidden = true; showAuth(false); $("#password").value = ""; });
$("#unloadModel").addEventListener("click", async () => { const button = $("#unloadModel"); button.disabled = true; try { const result = await api("/api/assistant/unload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }); $("#assistantStatus").textContent = result.unloaded ? "Local assistant released · VRAM available" : "Local assistant is not loaded"; } catch (error) { $("#assistantStatus").textContent = error.message; } finally { button.disabled = false; } });
$("#assistantForm").addEventListener("submit", async (event) => { event.preventDefault(); const input = $("#assistantInput"); const message = input.value.trim(); if (!message) return; const chat = $("#assistantChat"); const userMessage = document.createElement("p"); userMessage.className = "assistant-message user-message"; userMessage.textContent = message; chat.append(userMessage); input.value = ""; $("#assistantError").textContent = "Thinking…"; try { const result = await api("/api/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history: state.assistantHistory }) }); const answer = document.createElement("p"); answer.className = "assistant-message"; answer.textContent = result.message; chat.append(answer); addSpeechControl(chat, result.message); addActionProposal(result.actions); state.assistantHistory.push({ role: "user", content: message }, { role: "assistant", content: result.message }); $("#assistantError").textContent = result.model ? `Using ${result.model}` : ""; chat.scrollTop = chat.scrollHeight; } catch (error) { $("#assistantError").textContent = error.message === "Local assistant is unavailable." ? "Ollama is offline. Your tasks are still available." : error.message; } });
async function start() { const existing = await fetch("/api/auth/session"); if (existing.ok) { const session = await existing.json(); state.user = session.user; $("#manager").hidden = false; await loadTasks(); connectLiveUpdates(); await loadCalendar(); await loadMemory(); await loadBriefing(); await loadMorningBriefing(); await loadWeeklyPlan(); await loadVoiceStatus(); await loadNotificationStatus(); return; } const { setupRequired } = await api("/api/auth/status"); showAuth(setupRequired); }
start().catch((error) => { showAuth(false); $("#authError").textContent = error.message; });
