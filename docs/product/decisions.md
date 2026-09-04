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

---

## 2026-09-04 18:20 — A generated test asserts the transition, not the destination

`generator.Expectation` records what an action **changed** — `moved`,
`mutating`, and the added/removed lines of `explain(before, after)` — and never
`actual_key == expected_key`.

**Why:** `normalize()` deliberately keeps accessible names, so a button whose
copy goes from "Sign in" to "Log in" changes the `state_key` of every state it
appears in. An absolute-key assertion therefore reports **cosmetic markup drift
as an application defect** — the exact confusion the Runner exists to resolve,
reintroduced one level down. A transition delta is drift-immune because both
sides of the diff drift together: the renamed button cancels out, a missing
confirmation heading does not.

Same reasoning one level further: `_behavioural()` drops `/url:` property lines
from the delta. A link's href is markup, and the SUT's own variants differ by
`/sut?v=1` versus `/sut?v=2` and nothing else.

**Consequence:** `to_key` is still carried, as evidence for the report and never
as the pass condition. Field signatures are compared as **sets**, not sequences,
for the same reason — SUT v2 renders Password before Email, and an ordered
comparison refused to heal a form no user could perceive as changed.

**Who:** shivam + Claude.

---

## 2026-09-04 18:20 — Classification crosses two independent signals; it is not a judgement

`runner.py` never asks "is this a bug?". It asks two orthogonal, separately
observable questions and reads the answer off the cross product:

|                | expectation met | expectation missed |
|---|---|---|
| resolved as-is | `passed`        | **`defect`**       |
| resolved healed| `healed`        | **`escalate`**     |
| did not resolve| —               | `escalate`         |

**Why:** Google measured 84% of Pass→Fail transitions as flaky and only 1.23% of
tests ever catching a real breakage (`research/healing-and-triage.md`). Against
that base rate a model asked "script or defect?" scores well by always saying
"script" and knows nothing. Resolution is a fact about markup; expectation is a
fact about behaviour; neither is an opinion.

`escalate` is the cell nobody ships — ~25 products surveyed, zero with
policy-level escalation. When we healed the locator *and* the outcome changed,
two variables moved at once and one run cannot attribute it. Saying so beats
picking the answer that makes the dashboard greener.

**Healing is a ladder of observable rungs**, not a model call: `exact` →
`structural` (same kind, same role, same form field set — the only candidate) →
`similarity` (difflib, gated by a floor *and* a margin over the runner-up) →
refuse. The rung that fired is reported, because "healed structurally" and
"healed by name similarity" are different amounts of trust.

**Alternatives rejected:** an LLM healer proposing a replacement selector. It
can only speak once the deterministic rungs have failed — precisely when its
answer is least checkable — so it belongs above `escalate`, not above
`structural`. The seam is there when a real app needs it.

**Evidence:** `make probe` — ten checks driving the real crawler, generator and
browser. Baseline `passed`, `?v=2` `healed` via `structural`, `?bug=1` `defect`,
`?v=2&bug=1` `escalate`, and a check that healing refuses a control it cannot
justify (the `smoke_run.heal_locator` regression: any button will do).
`make specs` writes eight `.spec.ts` and stock `npx playwright test` passes 8/8.

**Who:** shivam + Claude. Sequenced from a ChatGPT critique that correctly read
the gap as "no executable layer" and incorrectly prescribed a linear
Planner→Generator→Runner→Healer rebuild.

---

## 2026-09-04 19:00 — The critic computes the gaps; the model only orders them

`agents/critic.py::candidates()` derives every coverage gap from the map with no
model call. `prioritise()` hands the model a numbered list and one tool that
takes ids back — there is no field in which to write a new finding, and any id
that was not a candidate is counted and discarded.

**Why:** `research/coverage-evaluation.md` is unusually blunt. GPT-4 as a plan
verifier has an **84.4% false-positive rate**. On graph colouring, self-critique
scored **1% against 16% for no iteration** — and scored *identically to
deliberately fabricated feedback*, meaning the loop was resampling, not
critique. A judge that **ranks** is achievable; one that scores is not. So the
model is given the one job judges are reliable at and is structurally prevented
from the one they are not.

We are in the configuration the research says works: the artifact under review
was **not written by a model**. `generator.py` compiles scenarios from recorded
transitions, so there is no self-preference bias to inherit.

