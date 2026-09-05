"use client";
import { useEffect, useState } from "react";
import {
  api,
  type AgentEvent,
  type Transcript,
  type TranscriptRow,
} from "@/lib/api";
import { AGENTS, agentForRole, type Agent } from "@/lib/agents";

/**
 * What the agents actually said, and what was said back to them.
 *
 * `agents/tracing.py` has always written these — the system prompt, the opening
 * brief, every model turn, every tool call with its arguments and every tool
 * result — to `artifacts/transcripts/run-N/`. Until this they were reachable
 * only by knowing that, and by opening a JSON file by hand. The brief pays for
 * *presenting* the agent's decisions, and a file path is not presenting them.
 *
 * Two panes rather than a list of links: the question is almost never "show me
 * transcript 3", it is "which ant looked at this state, and what did it see" —
 * so the list stays on screen beside the answer.
 *
 * **Grouped by agent, and that changed what the panel can answer.** It used to
 * be a flat list of files named after the role that wrote them, which answers
 * "what conversations happened" and not "what did the Generator do" — and the
 * brief names Planner, Generator and Healer, none of which is a role. Two of
 * the three make no model call at all, so under a flat list they had no rows,
 * and an agent with no rows reads as an agent that did not run. Each one now
 * has a section: its transcripts where it has them, and its *record* — the
 * lines it emitted, with the reason it needs no model — where it does not.
 */
