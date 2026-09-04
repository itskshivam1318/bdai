"use client";
import { API_BASE } from "@/lib/api";
import type { WidgetProps } from "./types";

/** Renders anything the agent wrote into api/artifacts/ — screenshots, diffs. */
export default function ScreenshotWidget({ config, setConfig }: WidgetProps) {
  const path = typeof config.path === "string" ? config.path : "";
  return (
    <div className="flex h-full flex-col gap-2">
      <input
        value={path}
        onChange={(e) => setConfig({ path: e.target.value })}
        onPointerDownCapture={(e) => e.stopPropagation()}
        placeholder="run-1/before.png"
        className="rounded border border-neutral-200 px-2 py-1 text-xs outline-none dark:border-neutral-700 dark:bg-neutral-900"
      />
      <div className="flex-1 overflow-hidden rounded border border-dashed border-neutral-200 dark:border-neutral-700">
        {path ? (
          // Plain <img>: artifact paths are dynamic and the API is not a
          // configured next/image host.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`${API_BASE}/artifacts/${path}`}
            alt={path}
            className="h-full w-full object-contain"
          />
        ) : (
          <p className="p-2 text-xs text-neutral-400">
            Path under api/artifacts/
          </p>
        )}
      </div>
    </div>
  );
}