**Denominators, all ISTQB CTFL v4.0.1:** unexercised input partitions (EP
"must include invalid partitions" — a form the crawler submitted successfully
whose rejection path nobody walked), ambiguous edges, untaken offered actions
(0-switch shortfall), and the empty cells of the states × actions table.

**Empty cells are filtered by near-universality.** A cell is a question only
when the action is offered by at least half the states and this one is the
exception. Unfiltered, the SUT produced **63 of 75** items — every one a correct
cell of a real table, and collectively unreadable. This also concedes what the
ISTQB criterion assumes and a browser does not provide: in a state machine you
can *attempt* an invalid event, so every empty cell is testable; in a web UI you
cannot click a control that is not rendered.

**Omission is demotion, not deletion.** A gap the model leaves out of its
ranking is reported after everything it ranked. A gap silently removed is
exactly what the brief's "coverage gaps remaining" line exists to prevent.

**No percentage anywhere.** A denominator exists, but its cells are not equally
meaningful, so dividing by their count would produce a number that looks
calibrated and is not.

**Evidence:** `make probe` — seven new checks, including "a fabricated gap is
discarded, not reported" (a scripted critic that cites a nonexistent id) and
"an omitted gap is still reported, after the ranked ones". `make gaps` against
the SUT returns six unexercised `submit[invalid]` partitions, which is correct:
that crawl had no API key, so the synthesizer was unavailable and no form's
invalid path was ever walked.

**Who:** shivam + Claude.

---

## 2026-09-04 19:30 — The meta-agent routes on computed evidence, and the policy is code

`agents/pipeline.py` is the meta-agent the brief names. It runs
explore → critique → (re-plan only if that would help) → generate → run →
re-verify → report, and records a `Decision(stage, choice, because, evidence)`
at every branch.

**Why the policy is not a prompt.** Almost nothing it decides is a judgement,
because the evidence it decides on was computed by something else: `runner.py`
already classified each failure from two orthogonal observations, and
`critic.py` already ranked the gaps and structurally could not invent one. What
is left is *routing*, and routing on computed evidence is a policy. The model
seams are real and named — `critic.prioritise` orders gaps, the colony chooses
where ants go — they are simply not here. The rubric also pays 15% for
presenting the agent's decisions, and a printed chain of stage/choice/reason/
numbers is more auditable than model prose describing the same routing.

**The decision that earns the file is `addressable()`.** When exploration ends
with gaps open, the naive move is to explore again. Often that cannot help: an
unexercised `submit[invalid]` partition needs an input synthesizer, and without
an API key there is not one — so another thousand actions close none of them.
Measured on the SUT: 6 gaps, **0 addressable**, and the pipeline says so and
proceeds instead of burning a second round. An agent that knows which of its own
gaps it cannot close is the difference between orchestration and a loop.

**`verifiable()` — re-running a suite against a different base URL.** A scenario
that navigates by link is dropped, because a link's destination is absolute:
`link:v2` against a base that is already `?v=2` correctly stays put, and the
classifier then correctly reports a defect — a true answer to a meaningless
question. Before this filter the demo produced 3 false defects on `?v=2`. On a
real deploy the filter keeps nearly everything, because links then point at the
new deploy too.

**Known limit:** re-exploration re-crawls from the entry rather than resuming
the frontier, because `crawl()` builds a fresh `WorldMap` per call. Cheap on a
small app, wasteful on a large one.

**Evidence:** `make pipeline` on the SUT — 9 states, 33 transitions, 6 gaps
none addressable, 8 scenarios (3 unhappy), baseline 8 passed, `?v=2` 2 healed
via `structural`, `?bug=1` 1 defect + 1 passed. `make probe` 41/41.

**Not done:** `app/CLAUDE.md` should gain `pipeline.py` in its layout table and
a line on `make pipeline`. Left untouched because the console work has that file
open; it is the one doc edit outstanding for this change.

**Who:** shivam + Claude.

---

## 2026-09-04 23:30 — A crawl-progress ratio is allowed; a coverage percentage still is not

`GET /api/runs/{id}/progress` reports `walked / offered` — of the (state, action)
pairs the application put in front of the crawler, how many it took — and the
map shows it as `EXPLORED n%`. Everything beside it is a count.

**Why this does not reopen 19:00.** That decision refused a percentage because
its denominator would be the states × actions table from `gaps()`, whose cells
are not equally meaningful: unfiltered, the SUT produced 63 of 75 items, each a
correct cell and collectively noise. Dividing by that count produces a number
that looks calibrated and is not.

