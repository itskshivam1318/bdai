"use client";
import { useEffect, useState } from "react";
import { api, type AgentEvent } from "@/lib/api";
import type { WidgetProps } from "./types";

const LEVEL_TONE: Record<string, string> = {
  error: "text-red-600",
  warn: "text-amber-600",
  decision: "text-violet-600",
  info: "text-neutral-500",
};

export default function EventLogWidget({ config, setConfig }: WidgetProps) {
  const runId = typeof config.runId === "number" ? config.runId : null;
  const [events, setEvents] = useState<AgentEvent[]>([]);

  useEffect(() => {
    if (runId === null) return;
    const load = () => api.listEvents(runId).then(setEvents).catch(() => {});
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [runId]);

  return (
    <div className="flex h-full flex-col gap-2 text-sm">
      <input
        type="number"
        value={runId ?? ""}
        onChange={(e) =>
          setConfig({ runId: e.target.value ? Number(e.target.value) : null })
        }
        onPointerDownCapture={(e) => e.stopPropagation()}
        placeholder="run id"
        className="w-24 rounded border border-neutral-200 px-2 py-1 text-xs outline-none dark:border-neutral-700 dark:bg-neutral-900"
      />
      <ul className="flex-1 space-y-1 overflow-auto font-mono text-[11px] leading-snug">
        {events.map((e) => (
          <li key={e.id} className={LEVEL_TONE[e.level] ?? "text-neutral-500"}>
            {e.message}
          </li>
        ))}
        {runId !== null && !events.length && (
          <li className="text-neutral-400">No events for run {runId}.</li>
        )}
      </ul>
    </div>
  );
}
