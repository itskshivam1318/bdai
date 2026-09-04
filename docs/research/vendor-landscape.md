# The AI testing market — is the brief's premise true?

Researched 2026-09-04. **Verdict: substantially TRUE**, but the real gap is
narrower and more interesting than "no orchestration exists".

## The two best pieces of evidence

**Forrester, "Beyond The Wave" (2026-04-03)** — 37 enterprise customer
interviews behind the Q4 2025 Autonomous Testing Platforms Wave:

> Customers rated current levels of full autonomy at just **2.2 out of 5**.
> On average they've automated **51–60%** of their tests.
> Instead they're acting as **copilots**.

**Tricentis 2026 Quality Transformation Report** (global survey):

> Trust in **AI agents making release-impacting decisions fell from 48% (2025)
> to 34% (2026)** — declining as adoption rose.
> Only **35%** feel fully prepared to govern AI agents.

**Read together: capability is rising, trust in delegated decision-making is
falling.** The gap is not "can't generate tests" — it's "won't be trusted to
decide". A project that ships **legible decisions and an audit trail** is
solving the actual 2026 problem.

## The single most important fact for this hackathon

**Playwright v1.56 (2025-10-06) shipped planner, generator and healer — and
shipped no orchestrator.** From the docs:

> "you can use your AI tool of choice to command these agents… independently,
> sequentially, or as chained calls in the agentic loop."

Microsoft shipped exactly the three capabilities the brief names, as three
separately-invoked agent definitions, and explicitly left coordination to the
user. **No coordinator component is named anywhere in their docs.** See
`research/playwright-agents.md` for the ten concrete seams.

## Where the loop actually breaks, vendor by vendor

**Momentic** — the closest anyone gets, and the clearest illustration.
Four-rung escalation ladder: locator auto-heal → transient recovery →
permanent heal → quarantine. Its **heal orchestrator reads the locked
`base..head` code diff before deciding how to repair**, so it separates
intentional product change from stale test drift. An App Graph marks journeys
Covered / Partial / **Missing**.
**But: rungs 1–2 are automatic; rung 3 requires a human to run `npx momentic ai
triage test-results`; rung 4 is manual; every coverage proposal is
human-approved.** Its autonomous agent `Mo` (private beta) *"does not write
Momentic test files"* — the autonomous half and the maintained suite are
disconnected. **The pieces exist and are not wired together.**

**Meticulous** — the *only* product that genuinely self-assesses coverage and
re-plans without a human: it tracks characters of code executed and
*"automatically update[s] the set of selected sessions to cover new features."*
But it only replays sessions humans already performed, asserts **pixel diffs**,
mocks the entire backend, and every diff is a human judgment call.

