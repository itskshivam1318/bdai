"use client";
import { useEffect, useState } from "react";
import { api, type Progress } from "@/lib/api";

/**
 * How far the crawl got, over the map it got it from.
 *
 * **The one ratio, and why it is allowed to be one.** `decisions.md`
 * (2026-09-04 19:00) rules out a coverage percentage, and that reasoning is
 * untouched here: its denominator would be the states x actions table, whose
 * cells are not equally meaningful. This denominator is the actions the app
 * *offered* — every one of them a control the application itself rendered, so
 * every cell is equal. It says how far the crawler got. It does not say how
 * well the app is tested, which is why the word "coverage" appears nowhere on
 * it and why `report()` still carries no percentage at all.
 *
 * Everything under the bar is a count. That is the same decision, kept.
 */
export default function MapProgress({ runId }: { runId: number | null }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (runId === null) return;
    let cancelled = false;
    const poll = () =>
      api
        .getProgress(runId)
        .then((next) => {
          if (cancelled) return;
          // Same trick as the map's own poll: keep the previous object when
          // nothing changed, so a finished run stops re-rendering every 2s.
          setProgress((prev) =>
            prev && JSON.stringify(prev) === JSON.stringify(next) ? prev : next,
          );
        })
        .catch(() => {
          // The run may not have written a state yet. Try again next tick.
        });
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId]);

  if (!progress || progress.offered === 0) return null;

  const { offered, walked, refused, remaining } = progress;
  const pct = Math.round((100 * walked) / offered);
  const share = (n: number) => `${(100 * n) / offered}%`;

  return (
    <div className="w-64 rounded-md border border-rule bg-paper/95 p-2.5 shadow-sm backdrop-blur">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="map-progress-detail"
        className="flex w-full items-baseline gap-2 text-left"
      >
        <span className="text-[10px] uppercase tracking-wide text-muted">
          Explored
        </span>
        <span className="ml-auto font-mono text-sm text-ink">{pct}%</span>
        <span className="text-[10px] text-muted">{open ? "▾" : "▸"}</span>
      </button>

      {/*
        Three segments, not one. A refused action is settled — the crawler
        tried it and the app would not have it — so painting it as unwalked
        would imply work still to do, and painting it as walked would claim a
        control was exercised when it was not.
      */}
      <div
        className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-hush"
        role="img"
        aria-label={`${walked} of ${offered} offered actions walked, ${refused} refused, ${remaining} remaining`}
      >
        <div className="bg-live" style={{ width: share(walked) }} />
        <div className="bg-muted/50" style={{ width: share(refused) }} />
      </div>

      <p
        className="mt-1.5 text-[11px] leading-snug text-muted"
        title="The denominator is every action a state offered. It grows as new states are found, so this can go down while the crawl is still working — that is the crawler discovering more app, not losing ground."
      >
        {walked} of {offered} offered actions walked
      </p>

      {open && (
        <dl
          id="map-progress-detail"
          className="mt-2 space-y-1 border-t border-rule pt-2 text-[11px]"
        >
          <Row label="on the frontier" value={remaining} />
          {refused > 0 && <Row label="offered but refused" value={refused} />}
          <Row label="states" value={progress.states} />
          <Row
            label="transitions"
            value={`${progress.transitions} · ${progress.mutating} mutating`}
          />
          {/* Absence of a verdict is not a pass — the coverage question the
              map can answer without inventing a denominator. */}
          <Row
            label="states nothing tested"
            value={progress.untested_states}
            warn={progress.untested_states > 0}
          />
          <Row
            label="ambiguous edges"
            value={progress.ambiguous_edges}
            warn={progress.ambiguous_edges > 0}
          />

          {progress.reasons.length > 0 && (
            <div className="pt-1">
              <dt className="text-muted">why actions were refused</dt>
              {progress.reasons.map((r) => (
                <dd key={r.reason} className="mt-0.5 flex gap-1.5 text-muted">
                  <span className="font-mono text-ink">{r.count}</span>
                  <span className="leading-snug">{r.reason}</span>
                </dd>
              ))}
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  warn,
}: {
  label: string;
  value: number | string;
  warn?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted">{label}</dt>
      <dd
        className={`ml-auto font-mono ${warn ? "text-fault" : "text-ink"}`}
      >
        {value}
      </dd>
    </div>
  );
}
