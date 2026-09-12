"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  api,
  type Briefing,
  type CalendarAvailability,
  type CalendarConnection,
  type CalendarEvent,
  type CalendarStatus,
  type DailySummary,
  type Memory,
  type MorningBriefing,
  type Task,
  type WeeklyPlan,
} from "../lib/api";

type InsightsProps = { taskRevision: string };

function localDateKey() {
  const now = new Date();
  return now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
}

function addDays(day: string, count: number) {
  const next = new Date(day + "T12:00:00");
  next.setDate(next.getDate() + count);
  return next.getFullYear() + "-" + String(next.getMonth() + 1).padStart(2, "0") + "-" + String(next.getDate()).padStart(2, "0");
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date(value + "T12:00:00"));
}

function eventTime(event: CalendarEvent) {
  if (event.allDay) return shortDate(event.start);
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(event.start));
}

function localTimeRange(start: string, end: string) {
  const format = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  return format.format(new Date(start)) + "–" + format.format(new Date(end));
}

function TaskLine({ task }: { task: Task }) {
  return <li className="border-t border-[var(--line)] py-2 first:border-t-0"><span className="font-semibold">{task.title}</span>{task.dueDate && <span className="muted ml-2 text-xs">{task.dueDate}</span>}</li>;
}

function CalendarList({ events }: { events: CalendarEvent[] }) {
  if (!events.length) return <p className="muted text-sm">No upcoming events in this window.</p>;
  return <ul className="space-y-2">{events.slice(0, 6).map((event) => <li className="border-t border-[var(--line)] pt-2 first:border-t-0 first:pt-0" key={(event.calendarId || "calendar") + ":" + event.id}><p className="font-semibold">{event.title}</p><p className="muted text-xs">{eventTime(event)}{event.calendarName ? " · " + event.calendarName : ""}{event.location ? " · " + event.location : ""}</p></li>)}</ul>;
}

