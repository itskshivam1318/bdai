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
│   ├── agents/ant.py       one ant: land, act a few times, report, die
│   ├── agents/tools.py     what an ant and the orchestrator may see and do
│   ├── agents/context.py   the free-text box → credentials + focus + claims
│   ├── agents/claims.py    a typed claim → the tests that already cover it
│   ├── agents/prompts/     ant, orchestrator, critic, analyst — the tunable part
│   ├── agents/llm/         one tool-calling loop, four providers, one catalogue
│   ├── agent_mcp/          MCP server — the pipeline, for an external coding agent
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

Read `agents/explorer/__init__.py` first — it explains the eight modules and
why only one of them (`synth.py`) calls a model. Then `worldmap.py`, which is
the contract.

Two entry points, both printing evidence rather than passing silently:

```bash
uv run python -m agents.explorer.probe     # is the state projection right? (no server needed)
uv run python -m agents.explorer.crawler <url>   # map an app, print states/transitions/gaps
```

### Credentials are redacted at record time, and only there

An `Observation` is persisted verbatim, and it has **three** fields that can
carry what a form was filled with — `snapshot`, `url`, and `network` — all
reaching `StateObservation`, with `url` reaching `AppState` and
`artifacts/runs/*.json` as well. Measured on this workspace's own database on
2026-09-05, before `observer.redact_snapshot` / `redact_url` existed:

| Path | Rows | What was in them |
|---|---|---|
| `snapshot` | 108 | a `Password` node's value — the a11y tree *does* expose it while the field holds one |
| `url` | 48 | a GET form's `password=`; two distinct values, **neither** producible by `synth.py` |
| `network` | 39 | the same credential again, in a recorded request URL |

Nothing masked any of it and no browser behaviour was protecting it. Redaction
happens in `observe()`, before the `Observation` is constructed, so plaintext
never enters one and no consumer has to know — masking at render time would be
too late, because by then it is in `app.db`.

Two properties hold it together, and `agents.probe` checks both. The placeholder
must be **non-empty**: `statekey.field_value` maps `""` to `""` and anything
else to `filled`, so an empty redaction would collapse a filled field into an
unfilled one and merge the post-rejection error state with the pristine form.
And a URL carrying no secret is returned **byte-identical** rather than
re-encoded, because the URL is evidence.

`make scrub` fixes what was recorded before this existed. It rewrites rather
than deletes, so every run, state key and transition survives — `make reset`
also removes the credentials, by removing the evidence. It reads through raw
SQL on purpose: the databases that need scrubbing are old ones, and an old
database is exactly the one whose schema has drifted, so the ORM cannot open it.

### Every model call leaves a transcript

`agents/tracing.py:save_transcript` is called by all five: the orchestrator, each
ant, the critic, the synthesizer and the console's analyst. Files land in
`api/artifacts/transcripts/run-<id>/` — or `adhoc/` when there is no run, which
is every CLI entry point. Each holds the system prompt as it was at the time,
because `prompts/*.md` changes hourly and a transcript that records only the
conversation cannot say which instructions produced it.

Three of the five were silent until 2026-09-05, and the shape of the omission is
worth knowing: `save_transcript` takes a `Transcript`, and only the two agents
running a multi-turn tool loop built one. A single-turn call had nowhere to put
its exchange, so the critic's one ranking — the call that decides the order of
the final report — left nothing behind but a count. Adding a role here is two
lines; skipping it is invisible.

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

**There are two ways in, and the catalogue is the same one.**
`agents/llm/catalog.py` lists every provider, its key variable, its models and
its cheap default. `llm.load()` resolves against it and `GET /api/providers`
serves it to the console's **Advanced** panel, so a model the dialog offers is a
model the backend can build. Add a provider there and both sides gain it; there
is no list of models in the frontend.

A key typed into Advanced is held in `localStorage` and sent as
`X-AIVAR-Provider` / `X-AIVAR-Key` / `X-AIVAR-Model` on every request
(`web/lib/api.ts`), read by one dependency (`app/byok.py`), and passed down to
`load(api_key=...)`. It is never written to `os.environ` -- the API serves every
run from one process, so an exported key would be every concurrent run's key --
and never stored. Absent those headers the server's own `.env` is used, which is
how the demo machine has always worked.