`frontier()` is a different denominator, and the difference is not cosmetic.
Every cell in it is a control the **application itself rendered**. It was either
taken or it was not. Nobody has to judge whether the cell was worth having, so
the ratio needs no judgement to read. It says how far the crawler got. It says
nothing about how well the app is tested, and the word "coverage" appears
nowhere on it.

**The report still carries no percentage.** `make probe` still checks `"%" not
in written`, and that check is now load-bearing twice: it is what keeps a
progress number on a map from migrating into a quality claim in a document.

**Refused actions had to be persisted first.** `worldmap.summary()` has always
said a refused action is "NOT unexplored — tried and could not be done", but
`skipped` lived only on the in-memory map. Anything reading the database counted
a login wall it could not fill as frontier it had not reached, so the ratio
could never reach 100% on any app with one. `SkippedAction` stores it with the
crawler's own reason, and `store.save`/`store.load` round-trip it — which also
stops a resumed crawl re-attempting what it already established it cannot do.

**Its denominator grows, and the UI says so.** Discovering a new state adds its
offered actions, so the number can fall while the crawl is working. That is the
crawler finding more application, not losing ground; the readout carries that
sentence rather than hiding the effect.

**The honest coverage line is a count, not a ratio:** `states nothing tested`.
Absence of a verdict is not a pass, and saying "4 states, nothing crossed them"
needs no denominator at all.

**Evidence:** 16 checks on the endpoint (parts sum to the whole; a walked action
beats a refused one; an edge whose origin no longer offers it cannot inflate the
numerator; an empty map is zeroes, not a division by zero) and a `store`
round-trip check that a refusal is written once across repeated checkpoints.
Measured on existing runs: run 1 30.4%, run 3 8.0%, run 6 26.9% — every one of
them reporting `passed`.

**Who:** shivam + Claude.

---

## 2026-09-05 01:45 — Commits are checked against the index, not against your disk

`.githooks/pre-commit` materialises the staged tree with `git checkout-index`,
runs `tsc --noEmit` on it, and imports the staged API. `make hooks` points git at
it; `make setup` calls that, because `core.hooksPath` is clone-local config and
cannot be committed.

**Why:** `git add <file>` stages the file *as it is now*, not the edits you made.
Two sessions were working in one worktree, and `d7270e8` committed a
`SessionView` passing a prop to a `StageRail` that was still uncommitted in the
other one. `make check` passed — the working tree had both halves. Checked out
alone, HEAD did not compile, and nothing said so for two commits. The working
tree is exactly the thing that cannot detect this, so the check had to move off
it.

**It found a second instance on its first run.** `StageRail.tsx` imports
`@/components/TranscriptViewer`, which was untracked; staging the rail without
it would have broken HEAD the same way.

**The API half nearly shipped broken, which is the argument for testing a
check against a known failure.** `python -c` puts the working directory first on
`sys.path`, so running from the real `app/api` imported the real modules and the
staged ones were shadowed — it passed an index with `SkippedAction` deliberately
removed. It now runs from inside the materialised tree.

**Generated types are carried in.** `next-env.d.ts` and `.next/dev/types/` come
from `next dev` and are gitignored, so a materialised tree has neither and every
run would report `Cannot find name 'LayoutProps'` — a fact about the checkout,
not the commit. Copied in so a failure always means the commit is genuinely
broken.

**Cost is ~20s a commit**, most of it the APFS clone of `node_modules` — the
same `cp -Rc` `scripts/worktree.sh` makes, for the same reason. `--no-verify`
remains, for a WIP commit you intend to amend.

**Not chosen: a worktree per session.** It removes the concurrent writer outright
and the machinery already exists (`make worktree name=x`), but it splits
`app.db`, so the crawls and threads this demo is built on would not follow.
Worth revisiting after the hackathon.

**Who:** shivam + Claude.

## 2026-09-05 02:30 — The console's chat is a real conversation, in real windows

Two changes, one cause. `Exchange.follow_up` in `agents/llm` lets a transcript
carry a human's next message, so the chat now serialises to genuinely
alternating user/assistant turns on all three providers. And `ChatThread` makes
a conversation a first-class object with its own window, its own history and its
own selection.

