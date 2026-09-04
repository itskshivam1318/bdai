# AIVAR — Hackathon Workspace

Time-constrained experimental workspace. Theme: **Autonomous Test Orchestration
Agent** — a URL in, a meaningful test suite out, no human between the stages.

## Map

| Path | Answers |
|---|---|
| `app/` | **The submission.** Frontend, backend, agent pipeline. Carries its own `CLAUDE.md` with the stack, layout, contracts and gotchas. |
| `docs/problem/statement.md` | What are we solving? FROZEN — the brief, verbatim in substance. |
| `docs/product/thesis.md` | What are we building, and what does the demo show? |
| `docs/product/bets.md` | What are we unsure about, and how will we find out? |
| `docs/product/decisions.md` | What has already been settled? Append-only. |
| `docs/execution/packets/P*.md` | Who owns what, and what "done" means. |
| `docs/research/README.md` | Prior art. **Read the index; open a full report only when the index says it answers your question** — the reports total ~1,300 lines. |

The repo — not the chat — holds project state. Read these before asking a human
anything; if the answer isn't there, that's a bug in the files.

**Never hand-maintain state that can be computed.** There is no `status.md`: run
`git log --oneline` and read the packet files. A stale status file misleads
three agents at once — worse than none.

## Operating principles

1. Validate the core loop before expanding the system.
2. Prefer an existing open-source implementation over rebuilding infrastructure.
   Search GitHub before writing a subsystem.
3. Every claim about behaviour needs an observable check — a run, a screenshot,
   a response body. Not "should work".
4. Surface ambiguity instead of guessing. Write it into `docs/product/bets.md`.
5. Parallel work has explicit ownership. Never modify files owned by another
   packet; propose a contract change in `docs/product/decisions.md` instead.
6. When evidence contradicts the design, change the design.
7. Preserve working behaviour. If the demo path worked an hour ago, it must
   still work now.

## Running

`make` at the repo root proxies into `app/`. Run it with no arguments to list
every target.

```bash
make setup   # first run only: npm install, uv sync, playwright
make dev     # web + api
make smoke   # walking skeleton: drive a browser, break a locator, heal it
make check   # typecheck + lint — run before handing work off
```

Working inside `app/`? `app/CLAUDE.md` carries the stack, the layout, what is
hardcoded on purpose, and the gotchas. It loads on its own when you touch a file
there — do not duplicate any of it here.
