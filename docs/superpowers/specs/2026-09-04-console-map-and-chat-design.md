# Console: the map as the demo surface

**Date:** 2026-09-04   **Status:** approved, not implemented

## Objective

A session screen where a judge watches the pipeline happen: the application's
state graph draws itself, the five brief stages fill in beside it, and a chat
below answers questions about any state you tag.

The map is not a visualisation *of* the pipeline's output. It **is** the
output — `generator.py` compiles each scenario from a path the crawler walked,
so a test is a route through this graph and a verdict is a colour on it.

## What already exists

Nothing below needs building. It is listed so the plan does not rebuild it.

| Capability | Where |
|---|---|
| States, transitions, evidence, per run | `app/models.py` — `AppState`, `StateTransition`, `StateObservation` |
| Map persisted incrementally during a crawl | `agents/explorer/store.py::save` |
| Map serialised to a file, and two maps diffed | `agents/explorer/snapshot.py` |
| Scenarios as paths, with relative assertions | `agents/generator.py` — `Scenario` / `Step` / `Expectation` |
| Verdicts `PASSED / HEALED / DEFECT / ESCALATE` + the diff behind each | `agents/runner.py` — `Result` / `StepResult` / `Resolution` |
| Streamed timeline with a `surface` seam | `app/models.py::Event`, `web/lib/widgets/surfaces.ts` |
| Static artifact serving at `/artifacts/<path>` | `app/main.py`, `app/config.py::artifacts_dir` |
| Exploration kicked off from the UI | `app/routers/explore.py` |

`surfaces.ts` already carries a TODO block naming the six pipeline surfaces
(`plan`, `coverage`, `suite`, `heal`, `defect`, `report`). This spec fills that
block in; it does not invent the seam.

## Layout

`SessionView` keeps its header and run controls, and splits its body:

```
┌─ header ── name · run 3 · running ─────────── url ── [Run again] ─┐
├──────────────────────────────────┬────────────────────────────────┤
│  MapPane            (60%)        │  StageRail          (40%)      │
│  React Flow + dagre              │  five cards, filled in order   │
│  state nodes, action edges       │  scrolls independently         │
├──────────────────────────────────┴────────────────────────────────┤
│  ChatPane — @-mention autocomplete over the run's discovered states│
└───────────────────────────────────────────────────────────────────┘
```

`Canvas.tsx` and the widget registry are not deleted; they stop being the
session's primary surface. The polling loop and the `widgetsToOpen` dedupe
(keyed on originating event id) move into `StageRail` essentially verbatim —
the surface→panel mapping is the same idea with fixed slots instead of free
placement.

## The map pane

**Data.** One node per `AppState` row, one edge per `StateTransition` row,
both scoped to the currently selected run. Layout by dagre from `is_entry`,
recomputed whenever the node set changes. Node positions are computed, not
persisted: the graph is regenerated per run and a saved position for a state
that no longer exists is worse than no position.

**A node renders:** screenshot thumbnail, `label ?? title`, the fillable field
names parsed from `actions[]`, an action count, and a verdict badge.

**Zoom-dependent detail.** Below zoom 0.6 a node collapses to a chip (icon,
label, verdict dot). A 25-state map — the size the practicesoftwaretesting.com
crawl actually produces — is unreadable as 25 thumbnails.

**Painting.** Node colour is the pipeline stage that has reached it, not test
status alone:

| Appearance | Means |
|---|---|
| grey | discovered; no scenario touches it |
| outlined | a generated scenario's path includes it |
| green / amber / red / purple | `PASSED` / `HEALED` / `DEFECT` / `ESCALATE` |

A scenario's path is `[step.from_key for step in steps] + [terminal.expect.to_key]`.
Where several scenarios cross one state, the **worst** verdict wins, matching
the severity order already implemented in `runner.Result.verdict`.

**Edges** carry the action string as a label. `mutating` edges get a heavier
stroke — that a non-GET fired is the signal `runner.py` classifies on, so it
belongs on the picture.

## Screenshots — first observation only

One thumbnail per node is all a card shows, and the colony already runs for
minutes. So capture happens on the **first** sighting of a state key and never
again.

