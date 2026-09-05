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

---

## 2026-09-05 02:15 — Intent is derived over the map; no agent declares it at action time

Actor, Action, Context, Intent and Outcome are five different things, and the
code currently models three. `Transition` carries Actor weakly (`found_by`,
recorded on discovery rather than on action), Action and Context first-class,
and Outcome first-class in two orthogonal observations. Intent is not modelled:
`generator.intent_of` is a pure function of the action string, so `button:Sign
in` becomes "click Sign in" and nothing else can ever come out.

**Why:** a field that cannot disagree with its input cannot be evidence.
Verified rather than asserted — the same action with opposite outcomes produces
identical intent, and the signature takes no map, no state and no outcome
(`falsify.py`, 2026-09-05). The report joins these sentences and calls the
result a test plan; what is honoured is the "readable", not the "plan".

**The decision:** intent is **derived data computed over the accumulated map**,
from more information about the application than the per-page action list, and
an LLM's job is to organise that information — not to assert intent at the
moment an action is taken.

**Alternatives rejected:**

*The ant declares its intent before acting.* Rejected on collision: an ant
declares at the moment it knows least, and two ants working the same region
produce contradictory claims with no rule for reconciling them. It also moves
intent to the one place in the system with the least context by design —
`orchestrator.py`'s whole argument is that an ant sees one state in full and
the map as two numbers.

*Computed from the outcome alone* (mutating + reachability, as `is_flow`
already does). Not rejected so much as insufficient: it is free and
falsifiable, but it can only ever say coarse things like "this accomplished
something". Kept as the structural floor; the derived layer sits above it.

**Still open:** what extra information the derivation is allowed to read — page
text, network payloads, form semantics, cross-state structure. That choice
decides what intent can say, and is the next thing to settle.

**Who:** shivam + Claude, on `work/agent-forensics`.

## 2026-09-05 06:45 — One provider seam, and every model call leaves a transcript

A forensic pass over the five model callers — orchestrator, ant, critic,
synthesizer, analyst — found three of them wired around the shared plumbing
rather than through it. All three failures were silent by construction.

**`synth.py` built its own `anthropic.Anthropic()`** and gated on
`ANTHROPIC_API_KEY`. This workspace runs on `OPENROUTER_API_KEY`, so `_ask`
returned `None` on every call and all five entries in
`artifacts/invalid-payloads.json` read `"source": "fallback"`. The seam
`explorer/__init__.py` calls *"the one place a model is worth its cost"* had
never once fired, while every other model call in the system worked.

**The decision:** every model call goes through `llm.load()`. There is exactly
one place that answers "which provider", and adding a second way to find one is
adding a second way to not find one. The structural guarantee that made the
synthesizer a `json_schema` response — a payload or nothing, never prose to
parse — is preserved as a `Tool`, which every provider serialises.

**Degradation must name itself.** `Synthesizer.unavailable` carries the reason
and the crawl prints it (`PAYLOADS 5 from fallback (RuntimeError: ...)`). A
fallback that cannot say why it fell back is indistinguishable from a design.

**`critic.prioritise` wrote no transcript** — the single call that decides the
order of the final report, recorded only as the count `critic ranked 9 of 14`.
The analyst wrote none either. The cause was not a policy: `save_transcript`
takes a `Transcript`, and only the two agents with a multi-turn tool loop built
one, so a single-turn call had nowhere to put its exchange. A dataclass boundary
had become a logging policy without anyone choosing it. All five now write.

**The console ran no critic.** `routers/explore.py` emitted `Exploration.gaps` —
free text the orchestrator wrote into its `finish` call — as `gap:` lines. That
is uncited, unverifiable model output presented as coverage evidence, which is
the exact class of output `critic.py` was built to make structurally impossible,
sitting on the one path the demo actually shows.

