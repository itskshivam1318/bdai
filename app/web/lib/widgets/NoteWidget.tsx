"use client";
import type { WidgetProps } from "./types";

export default function NoteWidget({ config, setConfig }: WidgetProps) {
  const text = typeof config.text === "string" ? config.text : "";
  return (
    <textarea
      value={text}
      onChange={(e) => setConfig({ text: e.target.value })}
      placeholder="Scratch notes, hypotheses, TODOs…"
      className="h-full w-full resize-none rounded-md border border-neutral-200 bg-yellow-50/60 p-2 text-sm outline-none focus:border-neutral-400 dark:border-neutral-700 dark:bg-yellow-900/10"
      // Stops xyflow from turning a text drag-select into a node drag.
      onPointerDownCapture={(e) => e.stopPropagation()}
    />
  );
}
