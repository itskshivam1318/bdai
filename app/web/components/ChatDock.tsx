"use client";
import { useState } from "react";
import ChatWindow from "@/components/ChatWindow";
import type { ChatThread, MapState } from "@/lib/api";

/**
 * The right margin of the console: the open chat windows, and a launcher for
 * the ones that are put away.
 *
 * **An overlay, not a third column.** The console is already a map beside a
 * stage rail, and adding a column would take width from the graph every time
 * somebody opened a conversation -- including the graph they are asking about.
 * Windows float over the rail instead, which is also what makes "minimise" mean
 * something: a collapsed window is a title bar and gives its space back.
 *
 * `pointer-events-none` on the column with `pointer-events-auto` on each card
 * is what keeps the map draggable through the gaps between windows.
 */
export default function ChatDock({
  threads,
  focusedId,
  attachedByThread,
  runId,
  onFocus,
  onPatch,
  onDelete,
  onNew,
  onDetach,
  onClearAttached,
}: {
  /** Every thread of the session, open and closed, oldest first. */
  threads: ChatThread[];
  focusedId: number | null;
  attachedByThread: Record<number, MapState[]>;
  runId: number | null;
  onFocus: (id: number) => void;
  onPatch: (
    id: number,
    patch: { title?: string; open?: boolean; minimised?: boolean },
  ) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
  onDetach: (id: number, key: string) => void;
  onClearAttached: (id: number) => void;
}) {
  // Which closed thread is one click from being destroyed. Keyed on the id
  // rather than held as a boolean so opening the confirm on a second row closes
  // the first for free -- the same shape as the run-failure popover in the
  // header, and for the same reason.
  const [confirming, setConfirming] = useState<number | null>(null);

  const open = threads.filter((t) => t.open);
  const closed = threads.filter((t) => !t.open);

  return (
    <div className="pointer-events-none absolute bottom-3 right-3 top-3 z-20 flex w-[22rem] max-w-[calc(100%-1.5rem)] flex-col justify-end gap-2">
      {/*
        Oldest at the top, so a window does not move when another is opened
        below it. Sorting by recency would put the thing you just used under
        your cursor and shuffle everything else while you read it.
      */}
      {open.map((thread) => (
        <ChatWindow
          key={thread.id}
          thread={thread}
          runId={runId}
          attached={attachedByThread[thread.id] ?? []}
          focused={thread.id === focusedId}
          onFocus={() => onFocus(thread.id)}
          onDetach={(key) => onDetach(thread.id, key)}
          onClearAttached={() => onClearAttached(thread.id)}
          onPatch={(patch) => onPatch(thread.id, patch)}
        />
      ))}

      <div className="pointer-events-auto flex flex-wrap items-center justify-end gap-1.5">
        {closed.map((thread) =>
          confirming === thread.id ? (
            <span
              key={thread.id}
              className="flex items-center gap-1 rounded-full border border-fault bg-paper px-2 py-1 text-[11px] shadow-sm"
            >
              <span className="text-fault">Delete “{thread.title}”?</span>
              <button
                type="button"
                onClick={() => {
                  setConfirming(null);
                  onDelete(thread.id);
                }}
                className="rounded px-1 text-fault hover:bg-hush"
              >
                yes
              </button>
              <button
                type="button"
                onClick={() => setConfirming(null)}
                className="rounded px-1 text-muted hover:bg-hush"
              >
                no
              </button>
            </span>
          ) : (
            <span
              key={thread.id}
              className="flex items-center gap-1 rounded-full border border-rule bg-paper py-1 pl-2 pr-1 text-[11px] shadow-sm"
            >
              <button
                type="button"
                onClick={() => {
                  onPatch(thread.id, { open: true, minimised: false });
                  onFocus(thread.id);
                }}
                title="Reopen this conversation"
                className="max-w-40 truncate text-ink hover:underline"
              >
                {thread.title}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(thread.id)}
                title="Delete this conversation"
                aria-label={`Delete ${thread.title}`}
                className="rounded px-1 text-muted hover:text-fault"
              >
                ⨯
              </button>
            </span>
          ),
        )}

        <button
          type="button"
          onClick={onNew}
          className="rounded-full border border-rule bg-paper px-2.5 py-1 text-[11px] shadow-sm hover:border-ink"
        >
          ＋ New chat
        </button>
      </div>
    </div>
  );
}
