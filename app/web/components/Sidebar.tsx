"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, type SessionSummary } from "@/lib/api";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

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

const COLLAPSE_KEY = "aivar:sidebar-collapsed";

/**
 * The collapsed flag lives in `localStorage`, which makes it state React does
 * not own — so it is read through `useSyncExternalStore` rather than mirrored
 * into a `useState`. Two things fall out of that: the server renders the
 * expanded panel and hydration agrees with it (`getServerSnapshot`), and the
 * `storage` event means collapsing in one tab collapses the others, which is
 * the same view the session list already takes of a second tab.
 *
 * Every access is guarded: a private window, or a browser set to block site
 * data, throws on `localStorage` itself. Losing the preference is acceptable;
 * an unusable console is not.
 */
const listeners = new Set<() => void>();

function subscribeToCollapse(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(next: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
  } catch {
    // The panel still moves this session; it just will not be remembered.
  }
  // `storage` does not fire in the tab that wrote it, so this tab is told
  // directly. Without it the button would appear dead in its own window.
  listeners.forEach((notify) => notify());
}

export default function Sidebar() {
  const params = useParams<{ id?: string }>();
  const activeId = params?.id ? Number(params.id) : null;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const collapsed = useSyncExternalStore(
    subscribeToCollapse,
    readCollapsed,
    // The server has no storage to read, so it renders the panel open.
    () => false,
  );

  const toggle = useCallback(() => writeCollapsed(!readCollapsed()), []);

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
      data-collapsed={collapsed || undefined}
      className={`flex shrink-0 flex-col border-r border-rule bg-hush transition-[width] duration-200 ease-out ${
        collapsed ? "w-12" : "w-64"
      }`}
    >
      <div
        className={`flex items-center px-2 py-4 ${collapsed ? "justify-center" : "justify-between pl-4 pr-2"}`}
      >
        {!collapsed && <span className="text-sm font-semibold tracking-tight">AIVAR</span>}
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sessions panel" : "Collapse sessions panel"}
          title={collapsed ? "Expand sessions panel" : "Collapse sessions panel"}
          className="rounded-md p-1.5 text-muted hover:bg-paper hover:text-ink"
        >
          {/* A panel with a bar on the side it collapses toward: the icon says
              which edge moves, so it reads the same in both states. */}
          <svg
            aria-hidden
            viewBox="0 0 16 16"
            className="size-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <rect x="1.5" y="2.5" width="13" height="11" rx="2" />
            <line x1="6" y1="2.5" x2="6" y2="13.5" />
          </svg>
        </button>
      </div>

      <Link
        href="/"
        title={collapsed ? "New session" : undefined}
        aria-label={collapsed ? "New session" : undefined}
        className={`mb-3 flex items-center rounded-md border border-rule text-sm hover:bg-paper ${
          collapsed ? "mx-2 justify-center px-0 py-2" : "mx-3 px-3 py-2"
        }`}
      >
        {collapsed ? (
          <svg
            aria-hidden
            viewBox="0 0 16 16"
            className="size-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <line x1="8" y1="3.5" x2="8" y2="12.5" />
            <line x1="3.5" y1="8" x2="12.5" y2="8" />
          </svg>
        ) : (
          "New session"
        )}
      </Link>

      <div className={`flex-1 overflow-y-auto pb-4 ${collapsed ? "px-2" : "px-3"}`}>
        {Object.entries(groups).map(([label, rows]) => (
          <section key={label} className="mb-4">
            {/* Collapsed, the heading has no room to be read, but the grouping
                it creates is still the order the rows are in. */}
            {!collapsed && <h2 className="px-2 pb-1 text-xs text-muted">{label}</h2>}
            <ul>
              {rows.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/s/${s.id}`}
                    aria-current={s.id === activeId ? "page" : undefined}
                    title={collapsed ? sessionLabel(s) : undefined}
                    className={`flex items-center rounded-md py-1.5 text-sm ${
                      collapsed ? "justify-center px-0" : "gap-2 px-2"
                    } ${s.id === activeId ? "bg-paper font-medium" : "hover:bg-paper/60"}`}
                  >
                    <span
                      aria-hidden
                      className={`size-1.5 shrink-0 rounded-full ${
                        STATUS_DOT[s.last_status ?? ""] ?? "bg-rule"
                      }`}
                    />
                    {/* The label is what disappears when the panel narrows; the
                        status dot is what is worth keeping, so a collapsed rail
                        still shows which run is alive. */}
                    {!collapsed && (
                      <>
                        <span className="truncate">{sessionLabel(s)}</span>
                        {s.run_count > 0 && (
                          <span className="ml-auto text-xs text-muted">{s.run_count}</span>
                        )}
                      </>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {!sessions.length && !collapsed && (
          <p className="px-2 text-xs text-muted">
            No sessions yet. Point one at a URL to start.
          </p>
        )}
      </div>
    </nav>
  );
}
