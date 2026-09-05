/**
 * Who did what in a run, and where the evidence for it is.
 *
 * The console had two views of the same run and neither answered "what did the
 * Generator do". `lib/stages.ts` maps an `Event.surface` to a slot in the
 * strip; `agents/tracing.py` writes a model conversation per call, named by
 * role. Both are keyed to something the *machinery* knows -- a surface, a role
 * -- and the brief's three sub-agents are named in neither.
 *
 * This table is the join. One row per agent the brief asks about, holding the
 * transcript roles it files under and the event surfaces it reports on, so a
 * panel can be built around the agent rather than around the storage.
 *
 * **`deterministic` is the load-bearing field.** Two of the three sub-agents
 * make no model call at all -- `generator.scenarios` compiles a recorded path
 * and `runner.resolve` walks a fixed ladder -- so a viewer that lists only
 * transcripts shows nothing under them, and nothing reads as broken rather than
 * as "this stage does not guess". Saying so is the disclosure.
 */
export type Agent = {
  key: string;
  /** The brief's name for it, not the module's. */
  title: string;
  /** What it is for, in one line. */
  what: string;
  /** `role` values written by `tracing.save_transcript` for this agent. */
  roles: string[];
  /** `Event.surface` values this agent reports on. */
  surfaces: string[];
  /**
   * What it does with no model, or null for an agent that is only ever a model
   * call. Shown when the agent has no transcript, which for the Generator and
   * the Healer is the normal case rather than a failure.
   */
  deterministic: string | null;
};

export const AGENTS: Agent[] = [
  {
    key: "planner",
    title: "Planner",
    what: "explores the app and turns the map into claims about it",
    // The colony and the crawl's payload chooser both plan: the orchestrator
    // decides where to send an ant, the ant decides what to do when it lands,
    // `behaviour` says what the resulting map means, and `synthesizer` chooses
    // what to type into a form it has never seen.
    roles: ["orchestrator", "ant", "behaviour", "synthesizer"],
    surfaces: ["explore", "plan"],
    deterministic:
      "The crawl underneath it is deterministic — explorer/crawler.py walks " +
      "the app breadth-first with no model, and the colony is only ever " +
      "applied to a map that already exists.",
  },
  {
    key: "critic",
    title: "Critic",
    what: "ranks what the map does not cover",
    roles: ["critic"],
    surfaces: ["coverage"],
    deterministic:
      "The gaps are computed from the map by critic.candidates; the model may " +
      "only reorder them, and anything it cites that was never a candidate is " +
      "dropped and counted.",
  },
  {
    key: "generator",
    title: "Generator",
    what: "compiles recorded paths into runnable Playwright tests",
    // `claims` is the only model call in this stage -- it decides whether the
    // suite already answers the sentence the tester typed. Listed ahead of the
    // writer: `claims.attribute` makes that call and does not yet record it,
    // which is why the Generator's evidence today is its record and not a
    // conversation. See the note in api/agents/claims.py.
    roles: ["claims"],
    surfaces: ["suite"],
    deterministic:
      "No model writes a test here. generator.scenarios compiles a path the " +
      "crawl actually walked, so every step is something the app was observed " +
      "to do — which is why a generated test cannot assert a screen that was " +
      "never seen.",
  },
  {
    key: "healer",
    title: "Healer",
    what: "replays the suite, repairs a locator, or reports a defect",
    // Filed as "healer" by `rescue.look`, which runs the same colony code as
    // the Planner and would otherwise be indistinguishable from it.
    roles: ["healer"],
    surfaces: ["run", "heal", "defect"],
    deterministic:
      "The resolution ladder is deterministic: re-observe, compare state keys, " +
      "and the failure classifies itself. A model is asked one question only — " +
      "when nothing on the page plays the recorded part at all, rescue.py " +
      "explores the region and asks the fresh map what replaced it.",
  },
  {
    key: "meta",
    title: "Meta-agent",
    what: "decides between the stages and writes the report",
    roles: [],
    surfaces: ["timeline", "report"],
    deterministic:
      "Its policy is code, not a prompt. Every branch routes on evidence " +
      "something else computed — the Runner's verdicts and the Critic's " +
      "candidates — which is what makes it a policy rather than an opinion.",
  },
  {
    key: "analyst",
    title: "Analyst",
    what: "answers questions you ask about the map",
    roles: ["analyst"],
    surfaces: [],
    deterministic: null,
  },
];

/** The agent a transcript role belongs to, or null for one nothing claims. */
export function agentForRole(role: string): Agent | null {
  return AGENTS.find((a) => a.roles.includes(role)) ?? null;
}
