"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChatDock from "@/components/ChatDock";
import MapPane from "@/components/MapPane";
import StageRail from "@/components/StageRail";
import SettingsDialog from "@/components/SettingsDialog";
import { sessionLabel } from "@/components/Sidebar";
import {
  api,
  type ChatThread,
  type MapState,
  type Run,
  type TestSession,
} from "@/lib/api";

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
  // Which run's failure the reason popover is open for -- not a boolean. A
  // reason belongs to one run, so keying it on the id means "Run again" closes
  // it for free rather than needing an effect to chase the change.
  const [reasonFor, setReasonFor] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [intent, setIntent] = useState("");
  /*
   * The session's context box, and whether it is on screen.
   *
   * Editable here and not only at creation because the single most likely thing
   * to be wrong with it is a mistyped password, and the alternative to fixing
   * it in place is starting a new session -- which throws away the map that
   * just told you the login was failing.
   */
  const [context, setContext] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  /*
   * The chat windows, and which one the map is aimed at.
   *
   * Both live here rather than in `ChatDock` because the *map* needs them: a
   * click on a state has to know which conversation it is attaching to, and
   * the rings drawn on the canvas have to show that conversation's selection.
   * Splitting focus from the selection it steers would put the two halves of
   * one gesture in two components.
   */
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [focusedId, setFocusedId] = useState<number | null>(null);

  /*
   * The states attached to the next question, per window. Whole states rather
   * than keys: the chips need a name, and the map is the only place one exists
   * -- keeping keys here would mean re-fetching the map to render a label.
   *
   * Per window and not global because a selection belongs to a question. With
   * one shared set, opening a second conversation to ask about something else
   * destroyed the first one's context, which is the opposite of what a second
   * window is for.
   *
   * Not persisted, unlike the windows themselves: a key identifies a state only
   * *within* a run, and what an old selection meant is already on the messages
   * that were sent with it (`ChatMessage.node_keys`). What is cleared on a run
   * change is the *pending* selection, which would otherwise attach keys the
   * new map has never heard of.
   */
  const [attachedByThread, setAttachedByThread] = useState<
    Record<number, MapState[]>
  >({});

  const refreshRuns = useCallback(() => {
    api.listSessionRuns(sessionId).then(setRuns).catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    api
      .getSession(sessionId)
      .then((s) => {
        setSession(s);
        setName(sessionLabel(s));
        setContext(s.context ?? "");
      })
      .catch(() => {});
    refreshRuns();
    const t = setInterval(refreshRuns, 3000);
    return () => clearInterval(t);
  }, [sessionId, refreshRuns]);

  const latest = runs[runs.length - 1];
  const reasonOpen = latest != null && reasonFor === latest.id;

  /*
   * The rail is open until someone shuts it, and it stays shut until they open
   * it again. Nothing else moves it.
   *
   *   open === null   never touched -- open
   *   open === true    held open
   *   open === false   held shut
   *
   * It used to hide itself four seconds after a run finished, on the theory
   * that a finished stage has stopped earning its width. That was wrong twice
   * over: the finished rail is the *report* -- eight verdicts, the gaps, the
   * scenario names -- and a panel that empties itself while you are reading it
   * is indistinguishable from a panel with nothing in it. It was reported as
   * exactly that. The map has a toggle in the header for anyone who wants the
   * width back.
   */
  const [open, setOpen] = useState<boolean | null>(null);

  const running = latest?.status === "running";
  const latestId = latest?.id ?? null;
  const railOpen = open ?? true;

  // Runs are scoped maps, not versions of one map: re-crawling after the app
  // changes writes a second graph beside the first, which is the drift story.
  // So the picker is not a convenience — it is how you compare two builds.
  const shownRunId = selectedRunId ?? latest?.id ?? null;

  // A state key identifies a state only *within* a run, so a selection made
  // against one map means nothing against another. Cleared during render for
  // the same reason the rail adjusts during render: an effect would let one
  // frame paint chips that belong to the map no longer on screen.
  const [seenMapRunId, setSeenMapRunId] = useState(shownRunId);
  if (shownRunId !== seenMapRunId) {
    setSeenMapRunId(shownRunId);
    if (Object.keys(attachedByThread).length) setAttachedByThread({});
  }

  // The map shows the focused window's selection, and only that one. A union
  // across every open chat would draw rings that no single Send would honour.
  //
  // Memoised because `MapPane` builds a Set from this and `StateCard` reads it
  // through context -- a fresh array every render would re-render every card on
  // every three-second poll.
  const attachedKeys = useMemo(
    () =>
      focusedId == null
        ? []
        : (attachedByThread[focusedId] ?? []).map((s) => s.key),
    [focusedId, attachedByThread],
  );

  function commitName() {
    const next = name.trim();
    if (!session || !next || next === sessionLabel(session)) return;
    void api.renameSession(sessionId, next);
  }

  function commitContext() {
    const next = context.trim();
    if (!session || next === (session.context ?? "")) return;
    void api.setSessionContext(sessionId, next).then(setSession);
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

  /*
   * Windows are loaded once, then owned locally. Unlike the runs above they are
   * not polled: nothing but this console writes a thread, so a poll would be a
   * request every three seconds that can never find anything new.
   *
   * A session with no chat at all opens one. An empty right margin reads as a
   * feature that is not there, and the first thing anyone does with a map is
   * ask about it.
   */
  const bootstrapped = useRef(false);
  useEffect(() => {
    let cancelled = false;
    api
      .listThreads(sessionId)
      .then(async (rows) => {
        if (cancelled) return;
        if (rows.length === 0) {
          // Guarded because React runs effects twice in development, and two
          // "New chat" windows on first open is a visible bug in the demo.
          if (bootstrapped.current) return;
          bootstrapped.current = true;
          const created = await api.createThread(sessionId);
          if (cancelled) return;
          setThreads([created]);
          setFocusedId(created.id);
          return;
        }
        setThreads(rows);
        const open = rows.filter((t) => t.open);
        setFocusedId((open.at(-1) ?? rows.at(-1))?.id ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /*
   * Window edits are applied locally and *then* sent. They are title, open and
   * minimised -- none of which the server can refuse and none of which anything
   * else writes -- so waiting for the round trip would only add a frame of lag
   * to collapsing a panel.
   */
  const patchThread = useCallback(
    (
      id: number,
      patch: { title?: string; open?: boolean; minimised?: boolean },
    ) => {
      setThreads((current) =>
        current.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      );
      void api.patchThread(id, patch).catch(() => {});
    },
    [],
  );

  const newThread = useCallback(async () => {
    const created = await api.createThread(sessionId).catch(() => null);
    if (!created) return null;
    setThreads((current) => [...current, created]);
    setFocusedId(created.id);
    return created;
  }, [sessionId]);

  const deleteThread = useCallback((id: number) => {
    setThreads((current) => current.filter((t) => t.id !== id));
    setAttachedByThread((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    // Focus falls to whatever is still open, so the map keeps a target.
    setFocusedId((current) => (current === id ? null : current));
    void api.deleteThread(id).catch(() => {});
  }, []);

  /*
   * A click on the map attaches to the **focused** window. With no window open
   * there is nothing to attach to, so one is opened -- clicking a state and
   * having nothing happen is the bug this whole surface replaced, and an empty
   * dock is no excuse to bring it back.
   */
  const toggleAttached = useCallback(
    (state: MapState) => {
      const target = focusedId;
      if (target == null) {
        void newThread().then((thread) => {
          if (thread) setAttachedByThread((c) => ({ ...c, [thread.id]: [state] }));
        });
        return;
      }
      setAttachedByThread((current) => {
        const held = current[target] ?? [];
        return {
          ...current,
          [target]: held.some((s) => s.key === state.key)
            ? held.filter((s) => s.key !== state.key)
            : [...held, state],
        };
      });
    },
    [focusedId, newThread],
  );

  const detach = useCallback(
    (threadId: number, key: string) =>
      setAttachedByThread((current) => ({
        ...current,
        [threadId]: (current[threadId] ?? []).filter((s) => s.key !== key),
      })),
    [],
  );

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

        {/*
          The status label is the only place a failure can be read. The agent
          writes a reason to `run.summary` on every terminal path, but until
          this was a disclosure it went to the database and nowhere else: a run
          could die on a missing API key and the console showed four grey
          characters. Header-only on purpose -- the canvas belongs to the map,
          not to the last thing that went wrong.
        */}
        {latest && (
          <div className="relative">
            <button
              type="button"
              onClick={() =>
                latest.summary && setReasonFor(reasonOpen ? null : latest.id)
              }
              aria-expanded={latest.summary ? reasonOpen : undefined}
              title={latest.summary ?? undefined}
              className={`rounded px-1.5 py-0.5 text-xs ${
                STATUS_TONE[latest.status] ?? "text-muted"
              } ${latest.summary ? "hover:bg-hush" : "cursor-default"}`}
            >
              run {runs.length} · {latest.status}
              {latest.summary && <span className="ml-1 opacity-60">ⓘ</span>}
            </button>

            {reasonOpen && latest.summary && (
              <div
                role="status"
                className="absolute left-0 top-full z-20 mt-1.5 w-[28rem] rounded-md border border-rule bg-paper p-3 shadow-md"
              >
                <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink">
                  {latest.summary}
                </p>
              </div>
            )}
          </div>
        )}

        {runs.length > 1 && (
          <select
            value={shownRunId ?? ""}
            onChange={(e) => setSelectedRunId(Number(e.target.value))}
            aria-label="Run to show on the map"
            className="rounded border border-rule bg-paper px-1.5 py-1 text-xs"
          >
            {runs.map((r, i) => (
              <option key={r.id} value={r.id}>
                run {i + 1} · {r.status}
              </option>
            ))}
          </select>
        )}

        <a
          href={session?.target_url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto truncate font-mono text-xs text-muted hover:text-ink"
        >
          {session?.target_url}
        </a>

        {/*
          A dot, not a count. Whether context exists is the thing worth seeing
          from the header; what is in it is a password, and the header is the
          part of the screen that is always on the projector.
        */}
        <button
          onClick={() => setContextOpen((open) => !open)}
          aria-expanded={contextOpen}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-rule px-2 py-1.5 text-xs text-muted hover:bg-hush hover:text-ink"
        >
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              context.trim() ? "bg-ink" : "border border-rule"
            }`}
          />
          Context
        </button>

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
        <button
          type="button"
          onClick={() => setOpen(!railOpen)}
          aria-expanded={railOpen}
          aria-controls="stage-rail"
          aria-label={railOpen ? "Hide stage rail" : "Show stage rail"}
          title={railOpen ? "Hide stage rail" : "Show stage rail"}
          className="rounded-md p-1.5 text-muted hover:bg-hush hover:text-ink"
        >
          {/* The left panel's icon with the bar on the other edge: both say
              "this side moves", so the pair reads as one control scheme. */}
          <svg
            aria-hidden
            viewBox="0 0 16 16"
            className="size-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <rect x="1.5" y="2.5" width="13" height="11" rx="2" />
            <line x1="10" y1="2.5" x2="10" y2="13.5" />
          </svg>
        </button>
      </header>

      {/* `relative` because the chat dock floats inside this box rather than
          taking a column of it -- see ChatDock for why an overlay. */}
      <div className="relative min-h-0 flex-1">
        {/* One column when the rail is away, so the graph reflows into the
            width rather than staying crowded into the left two-thirds. */}
        <div
          className={`grid h-full min-h-0 ${
            railOpen ? "grid-cols-[3fr_2fr]" : "grid-cols-1"
          }`}
        >
          <div className="min-w-0">
            <MapPane
              runId={shownRunId}
              selectedKeys={attachedKeys}
              onToggleSelect={toggleAttached}
            />
          </div>
          {railOpen && (
            <div id="stage-rail" className="min-w-0 overflow-hidden">
              <StageRail
                sessionId={sessionId}
                runId={shownRunId}
                running={running && shownRunId === latestId}
              />
            </div>
          )}
        </div>

        <ChatDock
          threads={threads}
          focusedId={focusedId}
          attachedByThread={attachedByThread}
          runId={shownRunId}
          onFocus={setFocusedId}
          onPatch={patchThread}
          onDelete={deleteThread}
          onNew={() => void newThread()}
          onDetach={detach}
          onClearAttached={(id) =>
            setAttachedByThread((current) => ({ ...current, [id]: [] }))
          }
        />
      </div>

      {/*
        The intent box, back to doing one job.
 
        It used to be the chat as well -- one input whose text was read both by
        Send and by "Start run" -- and that was already a compromise when there
        was one conversation. With a window per conversation it is not
        expressible: two windows cannot share a draft without one of them
        typing into the other. Each window owns its text now, and this owns the
        run's.
      */}
      {contextOpen && (
        <div className="border-t border-rule px-4 py-3">
          <label
            htmlFor="session-context"
            className="text-xs uppercase tracking-wide text-muted"
          >
            What the run should know about this app
          </label>
          <textarea
            id="session-context"
            value={context}
            onChange={(e) => setContext(e.target.value)}
            onBlur={commitContext}
            rows={3}
            placeholder={
              "Log in as standard_user / secret_sauce. Focus on checkout. " +
              "Check that an out-of-stock item can't be added to the cart."
            }
            className="mt-2 w-full resize-y rounded-md border border-rule bg-paper px-3 py-2 text-sm leading-relaxed outline-none focus:border-ink placeholder:text-muted/60"
          />
          <p className="mt-2 text-xs text-muted">
            Saved to this session and read by every run under it. Applies from
            the next run — it is not retroactive.
          </p>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void startRun();
        }}
        className="flex items-center gap-2 border-t border-rule px-4 py-2.5"
      >
        <label htmlFor="intent" className="sr-only">
          What the exploration should focus on
        </label>
        <input
          id="intent"
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="What should the colony focus on? — “checkout and sign-in”. Optional; the URL alone is enough"
          className="min-w-0 flex-1 rounded-md border border-rule bg-paper px-3 py-2 text-sm outline-none focus:border-ink"
        />
        <button
          type="submit"
          className="rounded-md border border-rule px-3 py-2 text-sm hover:bg-hush"
        >
          {runs.length ? "Run again" : "Start run"}
        </button>
      </form>

      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </>
  );
}
