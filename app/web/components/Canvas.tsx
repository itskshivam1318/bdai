"use client";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useNodesState,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import WidgetNode, { type WidgetNodeType } from "@/components/WidgetNode";
import { api, WORKTREE, type CanvasNodeRow } from "@/lib/api";
import { WIDGETS } from "@/lib/widgets/registry";
import type { WidgetConfig } from "@/lib/widgets/types";

function parseConfig(raw: string): WidgetConfig {
  try {
    return JSON.parse(raw) as WidgetConfig;
  } catch {
    return {};
  }
}

export default function Canvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState<WidgetNodeType>([]);
  const [status, setStatus] = useState<"connecting" | "ok" | "down">("connecting");

  const persistConfig = useCallback((rowId: number, next: WidgetConfig) => {
    void api.updateNode(rowId, { config: JSON.stringify(next) });
  }, []);

  const toNode = useCallback(
    (row: CanvasNodeRow): WidgetNodeType => ({
      id: String(row.id),
      type: "widget",
      position: { x: row.x, y: row.y },
      style: {
        width: row.width ?? undefined,
        height: row.height ?? undefined,
      },
      data: {
        rowId: row.id,
        widgetType: row.widget_type,
        config: parseConfig(row.config),
        onConfigChange: persistConfig,
      },
    }),
    [persistConfig],
  );

  useEffect(() => {
    api
      .health()
      .then(() => setStatus("ok"))
      .catch(() => setStatus("down"));
    api
      .listNodes()
      .then((rows) => setNodes(rows.map(toNode)))
      .catch(() => {});
  }, [setNodes, toNode]);

  const addWidget = useCallback(
    async (type: string, size: [number, number]) => {
      const row = await api.createNode({
        widget_type: type,
        // Slight scatter so consecutive adds don't stack perfectly.
        x: 80 + Math.random() * 240,
        y: 80 + Math.random() * 160,
        width: size[0],
        height: size[1],
        config: "{}",
      });
      setNodes((current) => [...current, toNode(row)]);
    },
    [setNodes, toNode],
  );

  // Position and size only reach the DB once the gesture ends — dragging
  // otherwise fires a PATCH per animation frame.
  const handleChanges = useCallback(
    (changes: NodeChange<WidgetNodeType>[]) => {
      onNodesChange(changes);
      for (const change of changes) {
        if (change.type === "position" && !change.dragging && change.position) {
          void api.updateNode(Number(change.id), {
            x: change.position.x,
            y: change.position.y,
          });
        }
        if (change.type === "dimensions" && change.resizing === false) {
          const dims = change.dimensions;
          if (dims) {
            void api.updateNode(Number(change.id), {
              width: dims.width,
              height: dims.height,
            });
          }
        }
        if (change.type === "remove") {
          void api.deleteNode(Number(change.id));
        }
      }
    },
    [onNodesChange],
  );

  const nodeTypes = useMemo(() => ({ widget: WidgetNode }), []);

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <span className="text-sm font-semibold">AIVAR</span>
        {/* Which stack am I looking at? Essential once three worktrees run. */}
        <span className="rounded bg-neutral-100 px-2 py-0.5 font-mono text-[11px] dark:bg-neutral-800">
          {WORKTREE}
        </span>
        <span
          className={`text-[11px] ${
            status === "ok"
              ? "text-emerald-600"
              : status === "down"
                ? "text-red-600"
                : "text-neutral-400"
          }`}
        >
          api {status}
        </span>
        <div className="ml-auto flex gap-1">
          {WIDGETS.map((w) => (
            <button
              key={w.type}
              onClick={() => addWidget(w.type, w.size)}
              title={w.blurb}
              className="rounded border border-neutral-200 px-2 py-1 text-xs hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              + {w.label}
            </button>
          ))}
        </div>
      </header>
      <div className="flex-1">
        <ReactFlow
          nodes={nodes}
          onNodesChange={handleChanges}
          nodeTypes={nodeTypes}
          proOptions={{ hideAttribution: true }}
          fitView
        >
          <Background />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
    </div>
  );
}
