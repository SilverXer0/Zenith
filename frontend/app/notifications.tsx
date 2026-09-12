"use client";

import { useEffect, useState } from "react";
import type { Task } from "../lib/api";

function localDateKey() {
  const now = new Date();
  return now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
}

async function showDueNotification(task: Task) {
  const title = "Due: " + task.title;
  const options: NotificationOptions = {
    body: task.dueDate === localDateKey() ? "Due today" : "Overdue · " + task.dueDate,
    tag: "zenith-task-" + task.id,
    data: { path: "/" },
  };
  if ("serviceWorker" in navigator) {
    try {
      const registration = await navigator.serviceWorker.ready;
      await registration.showNotification(title, options);
      return true;
    } catch { /* Fall back to a page notification when the service worker cannot display one. */ }
  }
  try {
    new Notification(title, options);
    return true;
  } catch { return false; }
}

let notificationRun: Promise<void> | null = null;

async function runDueTaskNotifications(tasks: Task[]) {
  if (!("Notification" in window) || !window.isSecureContext || Notification.permission !== "granted") return;
  const today = localDateKey();
  const storageKey = "zenith-notified-" + today;
  let notified = new Set<string>();
  try { notified = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); } catch { /* Ignore unavailable browser storage. */ }
  for (const task of tasks.filter((candidate) => !candidate.completed && candidate.dueDate && candidate.dueDate <= today && !notified.has(candidate.id))) {
    if (await showDueNotification(task)) notified.add(task.id);
  }
  try { localStorage.setItem(storageKey, JSON.stringify([...notified])); } catch { /* Ignore unavailable browser storage. */ }
}

function notifyDueTasks(tasks: Task[]) {
  if (notificationRun) return notificationRun;
  notificationRun = runDueTaskNotifications(tasks).finally(() => { notificationRun = null; });
  return notificationRun;
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
    if (permission === "granted") void notifyDueTasks(tasks);
  }, [permission, tasks]);

  useEffect(() => {
    if (permission !== "granted") return;
    const check = () => { void notifyDueTasks(tasks); };
    const interval = window.setInterval(check, 60_000);
    document.addEventListener("visibilitychange", check);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", check);
    };
  }, [permission, tasks]);

  async function enable() {
    if (permission === "unsupported") return;
    try {
      const next = await Notification.requestPermission();
      setPermission(next);
      if (next === "granted") void notifyDueTasks(tasks);
    } catch { setPermission("denied"); }
  }

  if (permission === "unsupported") return <span className="muted text-xs">Reminders need a secure connection</span>;
  if (permission === "granted") return <span className="muted text-xs">Reminders on</span>;
  if (permission === "denied") return <span className="muted text-xs">Reminders blocked in browser settings</span>;
  return <button className="quiet-button" type="button" onClick={() => void enable()}>Enable reminders</button>;
}