```bash
OPENROUTER_API_KEY                  # probed FIRST by llm.load(), on cost. One
                                    # colony run is ~78 model calls: measured
                                    # 2026-09-04 at $0.089 on qwen3-coder-next
                                    # vs ~$3.42 on claude-opus-5 — 112 runs per
                                    # $10 against 3. Optional companions:
                                    # OPENROUTER_MODEL (any model string),
                                    # OPENROUTER_BASE_URL, which repoints the
                                    # same OpenAICompat class at DeepSeek,
                                    # Groq, Cerebras or a local Ollama, and
                                    # LLM_MAX_TOKENS. That last one is named
                                    # for the class rather than for OpenRouter
                                    # because one OpenAICompat serves DeepSeek
                                    # at 16384 and MiniMax at 32768; unset, the
                                    # cap comes per-model from the catalogue.
                                    # It exists because OpenRouter refuses a
                                    # request it cannot afford *at the
                                    # requested cap* rather than at the tokens
                                    # produced: on a nearly-empty balance
                                    # max_tokens=4096 is a 402 while the same
                                    # call at 512 succeeds for a fraction of a
                                    # cent — and the key still reports $9.81 of
                                    # $10 left, so it reads as a broken key
                                    # rather than an empty account. Lower it
                                    # only when the alternative is not running
                                    # at all
SARVAM_API_KEY                      # Sarvam AI, which is OpenAI-compatible, so
                                    # it is the same OpenAICompat class at
                                    # https://api.sarvam.ai/v1. Default model
                                    # sarvam-m
ANTHROPIC_API_KEY / GEMINI_API_KEY  # llm.load() picks a provider by whichever
                                    # is present. With none of the three, a
                                    # console run degrades to
                                    # `explorer.crawler` — a real map,
                                    # breadth-first, but no flows, no summary,
                                    # no intent, and status `degraded` rather
                                    # than `passed`. Every model call in the
                                    # system goes through `llm.load()`,
                                    # synth.py included — it used to build its
                                    # own Anthropic client and so stayed dark
                                    # on an OpenRouter-only key while
                                    # everything else worked. With no provider
                                    # at all, invalid payloads come from a
                                    # static mutation table that knows nothing
                                    # about the app, and the crawl prints
                                    # "PAYLOADS n from fallback (<reason>)" so
                                    # a degraded run never looks like a good
                                    # one. Claude's default model is
                                    # `claude-haiku-4-5` and comes from the
                                    # catalogue, not from claude.py — changed
                                    # 2026-09-05, see decisions.md; Opus is one
                                    # select away
AIVAR_USERNAME / AIVAR_PASSWORD     # optional. forms.Credentials — without
                                    # these, any login wall stops the crawl at
                                    # one state
```

A run that ends in `error` puts its reason in `run.summary`, and the console's
status label (`run 2 · error ⓘ`) discloses it on click. If a failure is ever
invisible in the UI again, that path is what to fix — not the canvas.

The handoff contract is settled, both halves. Selectors vs intent went to
intent: a plan step carries an `Element.descriptor` (`button:Sign in`), resolved
live against the page by the same code the Generator and the Healer use, so
healing is re-resolution grounded in a state key that was actually observed
rather than in a model's belief.

The other half — *given a `Transition`, what makes it worth a test?* — is
`worldmap.is_flow()`. Three structural signals, no knowledge of what an action
string means: `mutating` (a non-GET fired, so the user accomplished something —
kept even as a self-loop), a self-loop with nothing fired (`textbox:Email
stays`, which asserts nothing), and whether the edge is the one that *discovered*
its destination (a second route into a known state re-tests a screen the suite
already covers). Before it existed the Generator compiled every recorded edge,
and a saucedemo crawl produced "activate the link" beside the login flow with
six sibling product links as six separate tests.

It is deliberately not the same question as `frontier()`: an edge can be highly
informative to the *map* and worthless as a *test*. Modelling and testing are
different jobs.

## The MCP server

