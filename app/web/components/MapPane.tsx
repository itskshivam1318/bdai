"use client";
import {
  Background,
  Controls,
  Panel,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import MapProgress from "@/components/MapProgress";
import StateCard from "@/components/StateCard";
import StateDetail from "@/components/StateDetail";
import { api, type MapState, type WorldMapPayload } from "@/lib/api";
import { AttachedStates } from "@/lib/attached";
import { antColour, antsIn, layout } from "@/lib/map";

/**
 * The graph one run discovered.
 *
 * Clicking a state attaches it to the chat; double-clicking opens what it
 * actually contains. Click was already spoken for, and the card has no room
 * for a second hit target -- at the zoom a ten-state map opens at, a button
 * inside it would be a few pixels across.
 *
 * Edges are tinted by the ant that walked them, so the graph shows the routes
 * each one took. Nodes are left alone: their border already carries the
 * verdict, and two meanings on the same pixels would make the map lie about a
 * test result. See --ant-N in globals.css.
 *
 * The
 * selection is owned by `SessionView` and passed in, because the thing that
 * consumes it -- the chat bar -- is a sibling of this pane, not a child: the
 * map has to be able to say "this one" to a control it does not contain.
 *
 * Polls rather than streams: `store.save` is incremental and writes after every
 * edge, so re-reading the map every two seconds is how the graph draws itself
 * while the colony is still walking. Positions are recomputed from scratch each
 * time the node set changes and never persisted — see lib/map.ts.
 */
export default function MapPane({
  runId,
  selectedKeys,
  onToggleSelect,
}: {
  runId: number | null;
  /** `AppState.key` values currently attached to the chat. */
  selectedKeys: string[];
  onToggleSelect: (state: MapState) => void;
}) {
  const [payload, setPayload] = useState<WorldMapPayload | null>(null);
  // The state whose panel is open, by key rather than by object: a poll
  // replaces every state object every two seconds, and holding one would pin
  // the panel to a stale copy that stops matching the map behind it.
  const [detailKey, setDetailKey] = useState<string | null>(null);

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

  const { nodes, edges, ants } = useMemo(() => {
    // A payload belongs to the run it was fetched for. Rendering run A's
    // states under run B's identity is worse than rendering nothing: the nodes
    // look authoritative, carry real screenshots, and are simply the wrong
    // application state. `.catch()` swallows a failed fetch and a brand-new run
    // has not written its first checkpoint yet, so "until the next poll" is not
    // a bound worth relying on.
    if (!payload || payload.run_id !== runId)
      return { nodes: [], edges: [], ants: [] as string[] };
    const positions = layout(payload.states, payload.transitions);
    const ants = antsIn(payload.states, payload.transitions);
    return {
      ants,
      nodes: payload.states.map<Node>((state) => ({
        id: state.key,
        type: "state",
        position: positions[state.key],
        data: { state, ants },
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
        style: {
          strokeWidth: edge.mutating ? 2 : 1,
          // Untinted when no ant walked it, which is what a deterministic
          // crawl produces -- a grey edge is a real answer, not a missing one.
          ...(antColour(edge.found_by, ants)
            ? { stroke: antColour(edge.found_by, ants) as string }
            : {}),
        },
        labelStyle: { fontSize: 10 },
      })),
    };
  }, [payload, runId]);

  const nodeTypes = useMemo(() => ({ state: StateCard }), []);

  // Attachment reaches the cards through context rather than through node
  // `data` -- see lib/attached.tsx. Touching `data` would change the `nodes`
  // array on every click, and xyflow re-syncs from that array, so every state
  // anyone had dragged would jump back to its computed position.
  const attached = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  // Attaching on click and not on a button inside the card: at the zoom a
  // ten-state map opens at, the card is small enough that a hit target inside
  // it would be a few pixels. Dragging still works -- xyflow fires this only
  // when the pointer did not move.
  const handleNodeClick = useCallback(
    // The whole state, not the key: the chip above the message box has to show
    // a name, and this is the only place that already holds one. Passing the
    // key would make `SessionView` re-fetch the map to render a label.
    (_: React.MouseEvent, node: Node) =>
      onToggleSelect((node.data as { state: MapState }).state),
    [onToggleSelect],
  );

  // Derived, never stored: when the run changes, the key stops matching any
  // state and the panel closes on its own. An effect chasing `runId` to clear
  // it would be the same thing with a frame of the wrong panel first.
  const detail =
    payload && payload.run_id === runId && detailKey
      ? (payload.states.find((s) => s.key === detailKey) ?? null)
      : null;

  // xyflow fires two clicks before this, so attachment toggles on and off and
  // lands where it started. That is the reason this can coexist with
  // `handleNodeClick` without either of them knowing about the other.
  const handleNodeDoubleClick = (_: React.MouseEvent, node: Node) =>
    setDetailKey((current) => (current === node.id ? null : node.id));

  if (runId === null) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        Start a run to map this application.
      </div>
    );
  }

  // Switching runs replaces the node set but keeps the old viewport otherwise,
  // which leaves the new map clipped -- keying on runId forces a fresh mount so
  // fitView re-fits for the run now showing.
  return (
    <AttachedStates.Provider value={attached}>
      <div className="flex h-full min-w-0">
        <div className="min-w-0 flex-1">
          <ReactFlow
            key={runId}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
            proOptions={{ hideAttribution: true }}
            fitView
            fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
          >
            <Background />
            <Controls />
            {/* Top-left is the only free corner: Controls own bottom-left. */}
            <Panel position="top-left">
              <MapProgress runId={runId} />
            </Panel>
          </ReactFlow>
        </div>
        {detail && (
          <StateDetail
            state={detail}
            transitions={payload?.transitions ?? []}
            states={payload?.states ?? []}
            ants={ants}
            runId={runId}
            onClose={() => setDetailKey(null)}
          />
        )}
      </div>
    </AttachedStates.Provider>
  );
}
