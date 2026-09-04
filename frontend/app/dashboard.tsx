"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, type AssistantAction, type AssistantResult, type Priority, type Task, type User, type VoiceStatus } from "../lib/api";
import PlanningPanels from "./insights";
import { InstallButton } from "./pwa";
import { ReminderControls } from "./notifications";
import { SpeakButton, VoiceInputButton } from "./voice";

type Draft = { title: string; notes: string; project: string; priority: Priority; dueDate: string };
type ChatEntry = { id: string; role: "user" | "assistant"; content: string; actions?: AssistantAction[]; applied?: boolean };

const blankDraft: Draft = { title: "", notes: "", project: "Inbox", priority: "medium", dueDate: "" };

function sortTasks(tasks: Task[]) {
  return [...tasks].sort((a, b) => Number(a.completed) - Number(b.completed)
    || Number(a.dueDate === null) - Number(b.dueDate === null)
    || (a.dueDate ?? "").localeCompare(b.dueDate ?? "")
    || b.updatedAt.localeCompare(a.updatedAt));
}

function dueLabel(task: Task) {
  if (!task.dueDate) return "No due date";
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  if (task.dueDate === today) return "Due today";
  return `Due ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${task.dueDate}T12:00:00`))}`;
}

function localDateKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function displayDate() {
  return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());
}

function actionLabel(action: AssistantAction) {
  if (action.type === "create_task") return `Create “${action.title ?? "task"}”`;
  if (action.type === "complete_task") return "Mark a task complete";
  if (action.type === "delete_task") return "Delete a task";
  return `Update “${action.title ?? "task"}”`;
}

function AuthPanel({ setupRequired, onAuthenticated }: { setupRequired: boolean; onAuthenticated: (user: User) => void }) {
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ user: User }>(setupRequired ? "/api/auth/setup" : "/api/auth/session", {
        method: "POST", json: { displayName, password },
      });
      onAuthenticated(result.user);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto flex min-h-[78vh] max-w-lg items-center">
      <section className="surface w-full p-7 sm:p-10">
        <p className="eyebrow">WELCOME TO ZENITH</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">{setupRequired ? "Set up your local account." : "Sign in to Zenith."}</h1>
        <p className="muted mt-3 leading-7">{setupRequired ? "Choose a passphrase. It stays on your home server and protects your tasks." : "Use your local account to continue."}</p>
        <form className="mt-8 space-y-4" onSubmit={submit}>
          <input className="field" required maxLength={80} autoComplete="username" placeholder="Your name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          <input className="field" required minLength={8} type="password" autoComplete={setupRequired ? "new-password" : "current-password"} placeholder="Passphrase (8+ characters)" value={password} onChange={(event) => setPassword(event.target.value)} />
          <button className="primary-button w-full" disabled={busy}>{busy ? "Working…" : setupRequired ? "Create account" : "Sign in"}</button>
          <p className="error" role="alert">{error}</p>
        </form>
      </section>
    </section>
  );
}

export default function Dashboard() {
  const [user, setUser] = useState<User | null>(null);
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [showCompleted, setShowCompleted] = useState(false);
  const [draft, setDraft] = useState<Draft>(blankDraft);
  const [editing, setEditing] = useState<Task | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(blankDraft);
  const [taskError, setTaskError] = useState("");
  const [live, setLive] = useState(false);
  const [today] = useState(displayDate);
  const [assistantStatus, setAssistantStatus] = useState("Checking local assistant…");
  const [assistantModel, setAssistantModel] = useState<string | null>(null);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantHistory, setAssistantHistory] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [chat, setChat] = useState<ChatEntry[]>([]);
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantError, setAssistantError] = useState("");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [authError, setAuthError] = useState("");
  const loadVersion = useRef(0);

  useEffect(() => {
    api<{ reachable: boolean; model: string | null }>("/api/assistant/status")
      .then(({ reachable, model }) => {
        setAssistantModel(model);
        setAssistantStatus(reachable ? `Local assistant ready${model ? ` · ${model}` : ""}` : "Local assistant offline · tasks stay available");
      })
      .catch(() => setAssistantStatus("Local assistant unavailable"));
    api<{ user: User }>("/api/auth/session")
      .then(({ user: existing }) => setUser(existing))
      .catch(() => api<{ setupRequired: boolean }>("/api/auth/status").then(({ setupRequired: required }) => setSetupRequired(required)).catch(() => setAuthError("Zenith could not be reached.")));
  }, []);

  useEffect(() => {
    if (!user?.id) {
      const reset = window.setTimeout(() => setVoiceStatus(null), 0);
      return () => window.clearTimeout(reset);
    }
    api<VoiceStatus>("/api/voice/status").then(setVoiceStatus).catch(() => setVoiceStatus({ configured: false, ttsConfigured: false }));
  }, [user?.id]);

  const userId = user?.id;
  const fetchTasks = useCallback(async () => {
    if (!userId) return;
    const version = ++loadVersion.current;
    try {
      const result = await api<{ tasks: Task[] }>("/api/tasks");
      if (version === loadVersion.current) setTasks(sortTasks(result.tasks));
    } catch (caught) {
      if (version === loadVersion.current) setTaskError(caught instanceof Error ? caught.message : "Tasks could not be loaded.");
    }
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    const initialLoad = window.setTimeout(() => { void fetchTasks(); }, 0);
    const source = new EventSource("/api/events");
    source.onopen = () => setLive(true);
    source.addEventListener("tasks_changed", () => { void fetchTasks(); });
    source.onerror = () => setLive(false);
    return () => { window.clearTimeout(initialLoad); source.close(); setLive(false); };
  }, [userId, fetchTasks]);

  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTaskError("");
    try {
      const result = await api<{ task: Task }>("/api/tasks", { method: "POST", json: { ...draft, dueDate: draft.dueDate || null } });
      setTasks((current) => sortTasks([...current, result.task]));
      setDraft(blankDraft);
    } catch (caught) {
      setTaskError(caught instanceof Error ? caught.message : "Task could not be saved.");
    }
  }

  async function toggleTask(task: Task) {
    try {
      const result = await api<{ task: Task }>(`/api/tasks/${task.id}`, { method: "PATCH", json: { completed: !task.completed } });
      setTasks((current) => sortTasks(current.map((candidate) => candidate.id === task.id ? result.task : candidate)));
    } catch (caught) { setTaskError(caught instanceof Error ? caught.message : "Task could not be updated."); }
  }

  function beginEdit(task: Task) {
    setEditing(task);
    setEditDraft({ title: task.title, notes: task.notes, project: task.project, priority: task.priority, dueDate: task.dueDate ?? "" });
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    try {
      const result = await api<{ task: Task }>(`/api/tasks/${editing.id}`, { method: "PATCH", json: { ...editDraft, dueDate: editDraft.dueDate || null } });
      setTasks((current) => sortTasks(current.map((candidate) => candidate.id === editing.id ? result.task : candidate)));
      setEditing(null);
    } catch (caught) { setTaskError(caught instanceof Error ? caught.message : "Task could not be updated."); }
  }

  async function deleteTask(task: Task) {
    if (!window.confirm(`Delete “${task.title}”?`)) return;
    try {
      await api(`/api/tasks/${task.id}`, { method: "DELETE" });
      setTasks((current) => current.filter((candidate) => candidate.id !== task.id));
    } catch (caught) { setTaskError(caught instanceof Error ? caught.message : "Task could not be deleted."); }
  }

  async function askAssistant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = assistantInput.trim();
    if (!message || assistantBusy) return;
    setAssistantInput("");
    setAssistantError("");
    setAssistantBusy(true);
    const userEntry: ChatEntry = { id: crypto.randomUUID(), role: "user", content: message };
    setChat((current) => [...current, userEntry]);
    try {
      const result = await api<AssistantResult>("/api/assistant/chat", { method: "POST", json: { message, history: assistantHistory } });
      setAssistantModel(result.model);
      setAssistantHistory((current) => [...current, { role: "user" as const, content: message }, { role: "assistant" as const, content: result.message }].slice(-8));
      setChat((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: result.message, actions: result.actions }]);
      setAssistantStatus(`Using ${result.model}`);
    } catch (caught) {
      setAssistantError(caught instanceof Error ? caught.message : "The local assistant is unavailable.");
    } finally { setAssistantBusy(false); }
  }

  async function confirmActions(entry: ChatEntry) {
    if (!entry.actions?.length) return;
    setAssistantError("");
    try {
      const result = await api<{ tasks: Task[] }>("/api/assistant/actions", { method: "POST", json: { actions: entry.actions } });
      setTasks(sortTasks(result.tasks));
      setChat((current) => current.map((candidate) => candidate.id === entry.id ? { ...candidate, applied: true } : candidate));
    } catch (caught) { setAssistantError(caught instanceof Error ? caught.message : "Those changes could not be applied."); }
  }

  async function unloadModel() {
    try {
      const result = await api<{ unloaded: boolean }>("/api/assistant/unload", { method: "POST", json: {} });
      setAssistantStatus(result.unloaded ? "Local assistant released · VRAM available" : "Local assistant is not loaded");
    } catch (caught) { setAssistantError(caught instanceof Error ? caught.message : "The local assistant could not be released."); }
  }

  async function signOut() {
    await api("/api/auth/session", { method: "DELETE" }).catch(() => undefined);
    setUser(null);
    setSetupRequired(false);
    setTasks([]);
    setChat([]);
    setAssistantHistory([]);
  }

  const visibleTasks = useMemo(() => tasks.filter((task) => showCompleted || !task.completed), [tasks, showCompleted]);
  const openCount = tasks.filter((task) => !task.completed).length;
  const completedCount = tasks.length - openCount;
  const localToday = localDateKey();
  const taskRevision = tasks.map((task) => `${task.id}:${task.updatedAt}:${task.completed}`).join("|");

  if (setupRequired === null && !user) return <main className="shell"><p className="muted">{authError || "Opening Zenith…"}</p></main>;
  if (!user) return <main className="shell"><AuthPanel setupRequired={setupRequired ?? false} onAuthenticated={setUser} /></main>;

  return (
    <main className="shell">
      <header className="mb-7 flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div><p className="eyebrow">ZENITH / PERSONAL MANAGER</p><h1 className="mt-2 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">Your day, in focus.</h1><p className="muted mt-2">{today}</p></div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="muted rounded-full border border-[var(--line)] bg-white/40 px-3 py-2">{live ? "● Live sync connected" : "○ Live sync reconnecting"}</span>
          <ReminderControls tasks={tasks} />
          <InstallButton />
          <button className="quiet-button" onClick={unloadModel} hidden={!assistantModel}>Release model</button>
          <button className="quiet-button" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <section className="mb-6 grid grid-cols-3 gap-2 sm:gap-3" aria-label="Task overview">
        <div className="surface p-4 sm:p-5"><span className="block text-2xl font-semibold sm:text-3xl">{openCount}</span><span className="muted text-xs sm:text-sm">open tasks</span></div>
        <div className="surface p-4 sm:p-5"><span className="block text-2xl font-semibold sm:text-3xl">{tasks.filter((task) => task.dueDate === localToday && !task.completed).length}</span><span className="muted text-xs sm:text-sm">due today</span></div>
        <div className="surface p-4 sm:p-5"><span className="block text-2xl font-semibold sm:text-3xl">{completedCount}</span><span className="muted text-xs sm:text-sm">completed</span></div>
      </section>

      <section className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(330px,.65fr)]">
        <section className="surface p-5 sm:p-7" aria-labelledby="tasks-title">
          <div className="mb-5 flex items-center justify-between gap-3"><h2 id="tasks-title" className="text-2xl font-semibold">Tasks</h2><button className="quiet-button" onClick={() => setShowCompleted((value) => !value)}>{showCompleted ? "Hide completed" : "Show completed"}</button></div>
          <form className="mb-4 grid gap-2 sm:grid-cols-[1fr_auto]" onSubmit={createTask}><input className="field" required maxLength={160} placeholder="Capture something…" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /><button className="primary-button" type="submit">Capture to Inbox</button></form>
          <details className="mb-5 rounded-xl border border-[var(--line)] bg-white/35 p-3"><summary className="cursor-pointer text-sm font-bold">Add project, priority, date, or notes</summary><div className="mt-3 grid gap-2 sm:grid-cols-2"><input className="field" placeholder="Project" value={draft.project} onChange={(event) => setDraft({ ...draft, project: event.target.value })} /><select className="field" value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value as Priority })}><option value="medium">Medium priority</option><option value="high">High priority</option><option value="low">Low priority</option></select><input className="field" type="date" value={draft.dueDate} onChange={(event) => setDraft({ ...draft, dueDate: event.target.value })} /><input className="field" placeholder="Note (optional)" value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></div></details>
          <p className="error" role="alert">{taskError}</p>
          {visibleTasks.length === 0 ? <p className="muted rounded-xl bg-[var(--sage)]/45 p-5 text-sm">{showCompleted ? "Your list is clear. Add the one thing that matters next." : tasks.length ? "No open tasks. Enjoy the clear space." : "Your list is clear. Add the one thing that matters next."}</p> : <ul className="divide-y divide-[var(--line)]">{visibleTasks.map((task) => <li className="flex items-start gap-3 py-4 first:pt-1" key={task.id}><input className="mt-1.5 size-5 accent-[var(--accent)]" type="checkbox" checked={task.completed} aria-label={`Complete ${task.title}`} onChange={() => void toggleTask(task)} /><div className="min-w-0 flex-1"><p className={`font-semibold ${task.completed ? "text-[var(--muted)] line-through" : ""}`}>{task.title}</p><p className="muted mt-1 text-xs">{task.project} · {dueLabel(task)} · {task.priority} priority{task.notes ? ` · ${task.notes}` : ""}</p></div><button className="quiet-button" onClick={() => beginEdit(task)}>Edit</button><button className="danger-button" onClick={() => void deleteTask(task)} aria-label={`Delete ${task.title}`}>Delete</button></li>)}</ul>}
        </section>

        <aside className="surface flex min-h-[430px] flex-col p-5 sm:p-7" aria-labelledby="assistant-title">
          <p className="eyebrow">LOCAL ASSISTANT</p><div className="mt-2 flex items-start justify-between gap-3"><div><h2 id="assistant-title" className="text-2xl font-semibold">Think it through.</h2><p className="muted mt-1 text-sm">{assistantStatus}</p></div></div>
          <div className="my-5 flex-1 space-y-3 overflow-y-auto" aria-live="polite">{chat.length === 0 && <p className="muted rounded-xl bg-[var(--sage)]/45 p-4 text-sm">Ask about your tasks. Ollama stays on your machine and is optional.</p>}{chat.map((entry) => <div key={entry.id} className={entry.role === "user" ? "ml-8 rounded-xl bg-[var(--ink)] p-3 text-sm text-white" : "rounded-xl border border-[var(--line)] bg-white/55 p-3 text-sm"}><p>{entry.content}</p>{entry.role === "assistant" && <SpeakButton enabled={voiceStatus?.ttsConfigured === true} text={entry.content} onError={setAssistantError} />}{entry.actions && entry.actions.length > 0 && <div className="mt-3 border-t border-[var(--line)] pt-3"><p className="font-bold">Suggested changes</p><ul className="mt-2 space-y-1 text-xs">{entry.actions.map((action, index) => <li key={`${entry.id}-${index}`}>{actionLabel(action)}</li>)}</ul>{entry.applied ? <p className="mt-3 text-xs font-bold text-[var(--accent-dark)]">Changes applied.</p> : <button className="primary-button mt-3 w-full text-sm" onClick={() => void confirmActions(entry)}>Confirm changes</button>}</div>}</div>)}</div>
          <form className="flex flex-col gap-2" onSubmit={askAssistant}><div className="flex gap-2"><input className="field min-w-0" maxLength={4000} placeholder="What should I focus on?" value={assistantInput} onChange={(event) => setAssistantInput(event.target.value)} disabled={assistantBusy} /><button className="primary-button" disabled={assistantBusy}>{assistantBusy ? "…" : "Ask"}</button></div><div className="flex min-h-8 items-center justify-between gap-2"><VoiceInputButton enabled={voiceStatus?.configured === true} onTranscript={(text) => { setAssistantInput(text); setAssistantError(""); }} onError={setAssistantError} />{voiceStatus && !voiceStatus.ttsConfigured && <span className="muted text-xs">Spoken replies not configured</span>}</div></form><p className="error mt-2" role="alert">{assistantError}</p><p className="muted mt-3 text-xs">Suggestions require confirmation. Zenith will not apply task changes on its own.</p>
        </aside>
      </section>

      <PlanningPanels taskRevision={taskRevision} />

      {editing && <div className="fixed inset-0 z-10 flex items-end justify-center bg-black/25 p-3 sm:items-center"><form className="surface w-full max-w-lg p-6" onSubmit={saveEdit}><div className="mb-5 flex items-center justify-between"><h2 className="text-xl font-semibold">Edit task</h2><button className="quiet-button" type="button" onClick={() => setEditing(null)}>Cancel</button></div><div className="grid gap-3"><input className="field" required maxLength={160} value={editDraft.title} onChange={(event) => setEditDraft({ ...editDraft, title: event.target.value })} /><div className="grid gap-3 sm:grid-cols-2"><input className="field" value={editDraft.project} placeholder="Project" onChange={(event) => setEditDraft({ ...editDraft, project: event.target.value })} /><select className="field" value={editDraft.priority} onChange={(event) => setEditDraft({ ...editDraft, priority: event.target.value as Priority })}><option value="medium">Medium priority</option><option value="high">High priority</option><option value="low">Low priority</option></select></div><input className="field" type="date" value={editDraft.dueDate} onChange={(event) => setEditDraft({ ...editDraft, dueDate: event.target.value })} /><textarea className="field min-h-24" maxLength={2000} value={editDraft.notes} placeholder="Notes" onChange={(event) => setEditDraft({ ...editDraft, notes: event.target.value })} /><button className="primary-button" type="submit">Save changes</button></div></form></div>}
    </main>
  );
}
