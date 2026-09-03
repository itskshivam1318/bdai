// Base URL is injected per worktree so the browser bundle talks to the API of
// the stack it was served from, not whatever happens to own port 8000.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export const WORKTREE = process.env.NEXT_PUBLIC_WORKTREE ?? "main";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${path} → ${res.status}`);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export type CanvasNodeRow = {
  id: number;
  widget_type: string;
  x: number;
  y: number;
  width: number | null;
  height: number | null;
  config: string;
};

export type Run = {
  id: number;
  target_url: string;
  status: string;
  summary: string | null;
  started_at: string;
};

export type AgentEvent = {
  id: number;
  run_id: number | null;
  level: string;
  message: string;
  created_at: string;
};

export const api = {
  health: () => request<{ status: string; worktree: string }>("/health"),

  listNodes: () => request<CanvasNodeRow[]>("/api/canvas/nodes"),
  createNode: (node: Partial<CanvasNodeRow>) =>
    request<CanvasNodeRow>("/api/canvas/nodes", {
      method: "POST",
      body: JSON.stringify(node),
    }),
  updateNode: (id: number, patch: Partial<CanvasNodeRow>) =>
    request<CanvasNodeRow>(`/api/canvas/nodes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteNode: (id: number) =>
    request<void>(`/api/canvas/nodes/${id}`, { method: "DELETE" }),

  listRuns: () => request<Run[]>("/api/runs"),
  createRun: (target_url: string) =>
    request<Run>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ target_url }),
    }),
  listEvents: (runId: number) =>
    request<AgentEvent[]>(`/api/runs/${runId}/events`),
};
