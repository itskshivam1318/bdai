# Problem Statement — Autonomous Test Orchestration Agent

Sources: sponsor video (2026-09-04) and `problem_explanation_9dm9yp4f98s.pdf`
(Bessemer Tech Catalyst, prepared by Aivar Innovations, September 2026).
**The PDF is authoritative.** Where the video and the PDF differ, the PDF wins.

FROZEN. Do not edit below except to correct a transcription error.

## Who is asking

Aivar Innovations — AI-native services and software company, AWS Preferred
Partner, backed by Bessemer Venture Partners and Sorin Investments. Runs three
accelerator platforms: Convogent (voice/agent AI automation), Velogent
(governed agentic process automation for regulated industries), Kubogent
(Kubernetes-native AIOps). Verticals: fintech, healthcare, technology.

Track: **AI / Machine Learning**.
Focus areas: Agentic AI · Developer Productivity · Software Quality Engineering.

## The gap they name

> "The core problem is not execution — it is **decision-making**: figuring out
> what to test, evaluating whether the right things were tested, and knowing
> when a failure reflects a real defect versus a broken script."

> "AI-assisted testing tools can now generate test plans and executable test
> files from a live application, and repair failing tests automatically. **What
> they do not do is orchestrate these capabilities end to end** — deciding when
> to plan, when to generate, when to heal, and when to escalate — without a
> human directing each step. Engineering teams that adopt these tools still
> carry the coordination burden themselves."

The video makes the same point in one line, and it is the sharpest statement of
the brief:

> "I've been using the Playwright agents but still **I am the one giving them
> context again and again.** It is a lot of manual work."

## The challenge, verbatim in substance

Build an autonomous test orchestration agent that takes a web application URL
as input and drives the full testing lifecycle — planning, test generation,
execution, and repair — **without human intervention between stages**.

The agent coordinates a pipeline of three specialised sub-agents:

| Sub-agent | Responsibility |
|---|---|
| **Planner** | explores the application, produces a structured test plan |
| **Generator** | converts the plan into executable test code, with live selector validation |
| **Healer** | replays failing tests, repairs broken locators or flows |

The **meta-agent** must coordinate this pipeline intelligently — evaluating
coverage quality between stages, deciding when to re-plan or escalate, and
synthesising all outputs into a final test quality report.

> "Success is a system where a developer provides a URL and receives a working,
> meaningful test suite with no manual scripting in between."

## Requirements

### Must have

1. Accept a web application URL as the **sole required input** and begin the
   pipeline autonomously
2. Planner sub-agent explores the app and produces a **human-readable** test
   plan covering meaningful user flows — **not just happy paths**
3. **Evaluate the generated plan for coverage gaps before passing it to the
   Generator** — identifying missing flows, edge cases, and error states
4. Generator sub-agent produces executable test files from the plan, with
   **live selector and assertion validation**
5. Run the suite and invoke the Healer on failures, **distinguishing between a
   broken test script and a genuine application defect**
6. Produce a final **test quality report**: scenarios covered, pass/fail
   outcomes, healer actions taken, coverage gaps remaining, untested flow risk

### Good to have

- Optional product requirements document to inform Planner scope
- Natural-language intent (e.g. "focus on checkout and authentication flows")
- Parallel test execution across flows to cut pipeline duration

### Bonus

- **PRD-to-test-plan gap analysis** — compare plan against stated requirements,
  surface what is not covered
- **Defect classification** — confidently distinguish a script issue from a
  genuine application bug

### Out of scope (explicitly)

- Production deployment or hosting at scale
- CI/CD pipeline integration
- Cross-browser matrix testing
- Complete test coverage of a production application
- **Manually written test scripts — all test behaviour must be produced by the
  agent pipeline**

## Resources and logistics

- The organiser **may** provide one or two app URLs with credentials **at the
  time of the event**, to be treated as the primary validation surface for the
  final demo.
- **"Teams are strongly advised not to wait for these. Bring your own test
  target"** — a self-hosted open-source app, a sample app the team built, or
  any publicly accessible demo app.
- "The agent should work against **any** web application."
- **LLM API keys must be arranged by each team in advance. The organiser will
  not provide API access.**

## Evaluation criteria (weights are given)

| Weight | Criterion |
|---|---|
| **30%** | Functionality and completeness — does the full pipeline run end to end **without manual intervention**? |
| **20%** | Innovation and originality — how intelligently does the orchestrator handle **coverage gaps, ambiguity, and failure classification**? |
| **20%** | Technical implementation and code quality — robustness of the agentic loop, quality of generated tests, **depth of the healer** |
| **15%** | User experience and demo clarity — how clearly does the team present **the agent's decisions** and output? |
| **10%** | Business impact and feasibility — meaningful reduction in manual QA effort |
| **5%** | Presentation — clarity of live demo, ability to explain trade-offs and architecture |

## Submission requirements

- Working prototype — the orchestration agent running live on a target app
- Source code repository (GitHub/GitLab) with clear setup instructions
- README documenting architecture, agent pipeline design, how to run it
- **Architecture diagram** showing orchestration flow between sub-agents
- **Demo video, 2–5 minutes**, walking through the pipeline end to end
- **Presentation deck** covering problem, approach, trade-offs, business impact

## Still unknown

- Event duration and submission deadline
- Whether the organiser's target app appears at all, and how late
- Whether AWS usage (Bedrock etc.) is scored — they are an AWS Preferred
  Partner but the criteria do not mention it
- Team size limit / whether 3 is within rules
