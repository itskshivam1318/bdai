# Thesis — SUPERSEDED, PENDING REWRITE

> Written against the video alone, before the PDF was available. The PDF names
> an explicit Planner/Generator/Healer + meta-agent architecture and weights
> orchestration intelligence heavily, which this draft underweights. Kept for
> the demo-script thinking and the live-DOM-mutation idea; do not build from it
> until rewritten against `problem/statement.md`.

## The claim

**A test suite should not store selectors. It should store intent.**

We keep a live model of the app — pages, elements, and the flows between them —
and compile each test step from intent (`click the primary action on the login
form`) down to a concrete selector *at run time*, against the DOM as it is
right now.

That reframes all four required capabilities as one mechanism:

| Requirement | In this design |
|---|---|
| Explore | build the app model by crawling from the URL + credentials |
| Write | derive test cases from the flows the model found |
| Run | compile intent → selector, execute |
| **Heal** | **re-compile against the changed DOM. Healing is not a repair heuristic; it is just running again.** |

The judge-facing version: *"We didn't build a self-healing feature. We built
tests that have nothing to break."*

## Why this beats the obvious build

The obvious build is: crawl the app, ask an LLM to emit Playwright `.spec.ts`
files, run them, and when one fails ask the LLM to guess a new selector. That
is a wrapper around `playwright codegen` — which the sponsors explicitly raise
and dismiss in the video — and its healing is a guess with no ground truth.

The video's real complaint is *"I am the one giving them context again and
again."* So the thing to demonstrate is that **the agent maintains its own
context**. The app model is that context, and it is the artifact that makes
healing principled instead of speculative.

## Demo script (write everything backwards from this)

Target: 90 seconds. Every packet must trace to a beat here. Anything that
traces to no beat does not get built.

| # | Beat | What the judge sees | Depends on |
|---|---|---|---|
| 1 | Hand it the brief | We paste URL + username + password. We type nothing else. **We then take our hands off the keyboard.** | P03 |
| 2 | It explores | Page graph draws itself on the canvas as the crawler logs in and walks the app. Screenshots fill in. | P01 |
| 3 | It writes | Test cases appear in plain English, each traceable to a flow it discovered. Not a prompt we wrote. | P01 |
| 4 | It runs | Suite goes green. Per-step evidence: screenshot, resolved selector, timing. | P02 |
| 5 | **The app changes** | We mutate the live DOM (see below) — ids renamed, button copy changed, a field moved. Re-run: the suite **still passes**, and the timeline shows *why* each locator re-resolved. | P02 |
| 6 | The receipt | Export the suite as runnable Playwright files they can keep. | P03 |

Beat 5 is the one that wins or loses. Beats 2–4 will be table stakes across the
room; nobody else will make the app change *live in front of the judges*.

## The beat-5 problem, and the trick

The target app is **theirs**. We cannot ship a new version of it mid-demo to
prove healing works.

So we simulate a release against the live page: a Playwright-injected DOM
mutation (rename `data-testid`s, rewrite button text, reorder or re-nest
fields) applied before the second run. It is a real DOM the agent has never
seen, changed in exactly the ways real releases change one.

**Say out loud that we are doing this.** Framed honestly — "here is a release
happening; watch the suite survive it" — it is the strongest 15 seconds in the
demo. Framed quietly, it is the thing that gets us caught and disqualified in
the Q&A. Non-negotiable.

Fallback if it looks staged: run beat 5 against `web/app/sut/?v=2`, our own
system under test, and say plainly that it is ours.

## Differentiator

The app model as a persistent, inspectable artifact — the thing the video says
today's tools make a human supply by hand, over and over.

## Explicitly not building

Per `CLAUDE.md`, plus:

- CI integration, scheduling, dashboards, history over time
- Any app the crawler cannot reach with one username and one password
- Visual regression diffing (tempting, off-brief — the brief says heal, not compare)
- Test *maintenance* UX — editing, reordering, tagging test cases by hand
