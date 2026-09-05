# AIVAR — Autonomous Test Orchestration Agent

A URL in, a meaningful test suite out, with no human between the stages.
A time-constrained hackathon workspace.

## Where things are

- **`app/`** — the submission. Frontend, backend, agent pipeline. It carries its
  own `CLAUDE.md` with the stack, the layout, what is hardcoded on purpose and
  the gotchas, and it loads on its own when you touch a file there. Do not
  duplicate any of it here.
- **`docs/problem/statement.md`** — the brief and the rubric. FROZEN.
- **`docs/product/decisions.md`** — why a settled design is the way it is.
  Append-only and ~1,000 lines: a citation target, not a document to read
  through. `app/CLAUDE.md` cites the entries that still matter.

Everything else under `docs/` is background — thesis, bets, prior-art reports,
transcripts. Nothing in the running system reads them. Open one only when a file
you are editing names it.

The repo, not the chat, holds project state. **Never hand-maintain state that
can be computed** — no status file, no check counts written into prose. Run
`git log --oneline` and read the code.

## Running

`make` at the repo root proxies into `app/`. Run it with no arguments to list
every target.

```bash
make setup     # first run only: npm install, uv sync, playwright, git hooks
make dev       # web :3000 + api :8000
make pipeline  # the whole claim: URL in, test quality report out
make probe     # observable checks. No API key, no quota
make check     # typecheck + lint
```

`make probe` and `make check` both pass before you hand work off. `make smoke`
is the original walking skeleton and is superseded — use `make pipeline`.

Parallel sessions share this checkout. `make worktree` gives you your own ports
and database; `make list` shows what is running.

## Operating principles

1. Validate the core loop before expanding the system.
2. Prefer an existing open-source implementation over rebuilding infrastructure.
3. Every claim about behaviour needs an observable check — a run, a response
   body, a probe check that fails without the fix. Not "should work".
4. When evidence contradicts the design, change the design.
5. Preserve working behaviour. If the demo path worked an hour ago, it must
   still work now.