The seam is `WorldMap.record()`: it already branches on `existing is None`, and
that branch is exactly "this state is new". But `record` receives an
`Observation` and has no `Page`, so it cannot shoot the picture itself.

**Mechanism.**

1. `StateNode` gains `screenshot: str | None = None` — a path relative to
   `artifacts_dir`.
2. `WorldMap` gains `attach_screenshot(key, path)`, which replaces the node.
3. `crawler.py` is the **only** site that holds a live page and calls
   `record()` / `connect()` — `tools.py` is read-only over the map ("an ant
   never tells the map what it saw"). So capture has exactly one home. After
   each `record()` / `connect()`:

   ```python
   key = world.record(observation)
   if world.states[key].screenshot is None:
       path = f"run-{run_id}/{key}.png"
       page.screenshot(path=settings.artifacts_dir / path, full_page=False)
       world.attach_screenshot(key, path)
   ```

   `state_key` is a 16-char hex digest, so it is a safe filename as-is.
4. `store.save` writes `AppState.screenshot`; `store.load` reads it back;
   `snapshot.py` serialises it so a stored map still renders offline.

Viewport screenshot, not `full_page`: the card is a thumbnail, and a full-page
shot of a long catalogue is mostly wasted bytes.

**Failure is non-fatal.** A screenshot that raises is swallowed and logged as a
`warn` event. A crawl must not die because a picture failed.

## The stage rail

Five cards, in the order of the brief's must-haves, each filled by the surface
the orchestrator emits:

| Card | Surface | Content | Brief |
|---|---|---|---|
| Plan | `plan` | flows from `Exploration.flows`, each as a sentence | must-have 2 |
| Coverage | `coverage` | gaps from `Exploration.gaps` | must-have 3 |
| Suite | `suite` | generated `Scenario`s, name + step count | must-have 4 |
| Run | `heal` / `defect` | per-scenario verdict; healed steps show rung + old→new | must-have 5 |
| Report | `report` | totals, healer actions, gaps remaining | must-have 6 |

**Cards cross-link into the map.** Clicking a coverage gap highlights the state
it concerns; clicking a heal entry highlights the edge it repaired. This is the
demo: coverage gaps and healer actions are *places on a map*, not lines in a
log.

A surface with no card yet falls through to the timeline, exactly as
`surfaces.ts` documents today.

## The chat

Deterministic and model, layered — not a router that picks one. The
deterministic layer is the model's only source of truth.

```
question + @tags
      │
      ▼
agents/facts.py — resolves each tag to a state_key and pulls the recorded rows:
      actions, incoming/outgoing transitions, scenarios crossing this state,
      their assertions and verdicts, evidence ids, network calls,
      coverage gaps naming it
      │
      ├──▶ intent matches a known question shape
      │        → render the facts directly. No model call.
      │          Renders as an evidence card.
      │
      └──▶ otherwise
               → one model call whose entire context is those facts.
                 Renders as prose, marked
                 "⚠ generated · not a recorded observation".
```

**Fast-path question shapes** (deterministic, zero latency, cannot be wrong):

- what did you assert here / what does this test check
- what happened here / why did this fail
- which tests cover this
- what can I do from here / what are this screen's inputs
- what changed since the last run  (uses `snapshot.compare`)

**Slow path.** Anything else. The model cannot invent a state, an assertion or
a verdict, because the only facts in its context are rows. Provider comes from
`agents/llm.load()`, the same seam exploration uses.

**Provenance is a hard rule.** Recorded rows and model prose never render the
same way. A hallucinated answer that looks like evidence is worse in front of a
judge than no chat at all.

**Steering messages** (`focus on @Checkout`) skip both paths and become the
orchestrator's `intent` for the next wave, as `SessionView.sendIntent` does now.

**Tag resolution.** `@` opens an autocomplete over the run's states, matched on
`label ?? title`. The wire format carries `state_key`, never the display name —
two states can share a title.

## Contracts

New endpoints. Existing routers are untouched.

```
GET  /api/runs/{run_id}/map
  → { entry_key: str | null,
      states: [{ key, url, title, label, is_entry, actions: [str],
                 screenshot: str | null, verdict: str | null }],
      transitions: [{ from_key, action, to_key, mutating, observation_id }] }

GET  /api/runs/{run_id}/suite
  → { scenarios: [{ name,
                    path: [str],                # state keys the scenario crosses
                    verdict: str,               # passed | healed | defect | escalate
                    steps: [{ intent, action, from_key,
                              expect: { moved, mutating, added, removed, to_key },
                              verdict, rung, detail }] }] }

`intent` is `generator.intent_of()` — the human sentence that makes the plan
readable. `rung` is `runner.Resolution.rung` (`exact | structural | similarity
| unresolved`): how much trust a repair earned, which the rail must show
because "healed by structural match" and "healed by name similarity" are
different amounts of evidence.

POST /api/sessions/{session_id}/chat
  body { message: str, state_keys: [str], run_id: int | null }
  → { kind: "facts" | "generated" | "intent",
      text: str | null,          # prose; set only when kind == "generated"
      card: FactCard | null,     # set only when kind == "facts"
      state_keys: [str] }        # what the answer is about, for highlighting

  FactCard = { title: str,
               rows: [{ label: str, value: str, ref: str | null }],
               evidence: [{ kind: str, href: str, label: str }] }

`ref` reuses the opaque-pointer shape of `Event.ref` (`"testcase:12"`), so a
row can link back into the rail. Two disjoint fields rather than one union: the
renderer must not be able to draw generated prose inside the evidence card's
chrome by accident.
```

`GET /map` reads `AppState` and `StateTransition` directly rather than calling
`store.load`, which rebuilds every `Observation` and re-parses every snapshot.
The UI needs neither.

`verdict` on a state is derived, not stored: the worst verdict among scenarios
whose path crosses it.

## Schema changes

| Change | Table | Note |
|---|---|---|
| `screenshot: str \| None` | `AppState` | path relative to `artifacts_dir` |
| `defect`, `escalate` allowed in `status` | `TestCase` | comment-level; the column is a free string |
| `path: str` (JSON list of state keys) | `TestCase` | which states this scenario crosses |

`TestCase` is reused rather than a new table: it already carries `selector`,
`healed_selector`, `status` and `detail`, which is the whole healer record.

SQLite has no migration story here and the hackathon has no data worth keeping
— adding a column means deleting `app.db` and re-running. Say so in the README
rather than building migrations.

## Ordering

1. `GET /api/runs/{id}/map` + `MapPane` rendering grey nodes from a live crawl.
2. Screenshots on first observation → thumbnails appear.
3. Persist scenarios and verdicts → nodes paint.
4. `StageRail` + orchestrator emits the five surfaces.
5. `facts.py` + `POST /chat` fast path.
6. Chat slow path (model).

**Steps 1–4 are the demo.** If time compresses, the chat is what gets cut, not
the map. Step 6 is the only step that can be dropped without leaving a visible
hole — the fast path alone is a working chat.

## Verification

Each is an observable check, per `CLAUDE.md` operating principle 3.

- **Map renders offline.** `snapshot.py` already writes maps to files. A stored
  map from a practicesoftwaretesting.com run loads into `MapPane` with no agent
  running. This is the fast iteration loop for the whole pane.
- **Screenshot discipline.** After a crawl, count `.png` files under
  `artifacts/run-<id>/` and assert it equals `len(world.states)`. A revisit that
  shoots a second picture fails this.
- **Chat fast path.** Unit tests against a fixture map: each question shape
  returns the rows it should, and no provider is constructed.
- **End to end.** `make probe`, then a live run against `/sut?bug=1`: the map
  must paint one red node carrying a `DEFECT` verdict, and the Run card must
  show the healer's reasoning for it.
- **Nothing regressed.** `make check` and `make smoke` still pass; the existing
  widget board still opens from a session that has one.

## Non-goals

- Persisting hand-arranged node positions.
- Editing the map by hand — renaming states, drawing edges, deleting nodes.
- Overlaying two runs' maps in one view. `snapshot.compare` answers "what
  changed" as text; a visual diff is a second feature.
- Replacing the widget board. It stays, unused by default.
