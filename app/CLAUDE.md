# app/ — the submission

Everything we ship lives here. Self-contained: `cd app && make dev` works, and
so does `make dev` from the repo root (which just proxies here). The thinking
that produced this — brief, thesis, bets, research — lives in `../docs/` and is
not shipped.

## Layout

```
app/
├── Makefile            the real one; the root Makefile proxies to it
├── api/                backend — FastAPI + SQLModel + SQLite, driven by uv
│   ├── app/                routers/, models.py, db.py, config.py, main.py
│   ├── agents/explorer/    observe → identify → map → crawl → store. See below
│   ├── agents/pipeline.py  **the meta-agent** — URL in, report out. Start here
│   ├── agents/critic.py    computes coverage gaps; the model may only rank them
│   ├── agents/generator.py map path → scenario → runnable .spec.ts
│   ├── agents/runner.py    execute a scenario; heal, or report a defect, or escalate
│   ├── agents/orchestrator.py  the *exploration* colony — ants, not the pipeline
│   └── smoke_run.py        walking skeleton, superseded by `make loop`. See below
├── web/                frontend — Next.js 16 + React 19 + Tailwind v4
│   ├── app/                layout, page, globals.css
│   │   └── sut/            the system under test (see below)
│   ├── components/         Canvas.tsx, WidgetNode.tsx
│   └── lib/widgets/        the widget registry
└── scripts/            dev.sh (runs both servers), worktree.sh (unused)
```

`web/app/sut/` is our own system under test, and it carries **two orthogonal
knobs** because the product claim is that we can tell two failures apart:

| Knob | Moves | Must not move | The agent should |
|---|---|---|---|
| `?v=1\|2\|3` | markup — ids, button copy, field order, nesting | behaviour | heal and carry on |
| `?bug=1` | behaviour — a completed form returns the form | markup | report a defect, and **not** heal |

Keep them orthogonal. If drift ever changes behaviour, or the bug ever changes
markup, a classifier that is only guessing will start scoring well. It is a
fixture, not a product surface.

## The agent pipeline

The brief (`../docs/problem/statement.md`, FROZEN) requires a meta-agent
coordinating three sub-agents, and weights the *orchestration* — not the
execution — most heavily:

| Sub-agent | Responsibility |
|---|---|
| Planner | explores the app, produces a human-readable test plan |
| Generator | converts the plan into executable tests, with live selector validation |
| Healer | replays failures, repairs broken locators, and distinguishes a broken script from a genuine defect |

The meta-agent evaluates coverage between stages, decides when to re-plan or
escalate, and synthesises a final test quality report. That is
**`agents/pipeline.py`** — `make pipeline` runs the whole thing from a URL with
no stage chosen by a human. Do not confuse it with `agents/orchestrator.py`,
which orchestrates *ants within exploration* and says so in its own docstring.

Its policy is code, not a prompt, and the reason is in `decisions.md`
(2026-09-04 19:30): the evidence it routes on was computed by something else —
`runner.py` classified each failure from two orthogonal observations, and
`critic.py` ranked gaps it structurally could not invent. Routing on computed
evidence is a policy, not an opinion. Every branch records a
`Decision(stage, choice, because, evidence)`, which is what the rubric's 15%
for *presenting the agent's decisions* is actually asking for.

The one to read first is `addressable()`: it decides whether a remaining gap is
one more exploration could close. Six unexercised `submit[invalid]` partitions
with no synthesizer configured are **not** a reason to explore again, and saying
so is the difference between orchestrating and looping.

`api/agents/` is where the pipeline lives; `git log --oneline -- api/agents/`
says how far it has got. Prior art on coverage evaluation — the hardest of the
four, and 20% of the score — is in `../docs/research/coverage-evaluation.md`;
read `../docs/research/README.md` first to see whether you need it.

### The World Map is the spine

Decided 2026-09-04 (`../docs/product/decisions.md`). The three sub-agents are
not a relay passing documents along — they are three operations on one
behavioural model, `agents/explorer/worldmap.py`:

