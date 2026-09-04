export type Priority = "low" | "medium" | "high";

export type Task = {
  id: string;
  title: string;
  notes: string;
  project: string;
  priority: Priority;
  dueDate: string | null;
  completed: boolean;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type User = { id: string; displayName: string };

export type Memory = {
  id: string;
  category: string;
  content: string;
  createdAt: string;
  updatedAt: string;
};

export type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  allDay: boolean;
  location: string | null;
  status: string;
};

export type CalendarStatus = {
  configured: boolean;
  connected: boolean;
  calendarName: string | null;
  connectedAt: string | null;
};

export type VoiceStatus = { configured: boolean; ttsConfigured: boolean };

export type CalendarProjection = {
  connected: boolean;
  available: boolean;
  events: CalendarEvent[];
};

export type Briefing = {
  date: string;
  counts: { open: number; overdue: number; dueToday: number };
  focusTasks: Task[];
};

export type MorningBriefing = {
  date: string;
  summary: string;
  overdue: Task[];
  dueToday: Task[];
  upcoming: Task[];
  calendar: CalendarProjection;
};

export type WeeklyPlan = {
  start: string;
  end: string;
  counts: { open: number; overdue: number; scheduled: number; unscheduled: number };
  days: { date: string; tasks: Task[] }[];
  unscheduled: Task[];
  calendar: CalendarProjection;
};

export type DailySummary = {
  date: string;
  summary: string;
  counts: { completed: number; created: number; open: number };
  completedTasks: Task[];
  createdTasks: Task[];
};

export type AssistantAction = {
  type: "create_task" | "update_task" | "complete_task" | "delete_task";
  taskId?: string;
  title?: string;
  notes?: string;
  project?: string;
  priority?: Priority;
  dueDate?: string | null;
  completed?: boolean;
};

export type AssistantResult = {
  message: string;
  actions: AssistantAction[];
  model: string;
};

export type ApiInit = RequestInit & { json?: unknown };

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const response = await fetch(path, { ...init, headers, body, cache: "no-store" });
  if (!response.ok) {
    let message = "Zenith could not complete that request.";
    try {
      const payload = (await response.json()) as { error?: string };
      if (payload.error) message = payload.error;
    } catch {
      // Preserve the generic message when the server does not return JSON.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function apiBlob(path: string, init: ApiInit = {}): Promise<Blob> {
  const headers = new Headers(init.headers);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const response = await fetch(path, { ...init, headers, body, cache: "no-store" });
  if (!response.ok) {
    let message = "Zenith could not complete that request.";
    try {
      const payload = (await response.json()) as { error?: string };
      if (payload.error) message = payload.error;
    } catch {
      // Preserve the generic message when the server does not return JSON.
    }
    throw new ApiError(message, response.status);
  }
  return response.blob();
}
