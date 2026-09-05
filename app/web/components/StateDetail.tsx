"use client";
import { useEffect, useState } from "react";
import TranscriptViewer from "@/components/TranscriptViewer";
import {
  api,
  artifactUrl,
  type MapState,
  type MapTransition,
  type Verdict,
} from "@/lib/api";
import { antColour } from "@/lib/map";

const VERDICT_GLYPH: Record<Verdict, { glyph: string; tone: string }> = {
  passed: { glyph: "✓", tone: "text-live" },
  healed: { glyph: "↻", tone: "text-live" },
  defect: { glyph: "✗", tone: "text-fault" },
  escalate: { glyph: "⚠", tone: "text-fault" },
};

/**
 * Everything one state knows about itself.
 *
 * The card can say "17 actions" and no more -- it is 220px wide and most of
 * that is a screenshot. But the action list is the crawl's actual evidence:
 * what the agent found to press, and therefore what the suite could ever be
 * built from. A count of it is not the same as it.
 *
 * A panel rather than an expanding card, because a 17-action node would be
 * taller than the viewport and would shove the graph layout sideways every
 * time one was opened.
 */
export default function StateDetail({
  state,
  transitions,
  states,
  ants,
  runId,
  onClose,
}: {
  state: MapState;
  /** Every edge in the run, not just this state's -- filtered here. */
  transitions: MapTransition[];
  /** Every state, so an edge's destination can be named rather than hashed. */
  states: MapState[];
  ants: string[];
  /** The run this state belongs to — an ant is dispatched into it. */
  runId: number;
  onClose: () => void;
}) {
  /*
   * Sending an ant from here is the one thing on this panel that changes the
   * application rather than describing it, so it reports its own outcome: the
   * API answers in milliseconds and the ant takes a minute, and a button that
   * went quiet would look broken for the whole of it.
   */
  const [note, setNote] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [transcripts, setTranscripts] = useState(false);

  async function send(action: string | null) {
    setSending(action ?? "");
    setFailed(null);
    setSent(null);
    try {
      await api.dispatchAnt(runId, {
        state_key: state.key,
        action,
        instruction: note.trim() || null,
      });
      setSent(
        action
          ? `sent — taking ${action}. Watch the rail.`
          : "sent. Watch the rail; new states land on the map.",
      );
      setNote("");
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(null);
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const nameOf = (key: string) => {
    const found = states.find((s) => s.key === key);
    return found?.label ?? found?.title ?? key.slice(0, 8);
  };

  const outgoing = transitions.filter((t) => t.from_key === state.key);
  const tint = antColour(state.found_by, ants);
  const title = state.label ?? state.title ?? state.url;

  return (
    <aside
      aria-label={`Details for ${title}`}
      className="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-rule bg-hush"
    >
      <div className="flex items-start gap-2 border-b border-rule px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium text-ink">{title}</h2>
          <a
            href={state.url}
            target="_blank"
            rel="noreferrer"
            className="block truncate font-mono text-[11px] text-muted hover:text-ink"
          >
            {state.url}
          </a>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="rounded p-1 text-muted hover:bg-paper hover:text-ink"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        {/* Null is not "unknown" -- it means no ant found this, which is true
            of the entry state and of every state in a model-free crawl. */}
        <span
          className="rounded border px-1.5 py-0.5 text-[11px]"
          style={
            tint
              ? { color: tint, borderColor: tint }
              : { color: "var(--muted)", borderColor: "var(--rule)" }
          }
        >
          {state.found_by ? `🐜 ${state.found_by}` : "entry · no ant"}
        </span>
        {state.is_entry && (
          <span className="rounded border border-rule px-1.5 py-0.5 text-[11px] text-muted">
            entry
          </span>
        )}
        {state.verdict && (
          <span className={`text-[11px] ${VERDICT_GLYPH[state.verdict].tone}`}>
            {VERDICT_GLYPH[state.verdict].glyph} {state.verdict}
          </span>
        )}
      </div>

      {state.screenshot && (
        <div className="px-3 pb-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={artifactUrl(state.screenshot)}
            alt={`Screenshot of ${title}`}
            className="w-full rounded border border-rule"
          />
        </div>
      )}

      {/*
        Every action the crawl found, and the ones with no edge beside them in
        "Goes to" are the unexplored ones — which is exactly where sending an
        ant is worth doing. Walked is marked rather than filtered out: an action
        can be worth re-walking after the app changes.
      */}
      <Section title="Actions" count={state.actions.length}>
        {state.actions.length ? (
          <ul className="space-y-0.5">
            {state.actions.map((action) => {
              const walked = outgoing.some((t) => t.action === action);
              return (
                <li key={action} className="group flex items-baseline gap-1.5">
                  <span
                    aria-hidden
                    title={walked ? "an ant has walked this" : "never walked"}
                    className={walked ? "text-live" : "text-muted"}
                  >
                    {walked ? "·" : "○"}
                  </span>
                  <span className="min-w-0 flex-1 break-all font-mono text-[11px] text-ink">
                    {action}
                  </span>
                  <button
                    type="button"
                    disabled={sending !== null}
                    onClick={() => send(action)}
                    title={`Send an ant from here, taking ${action}`}
                    aria-label={`Send an ant taking ${action}`}
                    className="shrink-0 rounded px-1 text-[11px] text-muted opacity-0 hover:bg-paper hover:text-ink focus:opacity-100 group-hover:opacity-100 disabled:opacity-40"
                  >
                    {sending === action ? "…" : "🐜→"}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty>nothing here could be pressed</Empty>
        )}
      </Section>

      <Section title="Fields" count={state.fields.length}>
        {state.fields.length ? (
          <ul className="space-y-0.5">
            {state.fields.map(([role, fieldName], i) => (
              <li key={`${role}:${fieldName}:${i}`} className="text-[11px]">
                <span className="text-muted">{role}</span>{" "}
                <span className="text-ink">{fieldName || "(unnamed)"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>no inputs on this screen</Empty>
        )}
      </Section>

      <Section title="Goes to" count={outgoing.length}>
        {outgoing.length ? (
          <ul className="space-y-1">
            {outgoing.map((t, i) => {
              const edgeTint = antColour(t.found_by, ants);
              return (
                <li key={`${t.action}:${t.to_key}:${i}`} className="text-[11px]">
                  <div className="flex items-baseline gap-1.5">
                    <span
                      aria-hidden
                      className="mt-1 inline-block size-1.5 shrink-0 rounded-full"
                      style={{ background: edgeTint ?? "var(--rule)" }}
                    />
                    <span className="break-all font-mono text-ink">{t.action}</span>
                  </div>
                  <div className="ml-3 text-muted">
                    →{" "}
                    {/* An edge back to where it started is the most informative
                        one in the graph, so it is named rather than drawn as a
                        destination the reader has to recognise. */}
                    {t.to_key === state.key ? "stayed here" : nameOf(t.to_key)}
                    {t.mutating && <span className="ml-1 text-ink">· sent a request</span>}
                    {t.found_by && <span className="ml-1">· 🐜 {t.found_by}</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty>no recorded way out of this state</Empty>
        )}
      </Section>

      {/*
        The colony picks its own targets; this is the override, and it is at the
        bottom because reading the state comes first. An ant sent from here
        writes into the same run, so what it finds appears on the map behind
        this panel rather than in a graph of its own.
      */}
      <Section title="Send an ant" count={state.actions.length}>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          placeholder="Optional: what should it look for here?"
          className="w-full resize-none rounded border border-rule bg-paper px-2 py-1.5 text-[11px] text-ink outline-none placeholder:text-muted focus:border-ink"
        />
        <div className="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            disabled={sending !== null}
            onClick={() => send(null)}
            className="rounded-md bg-ink px-2.5 py-1 text-[11px] text-paper disabled:opacity-50"
          >
            {sending === "" ? "sending…" : "🐜 Send from here"}
          </button>
          <button
            type="button"
            onClick={() => setTranscripts(true)}
            className="rounded-md border border-rule px-2.5 py-1 text-[11px] text-muted hover:text-ink"
          >
            Transcript
          </button>
        </div>
        <p className="mt-1.5 text-[11px] leading-snug text-muted">
          Or hover an action above and press 🐜→ to send one down that branch.
        </p>
        {sent && <p className="mt-1 text-[11px] text-live">{sent}</p>}
        {failed && <p className="mt-1 text-[11px] text-fault">{failed}</p>}
      </Section>

      {transcripts && (
        <TranscriptViewer
          runId={runId}
          initial={state.key}
          onClose={() => setTranscripts(false)}
        />
      )}
    </aside>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-rule px-3 py-2">
      <h3 className="pb-1 text-[11px] uppercase tracking-wide text-muted">
        {title} <span className="text-muted">({count})</span>
      </h3>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] italic text-muted">{children}</p>;
}
