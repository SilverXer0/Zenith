"use client";

import { useEffect, useState } from "react";
import type { Task } from "../lib/api";

function localDateKey() {
  const now = new Date();
  return now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
}

function notifyDueTasks(tasks: Task[]) {
  if (!("Notification" in window) || !window.isSecureContext || Notification.permission !== "granted") return;
  const today = localDateKey();
  const storageKey = "zenith-notified-" + today;
  let notified = new Set<string>();
  try { notified = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); } catch { /* Ignore unavailable browser storage. */ }
  for (const task of tasks.filter((candidate) => !candidate.completed && candidate.dueDate && candidate.dueDate <= today && !notified.has(candidate.id))) {
    try {
      new Notification("Due: " + task.title, { body: task.dueDate === today ? "Due today" : "Overdue · " + task.dueDate, tag: "zenith-task-" + task.id });
      notified.add(task.id);
    } catch { /* Ignore a notification that the browser declines. */ }
  }
  try { localStorage.setItem(storageKey, JSON.stringify([...notified])); } catch { /* Ignore unavailable browser storage. */ }
}

export function ReminderControls({ tasks }: { tasks: Task[] }) {
  const [permission, setPermission] = useState<string>("unsupported");

  useEffect(() => {
    const check = window.setTimeout(() => {
      const supported = "Notification" in window && window.isSecureContext;
      setPermission(supported ? Notification.permission : "unsupported");
    }, 0);
    return () => window.clearTimeout(check);
  }, []);

  useEffect(() => {
    if (permission === "granted") notifyDueTasks(tasks);
  }, [permission, tasks]);

  async function enable() {
    if (permission === "unsupported") return;
    try {
      const next = await Notification.requestPermission();
      setPermission(next);
      if (next === "granted") notifyDueTasks(tasks);
    } catch { setPermission("denied"); }
  }

  if (permission === "unsupported") return <span className="muted text-xs">Reminders need a secure connection</span>;
  if (permission === "granted") return <span className="muted text-xs">Reminders on</span>;
  if (permission === "denied") return <span className="muted text-xs">Reminders blocked in browser settings</span>;
  return <button className="quiet-button" type="button" onClick={() => void enable()}>Enable reminders</button>;
}
