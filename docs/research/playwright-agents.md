# Playwright's own agents — what already exists

Researched 2026-09-04. Verified against playwright.dev docs and the
microsoft/playwright `main` source tree. Stable `playwright` at time of
research: **1.62.1**. `@playwright/mcp`: **0.0.80**.

## The headline

**Playwright already ships Planner, Generator and Healer agents.** Introduced
in **v1.56.0** as "Playwright Test Agents". The brief's three sub-agents are
not a design we invent — they are named after files that exist:

```
packages/playwright/src/agents/
  playwright-test-planner.agent.md
  playwright-test-generator.agent.md
  playwright-test-healer.agent.md
  playwright-test-coverage.prompt.md   ← see below, this matters most
```

They are **Markdown agent definitions plus a purpose-built MCP server**, not
code. The "agent" is whatever LLM your client runs; Playwright supplies the
system prompt, the tool allowlist, and the tools.

Install them with:

```bash
npx playwright init-agents --loop=claude   # writes .claude/agents/playwright-test-*.md + .mcp.json
```

Loops supported: `claude`, `codex`, `copilot`, `opencode`, `vscode`,
`vscode-legacy`.

## What each one does

**Planner** — tools: `search`, 19 `browser_*`, plus `planner_setup_page` and
`planner_save_plan`. Explores the accessibility snapshot (instructed *not* to
screenshot unless necessary), maps user journeys, is told to cover happy path
**plus edge cases plus error handling**, and requires scenarios to be
independent and order-independent. Writes Markdown to `specs/<name>.md` — and
notably the Markdown is **rendered by Playwright from a strict Zod schema**
(`overview`, `suites[].tests[].steps[].perform/expect[]`), not free-written by
the LLM. There is also an unused `planner_submit_plan` that returns the plan as
**JSON** instead of writing a file.

**Generator** — `generator_setup_page` → for each step **actually performs it in
a live browser**, passing the step text as an `intent` argument → then
`generator_read_log` → `generator_write_test`. That is what "live selector
validation" means: every action is executed inside the *paused real test
worker*, and a `GeneratorJournal` accumulates `{intent, emitted Playwright
code}` pairs. Assertions come from `browser_verify_*` tools that record
structured actions, so they are code-generated rather than hallucinated.
Hardcoded rules include: never `waitForLoadState`, `waitForNavigation`,
`waitForTimeout`, or `evaluate`.

**Healer** — tools include `edit`, `test_list`, `test_run`, `test_debug`,
`browser_generate_locator`, console/network readers. Runs the suite, and per
failure re-runs that single test with `pauseOnError: true, timeout: 0,
workers: 1, headed`, inspects at the pause point, edits the source, re-runs.
Two directives worth noting: it may mark a test `test.fixme()` if it is
confident the test is right, and it is told **"Do not ask user questions, you
are not interactive tool."**

## Two different MCP servers — do not confuse them

| | `@playwright/mcp` | `playwright-test` (`npx playwright run-test-mcp-server`) |
|---|---|---|
| drives | a browser it launches | **the paused Playwright test worker** |
| knows | pages | config, projects, fixtures, testDirs, test IDs |
| extra tools | — | `planner_*`, `generator_*`, `test_list/run/debug` |
| browser tools | plain | carry an injected **`intent`** field |

The second is a **hidden command** (`{ hidden: true }` in `program.ts`).

## The gap — precisely

There *is* a file that chains all three: `playwright-test-coverage.prompt.md`,
emitted only with `--prompts`. It says: call the planner; then *"For each test
case from the test plan file (1.1, 1.2, ...), one after another, **NOT IN
PARALLEL**"* call the generator; then call the healer.

**It is a prompt template, not a program.** An LLM in a chat client has to
parse the plan Markdown, extract bullet numbers, and fan out calls. Nothing
enforces it, retries it, resumes it, or checks the result.

Everything below is the concrete seam the brief is pointing at:

| # | Where a human still intervenes | Evidence |
|---|---|---|
| a | **The seed test.** `init-agents` writes `tests/seed.spec.ts` containing only `// generate code here.` — no URL, no auth, no fixtures. A human writes the real one; every stage runs it first. | source + docs |
| b | **The task statement.** No autonomous "what should I test?" mode. Shipped prompt is literally `Create test plan for "add to cart" functionality`. A human supplies scope. No crawl-and-prioritise. | `playwright-test-plan.prompt.md` |
| c | **Plan → Generator handoff is a filename and a bullet number typed by a human**: `Generate tests for the test plan's bullet 1.1`. The Markdown plan is the *only* interface; no run manifest, no coverage ledger. | `playwright-test-generate.prompt.md` |
| d | **Fan-out is manual and strictly serial.** One MCP server holds one paused test. Issue #39235: "MCP test server hangs when multiple healer agents run in parallel" — closed. | source + issue |
| e | **Nothing triggers the healer.** It fires when a human types "run all my tests and fix the failing ones". No hook from `test_run`, no reporter integration, no watcher. | prompt file |
| f | **The pipeline is one-directional and lossy.** Healer edits `tests/`, never writes back to `specs/`. Plans go stale silently. | source |
| g | **Human-approval gates are baked into the tools.** `test_debug`'s schema literally has `title: "Human readable test title for granting permission to debug the test"`. | source |
| h | **No unattended/CI story at all.** No `npx playwright agents run`, no exit code, no artifact bundle, no resume, no budget or guardrail config. Headed by default off-CI. | — |
| i | Agent definitions are static snapshots; must be regenerated on every Playwright upgrade. | docs |
| j | Model choice is baked into frontmatter (`model: sonnet`). No per-stage routing, no token budget. | source |

