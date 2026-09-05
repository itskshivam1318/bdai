"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import TranscriptViewer from "@/components/TranscriptViewer";
import { api, type AgentEvent, type SuitePayload } from "@/lib/api";
import { STAGES } from "@/lib/stages";

/**
 * The suite, as files you can take away.
 *
 * This replaced the stage rail, and the reason is what the two panels are
 * *about*. The rail narrated the agent — six cards saying which phase had said
 * something most recently — and once a run finished it was a transcript of work
 * whose output lived somewhere the console could not reach. The output is the
 * product: "a URL in, a meaningful test suite out" is a claim about the suite,
 * so the suite is what the panel holds.
 *
 * The stages did not survive as cards but they did survive: the strip along the
 * top is the same `Event.surface` seam reduced to one line, because *which
 * stage is working* is still worth a glance and no longer worth two thirds of
 * the panel.
 *
 * **Three sources, deliberately, and they arrive in that order.** A scenario is
 * named on the timeline the moment the Generator compiles it, gets a verdict
 * minutes later when the Runner replays it, and only becomes a *file* when
 * `regression.keep` writes it to disk at the end of the run. Waiting for the
 * files would leave this panel empty for the whole run; showing only the
 * timeline would never offer a download. So a row is promoted as its evidence
 * arrives, and says which of the three it currently is.
 */

const VERDICT_TONE: Record<string, string> = {
  passed: "text-live",
  healed: "text-live",
  defect: "text-fault",
  escalate: "text-fault",
};

const VERDICT_MARK: Record<string, string> = {
  passed: "✓",
  healed: "⤳",
  defect: "✕",
  escalate: "!",
};

/** `complete the Sign in form and submit it (3 steps)` — the Generator's line. */
const COMPILED = /^(.+?) \((\d+) steps?\)$/;
/** `complete the Sign in form and submit it: defect (1 healed)` — the Runner's. */
const VERDICT = /^(.+?): (passed|healed|defect|escalate)\b/;
/** `3/8 replaying complete the Sign in form` — emitted before the replay. */
const REPLAYING = /^(\d+)\/\d+ replaying (.+)$/;

/** The database writes naive UTC; without the Z a browser reads it as local. */
function stamp(iso: string): number {
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
}

