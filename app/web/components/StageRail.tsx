"use client";
import { useEffect, useRef, useState } from "react";
import TranscriptViewer from "@/components/TranscriptViewer";
import { api, type AgentEvent } from "@/lib/api";
import { STAGES } from "@/lib/stages";

const LEVEL_TONE: Record<string, string> = {
  error: "text-fault",
  warn: "text-fault",
  decision: "text-ink",
  info: "text-muted",
};

const clock = (iso: string) => {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour12: false });
};

/**
 * Drop a progress line once its own result has arrived.
 *
 * The Run stage emits twice per scenario -- "3/8 replaying follow the Home
 * link" before the replay and "follow the Home link: passed" after it. Both are
 * right at the time they are written: the first is the only line that says what
 * is on the page right now, and the second is the verdict. Keeping both once
 * the run is over doubles the card and says nothing twice.
 *
 * Matched on the message rather than flagged on the stage, because the shape is
 * what makes a line superseded -- a stage that never emits it is unaffected,
 * and the scenario still being replayed keeps its line, which is the point.
 */
const REPLAYING = /^\d+\/\d+ replaying (.+)$/;

const condense = (events: AgentEvent[]) =>
  events.filter((event, i) => {
    const started = REPLAYING.exec(event.message);
    if (!started) return true;
    const verdict = `${started[1]}: `;
    return !events.slice(i + 1).some((later) => later.message.startsWith(verdict));
  });

/**
 * The agent prefixes its headline with the stage it belongs to ("plan: 3 flows
 * across 6 states") because the timeline has no columns. The card does, and the
 * title is already the word — so drop it rather than print it twice.
 */
const trim = (message: string, title: string) => {
  const head = `${title.toLowerCase()}: `;
  return message.toLowerCase().startsWith(head) ? message.slice(head.length) : message;
};

/**
 * The brief's five must-haves, filling in as the meta-agent advances.
 *
 * Reads the same `Event.surface` seam the widget board reads — the agent names
 * what deserves attention and never learns that a rail exists. A surface with
 * no stage falls through to the log at the bottom, which is the only place an
 * untagged line is visible now that the widget board is not on this screen.
 *
 * The rail is run-scoped like the map, even though the events endpoint is
 * session-scoped: a stage card describing a previous run beside the current
 * run's map is worse than an empty card.
 */
export default function StageRail({
  sessionId,
  runId,
  running,
}: {
  sessionId: number;
  runId: number | null;
  running: boolean;
}) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [logOpen, setLogOpen] = useState(false);
  const [transcripts, setTranscripts] = useState(false);
  const logEnd = useRef<HTMLDivElement>(null);

  // A stage advances in seconds; three seconds of latency on a five-second
  // stage is the difference between watching an agent work and reading its
  // minutes. Idle sessions do not need the same rate.
  const period = running ? 1000 : 4000;

  // Reset during render rather than in an effect -- the console's idiom for
  // this (see SessionView): an effect would let one frame paint the previous
  // session's stages.
  const [seenSession, setSeenSession] = useState(sessionId);
  if (sessionId !== seenSession) {
    setSeenSession(sessionId);
    setEvents([]);
  }

  /*
   * The high-water mark, tracked outside the polling closure. `period` changes
   * the moment a run starts, which re-runs the effect -- and a `let after = 0`
   * inside it re-fetched the whole session and appended every event a second
   * time (React's duplicate-key error, and every stage line printed twice).
   *
   * Derived from the events themselves rather than assigned by the fetch, so
   * clearing the list clears the cursor with it. The merge below still refuses
   * ids it already holds: a cursor is an optimisation, not the invariant.
   */
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
        .catch(() => {
          // API down mid-session: keep what we have and try again next tick.
        });
    poll();
    const timer = setInterval(poll, period);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId, period]);

  const mine = events.filter((e) => e.run_id === runId);

  useEffect(() => {
    if (logOpen) logEnd.current?.scrollIntoView({ block: "nearest" });
  }, [logOpen, mine.length]);

  // The furthest stage that has said anything: while the run is live that one
  // is working, and every stage above it is done rather than merely non-empty.
  const reached = STAGES.reduce(
    (last, stage, i) =>
      mine.some((e) => e.surface && stage.surfaces.includes(e.surface)) ? i : last,
    -1,
  );

  return (
    <div className="flex h-full flex-col border-l border-rule bg-hush">
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {STAGES.map((stage, i) => {
          const lines = condense(
            mine.filter((e) => e.surface && stage.surfaces.includes(e.surface)),
          );
          const shown = stage.accumulate ? lines : lines.slice(-1);
          const live = running && i === reached;
          // A stage the run passed *through*. Emptiness is the disclosure here:
          // ticking a card that never said anything is how the old rail ended
          // up claiming a run stage had happened when nothing had reported.
          const done = lines.length > 0 && (i < reached || !running);

          return (
            <section
              key={stage.title}
              className={`mb-3 rounded-md border bg-paper p-3 ${
                live ? "border-live/60" : "border-rule"
              }`}
            >
              <header className="flex items-baseline gap-2">
                <span className="text-[11px] text-muted">{stage.ordinal}</span>
                <h2 className="text-xs font-medium uppercase tracking-wide text-ink">
                  {stage.title}
                </h2>
                <span className="ml-auto text-[11px] tabular-nums text-muted">
                  {live && <span className="mr-1 text-live">● working</span>}
                  {done && <span className="mr-1 text-live">✓</span>}
                  {lines.length === 0
                    ? running && i > reached
                      ? "queued"
                      : "pending"
                    : stage.accumulate
                      ? `${lines.length}`
                      : clock(lines[lines.length - 1].created_at)}
                </span>
              </header>

              {lines.length === 0 ? (
                <p className="mt-1.5 text-[11px] italic leading-snug text-muted">
                  {stage.waiting}
                </p>
              ) : (
                <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto pr-1">
                  {shown.map((event) => (
                    <li
                      key={event.id}
                      className={`break-words text-xs leading-snug ${
                        LEVEL_TONE[event.level] ?? "text-muted"
                      }`}
                    >
                      {trim(event.message, stage.title)}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      {/*
        Everything the agent said, tagged or not. The six cards are the summary
        the brief asks for; this is the evidence under them, and without it the
        untagged two-thirds of the timeline — every locator it tried, every
        state it crawled — is written to the database and shown nowhere.
      */}
      {/*
        Two different questions, so two controls. The log is what the agent
        *said*; the transcripts are what it was asked and what it answered --
        the prompts, the tool calls and the tool results underneath every line
        in the log.
      */}
      <div className="border-t border-rule">
        {runId !== null && (
          <button
            type="button"
            onClick={() => setTranscripts(true)}
            className="flex w-full items-baseline gap-2 border-b border-rule px-3 py-2 text-left text-[11px] uppercase tracking-wide text-muted hover:text-ink"
          >
            <span aria-hidden>🐜</span>
            <span>Transcripts</span>
            <span className="ml-auto normal-case tracking-normal">
              prompts &amp; replies
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
              <div key={event.id} className="flex gap-2 font-mono text-[11px] leading-relaxed">
                <span className="shrink-0 tabular-nums text-muted">
                  {clock(event.created_at)}
                </span>
                <span className={LEVEL_TONE[event.level] ?? "text-muted"}>
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