**The decision:** the console runs `critic.prioritise` on both paths, model or
not, and the two kinds of statement are **kept apart rather than merged**.
Computed candidates print as `gap [kind] action in state -- risk` and can be
looked up in the map. The colony's prose prints as `noted:` and is kept, because
it names things no cell of a state table can — *"we never got past the login
wall"* is a real observation and not a citation. One is evidence, the other is a
claim; a reader who cannot tell them apart has neither.

`_crawl_only` also stopped formatting its own gap strings from the top three
rows of `WorldMap.gaps()`. That was one of four gap kinds, truncated, and it
made the no-model path's coverage claim mean something different from every
other path's.

**Not a bug, corrected here:** transcripts filed under `adhoc/` rather than
`run-<id>/` looked like broken `run_id` plumbing. It is not — every console run
in `app.db` had errored on "no model configured" or run `degraded` on the
deterministic crawler, so no console run had ever had a model to transcribe.
Same root cause as the synthesizer, one layer up.

**Evidence:** `make probe` — 149 checks, 0 fail, exit 0, measured inside a window
where `(git status --porcelain; git diff; git diff --cached) | shasum` was
identical before and after. Two earlier numbers taken in this session are
withdrawn: they were guarded by `git status --porcelain | shasum`, which hashes
filenames and status letters rather than content and so cannot see a second
session editing an already-modified file. Nine checks are new and each
is one of the above: the synthesizer asking a passed provider, refusing a field
the form has not got, degrading on prose, degrading on a raised exception,
serving the second identical form shape from cache, and the critic filing a
transcript under its run that can reconstruct the call.

**Who:** shivam + Claude, on `work/agent-forensics`.

## 2026-09-05 09:15 — Bring your own key: a provider, a key, and a model that all mean the same thing on both sides

The Advanced panel already existed and was a **prop**. It wrote two keys and a
model into `localStorage`; nothing read them. `grep -rn loadSettings web/`
returned one hit, its own definition. A demo where someone pastes a key, presses
Save and watches the run use ours is worse than no panel, because it looks like
it worked.

**Four providers, and one of them is four.** `claude` and `google` are the two
native SDKs. `openrouter` and `sarvam` are the same `OpenAICompat` class behind
two base URLs -- which is why adding Sarvam cost a table row and a `key_env`
rather than an integration.

**The catalogue is one table, read by both sides.** `agents/llm/catalog.py`
holds the providers, their key variables, their models and their cheap default;
`load()` resolves against it and `GET /api/providers` serves it to the dialog.
The alternative is a `MODELS` array in `SettingsDialog.tsx` next to an `if
provider ==` ladder in `load()` -- two lists that drift, where the drift is
invisible until someone picks a model the backend cannot build.

That is not hypothetical. Writing the check "every provider's default model is
one it lists" found the existing form of exactly this bug: `claude.py` carried
`DEFAULT_MODEL = "claude-opus-5"` while the catalogue's cheap default was Haiku,
so the dialog would have shown *Claude Haiku 4.5 (default)* over a run that
called Opus -- a 38x cost difference, visible only in one line of the timeline.
`DEFAULT_MODEL` in all three provider modules is now read from the catalogue.

**Claude's default model changes: `claude-opus-5` -> `claude-haiku-4-5`.** This
is a behaviour change to `make pipeline`, `make gaps` and anything else that
calls `load()` with no model on an `ANTHROPIC_API_KEY`. It follows the cost
argument this repo has already made twice and measured once: ~78 model calls per
colony run, ~$3.42 on Opus against ~$0.09 on a cheap route. Opus is one select
away and `ANTHROPIC_MODEL` is not consulted; pass `model=` to pin it.

**Keys travel as request headers, not as body fields.** Three endpoints start
model work -- explore, dispatch an ant, answer a chat -- and each already has a
request model describing the *task*. Which key pays for it is not part of any of
those questions. `X-AIVAR-Provider` / `-Key` / `-Model` are read by one
dependency (`app/byok.py`) and attached by one `request()` in `lib/api.ts`, so
the count of places that know about this is two.