| Sub-agent | Operation on the map |
|---|---|
| Planner | its transitions **are** the plan; `gaps()` are the missing error states |
| Generator | a path through the graph compiles to a test |
| Healer | re-observe, compare state keys, and the failure classifies itself |

Read `agents/explorer/__init__.py` first — it explains the four modules and why
none of them calls a model. Then `worldmap.py`, which is the contract.

Two entry points, both printing evidence rather than passing silently:

```bash
uv run python -m agents.explorer.probe     # is the state projection right? (no server needed)
uv run python -m agents.explorer.crawler <url>   # map an app, print states/transitions/gaps
```

### The map is persisted, and it is watchable

`agents/explorer/store.py` is the only place the explorer meets the database —
everything else holds plain dicts, and `frontier()` is called every loop
iteration, so a query in there would be a query in the hot path.

Three tables, scoped to a **run** rather than a session, so re-crawling after
the app changes leaves a second map beside the first instead of overwriting it.
Comparing two runs' maps is the drift story:

| Table | Holds |
|---|---|
| `StateObservation` | the raw a11y snapshot + network, verbatim. The primary record; states and transitions are derived from it |
| `AppState` | one behavioural state, keyed by `state_key` — never by URL |
| `StateTransition` | one edge, with `mutating` and a pointer to its evidence |

`store.save()` is incremental and idempotent, so `crawl(checkpoint=...)` calls
it after **every edge** rather than at the end. A map that only appears when the
crawl finishes cannot be watched, and watching it is the demo. Re-saving an
unchanged map writes zero rows.

That property is also what a worker pool needs later — several processes writing
into one run's rows. Nothing in `store.py` changes for that; what it needs is a
claim on an edge so two workers do not take the same one, which is a column.

### Configuration

Read from `.env` by `agents/__init__.py` at import — the nearest one walking up
from `api/agents/`, bounded to the repo, so `api/.env` wins over a root `.env`.
An exported variable always beats the file. Copy `api/.env.example` to start.

It is loaded in the package rather than in one entry point because there are
four of them (the API background task, `explorer.crawler`, `probe.py`,
`smoke_run.py`) and all four read these out of `os.environ`.

```bash
OPENROUTER_API_KEY                  # probed FIRST by llm.load(), on cost. One
                                    # colony run is ~78 model calls: measured
                                    # 2026-09-04 at $0.089 on qwen3-coder-next
                                    # vs ~$3.42 on claude-opus-5 — 112 runs per
                                    # $10 against 3. Optional companions:
                                    # OPENROUTER_MODEL (any model string) and
                                    # OPENROUTER_BASE_URL, which repoints the
                                    # same OpenAICompat class at DeepSeek,
                                    # Groq, Cerebras or a local Ollama
ANTHROPIC_API_KEY / GEMINI_API_KEY  # llm.load() picks a provider by whichever
                                    # is present. With neither, a console run
                                    # degrades to `explorer.crawler` — a real
                                    # map, breadth-first, but no flows, no
                                    # summary, no intent, and status `degraded`
                                    # rather than `passed`. Also feeds synth.py:
                                    # without it invalid payloads come from a
                                    # static mutation table that knows nothing
                                    # about the app, and the crawl prints
                                    # "PAYLOADS n from fallback" so a degraded
                                    # run never looks like a good one
AIVAR_USERNAME / AIVAR_PASSWORD     # optional. forms.Credentials — without
                                    # these, any login wall stops the crawl at
                                    # one state
```

A run that ends in `error` puts its reason in `run.summary`, and the console's
status label (`run 2 · error ⓘ`) discloses it on click. If a failure is ever
invisible in the UI again, that path is what to fix — not the canvas.

