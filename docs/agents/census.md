# Agent census

Written on `work/agent-forensics`, against `1bfbc48`. Every claim below cites a
`file:line` or a named check. Where a claim is read from code and *not* yet
demonstrated by a run, it says so — that distinction is the point of the file.

Baseline at the time of writing: **115 PASS / 0 FAIL** across the three probe
suites (`agents.explorer.probe` 32, `agents.probe` 71, `app.probe` 12), with
`make dev` up.

## The five headings

The vocabulary this file uses, because the codebase does not yet have one:

| Heading | Question | Where it lives today | Verdict |
|---|---|---|---|
| **Actor** | who acted | `StateNode.found_by`, `Transition.found_by` (`worldmap.py:75,98`), written from `WorldMap.attribution` (`:245,276`) | records who *discovered*, not who *acted* |
| **Action** | what was done | `Transition.action`, an opaque string (`button:Sign in`, `submit[invalid]`) | first-class, deliberately opaque |
| **Context** | the state it was done in | `from_key` — a digest of the normalised a11y tree | first-class, computed, stable across runs |
| **Intent** | what the action was trying to achieve | `generator.intent_of()` (`generator.py:111`) | **not modelled** — see below |
| **Outcome** | what actually happened | `Expectation(moved, mutating, added, removed, to_key)` (`generator.py:78`); `runner` verdicts (`runner.py:60`) | first-class, two orthogonal observations |

**Intent is not modelled.** `intent_of` is a pure function of the action string:
`button:Sign in` becomes the sentence `click Sign in`. That is a rendering, not
a claim. It cannot disagree with the Action, so it cannot be wrong, so it cannot
be evidence — and `pipeline.py:486` joins these sentences into what the report
calls a test plan. The brief asks for a *human-readable plan*; what is currently
honoured is the "readable" and not the "plan".

The correction that opened this branch — Actor is not Intent — is right, and the
conflation on disk is one seat over: **Action is being used as Intent.**

## How many agents

Six things take turns with a model. They are not peers, and the count depends
on what you mean by "agent", so all three layers are listed.

### Model-driven agents — 6

| # | Agent | Started by | Prompt | Decides |
|---|---|---|---|---|
| 1 | **Orchestrator** (colony) | `orchestrator.main` / API task | `prompts/orchestrator.md` | where to send ants, when to stop |
| 2 | **Ant** | `orchestrator.run`, one per assignment | `prompts/ant.md` | what to do in the one state it was sent to |
| 3 | **Critic** | `critic.prioritise` (`:469`) | `prompts/critic.md` | the order of coverage gaps — it may rank, never invent |
| 4 | **Analyst** | `app/routers/chat.py:300` | `prompts/analyst.md` | nothing; read-only answers about a saved map |
| 5 | **Synthesizer** | `synth.Synthesizer._ask` (`:151`) | in code, no file | what invalid input to type |
| 6 | **Pipeline** (meta-agent) | `pipeline.main` | **none — policy is code** | when to plan, generate, run, re-explore, escalate |

Agent 6 calls `load()` (`pipeline.py:575`) but only to name the provider and
build a `Synthesizer`; it takes no turns of its own. Its decisions are
`Decision(stage, choice, because, evidence)` records, which is the design
`decisions.md` (2026-09-04 17:00) argued for: it routes on evidence something
else computed, and routing on computed evidence is policy, not opinion.

Agents 4 and 5 are why `grep 'load()' agents/` undercounts: the analyst lives in
`app/routers/`, and the synthesizer does not use `load()` at all (see F4).

### The brief's three sub-agents — not processes

The brief names Planner, Generator, Healer. None of them is a process. All three
are operations on one `WorldMap` (`docs/product/decisions.md`, 2026-09-04):

| Sub-agent | Actually is |
|---|---|
| Planner | `WorldMap.transitions` filtered by `is_flow` (`worldmap.py:105`); `gaps()` are the missing error states |
| Generator | `generator.py` — compiles a path through the graph into `.spec.ts` |
| Healer | `runner.resolve` + the 2×2 at `runner.py:20` |

### Machinery with no model — 7

`observer`, `statekey`, `noise`, `forms`, `worldmap`, `crawler`, `store`. The
reason the loop calls no model is in `explorer/__init__.py`: 44.4% of
WebVoyager's failures are "navigation stuck", and a model choosing the next
action can loop unrecoverably. Deterministic spine, model at the edges.

## How the world view is built

