# Autonomous web exploration — what exists, what works, what's dangerous

Researched 2026-09-04. Stats verified via GitHub API that day.

## Headline

**No mainstream open-source browser agent does open-ended app exploration.**
browser-use, Skyvern, Stagehand, Playwright MCP, Agent-E, Magentic-UI, Notte,
Steel, Hyperbrowser are all *goal-directed task executors* — you give an
instruction, they pursue it. None ships a "map this app" / state-graph mode.

First-class autonomous discovery exists only in (a) classical crawlers
(Crawljax, Firecrawl) and (b) closed commercial QA products (QA Wolf).

**This is the hard part of the brief, and it is not a library call.**

## State representation — the evidence is unusually clear

**WebMall** (arXiv:2508.13024) ran a direct A/B of observation spaces, same
agent, same 91 tasks:

| Config | GPT-5.4 | Qwen 3.6 Plus |
|---|---|---|
| **AX-Tree only** | 62% | 75% |
| **AX-Tree + Memory** | **75%** | 75% |
| AX-Tree + Vision | 75% (specific-search *dropped* 69%→52%) | **13%** |
| Vision only | 25% | **0%** |

> "using the accessibility tree is most important for successful navigation"

**Adding vision to a working a11y-tree agent frequently made things worse.**
Documented failure: vision agents "repeatedly emit the same click against an
invisible or stale element… cannot maintain a stable mapping from screenshot to
element index across re-renders."

