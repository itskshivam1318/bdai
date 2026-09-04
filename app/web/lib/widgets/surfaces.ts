import type { CanvasNodeRow } from "@/lib/api";
import { WIDGETS_BY_TYPE } from "./registry";

/**
 * The agent↔canvas contract, frontend half.
 *
 * The agent emits an Event with a `surface` — a semantic key naming what
 * deserves attention. It never learns that widgets exist. This table turns that
 * intent into a widget type, and `placeWidget` decides where it lands.
 *
 * Adding a surface is one entry here plus one in `registry.ts`. A surface with
 * no entry is ignored (it stays an ordinary log line in the timeline), so the
 * agent can emit surfaces ahead of the widgets that render them.
 */
export type SurfaceDef = {
  /** Must match a `type` in registry.ts. */
  widgetType: string;
  /** Overrides the widget's default size when this surface opens it. */
  size?: [number, number];
  /**
   * One widget per session, reused when the surface fires again (a plan gets
   * revised, it doesn't get a second panel). False means every event opens
   * another — right for heal diffs, wrong for a report.
   */
  singleton?: boolean;
};

export const SURFACES: Record<string, SurfaceDef> = {
  // Wired to widgets that exist today.
  run: { widgetType: "runs", size: [280, 200], singleton: true },
  timeline: { widgetType: "events", size: [320, 240], singleton: true },
  evidence: { widgetType: "screenshot", size: [280, 240] },

  // TODO: the pipeline surfaces from docs/problem/statement.md. Each needs a
  // widget in registry.ts before it renders; until then the agent may emit them
  // freely and they fall through to the timeline.
  // plan     — human-readable test plan          (must-have 2)
  // coverage — gaps found before generation      (must-have 3)
  // suite    — generated tests, selectors valid  (must-have 4)
  // heal     — old locator → new, with reasoning (must-have 5)
  // defect   — script issue vs. genuine bug      (must-have 5, bonus)
  // report   — final test quality report         (must-have 6)
};

/** A node the canvas already holds, as far as placement cares. */
export type PlacedNode = Pick<CanvasNodeRow, "x" | "y" | "width" | "height">;

/**
 * Where a newly surfaced widget lands on the canvas.
 *
 * Scans left-to-right, top-to-bottom on a coarse grid and takes the first cell
 * that doesn't overlap anything already placed. Deliberately dumb: it never
 * moves an existing widget, so a canvas the user has arranged by hand stays
 * arranged.
 */
export function placeWidget(
  existing: PlacedNode[],
  size: [number, number],
): { x: number; y: number } {
  const [w, h] = size;
  const GAP = 24;
  const COLUMNS = 4;

  const overlaps = (x: number, y: number) =>
    existing.some((n) => {
      const nw = n.width ?? 280;
      const nh = n.height ?? 200;
      return x < n.x + nw + GAP && x + w + GAP > n.x && y < n.y + nh + GAP && y + h + GAP > n.y;
    });

  for (let row = 0; row < 20; row++) {
    for (let col = 0; col < COLUMNS; col++) {
      const x = GAP + col * (w + GAP);
      const y = GAP + row * (h + GAP);
      if (!overlaps(x, y)) return { x, y };
    }
  }
  // Canvas is full enough that tidiness stopped mattering.
  return { x: GAP + Math.random() * 400, y: GAP + Math.random() * 400 };
}

/**
 * Turns new events into the widgets they ask for.
 *
 * Idempotency is the whole job here: the originating event id is written into
 * the node's config, so polling the same event twice — or reloading the page —
 * can't clone a widget. Returns what should be created, and creates nothing
 * itself.
 */
export function widgetsToOpen(
  events: { id: number; surface: string | null; ref: string | null }[],
  existingNodes: CanvasNodeRow[],
): { widget_type: string; x: number; y: number; width: number; height: number; config: string }[] {
  const seenEventIds = new Set<number>();
  const seenSurfaces = new Set<string>();
  for (const node of existingNodes) {
    try {
      const cfg = JSON.parse(node.config) as { eventId?: number; surface?: string };
      if (cfg.eventId !== undefined) seenEventIds.add(cfg.eventId);
      if (cfg.surface !== undefined) seenSurfaces.add(cfg.surface);
    } catch {
      // A hand-added widget has no surface metadata. Nothing to dedupe on.
    }
  }

  const placed: PlacedNode[] = existingNodes.map((n) => ({
    x: n.x,
    y: n.y,
    width: n.width,
    height: n.height,
  }));
  const created = [];

  for (const event of events) {
    if (!event.surface) continue;
    const def = SURFACES[event.surface];
    if (!def) continue;
    if (seenEventIds.has(event.id)) continue;
    if (def.singleton && seenSurfaces.has(event.surface)) continue;

    const widget = WIDGETS_BY_TYPE.get(def.widgetType);
    if (!widget) continue;

    const size = def.size ?? widget.size;
    const { x, y } = placeWidget(placed, size);
    placed.push({ x, y, width: size[0], height: size[1] });
    seenEventIds.add(event.id);
    seenSurfaces.add(event.surface);

    created.push({
      widget_type: def.widgetType,
      x,
      y,
      width: size[0],
      height: size[1],
      config: JSON.stringify({ eventId: event.id, surface: event.surface, ref: event.ref }),
    });
  }
  return created;
}