There is also a **direct contradiction between Playwright's two official
paths**: the MCP healer says "do not ask user questions", while the CLI skill
says *"stop and ask the user"* when it cannot tell an intentional app change
from a regression.

## No runtime self-healing exists

Playwright has **no** runtime locator healing — no `aiFix`, no flag, nothing in
`types.d.ts`. The healer is offline source repair driven by an LLM in a chat
client. Feature requests #6874, #10872, #17756, #33586, #37308 are all closed;
#42468 (community runtime healing package) was closed as not planned.

Closest official primitives, both v1.59: **`locator.normalize()`** — rewrites a
locator to prefer test ids and ARIA roles over CSS — and
`browser_generate_locator`. Neither is automatic.

## Surfaces worth building on

- **`page.ariaSnapshot({ mode: 'ai', boxes: true })`** (v1.59/1.60) — public
  API, replaces the old `_snapshotForAI`. Element refs like `[ref=e2]`,
  includes iframes. `ariaSnapshotJSON` lands in 1.63.
- **Trace as a CLI, no browser needed** — `npx playwright trace
  actions|errors|console|requests|snapshot|attachments`. The single best
  programmatic post-mortem surface for a healer.
- **`Reporter.preprocess({config, suite, testRun})`** — new in **v1.62**, runs
  before `onBegin`, can `testRun.skip(test)` / mark excluded/fixed/failing. A
  real programmatic orchestration hook.
- `--reporter=json` (+ `PLAYWRIGHT_JSON_OUTPUT_FILE`), `--reporter=blob` +
  `merge-reports` for sharding, `.last-run.json` + `--last-failed` (cheapest
  failing-test feed), `--list`, `--test-list`, `--max-failures`.
- `page.screencast` (v1.59) — start/stop, `showActions`, `showChapter`,
  `showOverlay`. Release notes call these "agentic video receipts". Relevant to
  the demo-video deliverable.
- `browser.bind()` (v1.59) — expose a launched browser to `playwright-cli` and
  `@playwright/mcp`.
- Agent Skills shipped in `playwright-core`: `npx playwright init-skills`. The
  `playwright-cli` skill contains a section literally titled *"Test generation
  (plan → generate → heal)"* — a full CLI-driven, MCP-free version of the
  pipeline.

## Flagged as unverified

- `.claude/prompts/` vs `.claude/commands/`: Playwright writes prompts to
  `.claude/prompts/`, but Claude Code registers project slash commands from
  `.claude/commands/`. If still true, the shipped coverage prompt is **not**
  auto-exposed as a slash command. **Check locally.**
- An in-test LLM agent API is hinted at by `__llm_cache__` fixtures on `main`
  but has no source, no types, no docs. Do not build on it.
- `--loop=cursor` merged in PR #42245 but absent from the choices list read.
  Check `npx playwright init-agents --help` locally.
- Web search quota ran out mid-research, so the human-intervention analysis
  rests on primary sources rather than practitioner reports.

---

## Verified locally on this machine, 2026-09-04

Ran `npx playwright init-agents --loop=claude --prompts` in a scratch project.
Everything above is confirmed on disk, plus three corrections/confirmations:

**Installed version: 1.62.1.** `--loop` choices are exactly `claude`, `codex`,
`copilot`, `opencode`, `vscode`, `vscode-legacy` — **no `cursor`**, resolving
that open caveat.

**Files written:**
```
specs/README.md
tests/seed.spec.ts
.claude/prompts/playwright-test-{coverage,generate,heal,plan}.md
.claude/agents/playwright-test-{planner,generator,healer}.md
.mcp.json
```

**The seed test is exactly as described — an empty stub:**
```ts
import { test, expect } from '@playwright/test';
test.describe('Test group', () => {
  test('seed', async ({ page }) => {
    // generate code here.
  });
});
```
No URL, no auth, no fixtures. A human writes the real one, and **every stage
runs it first**. This is seam (a), confirmed.

**`.claude/commands/` does NOT exist** — prompts land in `.claude/prompts/`
only. Claude Code registers project slash commands from `.claude/commands/`, so
**the shipped coverage prompt is not exposed as `/playwright-test-coverage`**.
Caveat confirmed true.

**The planner's tool list confirms the exploration gap.** It has 19
`browser_*` tools plus `planner_setup_page` / `planner_save_plan` — and
`model: sonnet` hardcoded in frontmatter. Its instructions say "Thoroughly
explore the interface", but there is no crawl budget, no state-abstraction, no
loop detection, no stopping rule. Exploration quality is entirely whatever the
LLM improvises.

**`playwright-test-coverage.md` verbatim — this single file is the brief:**

> 1. Call #playwright-test-planner subagent with prompt: `<plan>…</plan>`
> 2. **For each test case from the test plan file (1.1, 1.2, ...), one after
>    another, not in parallel**, call #playwright-test-generator subagent…
> 3. Call #playwright-test-healer subagent with: `<heal>Run all tests and fix
>    the failing ones one after another.</heal>`

Read what is absent: no coverage evaluation between steps 1 and 2 (despite the
filename), no re-plan condition, no retry, no resume, no escalation, no
parallelism, no writing results back to the plan, no report. It is a
three-sentence instruction to a human's chat client.

**Every must-have in the brief maps to something missing from this file.**
