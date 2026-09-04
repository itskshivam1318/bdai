# Proposed repo document structure

> **External input, not a decision.** This is a captured ChatGPT conversation,
> kept verbatim for provenance. Nothing here is settled until it appears in
> `../product/decisions.md`. Treat the reasoning as an argument to weigh, and
> the citations as leads to check rather than established facts.

| | |
|---|---|
| Source | [Create Agreed Solution](https://chatgpt.com/share/6a9a91ad-a62c-83ee-9b64-7b3f36e76b3c) |
| Held | 2026-09-04, ~1:10 PM IST |
| Captured | 2026-09-04, rendered with Playwright (the page is JS-only; a plain fetch returns an empty shell) |

## What it covers

Reads the (pre-restructure) root `CLAUDE.md` and proposes a `product/solution.md`
sitting between the frozen statement and the thesis, with the taxonomy:

    statement.md  -> what they asked us to build
    thesis.md     -> what we choose to demonstrate
    solution.md   -> how the system conceptually solves it
    bets.md       -> what we don't yet know
    decisions.md  -> what we have committed to
    packets/      -> who builds what right now

Includes draft text for a shortened `thesis.md`, five bets (B1-B5) written as
hypothesis / test / evidence, and six decisions (D001-D006).

Two caveats worth carrying: it declines to overwrite `decisions.md` without
seeing the current contents (correctly - that file is append-only), and it
references a generated `AIVAR_agreed_solution.md` attachment that is **not
recoverable** from the share link.

---

Today 1:10 PM
Uploaded a file
# AIVAR — Hackathon Workspace

Time-constrained experimental workspace. Theme: **Autonomous QA Agent with
Design Intelligence & Self-Healing Test Automation**.

## What this repo is

The repo — not the chat — holds project state. Read the files below before
asking a human anything; if the answer isn't there, that's a bug in the files.

| Path | Answers |
|---|---|
| problem/statement.md | What are we solving? Frozen once written. |
| product/thesis.md | What are we building, and what does the demo show? |
| product/bets.md | What are we unsure about, and how will we find out? |
| product/decisions.md | What has already been settled? Append-only. |
| execution/packets/P*.md | Who owns what, and what "done" means. |

**Never hand-maintain state that can be computed.** There is no status.md:
run git worktree list, git log --oneline --all, and read the packet files.
A stale status file misleads three agents at once — worse than none.

## Operating principles

<!-- TODO(shivam): these are a starting point. Cut what you won't enforce,
     add what you will. A constitution nobody believes in is decoration. -->

1. Validate the core loop before expanding the system.
2. Prefer an existing open-source implementation over rebuilding infrastructure.
   Search GitHub before writing a subsystem.
3. Every claim about behaviour needs an observable check — a run, a screenshot,
   a response body. Not "should work".
4. Surface ambiguity instead of guessing. Write it into product/bets.md.
5. Parallel work has explicit ownership. Never modify files owned by another
   packet; propose a contract change in product/decisions.md instead.
6. When evidence contradicts the design, change the design.
7. Preserve working behaviour. If the demo path worked an hour ago, it must
   still work now.

## This is a prototype, not a product

<!-- TODO(shivam): your anti-quality bar. What are we explicitly NOT building?
     Be specific and ruthless — this is what stops Claude gold-plating. -->

Deliberately out of scope unless the demo needs it:

- Authentication, authorization, multi-tenancy
- Migrations — make reset is the migration tool
- Error handling off the demo path
- Tests other than the demo path and api/smoke_run.py
- Abstractions with exactly one implementation

Hardcoded values, fixtures, and mocked data are fine and expected. Say so in a
comment; don't hide it.

## Stack

- web/ — Next.js 16 + React 19 + Tailwind v4, canvas via @xyflow/react
- api/ — FastAPI + SQLModel + SQLite, driven by uv
- web/app/sut/ — the system under test; ?v=1|2|3 serve the same page with
  drifted markup, so self-healing has something real to heal
- api/smoke_run.py — the walking skeleton: browser → drift → heal → evidence

## Adding a canvas widget

1. New component in web/lib/widgets/ taking WidgetProps
2. One entry in web/lib/widgets/registry.ts

Nothing else changes — not the canvas, not the backend.

## Running

make with no arguments lists every target.

bash
make setup              # first run only: npm install, uv sync, playwright
make dev                # this worktree's full stack (web + api)
make smoke              # walking skeleton: drive a browser, break a locator, heal it
make check              # typecheck + lint — run before handing work off
make reset              # wipe this worktree's database and artifacts
make stop               # kill this worktree's servers

Parallel work:

bash
make worktree name=alice   # own branch, own ports, own database (~4s)
make list                  # who is running where
make rm name=alice         # remove it (branch is kept)

Every target behaves the same in the main checkout and inside a worktree —
scripts/dev.sh reads .worktree-env for this stack's ports. The header in the
UI shows which worktree you are looking at; check it before reporting a bug.

## Gotchas worth not rediscovering

**Do not symlink node_modules into a worktree.** Turbopack rejects it with
*"Symlink [project]/node_modules is invalid, it points out of the filesystem
root"*, and the failure is nasty: the API starts fine and only the web server
dies. scripts/worktree.sh uses an APFS copy-on-write clone instead — 3s for
475MB, near-zero real disk. If you are tempted to "simplify" that back to a
symlink, don't.

**Python needs no sharing.** uv sync hardlinks from uv's global cache, so a
fresh per-worktree venv costs about 0.1s warm.

**Widget config lives in local state**, not on the xyflow node's data.
Mutating data is a lint error and causes stale renders; WidgetNode holds
state and persists it 400ms after you stop typing.

based on our dicussion and the agreed solutions... 1st i need md file of the agreed solution and help with updating my current folder md file

The agreed solution should be captured separately from the frozen problem statement. I created:

Download AIVAR_agreed_solution.md

It consolidates the direction we've been converging on:

Behavioral model / belief state as the central shared state

Dynamic abstraction rather than raw DOM representation

Active exploration and experiment selection

Coverage as behavioral coverage, not page/URL count

Contradiction handling

Metamorphic testing where exact expected outputs are unavailable

Semantic anchors for self-healing

Failure classification: broken test vs application defect vs uncertain

Verifier/evidence as a hard boundary around LLM claims

Re-planning as an actual autonomous loop

Structured memory rather than fine-tuning

A deliberately narrow hackathon implementation

A demo story that makes the autonomous decision loop visible

I also kept the organiser's requirements as constraints rather than redefining the problem. The official statement specifically requires URL → Planner → Generator → execution → Healer → quality report, with coverage evaluation and autonomous orchestration between stages. problem_explanation_9dm9yp4f98s

For the repo, I would not put all of this into problem/statement.md. That file is correctly described in your workspace as frozen.

I would restructure the Markdown state like this:

problem/
  statement.md          # FROZEN — organiser's problem

product/
  thesis.md             # What AIVAR is + demo thesis
  solution.md           # ← the document I just created
  bets.md               # What we're still uncertain about
  decisions.md          # Append-only architectural decisions

execution/
  packets/
    P*.md               # Concrete implementation ownership

The important distinction is:

statement.md
    ↓
WHAT THEY ASKED US TO BUILD

thesis.md
    ↓
WHAT WE CHOOSE TO DEMONSTRATE

solution.md
    ↓
HOW THE SYSTEM CONCEPTUALLY SOLVES IT

bets.md
    ↓
WHAT WE DON'T YET KNOW

decisions.md
    ↓
WHAT WE HAVE COMMITTED TO

packets/
    ↓
WHO BUILDS WHAT RIGHT NOW
What I would change in your existing files

product/thesis.md should become very short. Something approximately like:

Markdown
# AIVAR — Product Thesis

## Thesis

AIVAR is an autonomous QA agent that builds an evolving behavioral model of a
web application and uses that model to decide what to explore, what to test,
how to interpret failures, and when it has enough evidence to stop.

The core differentiator is not test generation or selector healing in isolation.
It is autonomous decision-making across the testing lifecycle.

## Core Loop

URL
→ Explore
→ Build belief state
→ Identify coverage/uncertainty
→ Generate test
→ Execute
→ Verify evidence
→ Heal/classify failure
→ Update belief state
→ Re-plan or finish

## Demo

The demo should show:

1. Agent receives only a URL.
2. Agent explores the application.
3. Agent constructs a behavioral model.
4. Agent identifies a meaningful coverage gap.
5. Agent generates and executes a test.
6. UI drift breaks the generated test.
7. Healer repairs the semantic action/locator.
8. Replay verifies the repair.
9. Agent distinguishes test failure from application defect.
10. Final report shows coverage, outcomes, repairs, remaining gaps and risk.

## North Star

The system should demonstrate that it can decide:

- what should be tested,
- what should be explored next,
- whether evidence is sufficient,
- whether a failure is in the test or application,
- whether a repair actually worked,
- and whether another planning/exploration cycle is necessary.

Then product/bets.md should not contain settled architecture. It should contain experiments such as:

Markdown
# Product Bets

## B1 — Behavioral model improves planning

Hypothesis:
A lightweight behavioral model lets the agent discover more meaningful
scenarios than direct LLM test generation.

Test:
Compare direct generation against generation conditioned on the behavioral
model on the same target.

Evidence:
Number and quality of alternate/error flows discovered.

---

## B2 — Active exploration is better than exhaustive crawling

Hypothesis:
Selecting actions based on uncertainty/coverage produces useful coverage
faster than blindly exploring every available action.

Test:
Give the agent a fixed exploration budget.

Evidence:
Meaningful scenarios discovered per exploration step.

---

## B3 — Semantic representation improves healing

Hypothesis:
Preserving the intent of a test step separately from its locator makes
self-healing more reliable under markup drift.

Test:
Introduce controlled selector/markup drift.

Evidence:
Repair success rate + replay verification.

---

## B4 — Failure classification can be evidence-driven

Hypothesis:
Replay + DOM/state comparison + expected behavioral evidence can distinguish
broken scripts from application defects sufficiently well for the demo.

Test:
Create known script failures and known application failures.

Evidence:
Classification accuracy.

---

## B5 — Structured memory is sufficient for the prototype

Hypothesis:
Persisting behavioral observations and retrieving relevant evidence is
sufficient; fine-tuning is unnecessary.

Test:
Compare repeated decisions with and without accumulated structured evidence.

Evidence:
Improvement in planning/healing decisions.

And product/decisions.md should record things we've actually committed to, for example:

Markdown
# Product Decisions

## D001 — Behavioral model is the shared state

The Planner, Generator, Healer and Orchestrator operate against a shared
behavioral representation rather than passing only free-form text between
agents.

Reason:
The system needs persistent knowledge of states, actions, outcomes,
uncertainty and evidence across the testing lifecycle.

---

## D002 — The orchestrator owns control flow

Specialised agents perform bounded tasks. The orchestrator decides when to
invoke them, whether evidence is sufficient, whether to re-plan and whether
to escalate.

---

## D003 — Claims require executable evidence

LLM assertions are not treated as proof.

Important behavioral claims must be backed by observable checks such as browser
execution, DOM inspection, response data or replay.

---

## D004 — Healing preserves semantic intent

A test step's behavioral intent is stored separately from its concrete
selector so that locator repair does not redefine the intended behavior.

---

## D005 — Coverage is behavioral

Coverage is evaluated using flows, states, transitions, alternate paths,
error states, boundaries and invariants rather than page count or generated
test count.

---

## D006 — No fine-tuning for the hackathon

The prototype uses structured behavioral memory/retrieval rather than model
fine-tuning.

Fine-tuning is outside the prototype unless an experiment demonstrates that
context-based adaptation is insufficient.

One important point: I would not blindly replace your existing thesis.md, bets.md, or decisions.md yet. Your workspace explicitly says those files are project state, and decisions.md is append-only. I need the actual current contents to preserve existing decisions and avoid accidentally rewriting history.

If you upload the current repo (or just product/thesis.md, product/bets.md, product/decisions.md, and execution/packets/), I can turn this into a precise repo update rather than giving you generic replacement text.
