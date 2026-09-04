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

export type TestSession = {
  id: number;
  target_url: string;
  name: string | null;
  created_at: string;
};

/** A session as the sidebar shows it: the row plus its run rollup. */
export type SessionSummary = TestSession & {
  run_count: number;
  last_status: string | null;
};

export type CanvasNodeRow = {
  id: number;
  session_id: number | null;
  widget_type: string;
  x: number;
  y: number;
  width: number | null;
  height: number | null;
  config: string;
};

export type Run = {
  id: number;
  session_id: number | null;
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
  /** Set when the agent wants this surfaced on the canvas. See widgets/surfaces.ts. */
  surface: string | null;
  ref: string | null;
  created_at: string;
};

/** The four things the Runner can conclude. See api/agents/runner.py. */
export type Verdict = "passed" | "healed" | "defect" | "escalate";

export type MapState = {
  key: string;
  url: string;
  title: string;
  label: string | null;
  is_entry: boolean;
  actions: string[];
  /** [role, name] pairs — the fillable fields of this screen. */
  fields: [string, string][];
  /** Path under /artifacts, or null when capture was off or the shot failed. */
  screenshot: string | null;
  /** Worst verdict among scenarios crossing this state; null if untested. */
  verdict: Verdict | null;
  /**
   * The ant that first reached this state, e.g. "w2a1" — wave 2, ant 1.
   *
   * Null is meaningful, not missing: the entry state is recorded before the
   * first wave is dispatched, and a deterministic crawl has no ants at all.
   */
  found_by: string | null;
};

export type MapTransition = {
  from_key: string;
  action: string;
  to_key: string;
  /** A non-GET fired during this action. The signal the Runner classifies on. */
  mutating: boolean;
  observation_id: number | null;
  /** The ant that took this action. Null for a run with no colony. */
  found_by: string | null;
};

export type WorldMapPayload = {
  run_id: number;
  entry_key: string | null;
  states: MapState[];
  transitions: MapTransition[];
};

export type TestCaseRow = {
  id: number;
  run_id: number | null;
  name: string;
  selector: string | null;
  healed_selector: string | null;
  status: string;
  detail: string | null;
  /** JSON list of the state keys this scenario crosses. */
  path: string;
  created_at: string;
};

/** Artifacts are served by the API, not by Next. */
export const artifactUrl = (path: string) => `${API_BASE}/artifacts/${path}`;

export const api = {
  health: () => request<{ status: string; worktree: string }>("/health"),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),
  createSession: (target_url: string) =>
    request<TestSession>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ target_url }),
    }),
  getSession: (id: number) => request<TestSession>(`/api/sessions/${id}`),
  renameSession: (id: number, name: string) =>
    request<TestSession>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteSession: (id: number) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  listSessionRuns: (id: number) => request<Run[]>(`/api/sessions/${id}/runs`),
  /** Tail of the session's event stream. `after` is the highest id already seen. */
  listSessionEvents: (id: number, after = 0) =>
    request<AgentEvent[]>(`/api/sessions/${id}/events?after=${after}`),

  listNodes: (sessionId: number) =>
    request<CanvasNodeRow[]>(`/api/canvas/nodes?session_id=${sessionId}`),
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

  listRuns: (sessionId?: number) =>
    request<Run[]>(
      sessionId === undefined ? "/api/runs" : `/api/runs?session_id=${sessionId}`,
    ),
  createRun: (target_url: string, session_id?: number) =>
    request<Run>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ target_url, session_id }),
    }),
  /**
   * Start the agent colony on an existing run. Returns as soon as it has
   * started, not when it finishes — progress arrives as events on the run's
   * timeline, which the canvas is already polling.
   */
  explore: (runId: number, intent?: string) =>
    request<{ run_id: number; status: string }>(`/api/runs/${runId}/explore`, {
      method: "POST",
      body: JSON.stringify({ intent: intent || null }),
    }),
  listEvents: (runId: number) =>
    request<AgentEvent[]>(`/api/runs/${runId}/events`),
  getMap: (runId: number) => request<WorldMapPayload>(`/api/runs/${runId}/map`),
  listTests: (runId: number) => request<TestCaseRow[]>(`/api/runs/${runId}/tests`),
  addEvent: (runId: number, event: Partial<AgentEvent>) =>
    request<AgentEvent>(`/api/runs/${runId}/events`, {
      method: "POST",
      body: JSON.stringify(event),
    }),
};
