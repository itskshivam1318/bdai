# Judging a test plan for gaps — and when self-critique makes things worse

Researched 2026-09-04. The single most important file for must-have #3
("evaluate the plan for coverage gaps before passing it to the Generator").

## ⚠️ The finding that should shape the architecture

**Intrinsic self-critique — an LLM reviewing its own output with no external
signal — reliably makes output worse.**

**Huang et al., "LLMs Cannot Self-Correct Reasoning Yet", ICLR 2024**
(arXiv:2310.01798). Same models, same prompts, with vs without oracle labels:

| | GSM8K | CommonSenseQA | HotpotQA |
|---|---|---|---|
| GPT-4 standard | 95.5 | 82.0 | 49.0 |
| GPT-4 self-correct **round 1** | 91.5 | 79.5 | 49.0 |
| GPT-4 self-correct **round 2** | **89.0** | 80.0 | **43.0** |
| GPT-3.5 standard | 75.9 | 75.8 | 26.0 |
| GPT-3.5 round 1 | 75.1 | **38.1** | 25.0 |

*"After self-correction, the accuracies of all models drop across all
benchmarks."* Cost: **3 model calls for round 1, 5 for round 2** vs 1 — paying
3–5× to lose accuracy.

The mechanism (GSM8K, GPT-3.5, two rounds): No change 74.7% · **Incorrect→
Correct 7.6%** · **Correct→Incorrect 8.8%**. It breaks more than it fixes. On
CommonSenseQA, **39.8% correct→incorrect**.

### The verifier false-positive numbers

**Valmeekam et al. (arXiv:2310.08118)**, Blocksworld, 100 instances:

| System | Accuracy |
|---|---|
| Generator only | 40% |
| **+ LLM self-critic** | **55%** |
| **+ sound external verifier (VAL)** | **88%** |

GPT-4 as verifier: **false positive rate 38/45 = 84.4%.** It waved through 38
of 45 invalid plans. **A critic that accepts 84% of the bad artifacts it sees
is worse than none — it manufactures confidence.**

**Stechly et al. (arXiv:2310.12397)**, graph colouring:

| Strategy | Accuracy |
|---|---|
| Direct, no iteration | 16% |
| **LLM self-critique** | **1%** |
| External verifier feedback | ~40% |
| **Deliberately FALSE feedback** | **~40%** |
| **Random resampling, top-15** | **40%** |

Self-critique made it **16× worse than doing nothing**. GPT-4 "corrected" 94%
of real first-errors and **94% of deliberately fabricated errors** —
indiscriminately. And false feedback scored identically to correct feedback,
which scored identically to plain resampling: **the loop was an expensive
resampling scheme, not critique.**

**You cannot tell a working critique loop from extra sampling without an
ablation against plain resampling at matched compute.**

### When it *does* work — Kamoi et al., **TACL 2024** (arXiv:2406.01297)

> "(1) **no prior work demonstrates successful self-correction with feedback
> from prompted LLMs**, except in tasks exceptionally suited for it, (2)
> self-correction works well in tasks that **can use reliable external
> feedback**, and (3) large-scale fine-tuning enables self-correction."

External signals that demonstrably work:

| Signal | Evidence |
|---|---|
| Sound external verifier | 88% vs 55% vs 40% |
| **Execution / tests** | Reflexion: 91% pass@1 on HumanEval vs GPT-4's 80% |
| **Killed mutants** | Meta ACH: a surviving mutant is an incontestable gap |
| **Reconstructed sub-problem** | ProCo: mask a condition, ask the model to predict it back. +14.1 arithmetic, +9.6 commonsense |
| **Information asymmetry between agents** | MARCH: a checker sharing the generator's context inherits *"confirmation bias… reproduces the errors of the original generation"* |

**ProCo is the most transferable pattern**: don't ask "is this right?" — convert
verification into a *different generation task whose answer is independently
known*. For a test plan: derive something checkable from the plan and confirm
it against the crawl.

### Iteration count

Self-Refine caps at **4**, and reports *"diminishing returns as the number of
iterations increases"*. **Non-monotonic on multi-aspect tasks** — gap analysis
is inherently multi-aspect (flows, edge cases, error states), so expect this.
Its own failure attribution: **61% of failures come from faulty feedback
generation, only 6% from the reviser.** The critic is the bottleneck.

**Practical: 1–3 rounds, most gain in round 1, active risk past round 2.**

### One counter-intuitive 2026 result (suggestive, not settled)

