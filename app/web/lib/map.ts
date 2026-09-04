import dagre from "@dagrejs/dagre";
import type { MapState, MapTransition } from "@/lib/api";

/** Card footprint. Must match the fixed size StateCard renders at. */
export const NODE_W = 220;
export const NODE_H = 176;

/**
 * Where each state sits.
 *
 * Left-to-right rather than top-down: a user flow reads as a sequence, and the
 * action labels on the edges have somewhere to go. Recomputed whenever the node
 * set changes — positions are never persisted, because the graph is rebuilt per
 * run and a saved position for a state that no longer exists is worse than
 * none.
 */
export function layout(
  states: MapState[],
  transitions: MapTransition[],
): Record<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 48, ranksep: 96 });

  for (const state of states) {
    graph.setNode(state.key, { width: NODE_W, height: NODE_H });
  }
  for (const edge of transitions) {
    // A self-loop is the most informative edge in the graph (the app was asked
    // to do something and stayed put) but dagre cannot rank one, and feeding it
    // one shifts every other node. React Flow draws it from the node's own
    // handles instead.
    if (edge.from_key === edge.to_key) continue;
    if (!graph.hasNode(edge.from_key) || !graph.hasNode(edge.to_key)) continue;
    graph.setEdge(edge.from_key, edge.to_key);
  }

  dagre.layout(graph);

  const positions: Record<string, { x: number; y: number }> = {};
  for (const state of states) {
    const node = graph.node(state.key);
    // dagre centres nodes; React Flow positions by top-left corner.
    positions[state.key] = node
      ? { x: node.x - NODE_W / 2, y: node.y - NODE_H / 2 }
      : { x: 0, y: 0 };
  }
  return positions;
}
