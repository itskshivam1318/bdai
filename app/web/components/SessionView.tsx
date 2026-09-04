"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Canvas from "@/components/Canvas";
import SettingsDialog from "@/components/SettingsDialog";
import { sessionLabel } from "@/components/Sidebar";
import { api, type Run, type TestSession } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  passed: "text-live",
  failed: "text-fault",
  error: "text-fault",
  running: "text-live",
};

export default function SessionView({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<TestSession | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [name, setName] = useState("");
  const [intent, setIntent] = useState("");

  const refreshRuns = useCallback(() => {
    api.listSessionRuns(sessionId).then(setRuns).catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    api
      .getSession(sessionId)
      .then((s) => {
        setSession(s);
        setName(sessionLabel(s));
      })
      .catch(() => {});
    refreshRuns();
    const t = setInterval(refreshRuns, 3000);
    return () => clearInterval(t);
  }, [sessionId, refreshRuns]);

  const latest = runs[runs.length - 1];

  function commitName() {
    const next = name.trim();
    if (!session || !next || next === sessionLabel(session)) return;
    void api.renameSession(sessionId, next);
  }

  /**
   * The whole product in four lines: a URL becomes a run, and the run starts
   * exploring itself. Anything typed in the intent box steers where the ants go
   * — the brief's "focus on checkout and authentication flows" — but the URL
   * alone is enough, which is the requirement.
   */
  async function startRun() {
    if (!session) return;
    const run = await api.createRun(session.target_url, sessionId);
    refreshRuns();
    // Explicitly not awaited: the colony runs for minutes and the button
    // should return now. Progress arrives on the timeline.
    void api.explore(run.id, intent.trim() || undefined).catch(() => {});
  }

  /**
   * Optional steering, not the driver: the pipeline starts from the URL alone.
   * Anything typed here lands on the run's timeline as intent the agent can
   * read — the brief's "focus on checkout and authentication flows".
   */
  async function sendIntent(e: React.FormEvent) {
    e.preventDefault();
    const message = intent.trim();
    if (!message || !session) return;
    setIntent("");
    const run = latest ?? (await api.createRun(session.target_url, sessionId));
    await api.addEvent(run.id, { level: "info", message: `Intent: ${message}` });
    refreshRuns();
  }

  return (
    <>
      <header className="flex items-center gap-3 border-b border-rule px-4 py-2.5">
        <Link
          href="/"
          aria-label="Back to sessions"
          className="rounded px-1 text-muted hover:text-ink"
        >
          ←
        </Link>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          aria-label="Session name"
          className="min-w-0 max-w-64 flex-1 rounded bg-transparent px-1 py-0.5 text-sm font-medium outline-none hover:bg-hush focus:bg-hush"
        />

        {latest && (
          <span className={`text-xs ${STATUS_TONE[latest.status] ?? "text-muted"}`}>
            run {runs.length} · {latest.status}
          </span>
        )}

        <a
          href={session?.target_url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto truncate font-mono text-xs text-muted hover:text-ink"
        >
          {session?.target_url}
        </a>

        <button
          onClick={startRun}
          className="rounded-md bg-ink px-3 py-1.5 text-xs text-paper"
        >
          {runs.length ? "Run again" : "Start run"}
        </button>
        <button
          onClick={() => setSettingsOpen(true)}
          className="rounded-md border border-rule px-2.5 py-1.5 text-xs hover:bg-hush"
        >
          Advanced
        </button>
      </header>

      <div className="min-h-0 flex-1">
        <Canvas sessionId={sessionId} />
      </div>

      <form
        onSubmit={sendIntent}
        className="flex items-center gap-2 border-t border-rule px-4 py-3"
      >
        <label htmlFor="intent" className="sr-only">
          Steer the agent
        </label>
        <input
          id="intent"
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="Optional — steer it, e.g. focus on checkout and sign-in"
          className="min-w-0 flex-1 rounded-md border border-rule bg-paper px-3 py-2 text-sm outline-none focus:border-ink"
        />
        <button
          type="submit"
          disabled={!intent.trim()}
          className="rounded-md border border-rule px-3 py-2 text-sm disabled:opacity-30"
        >
          Send
        </button>
      </form>

      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </>
  );
}
