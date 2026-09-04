# Discussions — index

Captured external conversations, kept verbatim for provenance. **Read this index.
Open a transcript only when the row below says it answers your question** — they
total ~5,500 lines and none of them is short.

> **None of this is settled.** These are arguments, not decisions. A thing
> becomes true for this project when it lands in `../product/decisions.md` or in
> code. Citations inside the transcripts are leads to check, not established
> facts — several are paraphrased from memory by a model that says at the bottom
> of every page that it can make mistakes.

| # | Transcript | Open it when you need |
|---|---|---|
| 01 | [Research framing and the level ladder](01-research-framing-and-levels.md) — 3,255 lines | The prior-art map (active automata learning, GUI ripping, SLAM, active learning, stateful fuzzing, metamorphic testing, runtime verification); the Level 0–8 capability ladder with a no-regression rule; the B1–B12 block decomposition with contracts, per-block acceptance tests, and fixture apps. |
| 02 | [Observation and the world map](02-observation-and-world-map.md) — 1,816 lines | The explorer→model data contract: `ObservationChunk`, `Finding` triples, behavioral state abstraction, contradictions as first-class, state template vs. instance, delta observations. |
| 03 | [Repo document structure](03-repo-doc-structure.md) — 413 lines | A proposed `product/solution.md` layer, plus draft text for a shortened thesis, five bets (B1–B5), and six decisions (D001–D006). |

## The through-line

All three converge on one claim: **the central artifact is a behavioral model of
the application, and test generation is a consequence of having a good one** —
not the problem itself. Exploration, coverage, healing and failure classification
all become operations on that model.

That is compatible with what `../problem/statement.md` requires (Planner /
Generator / Healer + meta-agent) but is not the same shape. The brief names a
pipeline; these transcripts argue for a shared model the pipeline reads and
writes. Reconciling those two is a real design decision and has not been made.

## Where these agree with what is already built

`app/api/agents/explorer/` arrived at similar conclusions from a different
direction — Crawljax's twenty-year history rather than first principles:

- **Observation ≠ state.** Transcript 02 argues it; `observer.py` / `statekey.py`
  already split exactly that way.
- **Keep the model cheap and deterministic; use an LLM only at narrow seams.**
  Transcript 01 §B3 says don't ask an LLM whether two pages are the same state;
  `explorer/__init__.py` says the same and cites WebVoyager's 44.4% navigation-stuck
  failure rate as the reason.
- **Behavioral partition over syntactic explosion.** Transcript 02's worked
  example — `Dashboard[17 projects]` ≡ `Dashboard[18]`, but `Dashboard[0]` splits
  when its available actions differ — is now what `normalize()` produces.

  **An earlier version of this line claimed it already did, and that was wrong.**
  Measured 2026-09-04: 17 rows and 18 rows produced *different* state keys, and
  so did two different strings typed into the same field, and so did moving
  focus between two fields. `canonical_value` collapsed `"Project 17"` and
  `"Project 18"` per line, but seventeen identical lines and eighteen identical
  lines still hash differently — and `explain()` reported that difference as an
  empty diff, because as *sets* the two are equal.

  Transcript 02 was right that the layer was missing. It was wrong about the
  size: the fix was four projections inside `normalize()` (`statekey.py`), not a
  new agent emitting `StateCandidate` objects with relevance scores. The seven
  cases are now a pass/fail grid in `probe.py` section 0, which needs no server.

## Where to be careful

- **Confidence floats.** Both 01 and 02 attach `confidence: 0.9x` to nearly every
  object. An LLM-emitted probability is not calibrated — see
  `../research/coverage-evaluation.md` on 2025–26 LLM-judge bias. Keep the field
  only where a threshold actually gates a decision.
- **Schema size.** Transcript 02's `ObservationChunk` has ten top-level fields
  over five nested interfaces; 01's block set is twelve components. The brief
  weights *does the full pipeline run end to end* at 30%. An ontology with no
  Generator and no Healer scores zero there. Note that 02 concedes this itself in
  its closing paragraph — build four objects, not the full model.
- **Transcript 03 is pre-restructure.** It reads the old root `CLAUDE.md` and
  proposes paths like `product/solution.md` at the repo root. Those now live
  under `docs/`. It also references a generated `AIVAR_agreed_solution.md`
  attachment that cannot be recovered from a share link.

## Capturing another one

The pages are JS-only — a plain fetch returns an empty shell with just the title.
Render them:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    pg = p.chromium.launch().new_page()
    pg.goto(url, wait_until="networkidle")
    pg.wait_for_timeout(3000)
    text = pg.inner_text("body")
```

Then strip the nav chrome, add a provenance header matching the existing files,
and add a row above.
