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
};

export const STAGES: Stage[] = [
  { surfaces: ["plan"], title: "Plan", ordinal: "1" },
  { surfaces: ["coverage"], title: "Coverage", ordinal: "2" },
  { surfaces: ["suite"], title: "Suite", ordinal: "3" },
  { surfaces: ["heal", "defect"], title: "Run", ordinal: "4", accumulate: true },
  { surfaces: ["report"], title: "Report", ordinal: "5" },
];
