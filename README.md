# AIVAR — Hackathon Workspace

Autonomous QA Agent with Design Intelligence & Self-Healing Test Automation.

## Quickstart

```bash
./scripts/dev.sh
```

- canvas → http://localhost:3000
- system under test → http://localhost:3000/sut?v=1 (try `v=2`, `v=3`)
- API docs → http://localhost:8000/docs

First run only:

```bash
cd web && npm install
cd api && uv sync --python 3.12 && uv run playwright install chromium
```

## Walking skeleton

With the stack running:

```bash
cd api && uv run python smoke_run.py http://localhost:3000
```

Drives the browser against the system under test at v1, then v2 where the
markup has drifted, watches the recorded locator miss, heals it, and writes
screenshots plus an agent timeline into the database. Add an **Artifact
Viewer** widget on the canvas with path `run-1/v2.png` to see the evidence.

## Parallel work

```bash
./scripts/worktree.sh new alice   # own branch, own ports, own database
./scripts/worktree.sh list        # who is running where
./scripts/worktree.sh rm alice
```

Each worktree runs a full independent stack, so all three of us can have the
app open at once and compare. The UI header shows which worktree you are
looking at.

## Where things are

See `CLAUDE.md` — it maps the workspace and states the operating rules.
