"use client";
import { NodeResizer, type NodeProps, type Node } from "@xyflow/react";
import { useCallback, useRef } from "react";
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
 */
export default function WidgetNode({ data, selected }: NodeProps<WidgetNodeType>) {
  const def = WIDGETS_BY_TYPE.get(data.widgetType);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setConfig = useCallback(
    (patch: WidgetConfig) => {
      const next = { ...data.config, ...patch };
      // Typing in a widget shouldn't fire a PATCH per keystroke.
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => data.onConfigChange(data.rowId, next), 400);
      data.config = next;
    },
    [data],
  );

  if (!def) {
    return (
      <div className="rounded-lg border border-red-300 bg-red-50 p-2 text-xs text-red-700">
        Unknown widget type “{data.widgetType}”
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
        <def.Component
          nodeId={data.rowId}
          config={data.config}
          setConfig={setConfig}
        />
      </div>
    </div>
  );
}
