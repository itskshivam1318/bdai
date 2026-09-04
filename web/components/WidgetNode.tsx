"use client";
import { NodeResizer, type NodeProps, type Node } from "@xyflow/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { WIDGETS_BY_TYPE } from "@/lib/widgets/registry";
import type { WidgetConfig } from "@/lib/widgets/types";

export type WidgetNodeData = {
  rowId: number;
  widgetType: string;
  config: WidgetConfig;
  onConfigChange: (rowId: number, next: WidgetConfig) => void;
};

export type WidgetNodeType = Node<WidgetNodeData, "widget">;

/**
 * Chrome shared by every widget: title bar, resize handles, and debounced
 * config persistence. Widgets themselves stay ignorant of the canvas.
 *
 * Config lives in local state rather than on the node's `data`, so typing feels
 * instant while the network write lags behind it.
 */
export default function WidgetNode({ data, selected }: NodeProps<WidgetNodeType>) {
  const { rowId, widgetType, onConfigChange } = data;
  const def = WIDGETS_BY_TYPE.get(widgetType);

  const [config, setLocalConfig] = useState<WidgetConfig>(data.config);
  const initialConfig = useRef(data.config);

  const setConfig = useCallback((patch: WidgetConfig) => {
    setLocalConfig((current) => ({ ...current, ...patch }));
  }, []);

  // One PATCH per pause in typing, not one per keystroke. Skipped on mount so
  // loading the canvas doesn't immediately write back what it just read.
  useEffect(() => {
    if (config === initialConfig.current) return;
    const timer = setTimeout(() => onConfigChange(rowId, config), 400);
    return () => clearTimeout(timer);
  }, [config, onConfigChange, rowId]);

  if (!def) {
    return (
      <div className="rounded-lg border border-red-300 bg-red-50 p-2 text-xs text-red-700">
        Unknown widget type “{widgetType}”
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
      <NodeResizer minWidth={180} minHeight={120} isVisible={selected} />
      <div className="cursor-grab border-b border-neutral-100 px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-neutral-500 dark:border-neutral-800">
        {def.label}
      </div>
      <div className="flex-1 overflow-hidden p-2">
        <def.Component nodeId={rowId} config={config} setConfig={setConfig} />
      </div>
    </div>
  );
}