export default function TranscriptViewer({
  runId,
  initial,
  onClose,
}: {
  runId: number;
  /** Open on the transcript for this state key (its 8-char label), if there is one. */
  initial?: string | null;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<TranscriptRow[] | null>(null);
  const [name, setName] = useState<string | null>(null);
  /*
   * The run's own log, fetched here rather than passed in.
   *
   * It is what a deterministic agent leaves behind instead of a transcript, so
   * the panel cannot answer its own question without it — and both call sites
   * (the suite panel and a state's detail) would otherwise have to carry it.
   * One request, on open: this is a modal, not a poll.
   */
  const [events, setEvents] = useState<AgentEvent[]>([]);
  /** Which agent's record is on screen, when it is a record and not a file. */
  const [recordFor, setRecordFor] = useState<string | null>(null);
  // Keyed by the file it came from rather than cleared on every switch: the
  // clear would be a setState inside an effect, and this says the same thing
  // (`loaded.name !== name` *is* "still loading") without one.
  const [loaded, setLoaded] = useState<{ name: string; data: Transcript } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    api
      .listEvents(runId)
      .then((list) => !cancelled && setEvents(list))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => {
    let cancelled = false;
    api
      .listTranscripts(runId)
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        // Newest first in the list, but the ant for the state the panel was
        // opened from wins: that is the question that opened this.
        const wanted = initial
          ? list.filter((r) => r.label && initial.startsWith(r.label)).at(-1)
          : undefined;
        setName((current) => current ?? (wanted ?? list.at(-1))?.name ?? null);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [runId, initial]);

  const row = rows?.find((r) => r.name === name) ?? null;

  /*
   * Every agent, with what this run left under it. Built even for an agent with
   * nothing at all: the empty section is the finding — "the Healer never ran"
   * is an answer, and a list that simply omits it makes that indistinguishable
   * from a panel that cannot show it.
   */
  const groups: { agent: Agent; files: TranscriptRow[]; lines: AgentEvent[] }[] =
    AGENTS.map((agent) => ({
      agent,
      files: (rows ?? []).filter((r) => agentForRole(r.role)?.key === agent.key),
      lines: events.filter(
        (e) => e.surface !== null && agent.surfaces.includes(e.surface),
      ),
    }));
  // A role no row in the table claims still gets listed. The alternative is a
  // transcript that exists on disk, is served by the API, and appears nowhere —
  // which is the bug this panel was widened to fix.
  const orphans = (rows ?? []).filter((r) => agentForRole(r.role) === null);

  const record = recordFor
    ? (groups.find((g) => g.agent.key === recordFor) ?? null)
    : null;

  useEffect(() => {
    if (!row) return;
    let cancelled = false;
    api
      .readTranscript(row.url)
      .then((t) => !cancelled && setLoaded({ name: row.name, data: t }))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [row]);

  const body = loaded && loaded.name === row?.name ? loaded.data : null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink/30 p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Agent transcripts"
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-6xl overflow-hidden rounded-lg border border-rule bg-paper shadow-lg"
      >
        <nav className="flex w-64 shrink-0 flex-col border-r border-rule bg-hush">
          <header className="border-b border-rule px-3 py-2.5 text-xs uppercase tracking-wide text-muted">
            Transcripts · run {runId}
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {rows === null && <p className="p-2 text-xs text-muted">loading…</p>}

            {rows !== null &&
              groups.map(({ agent, files, lines }) => (
                <section key={agent.key} className="mb-2">
                  <h3
                    title={agent.what}
                    className="flex items-baseline gap-1.5 px-2 pb-1 pt-2 text-[10px] uppercase tracking-wide text-muted"
                  >
                    <span className="text-ink">{agent.title}</span>
                    {files.length > 0 && (
                      <span className="tabular-nums">{files.length}</span>
                    )}
                  </h3>

                  {files.map((r) => (
                    <button
                      key={r.name}
                      type="button"
                      onClick={() => {
                        setRecordFor(null);
                        setName(r.name);
                      }}
                      className={`block w-full rounded px-2 py-1.5 text-left text-xs ${
                        r.name === name && !recordFor
                          ? "bg-paper text-ink"
                          : "text-muted hover:bg-paper"
                      }`}
                    >
                      <span className="text-ink">{r.role}</span>
                      {r.label && <span className="ml-1 font-mono">{r.label}</span>}
                      <span className="ml-2 tabular-nums text-[11px] text-muted">
                        {new Date(r.written_at).toLocaleTimeString([], {
                          hour12: false,
                        })}
                      </span>
                    </button>
                  ))}

                  {/*
                    The record, always offered when there are lines — not only
                    as a fallback for an agent with no transcript. A Planner
                    with four ant conversations still emitted the crawl, and
                    "what did it do" and "what did it say to the model" are
                    different questions.
                  */}
                  {lines.length > 0 ? (
                    <button
                      type="button"
                      onClick={() => setRecordFor(agent.key)}
                      className={`block w-full rounded px-2 py-1.5 text-left text-xs ${
                        recordFor === agent.key
                          ? "bg-paper text-ink"
                          : "text-muted hover:bg-paper"
                      }`}
                    >
                      <span aria-hidden className="mr-1">
                        ⚙
                      </span>
                      what it did
                      <span className="ml-2 tabular-nums text-[11px] text-muted">
                        {lines.length} line{lines.length === 1 ? "" : "s"}
                      </span>
                    </button>
                  ) : (
                    files.length === 0 && (
                      <p className="px-2 py-1 text-[11px] italic leading-snug text-muted/70">
                        nothing yet
                      </p>
                    )
                  )}
                </section>
              ))}

            {orphans.length > 0 && (
              <section className="mb-2">
                <h3 className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wide text-muted">
                  Other
                </h3>
                {orphans.map((r) => (
                  <button
                    key={r.name}
                    type="button"
                    onClick={() => {
                      setRecordFor(null);
                      setName(r.name);
                    }}
                    className={`block w-full rounded px-2 py-1.5 text-left text-xs ${
                      r.name === name && !recordFor
                        ? "bg-paper text-ink"
                        : "text-muted hover:bg-paper"
                    }`}
                  >
                    <span className="text-ink">{r.role}</span>
                    {r.label && <span className="ml-1 font-mono">{r.label}</span>}
                  </button>
                ))}
              </section>
            )}
          </div>
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto">
          <header className="sticky top-0 flex items-baseline gap-2 border-b border-rule bg-paper px-4 py-2.5">
            <h2 className="text-sm font-medium text-ink">
              {record
                ? `${record.agent.title} — what it did`
                : row
                  ? `${row.role}${row.label ? ` @ ${row.label}` : ""}`
                  : "Transcripts"}
            </h2>
            {record && (
              <span className="text-[11px] text-muted">{record.agent.what}</span>
            )}
            {!record && body && (
              <span className="text-[11px] text-muted">
                {body.exchanges.length} turn{body.exchanges.length === 1 ? "" : "s"}
              </span>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close transcripts"
              className="ml-auto rounded p-1 text-muted hover:bg-hush hover:text-ink"
            >
              ✕
            </button>
          </header>

          {error && <p className="px-4 py-3 text-xs text-fault">{error}</p>}
          {!record && row && !body && !error && (
            <p className="px-4 py-3 text-xs text-muted">loading…</p>
          )}

          {/*
            An agent's record: why it needs no model, then every line it emitted.
            This is the whole answer for the Generator and the Healer, and it is
            deliberately the same shape as a transcript — an agent that decides
            without asking a model has still decided, and the decisions are the
            thing the brief pays for presenting.
          */}
          {record && (
            <div className="px-4 py-3">
              {record.agent.deterministic && (
                <p className="mb-3 rounded border border-rule bg-hush px-3 py-2 text-[11px] leading-relaxed text-muted">
                  {record.files.length === 0 && (
                    <span className="font-medium text-ink">
                      No model was asked.{" "}
                    </span>
                  )}
                  {record.agent.deterministic}
                </p>
              )}
              {record.lines.length === 0 ? (
                <p className="text-xs italic text-muted">
                  This agent has not reported yet.
                </p>
              ) : (
                <ol className="space-y-0.5">
                  {record.lines.map((line) => (
                    <li
                      key={line.id}
                      className="flex gap-2 font-mono text-[11px] leading-relaxed"
                    >
                      <span className="shrink-0 tabular-nums text-muted">
                        {new Date(
                          line.created_at.endsWith("Z")
                            ? line.created_at
                            : `${line.created_at}Z`,
                        ).toLocaleTimeString([], { hour12: false })}
                      </span>
                      <span
                        className={
                          line.level === "error" || line.level === "warn"
                            ? "text-fault"
                            : line.level === "decision"
                              ? "text-ink"
                              : "text-muted"
                        }
                      >
                        {line.message}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}

          {!record && body && (
            <div className="px-4 py-3">
              {/* Collapsed: the system prompt is the same file for every ant in
                  the run, so it is context rather than content — but it is
                  recorded because `prompts/*.md` changes hourly and a week
                  later "which instructions produced this" is the question. */}
              <details className="mb-3 rounded border border-rule">
                <summary className="cursor-pointer px-3 py-2 text-xs text-muted">
                  System prompt ({body.system.length.toLocaleString()} chars)
                </summary>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-rule px-3 py-2 font-mono text-[11px] leading-relaxed text-ink">
                  {body.system}
                </pre>
              </details>

              <Block label="Input · what it was told" tone="border-rule">
                {body.prompt}
              </Block>

              {body.exchanges.map((exchange, i) => (
                <section key={i} className="mt-3">
                  <h3 className="pb-1 text-[11px] uppercase tracking-wide text-muted">
                    Turn {i + 1}
                  </h3>
                  {exchange.text && (
                    <Block label="Output · what it said" tone="border-live/40">
                      {exchange.text}
                    </Block>
                  )}
                  {exchange.calls.map((call, j) => (
                    <Block key={j} label={`Output · called ${call.name}()`} tone="border-live/40">
                      {JSON.stringify(call.arguments, null, 2)}
                    </Block>
                  ))}
                  {exchange.results.map((result, j) => (
                    <Block key={j} label={`Input · ${result.name}() returned`} tone="border-rule">
                      {result.content}
                    </Block>
                  ))}
                  {!exchange.text &&
                    !exchange.calls.length &&
                    !exchange.results.length && (
                      <p className="text-[11px] italic text-muted">empty turn</p>
                    )}
                </section>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * One side of one exchange. Capped in height rather than truncated: a tool
 * result here is the whole state description the ant was handed, and cutting it
 * off is exactly the information someone opened this to read.
 */
function Block({
  label,
  tone,
  children,
}: {
  label: string;
  tone: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`mt-1.5 rounded border ${tone}`}>
      <div className="border-b border-rule px-3 py-1 text-[11px] text-muted">{label}</div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-ink">
        {children}
      </pre>
    </div>
  );
}
