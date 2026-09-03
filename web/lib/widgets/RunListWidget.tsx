"use client";
import { useEffect, useState } from "react";
import { api, type Run } from "@/lib/api";
import type { WidgetProps } from "./types";

const STATUS_TONE: Record<string, string> = {
  passed: "text-emerald-600",
  failed: "text-red-600",
  error: "text-red-600",
  running: "text-blue-600",
  pending: "text-neutral-500",
};

export default function RunListWidget({ config, setConfig }: WidgetProps) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);
  const target = typeof config.target === "string" ? config.target : "";

  const refresh = () =>
    api
      .listRuns()
      .then((r) => {
        setRuns(r);
        setError(null);
      })
      .catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex h-full flex-col gap-2 text-sm">
      <div className="flex gap-1">
        <input
          value={target}
          onChange={(e) => setConfig({ target: e.target.value })}
          onPointerDownCapture={(e) => e.stopPropagation()}
          placeholder="https://target.example"
          className="min-w-0 flex-1 rounded border border-neutral-200 px-2 py-1 text-xs outline-none dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          onClick={() => target && api.createRun(target).then(refresh)}
          className="rounded bg-neutral-900 px-2 py-1 text-xs text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
          disabled={!target}
        >
          Run
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <ul className="flex-1 space-y-1 overflow-auto">
        {runs.map((r) => (
          <li key={r.id} className="flex justify-between gap-2 text-xs">
            <span className="truncate" title={r.target_url}>
              #{r.id} {r.target_url}
            </span>
            <span className={STATUS_TONE[r.status] ?? "text-neutral-500"}>
              {r.status}
            </span>
          </li>
        ))}
        {!runs.length && !error && (
          <li className="text-xs text-neutral-400">No runs yet.</li>
        )}
      </ul>
    </div>
  );
}
