# Self-healing locators & defect-vs-script classification

Researched 2026-09-04. Algorithms below were read from source/papers, not docs.

## Part 1 — Healing

### Healenium's actual algorithm (read from `tree-comparing:0.4.14` source)

Stores, on every *successful* find, the **ancestor path** (not subtree): per node
`tag`, `id`, sibling `index`, `innerText`, `classes[]`, all other attributes.

On failure: enumerate every root→leaf path in the new DOM → **LCS over paths**
(where `Node.equals()` compares **only tag and id**) → per-node heuristic score:

| Component | Points |
|---|---|
| tag | 100.0 |
| LCS | 100.0 |
| id | 50.0 |
| class | 40.0 |
| innerText | 30.0 |
| other attribute | 30.0 |
| **sibling index** | **0.0** |

Normalised by max 350. Default `score-cap = 0.6`, `recovery-tries = 1`.
Candidate accepted only if `findElements(locator).size() == 1`.

Note: Healenium's 2026 "LLM integration" locates the locator's **definition site
in the VCS source file** so the fix can be written back. It does **not** use an
LLM to pick DOM elements. Don't repeat the common misreading.

### Similo (TOSEM 2023) — the most directly applicable algorithm

Scores **every** candidate on 14 properties, weighted sum, argmax:

- **Weight 1.5**: Tag, Name, ID, Visible Text, **Neighbor Texts** (text of
  elements in an enlarged bounding rect)
- **Weight 0.5**: Class, HRef, Alt, Absolute XPath, ID-relative XPath,
  IsButton, Location (0 beyond 100px), Area, Shape

Weights were **not learned** — stable/salient properties got +0.5, others −0.5.

**Result: 91/801 failures (11%) vs 214 (27%) for the multi-locator theoretical
limit. 4 ms average.**

**Its documented failure mode matters for us**: no threshold, no tie-breaking,
pure greedy argmax. On Aliexpress, target "Home & Garden" → Levenshtein scored
"Home Improvement" (0.43) above correct "Home" (0.30) → **wrong element at 3.21
vs 3.12**. Levenshtein on natural-language link text is a systematic weakness.

**Kluge & Stocco replication (EMSE 2026)** is the most important paper here.
They defined six metrics and showed the prior papers weren't measuring the same
thing. Under strict exact-match, **VON Similo is *worse* than plain Similo** —
inverting its own headline. Their extended benchmark (30 apps × 16 versions at
fixed 4-month spacing, **10,376 element pairs, 2,012 broken**) is the best
public benchmark; on it **Similo++ / HybridSimilo reach 98.8%**.

### Which locators actually survive (Leotta, WCRE 2013, 2,735 locators)

| Strategy | Broke |
|---|---|
| `id` | **< 2%** |
| LinkText | 12% |
| `name` | 18% |
| XPath | **58%** |

