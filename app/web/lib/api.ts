import { activeKey, loadSettings, type ProviderSpec } from "./settings";

// Base URL is injected per worktree so the browser bundle talks to the API of
// the stack it was served from, not whatever happens to own port 8000.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export const WORKTREE = process.env.NEXT_PUBLIC_WORKTREE ?? "main";

/**
 * The caller's own model credentials, if Advanced holds any.
 *
 * Attached to every request rather than to the three that start model work: the
 * browser has one `request()` and the server has one dependency reading these
 * (`app/byok.py`), and a header nobody reads costs nothing. Deciding *here*
 * which endpoints need a key would put that list in a second place, and the
 * second place is the one that goes stale.
 *
 * Empty strings are omitted, not sent — an empty header is a header, and the
 * server would have to tell it apart from an absent one.
 */
function byokHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const settings = loadSettings();
  // A model id belongs to exactly one provider, so an id with no provider
  // beside it is not a preference we can honour — the server would pair it with
  // whichever key its `.env` happens to hold and 404 on the first call. All
  // three headers or none.
  if (!settings.provider) return {};

  const key = activeKey(settings);
  const headers: Record<string, string> = {
    "X-AIVAR-Provider": settings.provider,
  };
  if (key) headers["X-AIVAR-Key"] = key;
  if (settings.model) headers["X-AIVAR-Model"] = settings.model;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    // After the spread, not before: a caller passing `headers` would otherwise
    // drop the content type and the keys along with it.
    headers: {
      "Content-Type": "application/json",
      ...byokHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    // FastAPI puts the reason in `detail`, and for the chat endpoint that
    // reason *is* the message worth showing ("the model could not answer:
    // ANTHROPIC_API_KEY is not set"). Collapsing every failure to a status code
    // is how a spent API key became four grey characters last time.
    const detail = await res
      .clone()
      .json()
      .then((body) => (typeof body?.detail === "string" ? body.detail : null))
      .catch(() => null);
    throw new Error(
      detail ?? `${init?.method ?? "GET"} ${path} → ${res.status}`,
    );
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export type TestSession = {
  id: number;
  /**
   * The session's own id, issued once and never reissued -- unlike `id`, which
   * is a row number and starts at 1 again after `make reset`. Anything kept
   * outside the database is keyed on this; see `regression.directory_for`.
   */
  uid: string;
  target_url: string;
  name: string | null;
  /**
   * Whatever the tester typed beside the URL: who to log in as, what to focus
   * on, statements they want checked. One box, sorted into fields per run by
   * `agents/context.py` -- the browser never parses it and never should, since
   * what it means depends on which model is answering.
   */
  context: string | null;
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

/**
 * One turn of the conversation held beside a session's map.
 *
 * `node_keys` is the JSON array of `MapState.key` values that were attached
 * when the message was sent — what was asked about, not what is selected now.
 */
export type ChatMessage = {
  id: number;
  session_id: number | null;
  thread_id: number | null;
  role: "user" | "assistant";
  content: string;
  node_keys: string;
  run_id: number | null;
  created_at: string;
};

/**
 * One chat window: its own history, its own attached states, its own place on
 * screen.
 *
 * `open` and `minimised` live on the server rather than in the browser so a
 * reload comes back to the same desk. Closing is not deleting — a closed thread
 * is still listed, and reopening it restores the conversation.
 */
export type ChatThread = {
  id: number;
  session_id: number | null;
  title: string;
  open: boolean;
  minimised: boolean;
  created_at: string;
};

/**
 * A question, the reply it produced, and the thread as it now stands. The API
 * writes the pair or neither; the thread comes along because the first message
 * names the window.
 */
export type ChatTurn = {
  user: ChatMessage;
  assistant: ChatMessage;
  thread: ChatThread;
};

/**
 * One agent conversation, as `agents/tracing.py` wrote it.
 *
 * The metadata comes from the filename; `url` addresses the file itself on the
 * artifacts mount, which is where the exchanges live. Listing does not read
 * them — a run writes one of these per ant and they are ~12KB each.
 */
export type TranscriptRow = {
  name: string;
  /** ant | orchestrator | ... — the role that held this conversation. */
  role: string;
  /** For an ant, the first 8 chars of the state it was sent to. */
  label: string | null;
  bytes: number;
  written_at: string;
  url: string;
};

/** One model turn: what it said, what it called, and what came back. */
export type TranscriptExchange = {
  text: string;
  calls: { name: string; arguments: Record<string, unknown> }[];
  results: { name: string; content: string }[];
  provider_state: boolean;
};

/** The file itself. `system` is the prompt file that produced the run. */
export type Transcript = {
  role: string;
  run_id: number | null;
  label: string;
  written_at: string;
  system: string;
  prompt: string;
  exchanges: TranscriptExchange[];
};

/** Why one offered action could not be taken, and how often. */
export type Refusal = { reason: string; count: number };

/**
 * How far a crawl got. `walked / offered` is the one ratio in this codebase,
 * and it is a fact about the crawl rather than a coverage score — see
 * `routers/progress.py` and decisions.md 2026-09-04 19:00. Everything else here
 * is a count on purpose.
 */
export type Progress = {
  run_id: number;
  status: string;
  offered: number;
  walked: number;
  refused: number;
  remaining: number;
  states: number;
  transitions: number;
  mutating: number;
  untested_states: number;
  ambiguous_edges: number;
  reasons: Refusal[];
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

/** One kept `.spec.ts`: the file, what it is about, and its source. */
export type SpecFile = {
  file: string;
  name: string;
  node: string | null;
  origin: string;
  covers: number;
  status: string | null;
  code: string;
};

export type SuiteVersion = {
  label: string;
  number: number;
  parent: string | null;
  because: string;
  source: string;
  saved_at: string;
  scenarios: number;
  heals: number;
  /** Verdicts from replaying the repaired scenarios before this version was declared. */
  reverified: Record<string, number>;
  /** Steps recovered by exploring the region that lost the control. */
  rescues: number;
};

/**
 * The suite on disk for a run's target.
 *
 * `version` is null until a run has kept one — which is not the same as a run
 * having produced no tests. A run still compiling has scenarios on the timeline
 * and nothing here, and the panel says so rather than reading as empty.
 */
export type SuitePayload = {
  target_url: string;
  directory: string;
  version: SuiteVersion | null;
  versions: SuiteVersion[];
  specs: SpecFile[];
};

/** Artifacts are served by the API, not by Next. */
export const artifactUrl = (path: string) => `${API_BASE}/artifacts/${path}`;

export const api = {
  health: () => request<{ status: string; worktree: string }>("/health"),

  /**
   * Which providers this server can be pointed at, and which it already has a
   * key for. The catalogue lives in `agents/llm/catalog.py`; fetching it is
   * what stops the dialog offering a model the backend cannot construct.
   */
  listProviders: () =>
    request<{ providers: ProviderSpec[] }>("/api/providers").then(
      (body) => body.providers,
    ),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),
  createSession: (target_url: string, context?: string) =>
    request<TestSession>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ target_url, context: context || null }),
    }),
  getSession: (id: number) => request<TestSession>(`/api/sessions/${id}`),
  renameSession: (id: number, name: string) =>
    request<TestSession>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  /** Editable after the fact: a mistyped password should not cost the map. */
  setSessionContext: (id: number, context: string) =>
    request<TestSession>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ context: context || null }),
    }),
  deleteSession: (id: number) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  listSessionRuns: (id: number) => request<Run[]>(`/api/sessions/${id}/runs`),
  /** Tail of the session's event stream. `after` is the highest id already seen. */
  listSessionEvents: (id: number, after = 0) =>
    request<AgentEvent[]>(`/api/sessions/${id}/events?after=${after}`),

  /** Every thread of a session, open and closed. Closed ones fill the reopen list. */
  listThreads: (sessionId: number) =>
    request<ChatThread[]>(`/api/sessions/${sessionId}/chat/threads`),
  createThread: (sessionId: number, title?: string) =>
    request<ChatThread>(`/api/sessions/${sessionId}/chat/threads`, {
      method: "POST",
      body: JSON.stringify({ title: title ?? null }),
    }),
  /** Rename, close, reopen, minimise, restore — every window edit is this one. */
  patchThread: (
    threadId: number,
    patch: { title?: string; open?: boolean; minimised?: boolean },
  ) =>
    request<ChatThread>(`/api/chat/threads/${threadId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  /** Destroys the thread and everything said in it. Closing is the soft one. */
  deleteThread: (threadId: number) =>
    request<void>(`/api/chat/threads/${threadId}`, { method: "DELETE" }),

  listChat: (threadId: number) =>
    request<ChatMessage[]>(`/api/chat/threads/${threadId}/messages`),
  /**
   * Ask about the map. Blocks for as long as the model takes — there is no
   * event stream behind this, the reply *is* the response.
   */
  sendChat: (
    threadId: number,
    text: string,
    node_keys: string[],
    run_id: number | null,
  ) =>
    request<ChatTurn>(`/api/chat/threads/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, node_keys, run_id }),
    }),
  /** Empties a thread without closing its window. */
  clearChat: (threadId: number) =>
    request<void>(`/api/chat/threads/${threadId}/messages`, { method: "DELETE" }),

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
  listTranscripts: (runId: number) =>
    request<TranscriptRow[]>(`/api/runs/${runId}/transcripts`),
  /** The file, straight off the artifacts mount — no API route reads it. */
  readTranscript: async (url: string): Promise<Transcript> => {
    const res = await fetch(artifactUrl(url), { cache: "no-store" });
    if (!res.ok) throw new Error(`transcript → ${res.status}`);
    return (await res.json()) as Transcript;
  },
  /**
   * Send one ant to a state on this run's map, optionally down a named action.
   * Returns as soon as it is dispatched; what it finds arrives on the rail and
   * on the map, like any other ant's work.
   */
  dispatchAnt: (
    runId: number,
    body: { state_key: string; action?: string | null; instruction?: string | null },
  ) =>
    request<{ run_id: number; state_key: string; status: string }>(
      `/api/runs/${runId}/ant`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  listEvents: (runId: number) =>
    request<AgentEvent[]>(`/api/runs/${runId}/events`),
  getMap: (runId: number) => request<WorldMapPayload>(`/api/runs/${runId}/map`),
  getProgress: (runId: number) => request<Progress>(`/api/runs/${runId}/progress`),
  listTests: (runId: number) => request<TestCaseRow[]>(`/api/runs/${runId}/tests`),
  /** The kept suite for this run's target, with every spec's source inline. */
  getSuite: (runId: number) => request<SuitePayload>(`/api/runs/${runId}/suite`),
  /**
   * Where the browser should go to download the archive.
   *
   * A plain URL rather than a fetch-and-blob: the server already sets
   * `Content-Disposition`, so an anchor gets the filename, the progress bar and
   * the download folder for free — and none of that survives being rebuilt out
   * of an object URL.
   */
  suiteZipUrl: (runId: number, version?: string) =>
    `${API_BASE}/api/runs/${runId}/suite/download` +
    (version ? `?version=${encodeURIComponent(version)}` : ""),
  specUrl: (runId: number, stem: string, version?: string) =>
    `${API_BASE}/api/runs/${runId}/suite/spec/${encodeURIComponent(stem)}` +
    (version ? `?version=${encodeURIComponent(version)}` : ""),
  addEvent: (runId: number, event: Partial<AgentEvent>) =>
    request<AgentEvent>(`/api/runs/${runId}/events`, {
      method: "POST",
      body: JSON.stringify(event),
    }),
};
