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

/**
 * The ants that appear in a map, in a stable order.
 *
 * Sorted rather than first-seen: the colour an ant gets must not depend on the
 * order the API happened to return rows in, or a state's colour would change
 * between two polls of the same finished run. Tags are `w<wave>a<ant>`, which
 * sorts chronologically for the single-digit waves a budget allows.
 */
export function antsIn(
  states: MapState[],
  transitions: MapTransition[],
): string[] {
  const tags = new Set<string>();
  for (const s of states) if (s.found_by) tags.add(s.found_by);
  for (const t of transitions) if (t.found_by) tags.add(t.found_by);
  return [...tags].sort();
}

/** How many distinct hues exist; see `--ant-N` in globals.css. */
const ANT_HUES = 6;

/**
 * The colour for one ant, or null when nothing found it.
 *
 * Null is a real answer and must stay visually distinct: the entry state is
 * recorded before the first wave, and a deterministic crawl has no ants at
 * all. Those render in the neutral rule colour, not in ant 0's.
 *
 * Wraps past six. Two ants sharing a hue is a smaller problem than inventing
 * a seventh colour the palette never agreed to.
 */
export function antColour(tag: string | null, order: string[]): string | null {
  if (!tag) return null;
  const index = order.indexOf(tag);
  if (index < 0) return null;
  return `var(--ant-${index % ANT_HUES})`;
}
