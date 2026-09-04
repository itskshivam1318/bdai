"use client";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import StateCard from "@/components/StateCard";
import { api, type WorldMapPayload } from "@/lib/api";
import { layout } from "@/lib/map";

/**
 * The graph one run discovered.
 *
 * Polls rather than streams: `store.save` is incremental and writes after every
 * edge, so re-reading the map every two seconds is how the graph draws itself
 * while the colony is still walking. Positions are recomputed from scratch each
 * time the node set changes and never persisted — see lib/map.ts.
 */
export default function MapPane({ runId }: { runId: number | null }) {
  const [payload, setPayload] = useState<WorldMapPayload | null>(null);

  useEffect(() => {
    // No run selected: nothing to poll. The render below never reads
    // `payload` while runId is null, so there is no stale state to clear —
    // and clearing it here synchronously is what react-hooks/set-state-in-effect
    // flags (a direct setState in the effect body, not inside a callback).
    if (runId === null) return;
    let cancelled = false;
    const poll = () =>
      api
        .getMap(runId)
        .then((next) => {
          if (cancelled) return;
          // Keep the previous object when the map has not actually changed.
          // `res.json()` allocates a new object every tick, so assigning it
          // unconditionally reruns the layout and throws away any node the user
          // has dragged -- every two seconds, invisibly. Comparing the
          // serialised form is cheap at this size and is exactly the "did
          // anything change" question being asked.
          setPayload((prev) =>
            prev && JSON.stringify(prev) === JSON.stringify(next) ? prev : next,
          );
        })
        .catch(() => {
          // The run may not have written a map yet. Try again next tick.
        });
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId]);

  const { nodes, edges } = useMemo(() => {
    // A payload belongs to the run it was fetched for. Rendering run A's
    // states under run B's identity is worse than rendering nothing: the nodes
    // look authoritative, carry real screenshots, and are simply the wrong
    // application state. `.catch()` swallows a failed fetch and a brand-new run
    // has not written its first checkpoint yet, so "until the next poll" is not
    // a bound worth relying on.
    if (!payload || payload.run_id !== runId) return { nodes: [], edges: [] };
    const positions = layout(payload.states, payload.transitions);
    return {
      nodes: payload.states.map<Node>((state) => ({
        id: state.key,
        type: "state",
        position: positions[state.key],
        data: { state },
        draggable: true,
      })),
      edges: payload.transitions.map<Edge>((edge, index) => ({
        id: `${edge.from_key}-${index}`,
        source: edge.from_key,
        target: edge.to_key,
        label: edge.action,
        type: "smoothstep",
        // A non-GET fired: heavier line. This is the distinction runner.py
        // classifies on, so it belongs on the picture.
        style: { strokeWidth: edge.mutating ? 2 : 1 },
        labelStyle: { fontSize: 10 },
      })),
    };
  }, [payload, runId]);

  const nodeTypes = useMemo(() => ({ state: StateCard }), []);

  if (runId === null) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        Start a run to map this application.
      </div>
    );
  }

  return (
    // Switching runs replaces the node set but keeps the old viewport
    // otherwise, which leaves the new map clipped -- keying on runId forces a
    // fresh mount so fitView re-fits for the run now showing.
    <ReactFlow
      key={runId}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      proOptions={{ hideAttribution: true }}
      fitView
      fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
    >
      <Background />
      <Controls />
    </ReactFlow>
  );
}