`api/agent_mcp/` exposes the pipeline to Claude Code and any other MCP client.
Same claim as the console, different consumer: the brief complains that a human
supplies application context over and over, and the other party with that
problem is **the coding agent that just changed the app** -- it holds the diff
and knows nothing about behaviour.

```bash
make mcp         # stdio server. Needs `make dev` running.
make mcp-probe   # observable checks. Also needs `make dev`.
```

Registered for this repo in `.mcp.json`, so Claude Code picks it up on open.

| Tool | Answers | Needs a key |
|---|---|---|
| `sessions` | what has been mapped already | no |
| `crawl` | map an app deterministically | **no** |
| `explore` | map an app with the agent colony | yes |
| `map` | states, transitions, flows, gaps | no |
| `impact` | which flows touch these user-visible strings | no |
| `verify` | replay flows; passed / healed / defect / escalate | no |

**The join is an accessible name, not a file path.** `impact` takes the strings a
diff changed (`- Place Order` / `+ Complete Purchase`) because that is what a
diff contains and what `statekey.normalize()` keys on. Mapping source ranges back
to files would be a subsystem, and the client can already read its own diff.

**Every mutation goes over HTTP** (`agent_mcp/client.py`), never straight to
SQLite. `list_sessions` computes `run_count` by query and `Canvas.tsx` polls
`sessions/{id}/events` for a `surface` -- both are behaviours of the API, so a
row written around it exists but stays invisible. Driving the API is what makes
an MCP-started run indistinguishable from a browser-started one.
`agent_mcp/probe.py` has a check named `visible` whose only job is to stop that
regressing.

**`crawl` exists so the server works with no API key at all.** An agent
connecting over MCP is on someone else's machine; a server whose every tool needs
a key the user has not set looks broken. `routers/explore.py` already names the
deterministic crawler as the other route to a map.

**It owns no file another track owns.** Reads that want `GET /api/runs/{id}/map`
go through `store.load` instead, because the console track owns that router and
it has not merged. Swap it when that lands -- `agent_mcp/read.py:world_of` is the
only place that knows.

**Crawling our own SUT maps three apps, not one.** `web/app/sut/page.tsx` links
to `?v=1|2|3`, so an unrestricted `crawl` walks into every drift variant and the
map mixes them. Flows recorded in one variant then classify correctly; a flow
recorded across two does not. `make loop` avoids this by using a single scenario.
Point `crawl` at a real target, or expect the mixture.

## Hardcoded on purpose

Fixtures and mocks are fine and expected — but they must be visible, or the next
agent builds on sand:

- `smoke_run.py` drives our own SUT at a fixed URL and a fixed pair of variants,
  and its `heal_locator` answers `get_by_role("button").first` — *any* button.
  It is the walking skeleton that proved the wiring, kept for that history. The
  real thing is `agents/runner.py`; `agents.probe` has a check named "healing
  refuses a control it cannot justify" whose whole job is to stop that toy
  behaviour coming back. Run `make loop`, not `make smoke`.
- No migrations, with one narrow exception. `make reset` is still the tool.
  `db._add_missing_columns()` adds columns `create_all` will never add, because
  it only creates tables it cannot find — and a column may go on that list only
  if an existing row is *correct* without it (nullable, or defaulted). Anything
  needing a value computed from the old row is a real migration, and that is
  `make reset`. `db.adopt_orphan_chat()` is the companion: it gives messages
  written before `ChatThread` existed a thread to belong to.
  `db.init_db()` imports `app.models` for a load-bearing reason — `create_all`
  only builds what has registered itself on the metadata, so without that import
  it silently creates nothing for any caller that has not already imported the
  models.
- The SUT's three variants are hand-written drift, not a real deploy.
- `artifacts/invalid-payloads.json` is the replay log for `synth.py`: what the
  model chose to type, keyed by form shape. It makes a crawl reproducible and
  re-runs free. Delete it to make the agent choose fresh payloads. A
  `"source": "fallback"` entry is a record of a degraded run rather than an
  answer, so it is **not** served once a provider exists — otherwise an
  afternoon with no key set silently pins every later run to the mutation
  table. Model-sourced entries are still served unconditionally, which is where
  the reproducibility comes from.

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

