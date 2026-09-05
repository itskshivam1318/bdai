# Incremental behavioural synthesis

**Status:** design, not implemented. 2026-09-05.

The behavioural model is built by one model call over a finished map. This
replaces that with a conversation that runs *beside* the crawl, fed a few
states at a time, on a thread the crawl never waits for.

## Why

Three problems, one shape.

**1. One call over a whole map is the largest reply the run ever asks for.**
On 2026-09-05 `sarvam-105b` came back `finish_reason: length` with the `model`
tool call severed mid-JSON. `_arguments` yielded `{}` and `behavior.synthesise`
reported *"no behavioural model returned; the map stands alone"* — a sentence a
model that had *declined* would also produce. The ceiling has since been raised
and the message now names truncation when it is the cause (see
`agents/llm/catalog.py`, `agents/probe.py:_ceiling_checks`), but the underlying
shape is unchanged: the reply's size scales with the map, so the ceiling is
approached again on every larger app. Six replies about four states each do not
approach it at all.

**2. Nothing sees a state until every state has been seen.** The crawl is the
longest stage of a run. Judgement about state 3 could begin while state 20 is
still being walked, and today it cannot begin until the crawl is over.

**3. A claim written over the finished map has no idea which states changed
it.** Fed incrementally, each turn is told what arrived since the last one, so
a hypothesis is attached to the evidence that prompted it.

## What is already here

- **`crawler.py:326` calls `checkpoint(world)` after every edge.** The trigger
  exists and `store.save` already uses it. Nothing new is needed to know when
  states arrive.
- **Multi-turn is the normal case.** `Transcript` / `Exchange` and the
  `provider.turn` loop already drive `ant.py` and `orchestrator.py` across many
  turns. `synthesise` is the outlier at one.
- **`admit()` needs almost nothing.** It reads `world.vocabulary()` and the
  keys of `world.states` (`behavior.py:292,305`) and nothing else.

## Constraints found while investigating

**`emit` is not thread-safe, and will not say so.** `routers/explore.py:283`
closes over one SQLModel `Session` and commits on it. `db.py:10` sets
`check_same_thread: False`, so SQLite will not raise from a second thread — the
Session's unit of work corrupts quietly instead. **The worker thread must never
touch the database.** Everything it wants to say crosses back on the crawl
thread.

**Two call sites, not one.** The console's Start button does not go through
`pipeline.run`. `routers/explore.py:468` has its own `crawl` → `orchestrator.run`
sequence. `make pipeline` uses `pipeline.run`. Both need wiring or the feature
exists on one path and not the other — the failure mode this repo has hit
before.

**`pipeline.run` takes no `checkpoint`.** Its crawl at `pipeline.py:288`
persists nothing until it finishes, so a pipeline run's map is unwatchable too.
Adding the parameter is required here and fixes that as a side effect.

## Design

### `Ground` — the citation guard, detached from a live map

New value in `worldmap.py`:

```python
@dataclass(frozen=True)
class Ground:
    states: frozenset[str]
    actions: frozenset[str]
```

`WorldMap.ground()` builds one. `admit(world, raw)` becomes
`admit(ground, raw)`; its rule is unchanged.

The point is threading. `world.vocabulary()` iterates `self.states`, and a
crawl thread inserting into that dict while a worker thread iterates it raises
`RuntimeError: dictionary changed size during iteration`. A frozen `Ground`
taken on the crawl thread cannot tear.

Validating a late reply against an *older* `Ground` is safe in one direction
and that is the direction we are in: states are never removed from a map, so a
citation that resolved then resolves now. The guard can only be too strict,
never too lax.

### `delta_brief(world, since)` — what arrived since the last turn

`brief()` restricted to states whose keys are not in `since`, plus the standing
`world.summary()` line so the model keeps the shape of the whole. Turn 1 is
today's `brief()` unchanged.

### `BehaviourSession` — one transcript, hypotheses accumulating

Holds a `Transcript`, an `admitted` list and a `dropped` count. `feed(text,
ground)` makes one `provider.turn`, admits what comes back, appends. `model()`
returns the accumulated `BehaviorModel`.

**Accumulate only. No turn may withdraw an earlier hypothesis.** A withdrawal
is a soft form of the thing `behavior.py` exists to prevent — a model grading
its own claim — and it is the *invisible* form, because a claim deleted before
`examine()` leaves no count behind. A claim that later states contradict comes
back `contradicted` from the map, which is a finding.

### The worker

```
crawl thread   ──edge──edge──edge──edge─────edge──edge──edge──edge──▶
  checkpoint()          │ push(delta_brief, ground)   │ push(…)
                        │ drain() ──▶ emit() ──▶ DB   │ drain()
worker thread           ├── provider.turn ──▶ admit ──┤
```

- `tick(world)` is the `checkpoint` callback. It counts new states; at ≥ N
  (default 4) it builds `delta_brief` and `ground()` **on the crawl thread**
  and puts them on an input queue. It then drains the output queue and calls
  `emit` for each — also on the crawl thread.
- The worker loops on the input queue, calls the model, admits, and puts
  `(level, message)` tuples on the output queue. It touches no database, no
  `Page`, and no live `WorldMap`.
- `close()` signals the end, joins with a timeout, drains what is left and
  returns the `BehaviorModel`.

Failure containment, in priority order:

1. No provider → no worker. Today's behaviour exactly.
2. The worker raises → the exception becomes a queued `error` event, the crawl
   is untouched, the run continues on the map alone.
3. `close()` times out → the run continues with whatever was admitted.

**A crawl must never fail because behaviour synthesis did** (`CLAUDE.md`
principle 5).

### Wiring

- `pipeline.run` gains `checkpoint=`; builds the worker; passes
  `checkpoint=worker.tick` to both `crawl` calls.
- `orchestrator.run` gains `behaviour=`. Given one, it **skips** `synthesise`
  at `orchestrator.py:498` — otherwise the model is paid for twice.
- `routers/explore.py:468` does the same around its own crawl.
- `examine()` is unchanged: one pass over the final map, at the end.

### Console

The worker's events emit on `"plan"`. The Planner already owns transcript role
`behaviour` (`web/lib/agents.ts:47`), so no new surface and no new row in two
hand-maintained tables. `agents/probe.py:_surface_checks` enforces that every
emitted surface has a listener.

## Observable checks

Each fails without the code it names.

| Check | Fails when |
|---|---|
| `delta_brief` names only states absent from `since` | a turn re-describes the whole map, and the reply grows with it again |
| `Ground` admits and refuses exactly what `admit(world, …)` did | the citation guard loosened while being detached |
| a `Ground` taken before N more states still resolves its citations | the "too strict, never too lax" claim above |
| the emit callback records `threading.get_ident()`; only the crawl thread's appears | the worker reached the database and the corruption is silent |
| two `feed` calls produce the union of their hypotheses | accumulation regressed to last-turn-wins |
| a worker whose `turn` raises leaves `world.states` intact and the run finishes | failure containment |
| `orchestrator.run` given `behaviour=` makes no `synthesise` call | the model is paid for twice |

## Not doing

- **Revision or withdrawal of hypotheses.** Reasons above. If the accumulate-only
  model produces visible duplication, revisit with the withdrawal itself
  recorded as a claim and ruled on by `examine`.
- **Resuming a crawl from an existing frontier.** Named as a known limit at
  `pipeline.py:44` and unrelated to this.
- **A worker pool.** `store.py` is ready for one and it needs a claim on an
  edge, which is a column. Out of scope.