**A brought key is passed down the call, never exported.** Writing it to
`os.environ` is the shorter fix and it is wrong: FastAPI runs `_explore` in a
worker thread of one shared process, so two runs started a second apart would
have the second person's key driving the first person's colony. `agents.probe`
asserts the environment is untouched after `load()`.

**A key with no provider is refused rather than guessed.** We cannot tell an
Anthropic key from an OpenRouter one by looking at it, and guessing wrong spends
someone's credit at the wrong vendor. Both layers refuse it -- the dependency
with a 400 so the dialog hears about it, `load()` with a `ValueError` so a
non-HTTP caller does too.

**Nothing is stored.** No row, no log line, no `.env` write. `Choice.redacted`
is what reaches an `Event`, because a timeline that prints a key is a timeline
someone screenshots. The probe compares the served catalogue against the real
environment and fails if any key's *contents* appear in it.

**Checks:** 16 new in `agents.probe` under `BYOK` (153 PASS / 0 FAIL overall).
Live, against the running stack: a bogus Sarvam key returns
`invalid_api_key_error` from `api.sarvam.ai` -- which is also the evidence that
its base URL and bearer auth are right; a bogus Claude key returns Anthropic's
401; an unknown provider returns the 400 naming the four; no headers falls back
to the server's OpenRouter key. In the browser, the dialog's saved state leaves
as `X-AIVAR-Provider: sarvam` / `-Key` / `-Model` on the console's own poll.

**Found while checking:** Custom model entry could not be opened. Choosing
`Custom…` seeded the box with the currently selected id, which was a *listed*
id, so a flag derived from "is this model unlisted" closed the box on the frame
it opened. Custom-open is now its own state.

**Who:** shivam + Claude.

## The reply ceiling is per model, and a truncated reply says so

**What was wrong.** `openai_compat.py` sent `max_tokens: 4096` on every request
to every model, and `claude.py` hardcoded the same number. Against the models
the catalogue offers that is 14% of what `qwen3-coder-next` will emit and 0.4%
of `minimax-m3`. Nothing anywhere read `finish_reason`, so a reply that hit the
ceiling arrived as a stump and was consumed as a finished answer -- which is why
this presented as "the summaries feel shallow" rather than as a configuration
error. The one call that suffers most is `finish`, the only call that returns
every flow *and* the summary in a single reply.

**Why 4096 was there.** A real 402: a paid route reserves the full `max_tokens`
against the balance before it starts, so a nearly-empty account refuses a large
request outright. The constant was lowered to route around a spent key and the
comment above it said to raise it back "once credits allow" -- which no comment
can ever do. A mitigation with no expiry is permanent.

**The number now lives in `catalog.py`**, per model, beside the model. It is
deliberately *not* the model's advertised cap: `max_tokens` plus the prompt must
fit the context window and the provider errors rather than clamping, and the
ceiling doubles as a per-call spend ceiling. 32768 leaves >120k of context
headroom on every listed model against the largest transcript this repo has
ever recorded (~8.4k tokens, measured across 466 files). DeepSeek's true cap of
16384 is below that budget, so it wins; an uncatalogued model from the dialog's
free-text box gets `FALLBACK_MAX_OUTPUT`, the lowest true cap we have seen.

**`LLM_MAX_TOKENS` survives** as the override for the emergency that produced
the original 4096 -- a spent key, where a low ceiling trades truncated replies
for a run that happens at all. The 402 now names it in the error rather than
leaving the raw provider body to be read as a bug in the request.

**Checks:** 5 new in `agents.probe` under `BYOK` (154 PASS / 0 FAIL overall),
including a stubbed `_post` that proves the request carries the model's own
ceiling and that `finish_reason: "length"` produces a warning rather than a
silent stump. Live against OpenRouter: `minimax-m3:free` returns `READY` at
`max_tokens=32768`; the same route at `LLM_MAX_TOKENS=24` truncates mid-word and
emits `the reply hit the 24-token ceiling and was cut off`.

