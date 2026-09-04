# AIVAR — Hackathon Workspace

Autonomous QA Agent with Design Intelligence & Self-Healing Test Automation.

## Quickstart

```bash
make setup   # first run only
make dev
```

- canvas → http://localhost:3000
- system under test → http://localhost:3000/sut?v=1 (try `v=2`, `v=3`)
- API docs → http://localhost:8000/docs

Run `make` on its own to list every target.

## Walking skeleton

With the stack running:

```bash
make smoke
```

Drives the browser against the system under test at v1, then v2 where the
markup has drifted, watches the recorded locator miss, heals it, and writes
screenshots plus an agent timeline into the database. Add an **Artifact
Viewer** widget on the canvas with path `run-1/v2.png` to see the evidence.

## Parallel work

```bash
make worktree name=alice   # own branch, own ports, own database (~4s)
make list                  # who is running where
make rm name=alice
```

Each worktree runs a full independent stack, so all three of us can have the
app open at once and compare. The UI header shows which worktree you are
looking at.

## Where things are

See `CLAUDE.md` — it maps the workspace, states the operating rules, and lists
the gotchas worth not rediscovering.
