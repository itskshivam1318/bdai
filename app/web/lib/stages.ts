/**
 * The brief's must-haves, in order, and the surface each one listens for.
 *
 * `widgets/surfaces.ts` maps a surface to a *floating widget* and stays as it
 * is — the widget board still works. This table maps the same surfaces to
 * *fixed slots*, which is what a rail is. Two tables because they answer
 * different questions, both fed by the one `Event.surface` the agent emits.
 */
export type Stage = {
  /** Event.surface values that feed this card. */
  surfaces: string[];
  title: string;
  ordinal: string;
  /** True when every matching event should be listed, not just the latest. */
  accumulate?: boolean;
  /** How many lines to keep on an accumulating card. Defaults to KEEP. */
  keep?: number;
  /** What the card says while it is still empty. */
  waiting: string;
};

export const STAGES: Stage[] = [
  // Stage 0 is the colony walking the app, and it is the only one that reports
  // while it is still happening. It accumulates like the rest, but three lines
  // deep rather than ten: a wave rationale is a paragraph, and ten of them
  // would bury the five cards underneath it. One was too few -- the last thing
  // the colony says is where it wrote its transcript, which is not what the
  // person watching wants the card to be showing.
  {
    surfaces: ["explore"],
    title: "Explore",
    ordinal: "0",
    waiting: "waiting for the colony",
    accumulate: true,
    keep: 3,
  },
  { surfaces: ["plan"], title: "Plan", ordinal: "1", waiting: "named after the map is walked", accumulate: true },
  { surfaces: ["coverage"], title: "Coverage", ordinal: "2", waiting: "computed before generation", accumulate: true },
  { surfaces: ["suite"], title: "Suite", ordinal: "3", waiting: "compiled from recorded paths", accumulate: true },
  // "run" is the live per-scenario line. `heal` and `defect` only exist when
  // something went wrong, so listening for those alone left this card saying
  // "pending" over a run where all eight scenarios passed.
  { surfaces: ["run", "heal", "defect"], title: "Run", ordinal: "4", waiting: "replays once the suite exists", accumulate: true },
  { surfaces: ["report"], title: "Report", ordinal: "5", waiting: "written last" },
];
