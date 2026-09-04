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
│   ├── agents/             the pipeline — NOT BUILT YET, see below
│   └── smoke_run.py        walking skeleton: browser → drift → heal → evidence
├── web/                frontend — Next.js 16 + React 19 + Tailwind v4
│   ├── app/                layout, page, globals.css
│   │   └── sut/            the system under test (see below)
│   ├── components/         Canvas.tsx, WidgetNode.tsx
│   └── lib/widgets/        the widget registry
└── scripts/            dev.sh (runs both servers), worktree.sh (unused)
```

`web/app/sut/` is our own system under test: `?v=1|2|3` serve the same page with
drifted markup, so self-healing has something real to heal. It is a fixture, not
a product surface.

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
escalate, and synthesises a final test quality report.

`api/agents/` is where the pipeline lives; `git log --oneline -- api/agents/`
says how far it has got. Prior art on coverage evaluation — the hardest of the
four, and 20% of the score — is in `../docs/research/coverage-evaluation.md`;
read `../docs/research/README.md` first to see whether you need it.

<!-- TODO(shivam): the handoff contract between stages. This is the single
     highest-leverage design decision in the build, and it belongs to you, not
     to Claude — everything downstream is shaped by it.

     Define what Planner hands Generator, and what Generator hands Healer.
     Roughly 10 lines. The choice that matters: does the plan carry *selectors*
     (concrete, brittle, but the Generator's job gets trivial), or *intent*
     (`the primary action on the login form` — the Generator resolves it live,
     and healing becomes re-resolution rather than repair)?

     `../docs/product/thesis.md` argues hard for intent, but is stamped
     SUPERSEDED — read it as an argument, not a decision. Write the answer here
     as a small schema or a pair of dataclass signatures, then it is settled for
     every agent and every packet after it. -->

## Hardcoded on purpose

Fixtures and mocks are fine and expected — but they must be visible, or the next
agent builds on sand:

- `smoke_run.py` drives our own SUT at a fixed URL and a fixed pair of variants.
  It is a skeleton proving the loop, not a general runner.
- No migrations. `make reset` is the migration tool.
- The SUT's three variants are hand-written drift, not a real deploy.

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
make setup   # first run only: npm install, uv sync, playwright install
make dev     # both servers — web :3000, api :8000
make smoke   # the walking skeleton
make check   # typecheck + lint — run before handing work off
make reset   # wipe the database and artifacts
make stop    # kill the servers
```
