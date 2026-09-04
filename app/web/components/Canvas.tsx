"use client";
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  useNodesState,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WidgetNode, { type WidgetNodeType } from "@/components/WidgetNode";
import { api, type CanvasNodeRow } from "@/lib/api";
import { WIDGETS } from "@/lib/widgets/registry";
import { placeWidget, widgetsToOpen } from "@/lib/widgets/surfaces";
import type { WidgetConfig } from "@/lib/widgets/types";

function parseConfig(raw: string): WidgetConfig {
  try {
    return JSON.parse(raw) as WidgetConfig;
  } catch {
    return {};
  }
}

export default function Canvas({ sessionId }: { sessionId: number }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<WidgetNodeType>([]);
  const [showAdd, setShowAdd] = useState(false);
  // Raw rows, kept alongside the xyflow nodes because placement and dedupe
  // both reason about config blobs that never reach the node's `data`.
  const rows = useRef<CanvasNodeRow[]>([]);
  const lastEventId = useRef(0);

  const persistConfig = useCallback((rowId: number, next: WidgetConfig) => {
    void api.updateNode(rowId, { config: JSON.stringify(next) });
  }, []);

  const toNode = useCallback(
    (row: CanvasNodeRow): WidgetNodeType => ({
      id: String(row.id),
      type: "widget",
      position: { x: row.x, y: row.y },
      style: { width: row.width ?? undefined, height: row.height ?? undefined },
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
      .listNodes(sessionId)
      .then((loaded) => {
        rows.current = loaded;
        setNodes(loaded.map(toNode));
      })
      .catch(() => {});
  }, [sessionId, setNodes, toNode]);

  // The agent's half of the contract: it emits events carrying a `surface`,
  // and we turn the ones we have widgets for into nodes. Dedupe lives in
  // widgetsToOpen, keyed on the originating event id, so a re-poll or a reload
  // can't clone a widget.
  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const events = await api.listSessionEvents(sessionId, lastEventId.current);
        if (cancelled || !events.length) return;
        lastEventId.current = events[events.length - 1].id;

        const wanted = widgetsToOpen(events, rows.current);
        for (const spec of wanted) {
          const row = await api.createNode({ ...spec, session_id: sessionId });
          if (cancelled) return;
          rows.current = [...rows.current, row];
          setNodes((current) => [...current, toNode(row)]);
        }
      } catch {
        // API down mid-session: keep the canvas as-is and try again next tick.
      }
    }

    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId, setNodes, toNode]);

  const addWidget = useCallback(
    async (type: string, size: [number, number]) => {
      const { x, y } = placeWidget(rows.current, size);
      const row = await api.createNode({
        session_id: sessionId,
        widget_type: type,
        x,
        y,
        width: size[0],
        height: size[1],
        config: "{}",
      });
      rows.current = [...rows.current, row];
      setNodes((current) => [...current, toNode(row)]);
      setShowAdd(false);
    },
    [sessionId, setNodes, toNode],
  );

  // Position and size only reach the DB once the gesture ends — dragging
  // otherwise fires a PATCH per animation frame.
  const handleChanges = useCallback(
    (changes: NodeChange<WidgetNodeType>[]) => {
      onNodesChange(changes);
      for (const change of changes) {
        if (change.type === "position" && !change.dragging && change.position) {
          const { x, y } = change.position;
          void api.updateNode(Number(change.id), { x, y });
          rows.current = rows.current.map((r) =>
            r.id === Number(change.id) ? { ...r, x, y } : r,
          );
        }
        if (change.type === "dimensions" && change.resizing === false) {
          const dims = change.dimensions;
          if (dims) {
            void api.updateNode(Number(change.id), {
              width: dims.width,
              height: dims.height,
            });
            rows.current = rows.current.map((r) =>
              r.id === Number(change.id)
                ? { ...r, width: dims.width, height: dims.height }
                : r,
            );
          }
        }
        if (change.type === "remove") {
          void api.deleteNode(Number(change.id));
          rows.current = rows.current.filter((r) => r.id !== Number(change.id));
        }
      }
    },
    [onNodesChange],
  );

  const nodeTypes = useMemo(() => ({ widget: WidgetNode }), []);

  return (
    <ReactFlow
      nodes={nodes}
      onNodesChange={handleChanges}
      nodeTypes={nodeTypes}
      proOptions={{ hideAttribution: true }}
      fitView
      // Without the cap, a canvas holding two widgets zooms them to fill the
      // viewport and the text renders comically large.
      fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
    >
      <Background />
      <Controls />
      <MiniMap pannable zoomable />
      <Panel position="top-right">
        <div className="relative">
          <button
            onClick={() => setShowAdd((v) => !v)}
            aria-expanded={showAdd}
            className="rounded-md border border-rule bg-paper px-2.5 py-1.5 text-xs shadow-sm"
          >
            Add widget
          </button>
          {showAdd && (
            <ul className="absolute right-0 mt-1 w-52 overflow-hidden rounded-md border border-rule bg-paper shadow-md">
              {WIDGETS.map((w) => (
                <li key={w.type}>
                  <button
                    onClick={() => addWidget(w.type, w.size)}
                    className="block w-full px-3 py-2 text-left text-xs hover:bg-hush"
                  >
                    <span className="block">{w.label}</span>
                    <span className="block text-muted">{w.blurb}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </ReactFlow>
  );
}
