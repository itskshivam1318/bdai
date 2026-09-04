"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, type SessionSummary } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

/** A session is named by its host until someone renames it — more use than "Untitled". */
export function sessionLabel(s: { name: string | null; target_url: string }): string {
  if (s.name) return s.name;
  try {
    return new URL(s.target_url).hostname.replace(/^www\./, "");
  } catch {
    return s.target_url || "New session";
  }
}

function dayGroup(iso: string): string {
  const then = new Date(iso);
  const today = new Date();
  const days = Math.floor(
    (new Date(today.toDateString()).getTime() - new Date(then.toDateString()).getTime()) /
      86_400_000,
  );
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "This week";
  return then.toLocaleDateString(undefined, { month: "long" });
}

const STATUS_DOT: Record<string, string> = {
  passed: "bg-live",
  failed: "bg-fault",
  error: "bg-fault",
  running: "bg-live animate-pulse",
};

export default function Sidebar() {
  const params = useParams<{ id?: string }>();
  const activeId = params?.id ? Number(params.id) : null;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);

  const refresh = useCallback(() => {
    api.listSessions().then(setSessions).catch(() => {});
  }, []);

  // Cheap poll: a run started in one tab should show up in another, and the
  // list is a handful of rows.
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const groups = sessions.reduce<Record<string, SessionSummary[]>>((acc, s) => {
    const key = dayGroup(s.created_at);
    (acc[key] ??= []).push(s);
    return acc;
  }, {});

  return (
    <nav
      aria-label="Sessions"
      className="flex w-64 shrink-0 flex-col border-r border-rule bg-hush"
    >
      <div className="px-4 py-4">
        <span className="text-sm font-semibold tracking-tight">AIVAR</span>
      </div>

      <Link
        href="/"
        className="mx-3 mb-3 rounded-md border border-rule px-3 py-2 text-sm hover:bg-paper"
      >
        New session
      </Link>

      <div className="flex-1 overflow-y-auto px-3 pb-4">
        {Object.entries(groups).map(([label, rows]) => (
          <section key={label} className="mb-4">
            <h2 className="px-2 pb-1 text-xs text-muted">{label}</h2>
            <ul>
              {rows.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/s/${s.id}`}
                    aria-current={s.id === activeId ? "page" : undefined}
                    className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
                      s.id === activeId ? "bg-paper font-medium" : "hover:bg-paper/60"
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`size-1.5 shrink-0 rounded-full ${
                        STATUS_DOT[s.last_status ?? ""] ?? "bg-rule"
                      }`}
                    />
                    <span className="truncate">{sessionLabel(s)}</span>
                    {s.run_count > 0 && (
                      <span className="ml-auto text-xs text-muted">{s.run_count}</span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {!sessions.length && (
          <p className="px-2 text-xs text-muted">
            No sessions yet. Point one at a URL to start.
          </p>
        )}
      </div>
    </nav>
  );
}
