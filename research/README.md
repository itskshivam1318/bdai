# Research

Everything here was gathered on 2026-09-04, after the problem statement
dropped. Read `problem/statement.md` first.

| File | Answers |
|---|---|
| `playwright-agents.md` | What Playwright already ships, and the exact seams where a human still intervenes. **Read this one first.** Includes local verification on this machine. |
| `vendor-landscape.md` | Is the brief's premise true? Who has tried this, what broke, what to copy. |
| `exploration-landscape.md` | How to explore an app autonomously, how to represent page state, and how not to destroy the target. |
| `healing-and-triage.md` | Healing algorithms with real numbers, and how to tell a broken script from a real bug. |
| `coverage-evaluation.md` | How to judge a test plan for gaps, and when LLM self-critique helps vs hurts. |

## The five findings that matter most

1. **Playwright v1.56 shipped planner, generator and healer — and no
   orchestrator.** Their docs leave chaining to "your AI tool of choice". The
   one file that chains them, `playwright-test-coverage.md`, is a
   three-sentence prompt template with no evaluation, retry, resume,
   escalation, parallelism or reporting. Verified locally.
   → `playwright-agents.md`

2. **Policy-level escalation is the empty square in the whole market.** ~25
   products surveyed; not one has it. Momentic comes closest and still needs a
   human to type `npx momentic ai triage` at rung 3.
   → `vendor-landscape.md`

3. **Trust, not capability, is the binding constraint.** Forrester: customers
   rate full autonomy **2.2/5**. Tricentis: trust in agents making
   release-impacting decisions **fell 48% → 34%** while adoption rose. This is
   why the rubric pays 15% for presenting *the agent's decisions*.
   → `vendor-landscape.md`

4. **Coverage cannot be self-assessed from the suite alone.** The only two
   credible commercial mechanisms both require data external to the tests
   (production telemetry; recorded sessions). Nobody publishes a black-box
   coverage denominator.
   → `vendor-landscape.md`, `coverage-evaluation.md`

5. **Defect-vs-script classification is a recall problem on a rare class.**
   Google: **84% of Pass→Fail transitions are flaky**; only **1.23% of tests
   ever found a breakage**. Always answering "script problem" is usually
   right — so accuracy without a class breakdown means nothing.
   → `healing-and-triage.md`

6. **Intrinsic self-critique reliably degrades output.** GPT-4 self-correcting
   drops GSM8K 95.5 → 89.0 over two rounds; as a plan verifier its false
   positive rate is **84.4%**; on graph colouring self-critique scored **1%** vs
   16% for no iteration, and identically to **deliberately false feedback**. It
   only works with an external, non-fabricable signal.
   → `coverage-evaluation.md`

## Cheap mechanisms worth stealing

Each is documented, specific, and needs no model call to be impressive.

| Mechanism | Source | What it does |
|---|---|---|
| **DeFlaker rule** | ICSE 2018 | A newly failing test that executed none of the changed code is flaky. 95.5% recall, 1.5% false alarms, 4.6% overhead |
| **Retry ladder** | QA Wolf | concurrent → batches of 5 → serial. Surviving serialisation ⇒ not environmental |
| **Healing invariant** | Functionize | *"Self-healing is constrained by your verifications. It cannot override a failed verification."* |
| **Reproducer agent** | Momentic `Mo` | A *separate* agent must reproduce a suspected bug from a clean start; one it cannot reproduce stays out of the report |
| **ActionGuard** | Magentic-UI (MIT) | Classify every action always/never/maybe-irreversible; route "maybe" to a judge |
| **Failure taxonomy** | mabl | Regression / Test-implementation / Environment / Network / Timing / self-blame |
| **Invalid-transition cells** | ISTQB CTFL v4.0.1 | The empty cells of a states × events table are enumerable candidate error-state tests — the closest thing to a computable "missing error states" denominator |
| **Extractive quotes** | Rulers, arXiv:2601.08654 | Every rubric decision must cite a verbatim span, so a judge cannot assert a gap it cannot point to |
| **Fixed external checklist** | ISTQB + Bach + Hendrickson | The checklist IS the external signal a critic needs — authored by standards bodies, not invented at runtime |

## Two rules that fall out of the research

1. **Report a prioritised list of gaps, never a calibrated percentage.** LLM
   judges rank reliably (Spearman ρ > 0.6) but score unreliably, and 33–41pp of
   kappa deflation makes any "coverage = 72%" claim indefensible.
2. **Never let the model that wrote the plan be the model that judges it.**
   Self-preference bias is measured and significant.

## Standing warnings

- **Do not claim novelty on planning, generation or healing individually.** All
  three are free in Playwright and shipped by 20+ vendors.
- **Exploring a live app with real credentials is the riskiest configuration in
  this field.** Every vendor with a documented position uses staging. An agent
  in ST-WebAgentBench created an unwanted repository while trying to file an
  issue.
- **Anchor expectations to WebTestBench (26% F1) and CATTest (43%)**, not
  WebVoyager's saturated 99%.
- **mabl built runtime autonomy, launched it 2026-04-23, killed it
  2026-08-03.** Know why before pitching.