**Found while checking:** the key is out of credit, and this is the more urgent
finding. `GET /api/v1/key` reports `limit_remaining: 9.80`, but that is the
key's *spend cap*, not the balance -- the account affords **268 output tokens**,
so on a paid route even the old 4096 was already 402ing. Free routes are not
reserved against and take the full ceiling. Paid OpenRouter is down until it is
topped up; `minimax/minimax-m3:free` is the working route.

**Who:** shivam + Claude.

---

## 2026-09-05 09:20 — Credentials are redacted at record time, in the observer

`Observation` is persisted verbatim and has three fields that can carry what a
form was filled with. All three were carrying credentials, and nothing masked
any of them. Measured on this workspace's `app.db`:

    snapshot   108 rows, a Password node's value, one distinct value, synthetic
    url         48 rows, a GET form's password=, two distinct values, NEITHER
                producible by synth.py -- both from a configured AIVAR_PASSWORD
    network     39 rows, the same credential again in a request URL

**The decision:** redact in `Observer.observe()`, before the `Observation` is
constructed. It is the one point where all three fields are built, so plaintext
never enters an `Observation` and every downstream consumer — `store`,
`autosave`, the transcripts, the console — is covered without knowing redaction
exists. Render-time masking was rejected: by then the plaintext is in `app.db`.

**Keyed on names, not values.** A field's accessible name and a query
parameter's name are matched against a secret-name pattern. Value-matching is
the backstop only, and only for a configured `AIVAR_PASSWORD` of four
characters or more — `synth.py`'s fallback password is `x`, and redacting every
letter x would destroy the evidence the record exists for.

**Two constraints, both checked:**

- The placeholder must be non-empty. `statekey.field_value` maps `""` to `""`
  and anything else to `filled`, so an empty redaction merges the post-rejection
  error state with the pristine form. `state_key` hashes the snapshot alone, so
  the URL half cannot move identity at all.
- A URL with no secret comes back byte-identical. The URL is evidence, and a
  round trip through `urlencode` would rewrite every other parameter's escaping
  for nothing.

**`make scrub` rewrites; it does not delete.** `make reset` also removes the
credentials — by removing the runs, which makes remediation and history loss the
same button. Scrubbing keeps every row and changes only the secret: verified at
108/48/39 → 0/0/0 on a copy, with all 57 state keys and 258 transitions
identical, and idempotent on a second run. It reads through raw SQL because the
databases that need scrubbing are old ones, and this workspace's own `app.db`
predates `AppState.fields` — `select(AppState)` raises `no such column` on
exactly the data that most needs the fix.

**How this was found, because the method matters more than the finding.** Three
sessions converged on it from three directions and the first two answers were
both wrong. Reasoning from `statekey.field_value` — which reduces input to
presence — says values are not stored; that is the projection, and
`StateObservation` is the record. Testing it *after* a submit shows no password;
that is the navigation having cleared the input. The window is mid-fill, which
is when the crawler observes a rejected or non-navigating submit.

**Severity:** local disk only. `artifacts/` is gitignored bar `.gitkeep` and
`app.db` is untracked, so nothing reached git.

**Who:** shivam + Claude on `work/agent-forensics`; snapshot path found by
bdai-68, URL path by bdai-16, network path and the fix by this session.

## 2026-09-05 09:50 — Redaction covers credentials only, and the model is the reason it can

`cac872e` redacts password-class fields in `observe()`. Emails and usernames are
deliberately left intact. Ratified rather than assumed, after a fourth exposure
path turned up that changes what the scope decides.

**The path.** An ant reads the page and writes free text into `report()`.
Anything it can see, it can repeat into prose that no name-keyed rule will ever
catch, because prose has no field name to key on. Observed on the canary run:
the email appears in an orchestrator transcript as *"Submitting with valid
credentials (email: canary@example.com) leads to a new authenticated state"* —
written by the model, not by any observation.