/** `47s`, `3m 20s` — how long this has been the newest thing that happened. */
function ago(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

type Row = {
  name: string;
  steps: number | null;
  status: string | null;
  /** The stem of the `.spec.ts` on disk, once one exists. */
  file: string | null;
  code: string;
  origin: string;
};

/**
 * What the panel should show right now, newest evidence winning.
 *
 * Kept files are authoritative: they carry the code and the download, and their
 * order is the order they will be in the archive. Timeline rows are appended
 * after them only when nothing on disk claims that name — during a run that is
 * every row, and after a keep it is none.
 *
 * Matched by name because a name is the only thing the timeline and the
 * manifest share. Two scenarios in one suite may genuinely have the same name,
 * so this counts occurrences rather than looking one up: the second "complete
 * the Sign in form and submit it" is a different row, not a duplicate.
 */
function rowsFor(suite: SuitePayload | null, events: AgentEvent[]): Row[] {
  const kept: Row[] = (suite?.specs ?? []).map((spec) => ({
    name: spec.name,
    steps: null,
    status: spec.status,
    file: spec.file,
    code: spec.code,
    origin: spec.origin,
  }));

  const compiled: Row[] = [];
  for (const event of events) {
    const hit = COMPILED.exec(event.message);
    if (hit) {
      compiled.push({
        name: hit[1],
        steps: Number(hit[2]),
        status: null,
        file: null,
        code: "",
        origin: "",
      });
    }
  }

  // Verdicts land on both lists: a kept row whose scenario just re-ran should
  // show what it did on *this* run, not what it did when it was recorded.
  const seen: Record<string, number> = {};
  const verdicts: Record<string, string[]> = {};
  for (const event of events) {
    const hit = VERDICT.exec(event.message);
    if (hit) (verdicts[hit[1]] ??= []).push(hit[2]);
  }
  const take = (name: string) => {
    const held = verdicts[name];
    if (!held) return null;
    const index = (seen[name] = (seen[name] ?? -1) + 1);
    return held[index] ?? held[held.length - 1] ?? null;
  };

  if (kept.length) {
    return kept.map((row) => ({ ...row, status: take(row.name) ?? row.status }));
  }
  return compiled.map((row) => ({ ...row, status: take(row.name) }));
}

export default function SuitePane({
  sessionId,
  runId,
  running,
}: {
  sessionId: number;
  runId: number | null;
  running: boolean;
}) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [suite, setSuite] = useState<SuitePayload | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [transcripts, setTranscripts] = useState(false);
  const logEnd = useRef<HTMLDivElement>(null);

  const period = running ? 1000 : 4000;

  // Reset during render, the console's idiom (see SessionView): an effect would
  // let one frame paint the previous session's tests.
  const [seenSession, setSeenSession] = useState(sessionId);
  if (sessionId !== seenSession) {
    setSeenSession(sessionId);
    setEvents([]);
  }
  const [seenRun, setSeenRun] = useState(runId);
  if (runId !== seenRun) {
    setSeenRun(runId);
    setSuite(null);
    setExpanded(null);
  }

  const after = useRef(0);
  useEffect(() => {
    after.current = events.length ? events[events.length - 1].id : 0;
  }, [events]);

  useEffect(() => {
    let cancelled = false;
    const poll = () =>
      api
        .listSessionEvents(sessionId, after.current)
        .then((batch) => {
          if (cancelled || !batch.length) return;
          setEvents((current) => {
            const held = new Set(current.map((e) => e.id));
            const fresh = batch.filter((e) => !held.has(e.id));
            return fresh.length ? [...current, ...fresh] : current;
          });
        })
        .catch(() => {});
    poll();
    const timer = setInterval(poll, period);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId, period]);

  const loadSuite = useCallback(() => {
    if (runId == null) return;
    api
      .getSuite(runId)
      .then(setSuite)
      .catch(() => {});
  }, [runId]);

  // Polled while the run is live because the files appear at the *end* of it —
  // the panel would otherwise need a reload to show the download it just
  // earned. Slower than the timeline: this is one write per run, not per line.
  useEffect(() => {
    loadSuite();
    if (!running) return;
    const timer = setInterval(loadSuite, 5000);
    return () => clearInterval(timer);
  }, [loadSuite, running]);

  /*
   * A clock, ticking only while the run is live.
   *
   * Measured on run 32 (2026-09-05): 97 lines over 507 seconds, with single
   * silences of 86s, 65s, 62s and 54s — every one of them a model call the
   * agent was waiting on. The console polled every second throughout and had
   * nothing new to draw, so a working run and a dead one looked identical for
   * a minute and a half at a time. Nothing here makes the model faster; the
   * fix is that a silence has to be *legible* as one, which needs a number
   * that moves while nothing else does.
   */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    // No initial set: the first tick is a second away, `now` was read at mount,
    // and `silent` floors at zero — so a stale clock shows 0s for one second
    // rather than a negative age. Setting state here is also a lint error.
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const mine = events.filter((e) => e.run_id === runId);
  const suiteEvents = mine.filter((e) => e.surface === "suite" || e.surface === "run");
  const rows = rowsFor(suite, suiteEvents);

  const last = mine.length ? mine[mine.length - 1] : null;
  const silent = last ? Math.max(0, Math.round((now - stamp(last.created_at)) / 1000)) : 0;

  /*
   * The scenario on the page right now.
   *
   * `explore.py` emits "3/8 replaying <name>" before each replay and the
   * verdict after it, so the newest replay line with no verdict behind it is
   * the one being executed. Without this a row sat at "compiling" for the
   * whole replay — the panel knew which test was running and did not say.
   */
  const replaying = (() => {
    if (!running) return null;
    for (let i = suiteEvents.length - 1; i >= 0; i -= 1) {
      const hit = REPLAYING.exec(suiteEvents[i].message);
      if (hit) return { ordinal: Number(hit[1]), name: hit[2] };
      if (VERDICT.exec(suiteEvents[i].message)) return null;
    }
    return null;
  })();

  useEffect(() => {
    if (logOpen) logEnd.current?.scrollIntoView({ block: "nearest" });
  }, [logOpen, mine.length]);

  const reached = STAGES.reduce(
    (last, stage, i) =>
      mine.some((e) => e.surface && stage.surfaces.includes(e.surface)) ? i : last,
    -1,
  );

  /*
   * The ordinal, not the name. "5/8 replaying X" carries its own position in
   * the plan, and one suite can hold two scenarios with the same name — this
   * SUT compiles "complete the Sign in form and submit it" twice — so matching
   * on the name alone marked the first of them, which had already passed, or
   * nothing at all once both had. The name is kept as the guard: if the row at
   * that position is not the one being replayed, the lists are not aligned and
   * marking anything would be a guess.
   */
  const activeIndex = (() => {
    if (!replaying) return -1;
    const at = replaying.ordinal - 1;
    if (rows[at]?.name === replaying.name) return at;
    return rows.findIndex((r) => r.name === replaying.name && !r.status);
  })();

  const version = suite?.version ?? null;
  const tally = rows.reduce<Record<string, number>>((acc, row) => {
    if (row.status) acc[row.status] = (acc[row.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex h-full flex-col border-l border-rule bg-hush">
      {/*
        The six stages, one line. Not a progress bar: a stage is done when it
        has *said* something, and emptiness is the disclosure — ticking a stage
        that never reported is how the old rail claimed a run had happened when
        nothing had.
      */}
      <div className="flex items-center gap-1 border-b border-rule px-3 py-2 text-[10px] uppercase tracking-wide">
        {STAGES.map((stage, i) => {
          const said = mine.some(
            (e) => e.surface && stage.surfaces.includes(e.surface),
          );
          const live = running && i === reached;
          return (
            <span
              key={stage.title}
              title={said ? stage.title : stage.waiting}
              className={
                live
                  ? "rounded bg-ink px-1.5 py-0.5 text-paper"
                  : said
                    ? "px-1.5 py-0.5 text-ink"
                    : "px-1.5 py-0.5 text-muted/50"
              }
            >
              {stage.title}
            </span>
          );
        })}
      </div>

      {/*
        What is happening right now, and how long it has been happening.
        Only while the run is live — after it ends the last line is history and
        a counter ticking beside it would be measuring the wrong thing.

        The elapsed number is the point. The agent's longest legitimate silence
        is a single model call, and "waiting on the model · 1m 26s" is a
        different message from the same screen with nothing on it, even though
        the pixels in between are identical.
      */}
      {running && last && (
        <div className="flex items-baseline gap-2 border-b border-rule bg-paper px-3 py-2">
          <span
            aria-hidden
            className="size-1.5 shrink-0 animate-pulse rounded-full bg-live"
          />
          <span className="min-w-0 flex-1 truncate text-[11px] leading-snug text-ink">
            {last.message}
          </span>
          <span
            title={
              silent >= 30
                ? "The agent is waiting on a model call. Runs of a minute or more are normal."
                : undefined
            }
            className={`shrink-0 tabular-nums text-[11px] ${
              silent >= 90 ? "text-fault" : "text-muted"
            }`}
          >
            {ago(silent)}
          </span>
        </div>
      )}

      <div className="flex items-baseline gap-2 border-b border-rule px-3 py-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-ink">
          Playwright tests
        </h2>
        {version && (
          <span
            title={version.because}
            className="rounded border border-rule bg-paper px-1.5 py-0.5 font-mono text-[10px] text-muted"
          >
            {version.label}
            {version.parent && ` ← ${version.parent}`}
          </span>
        )}
        {/*
          Two badges the version earns rather than declares. "re-verified" means
          the repaired scenarios were replayed and passed *before* this version
          was written -- without it a repair is a hypothesis, and a suite that
          wrote one it could not stand behind would be a worse liar than one
          that never repaired at all. "rescued" means a control was found by
          exploring the region that lost it, which is a different and weaker
          provenance than the resolution ladder, so it is shown separately.
        */}
        {version && Object.keys(version.reverified).length > 0 && (
          <span
            title={Object.entries(version.reverified)
              .map(([verdict, n]) => `${n} ${verdict}`)
              .join(", ")}
            className="rounded bg-live/15 px-1.5 py-0.5 text-[10px] text-live"
          >
            re-verified
          </span>
        )}
        {version && version.rescues > 0 && (
          <span
            title="steps recovered by exploring the region that lost the control"
            className="rounded border border-rule px-1.5 py-0.5 text-[10px] text-muted"
          >
            {version.rescues} rescued
          </span>
        )}
        <span className="ml-auto tabular-nums text-[11px] text-muted">
          {rows.length}
          {Object.keys(tally).length > 0 && (
            <>
              {" · "}
              {Object.entries(tally)
                .map(([verdict, n]) => `${n} ${verdict}`)
                .join(", ")}
            </>
          )}
        </span>
      </div>

      {/*
        The download, and it is an anchor rather than a button on purpose: the
        server sets the filename and the browser owns the rest. `download` is
        left off so a same-origin-less cross-port response keeps the
        Content-Disposition name the API chose.
      */}
      {runId !== null && (
        <div className="border-b border-rule px-3 py-2">
          {version ? (
            <a
              href={api.suiteZipUrl(runId, version.label)}
              className="block rounded-md bg-ink px-3 py-2 text-center text-xs text-paper hover:opacity-90"
            >
              ↓ Download {version.scenarios} test
              {version.scenarios === 1 ? "" : "s"} ({version.label}.zip)
            </a>
          ) : (
            <p className="text-[11px] italic leading-snug text-muted">
              {running
                ? "The suite is written to disk when the run finishes — the tests below are compiled and being replayed now."
                : "No suite kept for this target yet. Start a run: the first one records the baseline every later run is measured against."}
            </p>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {rows.length === 0 ? (
          <p className="mt-2 text-[11px] italic leading-snug text-muted">
            {running
              ? "Waiting for the Generator — tests appear here as they are compiled from the map."
              : "Nothing generated yet."}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {rows.map((row, i) => {
              const id = `${row.file ?? row.name}-${i}`;
              const open = expanded === id;
              // The first unverdicted row of that name, not every row of it:
              // one suite can hold two scenarios called the same thing, and
              // only one of them is on the page.
              const live = i === activeIndex;
              return (
                <li
                  key={id}
                  className="overflow-hidden rounded-md border border-rule bg-paper"
                >
                  <button
                    type="button"
                    onClick={() => setExpanded(open ? null : id)}
                    aria-expanded={open}
                    disabled={!row.code}
                    className="flex w-full items-baseline gap-2 px-2.5 py-2 text-left hover:bg-hush disabled:hover:bg-transparent"
                  >
                    <span
                      aria-hidden
                      className={`w-3 shrink-0 text-center text-xs ${
                        row.status ? VERDICT_TONE[row.status] ?? "text-muted" : "text-muted/40"
                      }`}
                    >
                      {row.status ? (
                        VERDICT_MARK[row.status] ?? "·"
                      ) : live ? (
                        <span
                          aria-hidden
                          className="inline-block size-1.5 animate-pulse rounded-full bg-live align-middle"
                        />
                      ) : (
                        "○"
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs leading-snug text-ink">
                        {row.name}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-muted">
                        {live
                          ? `${
                              row.steps !== null
                                ? `${row.steps} step${row.steps === 1 ? "" : "s"} · `
                                : ""
                            }replaying against the app now`
                          : row.file
                            ? `${row.file}.spec.ts`
                            : row.steps !== null
                              ? `${row.steps} step${row.steps === 1 ? "" : "s"} · compiled`
                              : "compiled"}
                        {row.origin && row.origin !== "map" && ` · ${row.origin}`}
                      </span>
                    </span>
                    {row.status && (
                      <span
                        className={`shrink-0 text-[10px] ${
                          VERDICT_TONE[row.status] ?? "text-muted"
                        }`}
                      >
                        {row.status}
                      </span>
                    )}
                  </button>

                  {open && row.code && (
                    <div className="border-t border-rule">
                      <div className="flex items-center gap-2 border-b border-rule px-2.5 py-1.5">
                        {runId !== null && row.file && (
                          <a
                            href={api.specUrl(runId, row.file, version?.label)}
                            className="text-[11px] text-muted hover:text-ink"
                          >
                            ↓ this file
                          </a>
                        )}
                        <button
                          type="button"
                          onClick={() =>
                            void navigator.clipboard?.writeText(row.code)
                          }
                          className="ml-auto text-[11px] text-muted hover:text-ink"
                        >
                          Copy
                        </button>
                      </div>
                      <pre className="max-h-72 overflow-auto bg-hush px-2.5 py-2 font-mono text-[10px] leading-relaxed text-ink">
                        {row.code}
                      </pre>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {suite && suite.versions.length > 1 && (
          <div className="mt-3 rounded-md border border-rule bg-paper p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-muted">
              History
            </p>
            <ul className="mt-1.5 space-y-1">
              {suite.versions.map((v) => (
                <li key={v.label} className="flex items-baseline gap-2 text-[11px]">
                  <span
                    className={`font-mono ${
                      v.label === version?.label ? "text-ink" : "text-muted"
                    }`}
                  >
                    {v.label}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-muted">
                    {v.because}
                  </span>
                  {runId !== null && (
                    <a
                      href={api.suiteZipUrl(runId, v.label)}
                      className="shrink-0 text-muted hover:text-ink"
                    >
                      ↓
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/*
        The evidence under the list, unchanged from the rail it replaced. The
        log is what the agent *said*; the transcripts are what it was asked and
        what it answered. Without these the untagged two thirds of the timeline
        is written to the database and shown nowhere.
      */}
      <div className="border-t border-rule">
        {runId !== null && (
          <button
            type="button"
            onClick={() => setTranscripts(true)}
            className="flex w-full items-baseline gap-2 border-b border-rule px-3 py-2 text-left text-[11px] uppercase tracking-wide text-muted hover:text-ink"
          >
            <span aria-hidden>🐜</span>
            {/*
              Named after the agents rather than after the storage. "Transcripts"
              is what is on disk; "which agent did what" is the question, and
              the panel behind this now answers it for the deterministic stages
              too — so the label that sent people looking for a Generator
              transcript that never existed was the label itself.
            */}
            <span>Agents</span>
            <span className="ml-auto normal-case tracking-normal">
              planner · generator · healer
            </span>
          </button>
        )}
        <button
          type="button"
          onClick={() => setLogOpen((v) => !v)}
          aria-expanded={logOpen}
          className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-[11px] uppercase tracking-wide text-muted hover:text-ink"
        >
          <span>{logOpen ? "▾" : "▸"}</span>
          <span>Log</span>
          <span className="ml-auto tabular-nums">{mine.length}</span>
        </button>
        {logOpen && (
          <div className="max-h-64 overflow-y-auto border-t border-rule bg-paper px-3 py-2">
            {mine.map((event) => (
              <div
                key={event.id}
                className="flex gap-2 font-mono text-[11px] leading-relaxed"
              >
                <span className="shrink-0 tabular-nums text-muted">
                  {new Date(
                    event.created_at.endsWith("Z")
                      ? event.created_at
                      : `${event.created_at}Z`,
                  ).toLocaleTimeString([], { hour12: false })}
                </span>
                <span
                  className={
                    event.level === "error" || event.level === "warn"
                      ? "text-fault"
                      : event.level === "decision"
                        ? "text-ink"
                        : "text-muted"
                  }
                >
                  {event.message}
                </span>
              </div>
            ))}
            <div ref={logEnd} />
          </div>
        )}
      </div>

      {transcripts && runId !== null && (
        <TranscriptViewer runId={runId} onClose={() => setTranscripts(false)} />
      )}
    </div>
  );
}
