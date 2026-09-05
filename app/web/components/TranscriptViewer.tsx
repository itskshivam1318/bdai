"use client";
import { useEffect, useState } from "react";
import { api, type Transcript, type TranscriptRow } from "@/lib/api";

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
          <ul className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {rows === null && <li className="p-2 text-xs text-muted">loading…</li>}
            {rows?.length === 0 && (
              <li className="p-2 text-[11px] italic leading-snug text-muted">
                Nothing written yet. A transcript appears when an ant finishes —
                a run with no model never writes one.
              </li>
            )}
            {rows?.map((r) => (
              <li key={r.name}>
                <button
                  type="button"
                  onClick={() => setName(r.name)}
                  className={`w-full rounded px-2 py-1.5 text-left text-xs ${
                    r.name === name ? "bg-paper text-ink" : "text-muted hover:bg-paper"
                  }`}
                >
                  <span className="text-ink">{r.role}</span>
                  {r.label && <span className="ml-1 font-mono">{r.label}</span>}
                  <span className="ml-2 tabular-nums text-[11px] text-muted">
                    {new Date(r.written_at).toLocaleTimeString([], { hour12: false })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto">
          <header className="sticky top-0 flex items-baseline gap-2 border-b border-rule bg-paper px-4 py-2.5">
            <h2 className="text-sm font-medium text-ink">
              {row ? `${row.role}${row.label ? ` @ ${row.label}` : ""}` : "Transcripts"}
            </h2>
            {body && (
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
          {row && !body && !error && (
            <p className="px-4 py-3 text-xs text-muted">loading…</p>
          )}

          {body && (
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
