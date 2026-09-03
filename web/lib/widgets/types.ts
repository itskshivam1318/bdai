import type { ComponentType } from "react";

export type WidgetConfig = Record<string, unknown>;

export type WidgetProps = {
  nodeId: number;
  config: WidgetConfig;
  /** Persists a partial config change back to the API (debounced by the node). */
  setConfig: (patch: WidgetConfig) => void;
};

export type WidgetDef = {
  /** Stable key stored in the DB. Renaming it orphans existing nodes. */
  type: string;
  label: string;
  blurb: string;
  /** Initial pixel size on the canvas. */
  size: [number, number];
  Component: ComponentType<WidgetProps>;
};
