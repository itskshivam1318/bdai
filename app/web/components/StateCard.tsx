"use client";
import { Handle, Position, useStore, type NodeProps } from "@xyflow/react";
import { artifactUrl, type MapState, type Verdict } from "@/lib/api";
import { useAttached } from "@/lib/attached";
import { antColour, NODE_H, NODE_W } from "@/lib/map";

/**
 * Below this zoom a thumbnail is unreadable, so the card becomes a chip.
 *
 * Calibrated against what `fitView` actually produces, not against a guess: a
 * 9-state map opens at ~0.5 and a 25-state one far lower. At 0.6 even a small
 * map opened as chips and the screenshots -- the most useful thing on the card
 * -- never appeared unless someone zoomed in by hand.
 */
const COMPACT_BELOW = 0.4;

const VERDICT: Record<Verdict | "untested", { tone: string; dashed: boolean; glyph: string }> = {
  passed: { tone: "text-live", dashed: false, glyph: "✓" },
  healed: { tone: "text-live", dashed: true, glyph: "↻" },
  defect: { tone: "text-fault", dashed: false, glyph: "✗" },
  escalate: { tone: "text-fault", dashed: true, glyph: "⚠" },
  untested: { tone: "text-muted", dashed: false, glyph: "·" },
};

/** `ants` is the run's ant order, which is what turns a tag into a hue. */
export type StateNodeData = { state: MapState; ants: string[] };

export default function StateCard({ data }: NodeProps) {
  const { state, ants } = data as unknown as StateNodeData;
  // Not from `data`: see lib/attached.tsx for why attachment must not travel
  // on the node.
  const attached = useAttached(state.key);
  const compact = useStore((s) => s.transform[2] < COMPACT_BELOW);
  const mark = VERDICT[state.verdict ?? "untested"] ?? VERDICT.untested;
  const name = state.label ?? state.title ?? state.url;
  // Null when no ant found this -- true of the entry state and of every state
  // in a model-free crawl -- and that reads as "no chip", not as a grey one.
  const tint = antColour(state.found_by, ants ?? []);

  /*
   * Attachment is drawn as a ring outside the border, never by recolouring it.
   * The border is already saying something — solid/dashed and the verdict
   * tone are how a healed state is told from a defect — and a second meaning
   * on the same pixels would make the map lie about test results the moment
   * someone clicked a node.
   */
  const ring = attached ? " ring-2 ring-ink ring-offset-2 ring-offset-paper" : "";

  const frame = `rounded-md border bg-paper cursor-pointer ${
    mark.dashed ? "border-dashed" : "border-solid"
  } ${state.verdict ? "border-current" : "border-rule"} ${mark.tone}${ring}`;

  if (compact) {
    return (
      <div className={`${frame} px-3 py-2`} style={{ width: NODE_W }}>
        <Handle type="target" position={Position.Left} />
        <span className="text-ink text-sm">{name}</span>
        <span className="ml-2">{mark.glyph}</span>
        {tint && (
          <span
            aria-hidden
            title={`Found by ant ${state.found_by}`}
            className="ml-1 inline-block size-1.5 rounded-full align-middle"
            style={{ background: tint }}
          />
        )}
        {attached && <span className="ml-1 text-ink">@</span>}
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  return (
    <div className={`${frame} overflow-hidden`} style={{ width: NODE_W, height: NODE_H }}>
      <Handle type="target" position={Position.Left} />
      <div className="h-24 bg-hush">
        {state.screenshot ? (
          // artifacts are served by the API on another origin; next/image
          // would need a loader config for a picture that is already the
          // right size.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={artifactUrl(state.screenshot)}
            alt={`Screenshot of ${name}`}
            className="h-24 w-full object-cover object-top"
          />
        ) : (
          <div className="flex h-24 items-center justify-center text-xs text-muted">
            no capture
          </div>
        )}
      </div>
      <div className="px-2.5 py-2">
        <div className="flex items-baseline gap-2">
          {/* The same glyph the chat chips use, so "in context" reads the
              same way on the map as it does above the message box. */}
          {attached && <span className="text-sm text-ink">@</span>}
          <span className="truncate text-sm text-ink">{name}</span>
          <span className="ml-auto text-sm">{mark.glyph}</span>
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
          <span>
            {state.fields.length} input{state.fields.length === 1 ? "" : "s"} ·{" "}
            {state.actions.length} action{state.actions.length === 1 ? "" : "s"}
          </span>
          {/* Colour and the emoji together, not colour alone: six hues are not
              six things anyone can name, and the tag is what the timeline and
              the detail panel also say. */}
          {tint && (
            <span className="ml-auto shrink-0" style={{ color: tint }}>
              🐜 {state.found_by}
            </span>
          )}
        </div>
        {state.fields.length > 0 && (
          <div className="mt-1 truncate text-[11px] text-muted">
            {state.fields.map(([, fieldName]) => fieldName).join("  ")}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
