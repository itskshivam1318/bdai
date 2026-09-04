"use client";
import { useEffect, useState } from "react";
import { api, type AgentEvent } from "@/lib/api";
import { STAGES } from "@/lib/stages";

const LEVEL_TONE: Record<string, string> = {
  error: "text-fault",
  warn: "text-fault",
  decision: "text-ink",
  info: "text-muted",
};

/**
 * The brief's five must-haves, filling in as the meta-agent advances.
 *
 * Reads the same `Event.surface` seam the widget board reads — the agent names
 * what deserves attention and never learns that a rail exists. A surface with
 * no stage falls through to the timeline, exactly as before.
 */
export default function StageRail({ sessionId }: { sessionId: number }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    let after = 0;
    const poll = () =>
      api
        .listSessionEvents(sessionId, after)
        .then((batch) => {
          if (cancelled || !batch.length) return;
          after = batch[batch.length - 1].id;
          setEvents((current) => [...current, ...batch.filter((e) => e.surface)]);
        })
        .catch(() => {
          // API down mid-session: keep what we have and try again next tick.
        });
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId]);

  return (
    <div className="h-full overflow-y-auto border-l border-rule bg-hush px-3 py-3">
      {STAGES.map((stage) => {
        const mine = events.filter((e) => e.surface && stage.surfaces.includes(e.surface));
        const shown = stage.accumulate ? mine : mine.slice(-1);
        return (
          <section key={stage.title} className="mb-3 rounded-md border border-rule bg-paper p-3">
            <header className="flex items-baseline gap-2">
              <span className="text-[11px] text-muted">{stage.ordinal}</span>
              <h2 className="text-xs font-medium uppercase tracking-wide text-ink">
                {stage.title}
              </h2>
              {shown.length === 0 && (
                <span className="ml-auto text-[11px] text-muted">pending</span>
              )}
            </header>
            {shown.length > 0 && (
              <ul className="mt-2 space-y-1">
                {shown.map((event) => (
                  <li
                    key={event.id}
                    className={`text-xs leading-snug ${LEVEL_TONE[event.level] ?? "text-muted"}`}
                  >
                    {event.message}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