**Unnamed form fields are told apart by position, and read-only ones are
skipped.** `fill_form` matches a field by its accessible name; with no name
there is nothing to match on, so `_next_unnamed` walks the role's fields in
document order and takes the next *editable* one. Both halves are load-bearing:
without the cursor, N unnamed fields all resolve to `.first` and one field gets
typed into N times; without the editability check, a read-only field burns the
full fill timeout and the form is declared unfillable. Measured on
`testingchallenges.thetestingmap.org`, whose form is three
`<input readonly>` and one real field, all unnamed.

Position is used here and refused in `available_actions` for a reason that is
not inconsistency: an *action* becomes a recorded test that must survive drift,
while this is one step inside performing an action, re-derived against the live
page on every run and never written into a spec.

**A button knows which fields it submits, `<form>` or not.** `forms.form_of`
prefers a real `<form>` ancestor -- an author's declaration beats anything
inferred -- and falls back to `_implicit_scope`: climb from the button, stop at
the first ancestor holding a fillable field, and give up the moment another
button or a page landmark comes into view. A region with two buttons in it has
not said which one owns the fields; a region that is `<main>` is not a form.

Measured both directions, and the probe's `FORM SCOPE` section holds both:
`practicetestautomation.com/practice-test-login/` has zero `<form>` elements and
now logs in (`submit[valid]:button:Submit -> /logged-in-successfully/`), where
before it clicked Submit empty until the budget ran out;
`practicesoftwaretesting.com/auth/login` -- the page that motivated the `<form>`
requirement -- gains no new actions, and `Sign in with Google`, `Open chat` and
the language switcher stay plain `button:` actions.

If you loosen either stop rule, run `make probe` and watch that section: the two
failures are opposite, and it is easy to fix one by causing the other.

**Commits are checked against the index, not your working tree.** `git add
<file>` stages the file as it *is*, not the change you made, so with a second
session editing the same checkout you can commit a half of something and still
pass `make check` — the other half is sitting on disk. `.githooks/pre-commit`
materialises the staged tree and typechecks *that*. Enable it with `make hooks`
(`make setup` does); bypass with `--no-verify` only for a WIP commit you intend
to amend.

It installs into the **shared** hooks directory (`$(git rev-parse
--git-common-dir)/hooks`), not via `core.hooksPath`. That setting is written to
the shared config so every worktree inherits it, but the path is relative and
git resolves it against the *invoking* worktree -- so a worktree on a branch
without `.githooks/` got the setting, found nothing, ran no hook and said
nothing. Measured: two worktrees were in exactly that state. The shared
directory is consulted by every worktree by construction and no branch can take
it away.

What lands there is a shim: it prefers `<worktree>/.githooks/pre-commit` so the
version-controlled hook stays the source of truth, and falls back to a snapshot
beside it on branches that have no such file. Edit `.githooks/pre-commit`, then
`make hooks` to refresh the snapshot.

A worktree that has never run `make setup` has no `node_modules` or `.venv`, so
the hook **says which check it skipped and does not claim the tree is sound**.
Refusing there would block a commit for a reason unrelated to it, and a guard
that cries wolf is a guard someone turns off.

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

**This branch adds columns** (`AppState.screenshot`, `AppState.fields`,
`AppState.found_by`, `StateTransition.found_by`, `TestCase.path`), so an
`app.db` from before it predates them. Left in place,
`GET /api/runs/{id}/map` raises `no such column: appstate.fields` and 500s —
and the failure is silent in the UI, because the map pane swallows the error.
Clear it with `make reset`.

## Running

From here or from the repo root; `make` with no arguments lists every target.

```bash
make setup     # first run only: npm install, uv sync, playwright install
make dev       # both servers — web :3000, api :8000
make pipeline  # the whole claim: URL in, test quality report out
make probe     # 266 observable checks. No API key, no quota
make gaps      # crawl an app and rank what the crawl did not cover
make specs     # write generated .spec.ts, then run them with Playwright
make check     # typecheck + lint — run before handing work off
make scrub     # redact credentials already recorded (keeps every run)
make reset     # wipe the database and artifacts
make stop      # kill the servers
```
