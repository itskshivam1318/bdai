# AIVAR — Hackathon Workspace

Time-constrained experimental workspace. Theme: **Autonomous QA Agent with
Design Intelligence & Self-Healing Test Automation**. Three people, three
worktrees, multiple Claudes.

## What this repo is

The repo — not the chat — holds project state. Read the files below before
asking a human anything; if the answer isn't there, that's a bug in the files.

| Path | Answers |
|---|---|
| `problem/statement.md` | What are we solving? Frozen once written. |
| `product/thesis.md` | What are we building, and what does the demo show? |
| `product/bets.md` | What are we unsure about, and how will we find out? |
| `product/decisions.md` | What has already been settled? Append-only. |
| `execution/packets/P*.md` | Who owns what, and what "done" means. |

**Never hand-maintain state that can be computed.** There is no `status.md`:
run `git worktree list`, `git log --oneline --all`, and read the packet files.
A stale status file misleads three agents at once — worse than none.

## Operating principles

<!-- TODO(shivam): these are a starting point. Cut what you won't enforce,
     add what you will. A constitution nobody believes in is decoration. -->

1. Validate the core loop before expanding the system.
2. Prefer an existing open-source implementation over rebuilding infrastructure.
   Search GitHub before writing a subsystem.
3. Every claim about behaviour needs an observable check — a run, a screenshot,
   a response body. Not "should work".
4. Surface ambiguity instead of guessing. Write it into `product/bets.md`.
5. Parallel work has explicit ownership. Never modify files owned by another
   packet; propose a contract change in `product/decisions.md` instead.
6. When evidence contradicts the design, change the design.
7. Preserve working behaviour. If the demo path worked an hour ago, it must
   still work now.

## This is a prototype, not a product

<!-- TODO(shivam): your anti-quality bar. What are we explicitly NOT building?
     Be specific and ruthless — this is what stops Claude gold-plating. -->

Deliberately out of scope unless the demo needs it:

- Authentication, authorization, multi-tenancy
- Migrations — `make reset` is the migration tool
- Error handling off the demo path
- Tests other than the demo path and `api/smoke_run.py`
- Abstractions with exactly one implementation

Hardcoded values, fixtures, and mocked data are fine and expected. Say so in a
comment; don't hide it.

## Stack

- `web/` — Next.js 16 + React 19 + Tailwind v4, canvas via `@xyflow/react`
- `api/` — FastAPI + SQLModel + SQLite, driven by `uv`
- `web/app/sut/` — the system under test; `?v=1|2|3` serve the same page with
  drifted markup, so self-healing has something real to heal
- `api/smoke_run.py` — the walking skeleton: browser → drift → heal → evidence

## Adding a canvas widget

1. New component in `web/lib/widgets/` taking `WidgetProps`
2. One entry in `web/lib/widgets/registry.ts`

Nothing else changes — not the canvas, not the backend.

## Running

`make` with no arguments lists every target.

```bash
make setup              # first run only: npm install, uv sync, playwright
make dev                # this worktree's full stack (web + api)
make smoke              # walking skeleton: drive a browser, break a locator, heal it
make check              # typecheck + lint — run before handing work off
make reset              # wipe this worktree's database and artifacts
make stop               # kill this worktree's servers
```

Parallel work:

```bash
make worktree name=alice   # own branch, own ports, own database (~4s)
make list                  # who is running where
make rm name=alice         # remove it (branch is kept)
```

Every target behaves the same in the main checkout and inside a worktree —
`scripts/dev.sh` reads `.worktree-env` for this stack's ports. The header in the
UI shows which worktree you are looking at; check it before reporting a bug.

## Gotchas worth not rediscovering

**Do not symlink `node_modules` into a worktree.** Turbopack rejects it with
*"Symlink [project]/node_modules is invalid, it points out of the filesystem
root"*, and the failure is nasty: the API starts fine and only the web server
dies. `scripts/worktree.sh` uses an APFS copy-on-write clone instead — 3s for
475MB, near-zero real disk. If you are tempted to "simplify" that back to a
symlink, don't.

**Python needs no sharing.** `uv sync` hardlinks from uv's global cache, so a
fresh per-worktree venv costs about 0.1s warm.

**Widget config lives in local state**, not on the xyflow node's `data`.
Mutating `data` is a lint error and causes stale renders; `WidgetNode` holds
state and persists it 400ms after you stop typing.