ROBULA+ (breadth-first XPath specialisation) cut breakage to **8%** vs absolute
XPath's 78%. Its attribute **blacklist** is worth copying: `src, href, onclick,
onload, tabindex, width, height, size, maxlength, style`.

**Conflicting official guidance — don't assume one canonical order exists:**
Testing Library and Playwright rank `getByRole` first and **test IDs last**;
Cypress ranks `data-cy` **first** and everything else "never/sparingly".

### LLM-based healing — the numbers and the traps

**VON Similo LLM (STVR 2024)**: algorithm produces top-10 candidates, GPT-4
picks. 91.3% → **95.0%**. But:
- **$35.86** for 804 localisations, and **67× slower** (29ms → 1,934ms)
- **The LLM *lost* 10 cases the algorithm had right**, and production runs
  emitted only an id, so *"it is impossible to analyze why"*
- Beyond 10 candidates, GPT-4 matched worse **and lost the output format**
- **No usable confidence signal** — no pattern distinguished right from wrong

**Xu et al. (ICST 2025)**: candidate narrowing is **not optional** — feeding all
page elements exceeded the token limit on **every single case** (average page =
224 elements × ~101 tokens ≈ **21,733 tokens**). Best result: EditDis + ChatGPT
**122/139 (87.8%)** — and note the *weakest* baseline combined best, because it
handed over a more **diverse** candidate list. Diversity > quality.
Their Finding 5: **the attribute ChatGPT prefers most (XPath) tends to lead to
incorrect matching.**

**Practical Limits of Autonomous Test Repair (2026)** — a preprint on industrial
logs, 636 executions. The two failure modes to design against:
- **Assertion weakening**: `expect(value).toBe(5)` rewritten to
  `expect(value).toBeTruthy()` at iteration 4
- **Silent test deletion** after repeated failures, then a 100%-pass report.
  *"This is not repair but avoidance."*
- 3/10 families never converged; worst case **113 consecutive reports each
  exhausting a retry depth of 16**

### The masking problem — and the one-sentence fix

Healing that "repairs" around a genuine regression destroys the suite's value.
Vendors that address it:

- **Katalon** ships **35 auto-excluded keywords** covering every `Verify*` and
  `WaitFor*`, and warns healing them *"may lead to false positives"*
- **mabl** documents the exact asymmetry: an "is present" assertion that heals
  to a different element **passes**; "is not present" **fails**. *"This could
  result in false passes and false failures."*
- **WATER's original paper** contains a real instance: it suggested negating an
  `assertElementNotPresent`, **which would have masked a business-logic bug**
- **Silent on the risk**: Testim (auto-applies below 70% with no approval),
  Applitools (auto-applies, on by default, accumulates selectors forever, and
  markets *"it is extremely rare for a test to fail"* as the benefit)

**The best design constraint found anywhere, from Functionize:**

> **"Self-healing is constrained by your verifications. It cannot override a
> failed verification."**

Adopt verbatim.

## Part 2 — Defect vs script failure

### The base rate that frames everything (Google, 4.2M tests, 150M runs/day)

- **84% of Pass → Fail transitions are from flaky tests**
- **Only 1.23% of tests ever found a breakage**
- ~16% of tests have some flakiness; 2–16% of compute spent re-running them
- They re-run failures **10×** to check flakiness

Combined with the ~74% locator-breakage share of web test failures: **a randomly
sampled failing E2E test is overwhelmingly likely to be flake or broken script,
rarely a genuine defect.** A classifier that always says "script problem" is
right most of the time — so the real task is **recall on the rare class**, and
any accuracy number quoted without a class breakdown is meaningless.

### Root causes of flakiness (Luo et al., FSE 2014, 161 classified)

**Async Wait 45% · Concurrency 20% · Test Order Dependency 12% = 77%.**
Async Wait is exactly what a UI healer sees as "element not found".

### The strongest signal: did the failing test touch changed code?

**DeFlaker (ICSE 2018)**: *"marks as flaky any newly failing test that did not
execute any of the changes."* No reruns needed. **95.5% recall vs 23% for
rerun-based detection, 1.5% false alarms, 4.6% overhead.**

Generalises to E2E if you can map a frontend diff to routes/components
exercised. Cheapest high-value discriminator that exists.

### Reruns are weaker than everyone assumes

**FlakeFlagger** reran 24 projects **10,000 times each**: *"Only roughly a
quarter of all flaky tests would have been found with 10 reruns, roughly half
with 100, roughly two thirds with 1,000."* **A test that fails 10/10 is not
thereby a real defect.**

### Signals ranked by evidence

| Signal | Status | Detail |
|---|---|---|
| Coverage of the diff | **Strongest, peer-reviewed** | DeFlaker |
| Element **removed** vs present-but-moved | **Well supported** | VISTA taxonomy over 733 breakages: Non-Selection·SamePage **88.5%**, ·NeighbouringPage 4.5%, **·Removed 2.8%**, Mis-Selection 4.2%. "Removed" is the class most likely to mean defect |
| Playwright's own error codes | **Verified from source** | `error:notvisible`, `error:notinviewport`, `error:optionnotenabled`, `error:notconnected`, strict-mode *"resolved to N elements"*, plus `hitTargetDescription`. Machine-readable *missing* vs *present-but-obscured/disabled/moved* |
| Failure-symptom matching | Supported | SAP HANA: **≥96% precision**, ~58% machine time saved |
| Rerun determinism | Supported, hard limit | see FlakeFlagger above |
| Cross-test correlation | **Ambiguous** | 75% of flaky tests cluster (mean size 13.5) — but the dominant causes are networking and external deps, the same shape a real outage produces |
| Visual diff | Large FP problem | *"semantically blind… treats rendering noise and genuine defects identically"*. WUICC-bench separates **30 meaningful** vs **7 non-meaningful** change rules |
| VLM triage | **Peer-reviewed, ICSME 2025** | **LLMShot** distinguishes *"genuine regressions from intentional design changes"*, Gemma3-12B **>84% recall**. Negative finding: you cannot reliably prompt a VLM to ignore a specified region |
| HTTP status / console errors | **Weak published evidence** | No peer-reviewed study found. High engineering value, no accuracy backing |

### LLM agents systematically over-report failure

**Chevrot et al., ISSTA 2025** — 113 test cases (62 passing, 51 failing):
**specificity never exceeds 0.57.** Both agents systematically report *passing*
tests as **failing**. Held across GPT-4o, Claude 3.5 Sonnet, Gemini 2.0 Pro;
26/113 misjudged by every model.

For a healer, that is exactly the false-alarm mode that destroys trust.

### Do not trust LLM self-reported confidence

Nass et al. found no pattern distinguishing GPT-4's motivations when right from
when wrong. Xu et al.'s explanation-consistency proxy correlated r=0.84 for one
baseline and r=0.49–0.51 for two others. **Not a threshold input.**

## Usable benchmarks

| Dataset | Size |
|---|---|
| **ReproBreak** (Cypress/Playwright, Docker-reproducible) | 449 confirmed breaks + 131 non-breaking; 9,572 locator changes across 359 repos |
| **Kluge & Stocco extended** | 30 apps × 16 versions, 10,376 pairs, 2,012 broken |
| **WebArena-derived ATA** | 113 NL test cases, 62 passing + 51 failing, each annotated with expected failure step |
| **WUICC-bench** | 9,906 UI-change samples, meaningful vs non-meaningful |
| VISTA | 4 apps, 86 releases, 733 labelled breakages **with class** |