**mabl** — best failure taxonomy in the market: **Regression** (*"mabl has
caught a bug"*) vs **Test implementation issue** vs Environment / Network /
Timing / *and a self-blame category*. Plus a stability-vs-reliability split:
high stability + low reliability = **broken, not flaky**. Auto-categorisation
on by default for workspaces created after 2026-06-17.
⚠️ **And the cautionary tale of this whole report:** mabl launched **Runtime
Recovery** on 2026-04-23 as the centrepiece of "Active Coverage" — autonomous
in-run obstacle resolution — and **retired it on 2026-08-03**: *"we've decided
to take a different approach… there is no setting to turn it back on."*
**Someone well-resourced built this exact thing and retreated within four
months.** Know why before pitching.

**Autify** — has the brief's architecture **running in production internally**
as a "Nightly QA Gate": analyses the day's diffs, *"identifies viewpoints not
covered by existing tests"*, then drives a browser. 9 runs, 164 coverage gaps,
July 2026. **No SKU, no doc page, no API endpoint**, and Autify disclaims its
effectiveness. Their public position: *"We always keep the QA agent's
determinations in a state a human can review… **rather than making it fully
autonomous**."*

**Functionize** — one genuinely falsifiable published mechanism, and the best
design constraint in the market:
> **"Self-healing is constrained by your verifications. It cannot override a
> failed verification."**
Plus an "adjoint model" reverse-validation that flags *"self-heal validation
failed"* and **escalates rather than silently proceeding**.

**QA Wolf** — real exploration agent ("Mapping AI", live for select users
**2026-09-01** — three days before this research). But *"if the agent
encounters a login prompt… it will ask you directly in the chat"*, and their
"zero flakes" is a **service-desk SLA, not an engineering property**: three-wave
retry then *"**a human steps in**… Failures are reproduced by humans."* Their
own metrics are reported *per human QA engineer*. Median contract **$83,100/yr**
(Vendr, 58 purchases).

**BrowserStack** — 20+ AI agents, one for every stage the brief names.
**Nothing chains them.**

**Testsigma** — the most honest sentence any vendor wrote, on their homepage:
> **"25+ agents run the loop. A human approves before anything ships."**

**Octomind — DEAD.** Wound down June 2026: *"we didn't find the market
validation we needed."* Domain no longer resolves; repos archived. Any 2026
listicle showing it live never checked.

**Propolis** — did the most autonomous thing (swarms of agents exploring and
proposing E2E tests), **acquired by Datadog 2026-01-28**. Datadog announced
intent to build *"the first solution to truly automate testing end-to-end"* and
has **shipped nothing visible in eight months**. That absence is itself a
finding.

## What is genuinely still hard, ranked by evidence

1. **The oracle problem** — knowing what "correct" means without a spec. An
   83-study review of LLM test oracles (arXiv 2607.05031) found *"just over
   half of the corpus reaches a verdict with **no specification at all**."*
   This is why Meticulous asserts only pixels.
2. **Bug vs broken script.** Only three products have a real mechanism (mabl's
   taxonomy, Momentic's diff-aware orchestrator, Functionize's invariant).
   Everyone else guesses, and **the failure mode is silent**.
3. **Deciding when to escalate.** **Not a single product has a policy-level
   escalation mechanism.** The closest shipped primitive is Autify Aximo's
   `interactiveMode`, which pauses *"when the instructions are too vague to
   complete without guessing"* — per-step, not policy. **This is the emptiest
   square on the board.**
4. **Coverage measurement for a black-box app.** Nobody publishes a mechanism.
   QA Wolf's "80%+ coverage" has **no defined denominator on any page**. The
   two credible approaches (Katalon's production telemetry, Meticulous's
   recorded sessions) both need something **external** to the suite —
   **coverage cannot be self-assessed from the suite alone.**
5. **Trust and governance** — now the binding constraint, not capability.
6. **State, auth and test data** — the universal breaking point. Propolis's
   founders called parallel-agent state conflicts *"one of our biggest
   challenges."*
7. **Long-horizon non-determinism** — *"reasoning drift… different paths to the
   same goal across runs"* (Autonoma).
8. **Cost.** An orchestrator that re-plans freely is expensive. Momentic's
   classifier *"short-circuits during a systemic outage where nearly every run
   is failing, which cuts the AI cost of a broken deploy."*

## Mechanisms worth copying (all documented, all specific)

- **Momentic's escalation ladder** — heal → recover → permanent heal →
  quarantine, with the orchestrator reading the code diff to separate
  intentional change from drift. **The ladder is the right structure; nobody has
  automated rungs 3–4.**
- **Functionize's invariant** — healing cannot override a failed verification.
- **mabl's failure taxonomy** — including a self-blame category.
- **Momentic's `Mo` reproducer pattern** — route every suspected bug to a
  *separate* agent that must reproduce it from a clean start; *"a bug it cannot
  reproduce stays out of the report."* Best documented false-positive defence
  found.
- **QA Wolf's retry ladder** — concurrent → batches of five → serial. A failure
  surviving serialised re-execution is almost certainly not environmental.
  **Cheap, deterministic, no LLM call.**
- **Autify Aximo's `interactiveMode`** — the only shipped escalation primitive.
- **WebTestBench** (arXiv 2603.25226) as an evaluation harness — measures
  checklist generation **plus** defect detection. **If we can report a
  WebTestBench number, we have something almost no vendor can offer: a
  third-party benchmark.**

## Do not claim novelty on

Planning, generation, or healing individually. All three are free in Playwright
and shipped by 20+ vendors. **The defensible claim is the policy layer**: an
explicit, inspectable escalation policy with confidence thresholds and an audit
trail — simultaneously the empty square in the market and the thing buyers say
they need.

## Evidence caveats

- No independent benchmark exists for **any** commercial autonomy claim. Every
  vendor number (99.97%, ~97%, 95%, 70%) is self-measured with no methodology.
- G2, Gartner, Capterra, TrustRadius and Reddit all block automated access, so
  practitioner sentiment on agentic features is essentially unverified.
- Thinnest claims in this file: Katalon's six agents, BrowserStack per-agent GA
  status, Tricentis's roadmap — each from a single vendor page.
- Two vendors contradict themselves on their own live sites: **mabl** still
  sells retired Runtime Recovery on three pages including pricing;
  **Functionize** PR says Studio *"builds its own tests against the live
  application"* while its FAQ says it *"does not autonomously explore or crawl"*.
