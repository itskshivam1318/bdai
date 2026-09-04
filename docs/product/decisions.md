# Decisions

Append-only. Newest last. One entry per decision that another person or agent
could otherwise re-litigate or contradict.

Format: `## YYYY-MM-DD HH:MM — <decision>` then **Why**, **Alternatives
rejected**, **Who**.

---

## 2026-09-03 22:30 — Stack: Next.js + FastAPI + SQLite, canvas via xyflow

**Why:** Fastest path to a widget canvas we can extend during the event;
FastAPI keeps Playwright and the Anthropic SDK in the same process as the data.

**Alternatives rejected:** dashboard grid (react-grid-layout) — less aligned
with agent/flow visuals; Postgres — needless ops cost for a 30-hour build.

**Who:** shivam + Claude, pre-event setup.

---

## 2026-09-03 22:30 — One worktree per person, each running its own stack

**Why:** Three people running `npm run dev` in one checkout collide on ports
and on `app.db`. Per-worktree port pairs let all three stacks run at once so
changes can be compared side by side.

**Alternatives rejected:** shared dev server (serialises the team); Docker
Compose (slower to start, more to debug at 2am).

**Who:** shivam + Claude, pre-event setup.

---

## 2026-09-04 15:30 — The World Map is the spine, not a Planner artifact

All three sub-agents read and write one behavioural model
(`app/api/agents/explorer/worldmap.py`): the Planner's test plan *is* its
transitions, the Generator compiles paths out of it, and the Healer classifies a
failure by re-observing into it and comparing state keys.

**Why:** it makes the 20% "coverage gaps" criterion computable rather than a
judgement. A states × actions table has empty cells; ISTQB CTFL v4.0.1 calls
those the enumerable invalid-transition candidates, and
`research/coverage-evaluation.md` records them as the closest thing this field
has to a black-box "missing error states" denominator. You only get that table
if you have a graph. It also gives the Healer a non-heuristic signal: a mutating
request fired and the state key did not move ⇒ application defect; no request
fired and the descriptor did not resolve ⇒ broken script.

**Alternatives rejected:** map lives inside the Planner and it emits a flat plan
(lower risk, matches the brief's literal pipeline shape — but coverage gaps
become an LLM judgement, and `research/README.md` is explicit that self-critique
without an external signal degrades output); map backs Planner and Generator
only, Healer stays a standalone re-resolver (hedge; drops the strongest signal
from the stage the rubric weights for "depth of the healer").

**Cost, stated plainly:** nothing downstream ships until the map exists. It is
on the critical path for the whole demo.

**Who:** shivam + Claude.

---

## 2026-09-04 15:30 — State identity is decided cheaply now and revised later

Two moments, not one:

1. **Observation time** — `state_key()`. Match an existing state or create one.
   Deterministic, no model, no confidence score.
2. **Reconciliation time** — over the accumulated graph. `WorldMap` stores
   `transitions[(from, action)]` as a *list*, so a projection that was too
   coarse shows up structurally as an edge with two different successors
   (`nondeterministic()`), and the two observations behind it name the variable
   that should have been identity.

**Why:** whether a difference is behaviourally relevant is not knowable from one
observation, so asking anything — a model or a heuristic — to decide it pairwise
at observation time is asking for a guess. It *is* knowable from the graph:
ignoring a variable that mattered makes the transition function
nondeterministic, which is detectable. This is counterexample-guided refinement,
the same move active automata learning makes when a distinguishing suffix splits
a state (`discussions/01` flagged the prior art).

It also disposes of two objects from `discussions/02`: `Contradiction` (in a
computed map a contradiction *is* a nondeterministic edge — strictly better,
because it is detected rather than reported) and `WorldMapPatch` (it exists to
stop an LLM returning a whole serialised map; our updater is code and can mutate
directly — an abstraction with exactly one implementation).

**Alternatives rejected:** an LLM State Abstractor emitting `StateCandidate`
objects with `behavioralRelevance` floats, per `discussions/02` §6 — puts a
model back inside the loop we removed it from, and `research/README.md` warns
that LLM-emitted probabilities are not calibrated.

**Who:** shivam + Claude.

---

## 2026-09-04 17:00 — Deterministic vs model is decided per component by failure mode

The rule is not "keep the model out of the loop". It is: **what happens when
this component is wrong?**

- Unrecoverable when wrong → deterministic. State identity (`state_key`),
  seen-before lookup, frontier ordering, replay landing checks.
- Self-correcting and *observable* when wrong → let the model do it. Form input
  values are the first case: bad input is rejected, the rejection is observed,
  and that rejection is a state we wanted to find.

**Why:** the Temac/AutoDroid result and WebVoyager's 44.4% "navigation stuck"
are measurements about **choosing the next action**. Generalising them to every
model call imports the conclusion without the evidence. `synth.py` cannot
corrupt the map — the map records what actually happened — and error states are
a deliverable, not a failure.

Input synthesis is also a **precondition, not a plateau activity**: it is needed
at the login page before any exploration happens.

**Alternatives rejected:** deterministic values with an LLM upgrade later —
role-name heuristics produce garbage on any form that is not a login, and
ordering it as an upgrade gets the dependency backwards.

**Consequence:** payloads are logged (`artifacts/invalid-payloads.json`) keyed
by form shape, so the model picks the values *and* the crawl stays
byte-reproducible for a demo *and* re-runs cost nothing.

**Who:** shivam + Claude. shivam identified the over-generalisation.

---

## 2026-09-04 17:00 — The world model persists per run, and `WorldMap` stays pure

`agents/explorer/store.py` is the only place the explorer touches SQLModel.
`WorldMap` keeps plain dicts.

**Why:** `frontier()` is called every iteration of the crawl loop. An ORM-backed
map puts a query in the hot path and couples the model of the application to
the schema it is stored in today.

Scoped to a **run**, not a session: re-crawling after the app changes leaves a
second map beside the first. Comparing two runs' maps is the drift story the
healer is about, and it needs both.

`StateTransition` is deliberately **not** unique on `(run_id, from_key,
action)` — the same action landing somewhere else is `nondeterministic()`
surviving into the database, and unique-ing it would silently discard the one
signal that says the projection collapsed two behaviours.

**Consequence:** `save()` is incremental and idempotent, so `crawl()` takes a
`checkpoint` callback and calls it after every edge. A map that appears only
when the crawl finishes cannot be watched. The same property is what a worker
pool needs — several processes writing one run's rows — which then costs a
claim column rather than a rewrite.

**Who:** shivam + Claude.
