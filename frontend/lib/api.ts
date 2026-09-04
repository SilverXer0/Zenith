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
