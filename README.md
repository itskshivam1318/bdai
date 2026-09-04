# AIVAR — Autonomous Test Orchestration Agent

A URL goes in; a running test suite comes out — explored, planned, generated,
executed, and triaged, with no human choosing between the stages.

The hard part is not driving a browser. It is **deciding**: what is worth
testing, whether enough was tested, and — when something fails — whether the
script broke or the application did.

```bash
make pipeline URL=https://www.saucedemo.com/
```

One command. It explores the app, compiles a test plan from what it found,
runs it, classifies every failure, ranks what it missed, and prints a report —
choosing at each step whether to re-plan, proceed, or escalate.

## The claim

**A failure is not a verdict.** Every step lands in one of four cells, decided
from two observable signals — did the locator still resolve, and did the app do
what it did when this was recorded:

| | Verdict | Means |
|---|---|---|
| resolved as-is, app agreed | `passed` | nothing to do |
| resolved after repair, app agreed | `healed` | **cosmetic drift.** The button was renamed. No action needed. |
| resolved as-is, app disagreed | `defect` | **a real bug.** Nothing here is repairable — there is no broken locator. |
| could not resolve at all | `escalate` | a human has to say what this step now means |

`escalate` is the cell that makes the rest honest. When a locator was repaired
**and** the outcome changed, those two cannot be told apart from a single run,
so the agent says so rather than guessing.

## Measured, not asserted

Every claim here has a command behind it. Against public targets nobody
rehearsed against:

| | Result |
|---|---|
| `saucedemo.com` | 19 states, 24 transitions, crossed the login wall, longest journey 4 steps |
| `practicetestautomation.com` | logs in through a page with **zero `<form>` elements**; 4 replayed flows → 4 × `passed`, 19–183 recorded effects per state, no false positives |
| `testingchallenges.thetestingmap.org` | reported a `defect` — that page was serving `Failed to connect to database`. The locator resolved fine, so it refused to "heal" it. |

Against our own system under test, one recorded scenario replayed three ways:

```
--- 1. baseline            ---   PASSED
--- 2. markup drift  (v=2) ---   HEALED
      structural -- button 'Sign in' -> 'Log in': the only button of its kind
      here, and the form still has 2 matching fields
--- 3. injected defect     ---   DEFECT
      the control resolved exactly and the click landed, but the app stayed put
      where it previously moved. Nothing here is repairable.
```

Same scenario, three outcomes — which is the only way to show the
classification is doing work rather than always answering the same thing.

## For your coding agent

The world map is exactly the context a coding agent lacks. It holds the diff
and knows nothing about behaviour; the map knows behaviour and nothing about
files. `app/api/agent_mcp/` closes that loop over MCP — registered in
`.mcp.json`, so Claude Code picks it up on open:

```
> I renamed the checkout button.

⏺ aivar - impact(names=["Place Order", "Complete Purchase"])
  ⎿ 3 flows act on it; 5 only observe it

⏺ aivar - verify(flows=[...])
  ⎿ HEALED   guest-checkout      'Place Order' -> 'Complete Purchase'
     DEFECT  validation-reject   submit invalid email -> "Order confirmed"

⏺ Two survived the rename — cosmetic. The third is real: moving the email
  field took it out of the form, so it is no longer validated.
```

The join is the **accessible name** — what a diff literally contains, and what
state identity is keyed on. No source instrumentation: the client already reads
its own diff better than we could.

Six tools — `sessions`, `crawl`, `explore`, `map`, `impact`, `verify`. Only
`explore` needs an API key.

## Quickstart

```bash
make setup   # first run only
make dev
```

- console → http://localhost:3000
- system under test → http://localhost:3000/sut?v=1 (try `v=2`, `v=3`, `bug=1`)
- API docs → http://localhost:8000/docs

No API key is required. Without one you still get a map — `crawl` builds it
breadth-first — and the run finishes `degraded` rather than failing: no flow
names, no summary. Copy `app/api/.env.example` to `app/api/.env` to add one.

## What to run

`make` on its own lists every target. The ones worth knowing:

| | |
|---|---|
| `make pipeline URL=…` | **the whole thing.** URL in, test quality report out. |
| `make loop URL=…` | the claim in one command: crawl, generate, run, drift, defect |
| `make crawl URL=…` | map an app deterministically — no model, no key |
| `make explore URL=…` | map it with the agent colony (needs a key) |
| `make gaps URL=…` | rank what the crawl did not cover |
| `make specs` | write real `.spec.ts` files and run them under Playwright |
| `make probe` | 75 observable checks — no API key, real browser, needs `make dev` |
| `make mcp` | the MCP server, for Claude Code |

## Layout

| Path | What it is |
|---|---|
| `app/` | the application — frontend, backend, agent pipeline. Everything shipped. |
| `app/api/agents/explorer/` | observe → identify → map → crawl → store. No model calls. |
| `app/api/agent_mcp/` | the MCP server |
| `docs/` | brief, product thinking, research, decisions. Not shipped. |

## A note on how this is built

There is no coverage percentage anywhere in the output, and that is deliberate
— a percentage over a space you cannot enumerate is indefensible, so gaps are
reported as a ranked list of real cells in a real table instead.

Deterministic and model-driven work is split by one rule: **what happens when
this component is wrong?** Unrecoverable when wrong → code (state identity,
seen-before, frontier ordering). Self-correcting and observable when wrong →
the model may decide it (which inputs to type, which gaps matter most). The
reasoning is in `docs/product/decisions.md`, and the bets that have been
settled — with their measurements, including the ones that came out wrong — are
in `docs/product/bets.md`.

`CLAUDE.md` maps the workspace and states the operating rules. `app/CLAUDE.md`
covers the stack, the pipeline contract, and the gotchas worth not
rediscovering.