arXiv:2601.00828 — **"providing error location hints hurts all models."** If the
critic says "the gap is in the checkout flow", that pointer may make the
reviser *worse*. Also an **Accuracy-Correction Paradox**: GPT-3.5 (66% base)
self-corrects at 26.8%; DeepSeek (94% base) at 16.7% — *"stronger models make
fewer but deeper errors that resist self-correction."* Cheap to ablate.

## LLM-as-judge — the 80% number is inflated

The familiar *"over 80% agreement, same as humans"* is Zheng et al. (MT-Bench,
NeurIPS 2023). **arXiv:2606.19544** (2026-06-17; 21 judges, ~541,000 judgments —
largest study to date) dismantles it:

1. **Kappa deflation is universal: 33–41 percentage points** between exact-match
   agreement and Cohen's κ. An 80% raw agreement ≈ κ of ~0.4. Exact match
   *"systematically overstates discriminative ability."*
2. **Judge rankings shift by up to 14 positions across benchmarks.**
3. **Test–retest reliability >0.95 coexists with severe position bias >0.10.**
   Consistency is not validity — a judge can be perfectly repeatable and
   perfectly wrong.
4. Verbosity bias is small (<0.011) — contradicts received wisdom.

**Report chance-corrected κ, not raw agreement.**

### Rubrics carry their own biases

- **arXiv:2602.02219** — rubric scoring *"implicitly resembles a multiple-choice
  setting"* and has **position bias over score options**; bias direction is
  **model-specific**, so no generic correction. Second axis: **the ordering of
  the criteria themselves shifts scores.** Mitigation: permute option order; a
  small number of permutations suffices.
- **arXiv:2506.22316** — three prompt-origin biases: **rubric order**, **score
  ID** (how options are labelled), **reference answer score**.
- **arXiv:2609.02942** — classifiers trained on **rubric text alone, with no
  access to the evaluated response**, predict judge outputs non-trivially.
  Scores are partly predetermined by the rubric.
- **arXiv:2604.16790** (SE-specific, covers **test generation**): *"small prompt
  edits can swing outcomes… semantics-preserving perturbations elicit divergent
  verdicts"* — enough to reorder models.

### What actually helps

**Rulers (arXiv:2601.08654)** — three-stage protocol:
1. **Lock the rubric** into a spec before scoring (prevents drift)
2. **Execute as structured checklist decisions with extractive quote
   requirements** — every criterion decision must cite a verbatim span
3. **Post-hoc calibrate** to human score boundaries

The extractive-quote requirement is the cheapest defence against a judge
asserting a gap it cannot point to.

**CalibratedRubric (arXiv:2607.29252)** — deleting rubric items judges cannot
agree on among themselves moved **κ from 0.604 to 0.743**.

**Realistic expectation:** raw agreement 60–80%, κ substantially lower. And a
crucial distinction (arXiv:2603.14732): **a judge that reliably *ranks* plans by
adequacy is achievable; one that reports "this plan is 82% complete" is not.**

## Encodable gap-detection content

### ISTQB CTFL v4.0.1 — the only source giving formal denominators

| Technique | 100% coverage definition |
|---|---|
| Equivalence Partitioning | partitions exercised / total identified. Must include **invalid** partitions |
| Each Choice | every partition of every parameter ≥1× (no combinations) — the cheap multi-input criterion |
| BVA 2-value | boundary + closest neighbour in adjacent partition |
| BVA 3-value | value + **both** neighbours. `if (x ≤ 10)` mis-written as `if (x = 10)` is invisible to 2-value but caught by x=9 |
| Decision Table | **feasible** condition-combination columns exercised. Named benefit: *"helps find gaps or contradictions in the requirements"* |
| State transition — all states | weakest |
| **0-switch (valid transitions)** | *"the most widely used coverage criterion"* |
| **All transitions** | **includes empty cells of the state table = invalid transitions.** One invalid transition per test case, to avoid defect masking |

**The invalid-transition criterion is the single most directly encodable gap
heuristic for a web app.** A crawl gives states and existing transitions; the
state *table* (states × events) gives the **empty cells** — enumerable candidate
error-state tests. This is the closest thing in the standards literature to a
computable "missing error states" denominator.

Error-guessing fault taxonomy, rubric-ready: **input** (valid input not
accepted, wrong/missing parameters) · **output** (wrong format/result) ·
**logic** (missing cases, wrong operator) · **computation** · **interfaces**
(parameter mismatch) · **data** (bad initialisation, wrong type).