**Why:** the chat was a single stateless completion wearing a chat's clothes. It
rendered its whole history into one user message as `Them: … You: …` prose, so
the model never saw its own replies as turns it had taken — the transcript was
one question, forever. It answered a follow-up only because the answer was
pasted into the question. Nothing about that is visible until you want caching,
or a longer thread, or the model to reason about what it committed to earlier.

**Why `Exchange` and not a flat message list.** `Transcript` models a *round* —
what the model said and did, and what came back — and both serialisers depend on
that shape: Anthropic echoes tool calls inside the assistant turn with results in
the following user turn, Google wants function calls and responses as separate
parts. Neither is reconstructible from the other's flattened form. An ant's round
ends with tool results and a chat's ends with a follow-up question; those are the
same slot. `follow_up` defaults to `""`, so every ant transcript serialises byte
for byte as before, and the probe has a check that says so.

**What each turn now carries.** The map index and the full detail of the attached
states ride on the **current** question only; older questions keep just the names
of what was attached. This is how a chat with attachments behaves — you do not
re-send yesterday's document — and it is why the thread got *cheaper* as it got
longer, not more expensive. What the model knew about an old state is already in
its own reply, which is now actually in the transcript.

**Why threads, and why they are windows.** One question is rarely one subject,
and a selection belongs to a question: with one global set of attached states,
opening a second conversation destroyed the first one's context. Each window
owns its attachments, and the map's rings show the **focused** window's — a union
across every open chat would draw rings that no single Send would honour.

**Why an overlay and not a third column.** The console is a map beside a stage
rail. A column would take width from the graph every time somebody opened a
conversation — including the graph they are asking about. Windows float over the
rail, which is also what makes "minimise" mean something.

**`open` and `minimised` live on the row, not in the browser.** A console
reloaded mid-demo that comes back to an empty right margin has lost work that
looks like it was never there. Closed is not deleted; deleting asks first.

**The intent box went back to doing one job.** It was the chat's input too — one
draft read by both Send and "Start run", already a compromise with one
conversation and not expressible with several, since two windows cannot share a
draft without one typing into the other.

**This adds the first real migration**, narrowly: `db._add_missing_columns()`
adds a nullable column `create_all` will not, and `adopt_orphan_chat()` gives
pre-threads messages a thread. `make reset` is still the tool for anything that
needs a value computed from an old row. The rule is written where the list is:
a column may go there only if an existing row is *correct* without it. A
hackathon database is disposable right up until it holds the map of a
twenty-minute crawl.

**Checks:** 30 against the endpoints with a stub provider (thread lifecycle,
transcript shape per turn, a failing provider writing no rows); 7 in
`agents.probe` on the transcript itself, including a torn thread that would
otherwise send two user messages in a row, and one asserting an ant's round is
untouched; 31 driving the live console with Playwright, ending in a real
two-turn exchange where the model quotes the previous question back from its own
transcript.

**Who:** shivam + Claude.

---

## 2026-09-05 02:00 — Correction: what actually makes a worktree per session expensive

The 01:45 entry, "Commits are checked against the index, not against your disk",
closes with a reason for deferring a worktree per session that is **wrong**. An
append-only log carrying a wrong reason is worse than one carrying none, because
the next reader believes it.

**What it said:** worktrees would split `app.db`, so "the crawls and chat threads
this demo is built on would not follow."

**What is actually true:** `app/api/app/config.py` is a pydantic `BaseSettings`,
so every field takes an environment override. Both of the ones that matter do:

    DATABASE_URL=…  ARTIFACTS_DIR=…  →  database_url  = sqlite:////tmp/x.db
                                        artifacts_dir = /tmp/shared-artifacts

A second worktree can point at this database and these artifacts with two
variables, and the data follows fine. The objection was soft and stated as if it
were hard.

**The real cost, which the original entry did not name:** two live stacks
writing one SQLite file. `store.save` is called after *every edge* -- that is
deliberate, it is what makes a crawl watchable -- so two concurrent crawls
contend on the same file and meet `database is locked`. Sharing the database is
what makes worktrees affordable and is also exactly what makes them risky; the
two cannot be had together without moving off SQLite or serialising crawls.

**The conclusion is unchanged: defer.** But it is deferred because concurrent
writers contend, not because the data cannot be shared. Anyone revisiting this
should start from that, and should note the alternative it implies -- separate
worktrees with *separate* databases are cheap and safe, and cost only that a run
recorded in one is not visible in the other.

**Evidence:** the two-variable override above, run against `app.config` directly.

**Who:** shivam + Claude.
