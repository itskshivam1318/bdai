"use client";
import { Handle, Position, useStore, type NodeProps } from "@xyflow/react";
import { artifactUrl, type MapState, type Verdict } from "@/lib/api";
import { NODE_H, NODE_W } from "@/lib/map";

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

export type StateNodeData = { state: MapState };

export default function StateCard({ data }: NodeProps) {
  const { state } = data as unknown as StateNodeData;
  const compact = useStore((s) => s.transform[2] < COMPACT_BELOW);
  const mark = VERDICT[state.verdict ?? "untested"];
  const name = state.label ?? state.title ?? state.url;

  const frame = `rounded-md border bg-paper ${
    mark.dashed ? "border-dashed" : "border-solid"
  } ${state.verdict ? "border-current" : "border-rule"} ${mark.tone}`;

  if (compact) {
    return (
      <div className={`${frame} px-3 py-2`} style={{ width: NODE_W }}>
        <Handle type="target" position={Position.Left} />
        <span className="text-ink text-sm">{name}</span>
        <span className="ml-2">{mark.glyph}</span>
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
          <span className="truncate text-sm text-ink">{name}</span>
          <span className="ml-auto text-sm">{mark.glyph}</span>
        </div>
        <div className="mt-0.5 text-[11px] text-muted">
          {state.fields.length} input{state.fields.length === 1 ? "" : "s"} ·{" "}
          {state.actions.length} action{state.actions.length === 1 ? "" : "s"}
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