**Which is why `observe()` is the only correct position.** The password is
absent from every transcript precisely because it was redacted *before*
`tools.describe` rendered the state for the model, so the model never held it to
repeat. Upstream of the model is the one place where that property holds; there
is no second line of defence behind it. Anything left unredacted there can be
laundered into free text, and a later redactor cannot recover it.

**The decision:** credentials only. An email address is frequently the
behaviourally interesting part of a state — *logged in as alice@* is what
distinguishes an authenticated state from an anonymous one — and redacting it
would remove evidence the record exists to hold, on an auth flow, which is the
flow this system is most often pointed at. The acute exposure was the password,
and that is closed.

**What this decision costs, stated so nobody rediscovers it as a bug:** an email
typed into a form reaches `app.db`, `artifacts/runs/*.json` and the transcripts,
and can be repeated by a model into prose. Point this at a production system with
real user data and that is real PII on local disk. `_SECRET_NAME` in
`observer.py` is the one line to widen; the value backstop in
`_configured_secrets` is bounded at four characters and would need the same care,
since a short common value redacts everything.

**Who:** shivam decided the scope; Claude on `work/agent-forensics`.

## 2026-09-05 10:00 — A document between states is a normal thing to meet, not a reason to end a run

A console run of `practicetestautomation.com/practice-test-login/` ended in
`error` after five states:

    Locator.aria_snapshot: Selector "body" does not match any element

**Root cause, reproduced.** That site's "AI Workshop" link leaves for
`luma.com`. While a cross-origin document is committing there is no `body` to
snapshot, and `Observer.observe()` assumed one. Clicking that link and polling
`body` reproduced the empty window **3 times in 6 attempts**. It is not about
content type -- every destination on that site serves `text/html` with a body
once settled; it is about *when* we look.

**Two defects compose, and only one was fixed.**

`crawler.py` calls `observer.observe()` at line 279 and `_same_origin` at line
282. The rule that refuses off-site destinations is correct and cannot fire,
because observing the foreign page raises first. A correct policy one line too
late is indistinguishable from no policy.

The fix is in `observe()`, not in the ordering. It now waits for a `body` on the
same patience budget it already spends on instability, and returns an *empty*
Observation if none arrives -- which the crawler's origin check then refuses on
the very next line. The ordering was deliberately left alone: moving the origin
test above `observe()` would save one settle per off-site link, but it reads
`page.url` before navigation has necessarily committed, and a false refusal
silently drops a legitimate state from the map. That is a worse failure than the
one being fixed. Observing and discarding is always correct.

This follows the rule `observe()` already stated for its stability loop:
returning without agreement "is not a failure and is not raised". A page between
documents is the same kind of fact.

**Measured, same URL, same budget:** before, 5 states / 5 transitions / `error`.
After, 15 states / 36 transitions / 8 scenarios / `failed` -- 5 passed, 2 defect,
1 escalate, 1 gap, 8 rows persisted. `failed` is a verdict; `error` was a crash.

**Checks:** 5 new in `agents.probe` under `NAVIGATION`, offline and
deterministic -- the bodyless document is built by removing the element rather
than by racing a real navigation, and one check asserts the reproduction really
reproduced so the rest cannot pass vacuously. 207 PASS / 0 FAIL overall.

**Also corrected:** the provider catalogue shipped `minimax/m3:free`, which
OpenRouter answers with *"is not a valid model ID"*. The real id is
`minimax/minimax-m3:free`, verified against `GET /openrouter/api/v1/models`.
This is the drift the catalogue exists to prevent, in the catalogue itself --
the check that would have caught it must compare against the live model list,
and does not exist, because the probe is offline by design. Left as a known gap
rather than making `make probe` need the internet.

**Who:** shivam + Claude.

---

## 2026-09-05 10:20 — A 402 may fall back to a `:free` route, but only when asked

