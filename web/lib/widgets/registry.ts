import EventLogWidget from "./EventLogWidget";
import NoteWidget from "./NoteWidget";
import RunListWidget from "./RunListWidget";
import ScreenshotWidget from "./ScreenshotWidget";
import type { WidgetDef } from "./types";

/**
 * The extension point. To add a widget tomorrow:
 *   1. drop a component in lib/widgets/ taking WidgetProps
 *   2. add one entry here
 * Nothing else — not the canvas, not the backend — needs to change.
 */
export const WIDGETS: WidgetDef[] = [
  {
    type: "runs",
    label: "QA Runs",
    blurb: "Launch a run and watch statuses",
    size: [280, 200],
    Component: RunListWidget,
  },
  {
    type: "events",
    label: "Agent Timeline",
    blurb: "Streamed reasoning for one run",
    size: [300, 220],
    Component: EventLogWidget,
  },
  {
    type: "screenshot",
    label: "Artifact Viewer",
    blurb: "Screenshots and visual diffs",
    size: [280, 240],
    Component: ScreenshotWidget,
  },
  {
    type: "note",
    label: "Note",
    blurb: "Scratch space",
    size: [220, 160],
    Component: NoteWidget,
  },
];

export const WIDGETS_BY_TYPE = new Map(WIDGETS.map((w) => [w.type, w]));