Rubric design rule from §4.4.3: *"Checklists should not contain items that can
be checked automatically… It should be possible to check each item separately
and directly."*

### Hendrickson's Test Heuristics Cheat Sheet — the most encodable artifact

(Quality Tree Software 2006; original host dead, recovered via Wayback.)

**Heuristics**: Goldilocks · **CRUD** · **Follow the Data** (*"Enter → Search →
Report → Export → Import → Update → View"*) · **Dependencies** (*"Customer has
0, 1, many Invoices; Delete last Line Item then Read; Delete Customer with 0, 1,
Many Invoices"*) · Boundaries · Interruptions · Position · Starvation ·
Selection (some/none/all) · Constraints · Multi-User · Sorting · Sequences ·
Input Method · **Count (0, 1, Many)** · Flood

**Web-specific**: Back button (*"watch for 'Expired' messages and double-posted
transactions"*) · Refresh · Bookmark URL · select bookmark when logged out ·
**hack the URL** · multiple browser instances · HTML/JS injection · max length
on inputs · >5000 chars in textareas · JavaScript off · cookies off

**Strings**: 255/256/257/1000/1024/2000/2048 chars · accented · Asian ·
delimiters `" ' \` | / \ , ; : & < > ^ * ? Tab` · blank · single space · leading
spaces · SQL injection

**Dependencies + Follow the Data together are effectively a CRUD-completeness
rubric with cardinality** — the most useful pairing for judging a CRUD app.

### Bach's HTSM — note our brief had stale mnemonics

Current **v6.3 (2024-11-05)**:
- Product factors are **SFDIPOT** — Structure, Function, Data, **Interfaces**,
  Platform, Operations, Time. Not SFDPOT.
- Quality criteria: Capability, Reliability, Usability, **Charisma**, Security,
  Scalability, Compatibility, Performance, Installability, **Development**.
  CRUSSPIC STMPL is ~10 years out of date; the STMPL items are now sub-items
  under Development.

**Claims Testing is literally the PRD-gap bonus, decomposed**: *"1. Identify
reference materials that include claims about the product. 2. Analyze individual
claims, and clarify vague claims. 3. Test each claim. 4. Expect the spec and the
product to be brought into alignment."*

**Data** sub-items are the best "edge case" ontology: Persistent ·
Interdependent · Sequences/Combinations · **Cardinality (zero, one, many, max,
open limit)** · Invalid/Noise · **Lifecycle** (= CRUD as a coverage dimension).

**Operations**: Common Use · **Uncommon Use** · **Extreme Use** · **Disfavored
Use** (*"ignorant, mistaken, careless or malicious"*).

⚠️ These are practitioner catalogues, **not empirically validated instruments**.
High-quality rubric *content*; no evidence that a rubric built from them works.

## Coverage denominators — the honest table

**There is no established computable denominator for "user-flow coverage".**
Every credible attempt either substitutes a *specification* as denominator, or
*manually constructs* a ground-truth feature list.

**AutoE2E / E2EBench** (Mesbah et al., arXiv:2408.01894) is the closest work.
Its ground truth required **multiple authors independently enumerating features
per app, reconciled by consensus, plus source instrumentation**. Calibration
numbers worth knowing:

| System | Avg feature coverage |
|---|---|
| AutoE2E | **79%** |
| **Crawljax** | **12%** |
| BrowserGym | 9.5% |
| OpenDevin | 7.9% |
| AutoGPT | 6.1% |

**A general-purpose crawler covers ~12% of an app's features; general web agents
6–10%.** Expect an agentic planner in that band unless it is feature-directed.

**Restats** (arXiv:2108.08209) shows black-box coverage becomes computable the
moment you accept a *declared* artifact as denominator. Two metrics transfer to
UI directly: **status-code-class coverage** (does the plan exercise any error
path at all?) and **parameter value coverage restricted to enumerable domains**
(selects, radios, toggles — not free text).

**State counts are a corrupt denominator.** Near-duplicate states inflate them;
Siamese-network detection improves near-dup F1 by **56%**, which raises
generated-suite coverage **6–21%** (arXiv:2306.07400, ICST 2026). Report
*transitions*, not states, and say the denominator is model-relative.

| Notion | Computable black-box? | Verdict |
|---|---|---|
| **Requirements coverage** | **Yes — the PRD is the denominator** | Most defensible |
| Event/interaction coverage | Yes, from a crawl | Weakly meaningful ("did we click everything") |
| Enumerable-domain input coverage | Yes | Narrow and honest |
| State/transition coverage | Partially | Transitions only; model-relative |
| **User-flow / feature coverage** | **No** | Needs manual enumeration or telemetry |

## Requirements traceability — the best-evidenced item

A 20-year IR problem with public benchmarks; LLM methods now beat IR baselines.

- **TraceLLM** (arXiv:2602.01253) — SOTA **F2** across four datasets covering
  requirements, design, **test cases**, regulations. *"Performance depends not
  only on model capacity but critically on prompt engineering"*;
  **label-aware, diversity-based demonstration sampling** was most effective.
  ⚠️ Reports F2 (recall-weighted) — precision likely mediocre and unreported.
- **arXiv:2608.15726** (2026-08-16) — the highest-leverage result: improve the
  *requirement text*, not the matcher. Decompose raw requirements into
  standardised use-case specs first. *"Existing approaches ignore the inherent
  quality of requirement descriptions."*
- **TVR** (arXiv:2504.15427) — RAG-based traceability *validation and recovery*,
  industrial automotive data. Addresses **erroneous or missing** links — the
  closest analogue to gap analysis.

**The PRD bonus is on firmer evidential ground than the primary requirement it
is attached to.**

## Mutation-style validation

**Definitions** (Stryker, the production JS/TS tool): mutant states killed /
survived / no coverage / timeout (counts as detected) / runtime error / compile
error. **Mutation score = detected / valid.** Better metric when coverage is
partial: **detected / covered**.

**The structural cost problem for E2E**: cost ≈ (mutants) × (suite runtime).
Unit suites run in seconds, a Playwright suite in minutes — naive mutation is
2–3 orders of magnitude worse. And `coverageAnalysis: perTest`, the main
mitigation, is *weakest* for E2E because each test touches a lot of code.

**The approach that scales — invert it. Meta ACH** (arXiv:2501.12862):
> *"ACH generates relatively few mutants… Instead, it focuses on generating
> currently undetected faults that are specific to an issue of concern."*

10,795 classes → **9,095 mutants → 571 hardening tests**. **73% of generated
tests accepted by engineers.** And the classic blocker is now tractable: the
LLM equivalent-mutant detector hit **precision 0.95 / recall 0.96** with simple
pre-processing.

⚠️ **DOM-level mutation as an E2E adequacy measure: no prior art found.**
Genuinely unexplored — flag as speculative if we pitch it.

## The unifying finding

**The coverage-gap problem and the self-critique problem have the same shape.**
Both fail when the evaluator has no information the generator lacked; both work
when a non-fabricable external signal is present.

Every credible coverage denominator here — OpenAPI spec, manual feature list,
PRD, telemetry — **is exactly such a signal.**

## Evidence grading

**Well-evidenced**: ISTQB coverage items; self-critique degrades without
external signal (ICLR + TACL, multiple groups, large effects); 84.4% verifier
FPR; traceability maturity; 33–41pp κ deflation.

**Moderate**: rubric-specific biases (2025–26 preprints); manual feature
enumeration as the only route to a feature denominator (n=8 apps, one group);
targeted mutation viability (one deployment, Kotlin/Android not web).

**Speculative — flag clearly**: DOM-level mutation as adequacy measure (no prior
art); whether "cannot self-correct" transfers from single-answer reasoning to
open-ended gap enumeration (untested — plausible both ways); **"untested flow
risk" as a reportable quantity — no source defines or validates such a metric.**

---

## Additions and corrections from the full report

### ⚠️ Three items reported as found that do NOT exist — never cite these

The researching agent initially claimed these and then retracted them on review:

- **"MAEWU 16-operator catalog" with per-operator mutation scores** — **no
  evidence this exists.** The real, DBLP-verified web mutation line is
  Praphamontripong & Offutt (ICSTW 2010, ICST 2012, ICSTW 2016 "An Experimental
  Evaluation of Web Mutation Operators", ICSTW 2017 "Finding Redundancy in Web
  Mutation Operators") — all IEEE-paywalled and **not retrieved**. If we want an
  operator catalogue, that is where to look, but we do not have one.
- **SpecBench** — no such benchmark found. Unverified.
- **T-BERT** — real prior art (Lin et al., ICSE 2021, arXiv:2102.04411) but
  **not fetched**, so we have **no numbers**. A known lead, not a finding.

Note also: **arXiv has essentially no coverage of mutation testing for web/E2E.**
That literature lives in ICST/ICSTW/TSE behind paywalls — itself a finding about
how much accessible evidence exists.

### Self-preference bias — a direct risk for our architecture

**arXiv:2410.21819** — *"GPT-4 exhibits a significant degree of self-preference
bias"*, hypothesised to track lower perplexity (familiarity).

**If the same model family writes the plan and judges it, expect inflated
scores.** Straightforward mitigation: have a different model judge than the one
that planned.

### Evidence-grounding measurably improves critic calibration

**Agent-Testing Agent (arXiv:2508.17393)** is architecturally close to this
brief: a meta-agent combining static analysis, designer interrogation,
literature mining and persona-driven adversarial test generation, with
difficulty adapted via judge feedback. Surfaced *"more diverse and severe
failures than expert annotators while matching severity"*, in 20–30 minutes vs
days.

**The ablation is the useful part:** *"Ablating code analysis and web search
increases variance and miscalibration, underscoring the value of
evidence-grounded test generation."*

### Bach's nine general test techniques — the best top-level rubric decomposition

Each is a distinct *gap lens*, and this is arguably the cleanest decomposition
found anywhere:

1. **Function Testing** — "test what it can do… and not what it isn't supposed
   to do"
2. **Domain Testing** — "partition the data"; look at **outputs as well as
   inputs**; *"use inputs that force the whole range of possible outputs"*
3. **Stress Testing** — "overwhelm the product"
4. **Flow Testing** — *"do one thing after another… **don't reset the system
   between actions**. Vary timing and sequencing, try parallel threads."*
   ← **the direct heuristic for user-flow gaps, and the one E2E plans most often
   miss**
5. **Scenario Testing** — *"a compelling story of how someone who matters might
   do something that matters"*
6. **Claims Testing** — ← **this is the PRD-gap bonus, decomposed into steps**
7. **User Testing** · 8. **Risk Testing** ("imagine a problem, then look for
   it") · 9. **Tool-Supported Testing**

Framing sentence worth giving to any plan critic verbatim: *"For each item
below, determine if it is important to your project, then think how you would
recognize if the product worked well or poorly in that regard."*

### Feedback granularity barely matters once the signal is sound

From the Blocksworld study — note how little detail buys once you have a
**sound** verifier:

| Feedback type | Accuracy |
|---|---|
| No feedback | 40% |
| **Binary only** | **74%** |
| Binary + first error | 86% |
| Binary + all errors | 86% |

**The existence of a sound signal does nearly all the work.** Elaborate critique
prose adds little. Combined with the 2026 finding that *"error location hints
hurt all models"*, this argues for a terse, hard signal over a chatty critic.

### Reflexion — the clearest "external signal works" datapoint

Unit tests / environment feedback stored as verbal reflections in episodic
memory: **91% pass@1 on HumanEval vs GPT-4's 80%** (arXiv:2303.11366).

### CriticGPT — trained critics beat prompted ones, but still hallucinate

**arXiv:2407.00215** (OpenAI): RLHF-**trained** critic model — critiques
preferred over human critiques **63%** of the time; found *"hundreds of errors
in ChatGPT training data rated as 'flawless'"*; caught more bugs than paid human
code reviewers.

**But**: critics *"sometimes produced hallucinated bugs that could mislead
humans"*, and **"human-machine teams catch similar numbers of bugs to LLM
critics while hallucinating less than LLMs alone."**

Consistent with the 84.4% false-positive rate: **LLM critics have a systematic
false-positive problem; pairing with a hard signal or a human is what suppresses
it.**

### The single strongest conclusion

**The checklist IS the external signal.**

The value of a gap-evaluation step comes from confronting the plan with a
**fixed, externally-authored checklist** — ISTQB coverage items, Bach's SFDIPOT
× quality criteria, Hendrickson's heuristics — **not** from asking a model to
introspect.

Supported independently by three lines of evidence: Kamoi's condition (2)
(external feedback is what works), the Agent-Testing Agent ablation (grounding
improves calibration), and Self-Refine's attribution (**61% of failures are bad
feedback, 6% bad revision**).

This turns must-have #3 from an unsolved research problem into a tractable
engineering one: the rubric is authored by ISTQB and Bach, not invented by us or
by the model at runtime.

### Two reporting rules that follow from all of this

1. **Report a prioritised list of gaps, never a calibrated percentage.**
   Rank-order agreement holds (ρ > 0.6) where absolute accuracy does not, and κ
   deflation makes any "coverage = 72%" claim indefensible.
2. **Label any flow-coverage figure as an estimate against a self-generated
   denominator.** E2EBench needed hand-enumeration plus instrumentation to reach
   79%; Crawljax gets 12%.