Token economics: a11y snapshot ~200–400 tokens vs screenshot ~3,000–5,000
(Playwright's own figures). Raw HTML on WorkArena pages: **40K–500K tokens per
page**. Form-filling study (arXiv:2405.09965, 146 forms): pruned HTML 70.6% >
raw HTML 60.2% > LLM-processed HTML 50.3% — **structured pruning beats both
extremes**.

**The a11y tree's Achilles heel** — WebAIM Million, Feb 2026: 95.9% of
top-million home pages have WCAG failures. Empty buttons **30.6%**, missing
form labels **51%**, empty links 46.3%. A pure a11y explorer is blind to a real
fraction of controls. Argues for vision as *fallback*, not co-primary.

**Practical default:** a11y-derived state with stable per-snapshot handles
(`ref=eN` / `bid` / index), aggressive pruning, explicit change/error feedback
after each action, vision reserved for canvas/WebGL/broken-semantics pages.

## App modelling — 20 years of prior art more relevant than any 2026 framework

**Crawljax** (Apache-2.0, still the reference): Robot + DOM Analyzer + FSM.
Nodes are distinct DOM states, edges are the events causing transitions.

State equivalence evolved through four generations, and this is *the* core
problem:
1. **Levenshtein edit distance** over serialized DOM, threshold τ
2. **Comparator pipelines** stripping volatile aspects (e.g. `DateTimeComparator`
   strips timestamps so states differing only by a clock collapse)
3. **Fragment-level equivalence** (FragGen, arXiv:2110.14043) — no global
   threshold; +123% near-duplicates detected, +62% precision / +70% recall
4. **Learned Siamese embeddings** (WebEmbed, arXiv:2306.07400) — +56% F1 →
   6–21% downstream coverage gain

Ground truth for this problem exists: Yandrapally/Stocco/Mesbah, **ICSE 2020**,
493K state-pairs from 6,000+ websites.

**Loop avoidance** — nobody has a bespoke solution for calendars or faceted
search. Everyone uses: visited-element sets keyed on (tag, attrs, XPath); hard
caps (depth/states/time); novelty rewards; coverage-plateau handoff; stall
detection; and **coarse state abstraction that collapses near-identical
paginated pages into one state**.

**Authentication is universally pre-crawl configuration**, never emergent. QA
Wolf openly admits its agent misreads login walls as dead ends.

**Warning worth heeding** (arXiv:2606.16650): **code coverage correlates only
weakly with failure-revealing ability.** Don't optimise for it.

### Two architectures worth copying

**QA Wolf "Mapping AI"** (closed but well documented, qawolf.com/blog):
- **Two-phase BFS-then-DFS.** Phase 1 clicks every top-level section to map
  structure and queue areas; phase 2 works the queue depth-first
- Stops at **~200 outlined tests + empty queue**
- **Flow definition:** a flow must describe what a user *accomplishes*.
  Explicitly rejects "Display Search Dropdown" / "View Hero Banner" as non-flows
- Restricted to **discovered URLs only** — never invents addresses, so no
  fabricated-404 flows
- **Stall detection** — if several turns pass with no new destinations, change
  strategy
- Re-exploration reconciles new observations with the existing map using
  **conventional non-LLM code**, preserving published flows

**Temac** (arXiv:2506.00520) — hybrid: classical crawler explores broadly, **LLM
agents invoked only once coverage plateaus**, then target uncovered
functionality. +12.5% to +60.3% coverage over baseline. Same split as
**AutoDroid**: cheap crawler builds the graph, expensive LLM reasons over it.

**DroidAgent** (arXiv:2311.08649) — closest published analogue to "discover
meaningful flows": a planner autonomously sets *realistic multi-step goals*
("create a second account and add the first as a friend"), actor executes to
goal or action budget, memory prevents repetition. 61% vs 51% activity
coverage; 317/374 generated tasks rated realistic by humans.

**Meticulous.ai** — the important contrast: **sidesteps exploration entirely**.
Records real user sessions via a script tag and replays them, mocking backend
responses so tests are *"side-effect free"*.

## Destructive actions — the highest-risk part of this brief

**Running an exploring agent against a live app with real credentials is the
highest-risk configuration in this entire landscape.** Every vendor with a
documented position runs against **staging** (Bug0: "You share access to your
staging environment. That's all we need.").

### The one design worth copying: Magentic-UI **ActionGuard** (arXiv:2507.22358, MIT)

Every action type carries an irreversibility heuristic with three values:
- **always irreversible** (file upload) → always prompt
- **never irreversible** (scroll) → auto-execute
- **maybe irreversible** (click a button) → routed to an **ActionGuard LLM
  judge** deciding per instance

With ActionGuard on, none of the paper's adversarial scenarios succeeded. Note
the usability tax: users found approvals on low-risk actions annoying.

Independent convergence: browser-use issue #5411 proposes OWASP AISVS C9.2.3 —
classify every action READ_ONLY vs IRREVERSIBLE, fail closed.

### OpenAI Operator's measured guard efficacy (only vendor with numbers)
- Confirmation before significant actions: **92% recall** over 607 tasks
- Proactive refusal of banking/trading: **94% recall**
- Prompt-injection monitor: 99% recall / 90% precision; susceptibility
  **62% → 23%**
- Best number: 100 prompts → 13 raw model mistakes; **confirmation cut
  real-world impact ~90%**

### Safety benchmarks — the numbers are bad
- **ST-WebAgentBench** (ICLR 2026): agents lose **up to 38% of raw successes**
  when policies are enforced. Observed: an agent **created an unwanted
  repository** while trying to create an issue; filled fields with hallucinated
  info; submitted forms without consent
- **WASP** (Meta): prompt injection partially succeeds in **up to 86%** of
  cases. Authors call current resistance *"security by incompetence"*
- **RedTeamCUA**: Claude 4.5 Sonnet + CUA attack success rate **60%**
- Prompt injection is stated as unsolved by OpenAI's own CISO

**This is not abstract for us.** Any text in the app being explored — a
comment, a product description, a support ticket — is a potential instruction
to our explorer.

### Technique inventory (concrete)
- **Backend response mocking** (Meticulous) — strongest single technique;
  structurally side-effect free
- **Playwright `route()` interception** — abort/modify by URL glob, resource
  type, or `route.request().method()`. The natural place to block
  POST/PUT/DELETE
- **Credential isolation from the LLM** — Skyvern encrypts secrets and injects
  them into fields so the model never sees plaintext; browser-use has a
  `sensitive_data` dict
- Playwright MCP `--allowed-origins`/`--blocked-origins` (docs honestly say
  origin filtering *"does not serve as a security boundary"*)
- **`force: true` in Playwright bypasses all actionability checks** — never
  expose it to an autonomous explorer

## Benchmarks — and why to discount them

| Benchmark | Best 2026 | Note |
|---|---|---|
| WebVoyager | **99.19%** | **Saturated → meaningless** |
| WebArena | ~74.3% | human 78.24% |
| WorkArena **L3** | **0.4%** | 0.0% for everything else |
| **WebTestBench** (test generation!) | **26.4% F1** | all models under 30%; ~70% false-positive on defect detection |
| **CATTest** (free-exploration bug discovery) | R-score **42.57** | Claude-Opus-4.7, under half of ground-truth bugs |

**Read WebArena vs WorkArena-L3 together**: same agents, ~74% on sandboxed
replicas, **0.4%** on multi-step enterprise workflows. That delta is the honest
estimate of transfer to an arbitrary real business app.

**LLM judges systematically overestimate** — AgentRewardBench (1,302
expert-reviewed trajectories): GPT-4 judging is **16.7 points off on WebArena**.
*"An Illusion of Progress?"* (COLM 2025) shows WebVoyager/Mind2Web "dramatically
overestimate agent performance".

Anchor expectations to WebTestBench (26%) and CATTest (43%), not WebVoyager.

Cost, published: WorkArena L2 with Claude-3.5-Sonnet **$1.27/task**, 33.8
steps/episode. WebMall/GPT-5.4 $0.34–$0.56/task.

Top failure modes (WebVoyager's own 300-case analysis): **44.4% navigation
stuck** (search/scroll loops), 24.8% visual grounding errors, 21.8%
hallucination.

## Practical gotchas

- **`networkidle` is DISCOURAGED by Playwright's own docs.** SPAs with polling
  never go idle. Sanctioned replacement is auto-retrying assertions — but an
  *explorer* has no expected end state to assert on. Genuine unsolved
  sub-problem.
- **`opacity: 0` still counts as "visible"** to Playwright actionability — an
  agent can "click" what no human can see
- **Shadow DOM**: browser-use issues #3810, #3820, #4306, #2276 all closed
  **"not planned"**. Standing acknowledged gap, not a bug queue
- **iframes**: Stagehand #2324, #870, #972 all open
- **Token blowup**: browser-use #4742 — base64 screenshots resent every turn
  until the API rejects the block and **the session is permanently
  unrecoverable**
- **Runaway loops cost money**: documented incident of **200+ navigations on a
  $3 task**
- **Bot detection**: Cloudflare measured 57.2% of HTML requests as automated
  bots. Expect to look like one

## Licensing traps

- **AGPL-3.0**: Skyvern, Firecrawl, HyperAgent — network-use copyleft
- **SSPL-1.0**: Notte. **Elastic 2.0**: Suna. Neither is OSI-approved despite
  "open source" marketing
- **Safe to embed**: browser-use (MIT), Stagehand (MIT), Playwright MCP
  (Apache-2.0), Agent-E (MIT), Magentic-UI (MIT), Steel (Apache-2.0), Crawljax
  (Apache-2.0)

## Dead — do not build on

- **Index** (Laminar) — repo archived 2025-07-03
- **Dendrite** — README says not under active development
- **LaVague** — 6.4k stars, last push 2025-01-21, org dormant ~20 months.
  Silent abandonment
- **Octomind** — domain returned DNS ENOTFOUND on 2026-09-04

## Hype flags

Notte's ">95% success" and "agents that actually understand websites" have no
published benchmark or paper. Nova Act's "highly reliable" has no public evals.
Firecrawl's 176k stars reflect its crawler, not its agent.
