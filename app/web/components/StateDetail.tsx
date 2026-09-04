"use client";
import { useEffect } from "react";
import { artifactUrl, type MapState, type MapTransition, type Verdict } from "@/lib/api";
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
  onClose,
}: {
  state: MapState;
  /** Every edge in the run, not just this state's -- filtered here. */
  transitions: MapTransition[];
  /** Every state, so an edge's destination can be named rather than hashed. */
  states: MapState[];
  ants: string[];
  onClose: () => void;
}) {
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

      <Section title="Actions" count={state.actions.length}>
        {state.actions.length ? (
          <ul className="space-y-0.5">
            {state.actions.map((action) => (
              <li key={action} className="break-all font-mono text-[11px] text-ink">
                {action}
              </li>
            ))}
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