<!-- TODO(shivam): the handoff contract is now half-settled, and the half that
     is settled is the half that was contentious. Selectors vs intent went to
     intent: a plan step carries an `Element.descriptor` (`button:Sign in`),
     resolved live by `crawler._locate`, which is the same resolution the
     Generator and Healer use. Healing is re-resolution, as
     `../docs/product/thesis.md` argued — but grounded in a state key that was
     actually observed, rather than in a model's belief.

     What is still yours, and still ~10 lines: given a `Transition`, what makes
     it worth a test? QA Wolf's rule is that a flow must describe what a user
     *accomplishes* — it explicitly rejects "Display Search Dropdown". Right now
     `WorldMap.transitions` contains every edge the crawler walked, including
     `link:v2 -> v2` and `textbox:Email stays`. Something has to filter that,
     and the filter decides what the whole test suite looks like.

     Candidate signals, all already recorded on the objects: `mutating` (a
     POST fired), `self_loop` (it stayed put), whether the destination state is
     otherwise unreachable, and path length from the entry. A function
     `is_flow(map, transition) -> bool` in `worldmap.py` settles it for every
     packet after it. -->

## Hardcoded on purpose

Fixtures and mocks are fine and expected — but they must be visible, or the next
agent builds on sand:

- `smoke_run.py` drives our own SUT at a fixed URL and a fixed pair of variants,
  and its `heal_locator` answers `get_by_role("button").first` — *any* button.
  It is the walking skeleton that proved the wiring, kept for that history. The
  real thing is `agents/runner.py`; `agents.probe` has a check named "healing
  refuses a control it cannot justify" whose whole job is to stop that toy
  behaviour coming back. Run `make loop`, not `make smoke`.
- No migrations. `make reset` is the migration tool. `db.init_db()` imports
  `app.models` for a load-bearing reason — `create_all` only builds what has
  registered itself on the metadata, so without that import it silently creates
  nothing for any caller that has not already imported the models.
- The SUT's three variants are hand-written drift, not a real deploy.
- `artifacts/invalid-payloads.json` is the replay log for `synth.py`: what the
  model chose to type, keyed by form shape. It makes a crawl reproducible and
  re-runs free. Delete it to make the agent choose fresh payloads.

If you add another, say so in a comment where it lives and add a line here.

## This is a prototype, not a product

Deliberately out of scope unless the demo needs it:

- Authentication, authorization, multi-tenancy
- Error handling off the demo path
- Tests other than the demo path and `api/smoke_run.py`
- Abstractions with exactly one implementation
- CI/CD integration and cross-browser matrix — the brief excludes both explicitly

## Adding a canvas widget

1. New component in `web/lib/widgets/` taking `WidgetProps`
2. One entry in `web/lib/widgets/registry.ts`

Nothing else changes — not the canvas, not the backend.

## Gotchas worth not rediscovering

**Widget config lives in local state**, not on the xyflow node's `data`.
Mutating `data` is a lint error and causes stale renders; `WidgetNode` holds
state and persists it 400ms after you stop typing.

**Do not symlink `node_modules`.** Turbopack rejects it with *"Symlink
[project]/node_modules is invalid, it points out of the filesystem root"*, and
the failure is nasty: the API starts fine and only the web server dies.
`scripts/worktree.sh` uses an APFS copy-on-write clone instead. Worktrees are no
longer in use, but the trap is still live for anyone copying this tree by hand.

**`config.py` resolves every path from `__file__`**, so `app.db` and
`artifacts/` follow `api/` wherever it moves. Keep it that way.

**A moved or renamed checkout breaks `api/.venv` silently.** uv writes absolute
shebangs (`#!/…/api/.venv/bin/python`), and after a move `uv run uvicorn` fails
with *"Failed to spawn: uvicorn — No such file or directory"* while the web
server starts fine, so it reads as an API bug. `uv sync` will not fix it — it
sees the package set as current and leaves the shebangs alone. The fix is
`rm -rf api/.venv && uv sync`, which takes about 0.1s from uv's cache.

## Running

From here or from the repo root; `make` with no arguments lists every target.

```bash
make setup     # first run only: npm install, uv sync, playwright install
make dev       # both servers — web :3000, api :8000
make pipeline  # the whole claim: URL in, test quality report out
make probe     # 41 observable checks. No API key, no quota
make gaps      # crawl an app and rank what the crawl did not cover
make specs     # write generated .spec.ts, then run them with Playwright
make check     # typecheck + lint — run before handing work off
make reset     # wipe the database and artifacts
make stop      # kill the servers
```