`LLM_FREE_FALLBACK` lets an exhausted key retry the call that 402'd on the
provider's own `:free` route. **It is off by default, and that default is the
decision** — the fallback itself is the easy part.

**Why the mechanism is needed.** OpenRouter checks `max_tokens` against the
key's remaining budget *before* the model runs, so a nearly-empty key refuses a
large request outright: "requested up to 32768 tokens, but can only afford 268".
Observed on a wave-3 `dispatch` call while the account still held $10 — the
balance is not what binds, a per-key spend cap is, and the error's own remedy
link points at the key page rather than at `/credits`. This behaviour appears in
no OpenRouter documentation page; it is stated only in third-party write-ups and
in the error text itself. `catalog.py` is where we write it down.

**Why off by default.** `docs/product/bets.md` holds a crawler-vs-colony
comparison. A run that silently finished on a different model than it started on
would corrupt that measurement while looking exactly like a success — no error,
no gap, just numbers that mean something other than their label. Rescuing a demo
is worth an env var; rescuing it invisibly is not. When the fallback does fire it
is announced at `warn` naming both routes, so the timeline records which model
actually produced the flows.

**Alternatives rejected.** *Lowering `LLM_MAX_TOKENS` instead* — the standard
advice for this error, and it does not work at the observed budget: $0.0002 of
headroom is 268 tokens at qwen3-coder-next's $0.80/M, and even a 1024-token
ceiling costs $0.0008. The key was at zero, not merely below 32768. *On by
default* — see above. *Resolving the fallback route across the whole catalogue*
— the retry reuses the key already in hand, so `free_route_for()` is scoped to
one provider; a Sarvam key pointed at MiniMax's route turns a legible 402 into a
baffling 401. Sarvam and Claude have no free tier and correctly raise.

**Switch persists on the instance,** not per call: a colony makes ~78 calls and
re-attempting the dead route on each would spend a round trip per call to
rediscover what the first one proved. `max_tokens` is re-resolved for the new
route, because a ceiling above a model's cap is a 400 rather than a clamp.

**Checks:** 10 new in `agents.probe` under `FALLBACK`, offline and
deterministic — a stubbed `_client` scripts the 402. The first asserts an
*unflagged* 402 still dies, which is the check protecting the A/B; others pin
the loop guard (a negative balance 402s free models too, so falling back to the
route we are already on is reachable), the re-resolved ceiling, the persistence,
and the Sarvam no-free-tier path. 70 PASS / 0 FAIL across all offline sections.

**Not verified live.** The stub proves the wiring; a real 402 needs a spent key,
which lives in the browser under BYOK and never reaches this process.

**Who:** shivam + Claude.

## 2026-09-05 10:50 — Remediation covers the artifacts, not only the database

`make scrub` cleaned `app.db` and reported it clean, and 17 files under
`artifacts/` still carried a non-empty `password=` — two distinct values across
`runs/` and `transcripts/`. `crawler.autosave` writes `Observation.url` into
`runs/*.json`, and a model that read a url off the page repeats it into a
transcript, which no database scrub can reach.

**The decision:** `scrub_artifacts` walks the artifacts tree in the same command,
so "scrubbed" means the whole workspace rather than one store of it.

**Text substitution, not URL parsing.** A transcript is prose with urls embedded
in it — a tool result quoting a state, an ant's summary — so there is no field to
parse. What is stable is the `password=<value>` query form itself, which is how
the credential reached every one of these files. Verified: 17 files changed, 0
credentials remaining, 634 JSON files still valid, and a second run changes
nothing.

**How this was missed, which is the part worth keeping.** The audit that declared
the database remediation complete queried the database. The artifacts were never
in the sample. That is the same shape as three earlier errors in this branch —
reading the projection instead of the record, observing after the navigation had
cleared the field, and a fingerprint that only saw set membership. A green check
means what its sample covered and nothing else, and the sample is the part
nobody writes down.

**Who:** shivam + Claude on `work/agent-forensics`.