function PlanningPanels({ taskRevision }: InsightsProps) {
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [morning, setMorning] = useState<MorningBriefing | null>(null);
  const [week, setWeek] = useState<WeeklyPlan | null>(null);
  const [summary, setSummary] = useState<DailySummary | null>(null);
  const [calendarStatus, setCalendarStatus] = useState<CalendarStatus | null>(null);
  const [calendarConnections, setCalendarConnections] = useState<CalendarConnection[]>([]);
  const [availability, setAvailability] = useState<CalendarAvailability | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState("");
  const [calendarBusy, setCalendarBusy] = useState(false);
  const today = localDateKey();

  const refresh = useCallback(async () => {
    void taskRevision;
    setLoading(true);
    setRefreshError("");
    try {
      const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const [nextBriefing, nextMorning, nextWeek, nextSummary, nextCalendar, nextConnections, nextAvailability] = await Promise.all([
        api<Briefing>("/api/briefing?date=" + today),
        api<MorningBriefing>("/api/briefing/morning?date=" + today),
        api<WeeklyPlan>("/api/weekly-plan?start=" + today),
        api<DailySummary>("/api/summaries/daily?date=" + today + "&offset=" + new Date().getTimezoneOffset()),
        api<CalendarStatus>("/api/calendar/status"),
        api<{ connections: CalendarConnection[] }>("/api/calendar/connections"),
        api<CalendarAvailability>("/api/planning/availability?date=" + today + "&timezone=" + encodeURIComponent(browserTimezone)),
      ]);
      setBriefing(nextBriefing);
      setMorning(nextMorning);
      setWeek(nextWeek);
      setSummary(nextSummary);
      setCalendarStatus(nextCalendar);
      setCalendarConnections(nextConnections.connections);
      setAvailability(nextAvailability);
    } catch (caught) {
      setRefreshError(caught instanceof Error ? caught.message : "Planning could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [today, taskRevision]);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(initialLoad);
  }, [refresh]);

  async function updateCalendarConnection(connection: CalendarConnection, patch: { displayName?: string; enabled?: boolean }) {
    setCalendarBusy(true);
    try {
      await api("/api/calendar/connections/" + connection.id, { method: "PATCH", json: patch });
      await refresh();
    } catch (caught) {
      setRefreshError(caught instanceof Error ? caught.message : "Calendar connection could not be updated.");
    } finally { setCalendarBusy(false); }
  }

  async function renameCalendar(connection: CalendarConnection) {
    const name = window.prompt("Name this calendar connection", connection.displayName);
    if (name === null || !name.trim()) return;
    await updateCalendarConnection(connection, { displayName: name.trim() });
  }

  async function disconnectCalendar(connection: CalendarConnection) {
    if (!window.confirm("Disconnect " + connection.displayName + "?")) return;
    setCalendarBusy(true);
    try {
      await api("/api/calendar/connections/" + connection.id, { method: "DELETE" });
      await refresh();
    } catch (caught) {
      setRefreshError(caught instanceof Error ? caught.message : "Calendar could not be disconnected.");
    } finally { setCalendarBusy(false); }
  }

  return <section className="mt-6 grid gap-6 lg:grid-cols-3" aria-label="Planning and context">
    <section className="surface p-5 sm:p-7 lg:col-span-2" aria-labelledby="focus-title">
      <div className="mb-4 flex items-start justify-between gap-3"><div><p className="eyebrow">LOCAL PLANNING</p><h2 id="focus-title" className="mt-2 text-2xl font-semibold">Today’s focus</h2></div><button className="quiet-button" onClick={() => void refresh()} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button></div>
      {refreshError && <p className="error mb-3" role="alert">{refreshError}</p>}
      {loading && !briefing ? <p className="muted text-sm">Looking over your day…</p> : briefing && <><p className="muted text-sm">{morning?.summary}</p><div className="mt-4 grid grid-cols-3 gap-2"><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{briefing.counts.overdue}</strong><span className="muted text-xs">overdue</span></div><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{briefing.counts.dueToday}</strong><span className="muted text-xs">due today</span></div><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{briefing.counts.open}</strong><span className="muted text-xs">open</span></div></div><h3 className="mt-5 text-sm font-bold">Recommended next</h3>{briefing.focusTasks.length ? <ul className="mt-2">{briefing.focusTasks.map((task) => <TaskLine task={task} key={task.id} />)}</ul> : <p className="muted mt-2 text-sm">Nothing urgent is waiting. You have room to choose what matters next.</p>}</>}
    </section>

    <section className="surface p-5 sm:p-7" aria-labelledby="calendar-title">
      <p className="eyebrow">YOUR SCHEDULE</p><h2 id="calendar-title" className="mt-2 text-2xl font-semibold">Calendar</h2>
      {!calendarStatus ? <p className="muted mt-4 text-sm">Checking Calendar…</p> : !calendarStatus.configured ? <p className="muted mt-4 text-sm">Google Calendar is not configured on this home server yet. Your task planning remains available.</p> : <><p className="muted mt-2 text-sm">{calendarConnections.length ? "Choose which calendars Zenith should include." : "Connect a read-only Google Calendar to see your schedule beside your tasks."}</p>{calendarConnections.length > 0 && <ul className="mt-4 space-y-3">{calendarConnections.map((connection) => <li className="rounded-xl border border-[var(--line)] bg-white/45 p-3" key={connection.id}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate font-semibold">{connection.displayName}</p><p className="muted truncate text-xs">{connection.calendarName || "Google Calendar"}{!connection.enabled && " · paused"}</p></div><span className="muted text-xs">{connection.enabled ? "Included" : "Paused"}</span></div><div className="mt-3 flex flex-wrap gap-2"><button className="quiet-button" onClick={() => void updateCalendarConnection(connection, { enabled: !connection.enabled })} disabled={calendarBusy}>{connection.enabled ? "Pause" : "Include"}</button><button className="quiet-button" onClick={() => void renameCalendar(connection)} disabled={calendarBusy}>Rename</button><button className="danger-button" onClick={() => void disconnectCalendar(connection)} disabled={calendarBusy}>Disconnect</button></div></li>)}</ul>}<div className="mt-4 flex flex-wrap gap-2"><a className="primary-button inline-block" href="/api/calendar/connect">{calendarConnections.length ? "Connect another calendar" : "Connect Calendar"}</a>{calendarConnections.length > 0 && <span className="muted self-center text-xs">Events from included calendars appear together below.</span>}</div>{calendarConnections.length > 0 && <div className="mt-5">{morning?.calendar.available ? <CalendarList events={morning.calendar.events} /> : <p className="muted text-sm">Included calendars are temporarily unavailable.</p>}</div>}{availability?.available && <div className="mt-5 border-t border-[var(--line)] pt-4"><p className="text-sm font-bold">Open time today</p>{availability.conflicts.length > 0 && <p className="mt-2 text-xs text-[var(--accent-dark)]">{availability.conflicts.length} overlapping block{availability.conflicts.length === 1 ? "" : "s"} detected.</p>}{availability.freeWindows.length ? <ul className="mt-2 space-y-1">{availability.freeWindows.slice(0, 4).map((window) => <li className="muted text-xs" key={window.start}>Open {localTimeRange(window.start, window.end)} · {window.durationMinutes} min</li>)}</ul> : <p className="muted mt-2 text-xs">No open workday windows remain.</p>}{availability.recommendations.length > 0 && <><p className="mt-4 text-sm font-bold">Good fits for today</p><ul className="mt-2 space-y-1">{availability.recommendations.map((recommendation) => <li className="muted text-xs" key={recommendation.task.id}><span className="font-semibold text-[var(--ink)]">{recommendation.task.title}</span> · {recommendation.estimatedMinutes} min in {localTimeRange(recommendation.window.start, recommendation.window.end)}{recommendation.usesDefaultEstimate && " · default estimate"}</li>)}</ul></>}</div>}</>}
    </section>

    <section className="surface p-5 sm:p-7" aria-labelledby="week-title">
      <p className="eyebrow">LOOK AHEAD</p><h2 id="week-title" className="mt-2 text-2xl font-semibold">Shape the week</h2>
      {week ? <><p className="muted mt-2 text-sm">{shortDate(week.start)} – {shortDate(addDays(week.start, 6))}</p><div className="mt-4 grid grid-cols-2 gap-2 text-sm"><span className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{week.counts.scheduled}</strong><span className="muted text-xs">scheduled tasks</span></span><span className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{week.counts.unscheduled}</strong><span className="muted text-xs">in the inbox</span></span></div><ul className="mt-4 space-y-2">{week.days.filter((day) => day.tasks.length).slice(0, 4).map((day) => <li key={day.date}><p className="text-xs font-bold uppercase tracking-wide text-[var(--accent-dark)]">{shortDate(day.date)}</p><ul className="mt-1">{day.tasks.slice(0, 2).map((task) => <TaskLine task={task} key={task.id} />)}</ul></li>)}</ul>{week.unscheduled.length > 0 && <p className="muted mt-4 text-xs">{week.unscheduled.length} task{week.unscheduled.length === 1 ? "" : "s"} still need a day.</p>}</> : <p className="muted mt-4 text-sm">Loading the week…</p>}
    </section>

    <section className="surface p-5 sm:p-7" aria-labelledby="done-title">
      <p className="eyebrow">DAILY RHYTHM</p><h2 id="done-title" className="mt-2 text-2xl font-semibold">What got done</h2>
      {summary ? <><p className="muted mt-2 text-sm">{summary.summary}</p><div className="mt-4 grid grid-cols-3 gap-2 text-center"><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{summary.counts.completed}</strong><span className="muted text-xs">completed</span></div><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{summary.counts.created}</strong><span className="muted text-xs">captured</span></div><div className="rounded-xl bg-[var(--sage)]/55 p-3"><strong className="block text-xl">{summary.counts.open}</strong><span className="muted text-xs">open now</span></div></div>{summary.completedTasks.length > 0 && <ul className="mt-4">{summary.completedTasks.slice(0, 4).map((task) => <TaskLine task={task} key={task.id} />)}</ul>}</> : <p className="muted mt-4 text-sm">Loading today’s summary…</p>}
    </section>

    <MemoryPanel />
  </section>;
}

function MemoryPanel() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("general");
  const [editing, setEditing] = useState<Memory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await api<{ memories: Memory[] }>("/api/memory");
      setMemories(result.memories);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Context could not be loaded.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(initialLoad);
  }, [load]);

  function beginEdit(memory: Memory) {
    setEditing(memory);
    setContent(memory.content);
    setCategory(memory.category);
    setError("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!content.trim()) return;
    setError("");
    try {
      const path = editing ? "/api/memory/" + editing.id : "/api/memory";
      const result = await api<{ memory: Memory }>(path, { method: editing ? "PATCH" : "POST", json: { content, category } });
      setMemories((current) => editing ? current.map((memory) => memory.id === editing.id ? result.memory : memory) : [result.memory, ...current]);
      setContent("");
      setCategory("general");
      setEditing(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Context could not be saved."); }
  }

  async function remove(memory: Memory) {
    if (!window.confirm("Remove this context note?")) return;
    try {
      await api("/api/memory/" + memory.id, { method: "DELETE" });
      setMemories((current) => current.filter((candidate) => candidate.id !== memory.id));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Context could not be removed."); }
  }

  return <section className="surface p-5 sm:p-7 lg:col-span-3" aria-labelledby="memory-title"><div className="grid gap-6 lg:grid-cols-[minmax(0,.8fr)_minmax(0,1.2fr)]"><div><p className="eyebrow">PERSISTENT CONTEXT</p><h2 id="memory-title" className="mt-2 text-2xl font-semibold">Help Zenith remember.</h2><p className="muted mt-2 text-sm leading-6">Store useful preferences, routines, or project context. These notes stay local and are shown to the assistant as reference, not instructions.</p><form className="mt-5 space-y-2" onSubmit={save}><input className="field" maxLength={40} placeholder="Category, e.g. routines" value={category} onChange={(event) => setCategory(event.target.value)} /><textarea className="field min-h-24" maxLength={2000} required placeholder="What should Zenith remember?" value={content} onChange={(event) => setContent(event.target.value)} /><div className="flex gap-2"><button className="primary-button" type="submit">{editing ? "Save note" : "Remember this"}</button>{editing && <button className="quiet-button" type="button" onClick={() => { setEditing(null); setContent(""); setCategory("general"); }}>Cancel</button>}</div></form><p className="error mt-2" role="alert">{error}</p></div><div>{loading ? <p className="muted text-sm">Loading context…</p> : memories.length ? <ul className="grid gap-3 sm:grid-cols-2">{memories.map((memory) => <li className="rounded-xl border border-[var(--line)] bg-white/45 p-4" key={memory.id}><p className="text-xs font-bold uppercase tracking-wide text-[var(--accent-dark)]">{memory.category}</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{memory.content}</p><div className="mt-3 flex gap-3"><button className="quiet-button" onClick={() => beginEdit(memory)}>Edit</button><button className="danger-button" onClick={() => void remove(memory)}>Remove</button></div></li>)}</ul> : <p className="muted rounded-xl bg-[var(--sage)]/45 p-5 text-sm">No saved context yet. Add something Zenith should know about your routines or projects.</p>}</div></div></section>;
}

export default PlanningPanels;