`Observation` → `state_key` → `WorldMap`. Identity is decided once, cheaply, at
observation time — a digest of the a11y tree after normalisation, never a URL.
So `/product/1` and `/product/2` are usually one state, and `/cart` empty and
`/cart` with three items are two.

Whether that decision was *right* is decided later over the accumulated graph:
`WorldMap.nondeterministic()` (`:338`) reports every state whose projection
collapsed two behaviours. Measuring which differences matter beats asserting it.

**Observed:** the three SUT markup variants produce three *distinct* state keys
(`v1=9d7b742e… v2=1286906a… v3=27442465…`), because the accessible name `Sign in`
→ `Log in` is inside the projection. This is correct and is *not* a leak of the
drift/behaviour separation — because the Healer never classifies on state-key
equality. It uses the 2×2 of (did the locator resolve) × (did the expectation
hold), and `to_key` is documented as "evidence for the report, never the pass
condition" (`generator.py:86`). Verified by the four checks `baseline`, `markup
drift is healed`, `a behavioural defect is reported`, `drift and defect at once
escalates`.

## How agents start and terminate

### Ant — `Report.ended`

| Exit | Set at | Meaning | Probe check? |
|---|---|---|---|
| `reported` | default (`ant.py:61`) | called `report()` | no |
| `stuck` | `ant.py:151` | could not reach its assigned state | no |
| `budget` | `ant.py:307` | spent all 5 actions | no |
| `stalled` | `ant.py:307` | answered in prose, never called a tool | **yes** (`probe.py:259`) |
| `error` | `orchestrator.py:294` | the ant raised; the colony survives it | no |

Bounded twice, deliberately: `actions_taken < budget` **and** `turns < budget*3`
(`ant.py:174`). The turn bound is the fix for the hang that burned three models'
daily quota — an ant answering in prose costs an API call and makes no progress,
so a loop bounded only by actions need never end.

### Colony — `Result.stopped`

| Exit | Set at | Meaning |
|---|---|---|
| `covered` / `plateau` / `budget` | `orchestrator.py:237`, from the model's `finish(reason=…)` | the model's own account of why it stopped |
| `budget` | `orchestrator.py:372` | deadline or wave cap hit, deterministically |
| `error` | **nowhere** | documented at `:82`, never assigned |

Caps: `max_waves=6`, `max_ants=12`, `ant_actions=5`, `max_seconds=900`
(`orchestrator.py:55`). Two caps rather than one because four waves of one ant
and one wave of four ants cost the same in ants and differently in wall-clock.

## Findings

Read from code; none is yet demonstrated by a failing check. Turning them into
checks is the next step, and is what would make them true by this repo's rule 3.

**F1 — a missing stop reason silently becomes the best one.**
`orchestrator.py:237`: `result.stopped = str(call.arguments.get("reason", "covered"))`.
The tool schema constrains `reason` to an enum (`tools.py:193`), but the read
does not re-check it, and the default for an absent field is `covered` — the
strongest claim the system can make about itself ("the map covers the
application's real work"). A model that omits the field, or a provider that does
not enforce enums, produces a run that reports full coverage it never claimed.
The honest default is `unknown`.

**F2 — `stopped` is Intent wearing Outcome's clothes.**
`Result.stopped` reads like a record of what happened. It is the model's
*assertion* about why it stopped, unvalidated against anything observable. The
deterministic sibling one line down (`:372`) is a true Outcome. Two different
kinds of fact share one field, which is the exact confusion this branch exists
to fix — and it is worth fixing here first because it is five lines, not fifty.

**F3 — four of the ant's five exits are unchecked.**
Only `stalled` has a check. `stuck` and `error` are the two that matter: both
mean an ant contributed nothing, and neither is distinguishable from a quiet
success in any output except the timeline.

**F4 — the synthesizer bypasses provider selection.**
`synth.py:39` hardcodes `MODEL = "claude-opus-5"` and constructs `Anthropic()`
directly (`:163`), ignoring `llm.load()`. `load()` probes OpenRouter first
explicitly on cost (`llm/__init__.py:136` — ~$2.15 vs ~$0.06 for a full colony
run). So on the documented cost-optimised path — an OpenRouter key and no
Anthropic key — ants run on OpenRouter and the synthesizer falls back to the
static mutation table. The crawl does print `PAYLOADS n from fallback`, so it is
disclosed, but the cause is not: nothing says *why* it fell back.

**F5 — `intent_of` is a phrasebook.** See "The five headings" above. This is the
one that needs a design, not a patch.

## What this file is not

It does not cover the console, the store's schema, or the API. It covers the
things that take turns and the things that bound them.
