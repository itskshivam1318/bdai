# AIVAR — Autonomous Test Orchestration Agent

A URL goes in; a meaningful, running test suite comes out — planning, test
generation, execution and repair, with no human between the stages.

## Layout

| Path | What it is |
|---|---|
| `app/` | The application — frontend, backend, agent pipeline. Everything shipped. |
| `docs/` | Problem brief, product thinking, research, execution packets. Not shipped. |

## Quickstart

```bash
make setup   # first run only
make dev
```

- canvas → http://localhost:3000
- system under test → http://localhost:3000/sut?v=1 (try `v=2`, `v=3`)
- API docs → http://localhost:8000/docs

`make` runs from the repo root and proxies into `app/`. Run it on its own to
list every target.

## Walking skeleton

With the stack running:

```bash
make smoke
```

Drives the browser against the system under test at v1, then v2 where the markup
has drifted, watches the recorded locator miss, heals it, and writes screenshots
plus an agent timeline into the database. Add an **Artifact Viewer** widget on
the canvas with path `run-1/v2.png` to see the evidence.

## Where things are

`CLAUDE.md` maps the workspace and states the operating rules. `app/CLAUDE.md`
covers the stack, the pipeline contract, and the gotchas worth not
rediscovering.
