"""The meta-agent: a URL in, a test quality report out, nobody in between.

    cd app/api && uv run python -m agents.pipeline http://localhost:3000/sut

The brief names three sub-agents and then says the hard part is none of them:

    "The core problem is not execution -- it is decision-making: figuring out
     what to test, evaluating whether the right things were tested, and knowing
     when a failure reflects a real defect versus a broken script."

    "What they do not do is orchestrate these capabilities end to end --
     deciding when to plan, when to generate, when to heal, and when to
     escalate -- without a human directing each step."

Every stage already existed and every stage had its own `make` target, which
means a human -- me -- was choosing which one to run. That is precisely the
coordination burden the brief is about. This file removes it.

**Why the policy is code and not a prompt.** `decisions.md` (2026-09-04 17:00)
settles this per component by failure mode, and the answer here is unusual:
almost nothing this file decides is a judgement call, because the evidence it
decides on was computed by something else. `runner.py` already classified each
failure; `critic.py` already ranked the gaps and could not have invented one.
What is left for the meta-agent is *routing*, and routing on computed evidence
is a policy, not an opinion.

There is a second reason, and it is the demo. The rubric pays 15% for how
clearly a team presents **the agent's decisions**. A `Decision` carrying a
stage, a choice, a reason and the numbers behind it prints as a chain a judge
can audit. Model prose explaining the same routing would be less checkable and
no more autonomous. The model seam is real and named -- `critic.prioritise`
orders the gaps, and the colony chooses where ants go -- it is simply not here.

**The decision that earns the file** is `addressable()`. When exploration ends
with gaps still open, the naive move is to explore again. Often that cannot
possibly help: an unexercised `submit[invalid]` partition needs an input
synthesizer, and with no API key configured there is not one, so another
thousand crawl actions would close exactly none of them. Saying *"six gaps
remain, no mechanism configured can close them, proceeding and reporting them"*
is a better answer than burning the budget to arrive at the same place. An agent
that knows which of its own gaps it cannot close is the difference between
orchestration and a loop.

**Known limit.** Re-exploration re-crawls from the entry rather than resuming
the existing frontier, because `crawl()` builds a fresh `WorldMap` per call.
Cheap on a small app and wasteful on a large one. Resuming needs `crawl()` to
accept a map to continue, which is a small change to a file the explorer owns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Page

from .critic import Gap, prioritise
from .critic import render as render_gaps
from .explorer.crawler import Budget as CrawlBudget
from .explorer.crawler import crawl
from .explorer.forms import Credentials
from .explorer.worldmap import WorldMap
from .generator import Scenario, scenarios
from .runner import DEFECT, ESCALATE, HEALED, PASSED, Result
from .runner import run as replay

# Which gap kinds another round of exploration could actually close.
#
#   untaken-action          the state offers it and nobody walked it. This is
#                           literally the frontier -- more budget closes it.
#   unexercised-partition   the mode is offered, so it is walkable -- but
#                           `invalid` needs a synthesizer, checked separately.
#   ambiguous-edge          `state_key` collapsed two behaviours. Crawling
#                           harder produces more of the same contradiction, not
#                           less. Closing it is a change to `normalize()`.
#   unreachable-action      an empty cell of the state table. You cannot click
#                           a control that is not rendered.
_EXPLORABLE = {"untaken-action", "unexercised-partition"}


@dataclass(frozen=True)
class Decision:
    """One choice the meta-agent made, and the evidence it made it on.

    `because` is written for a human reading the report, and `evidence` carries
    the numbers it cites so nobody has to take the sentence on trust. Together
    they are the answer to "how did it decide that?" -- the question the rubric
    pays 15% for being able to answer on stage.
    """

    stage: str
    choice: str
    because: str
    evidence: dict = field(default_factory=dict)

    def render(self) -> str:
        numbers = "  ".join(f"{k}={v}" for k, v in self.evidence.items())
        return f"  [{self.stage}] {self.choice}\n      {self.because}" + (
            f"\n      {numbers}" if numbers else ""
        )


@dataclass
class Pipeline:
    """Everything one end-to-end run produced. The final report reads this."""

    target_url: str
    decisions: list[Decision] = field(default_factory=list)
    world: WorldMap | None = None
    gaps: tuple[Gap, ...] = ()
    plan: tuple[Scenario, ...] = ()
    results: list[Result] = field(default_factory=list)
    rounds: int = 0
    stopped: str = ""

    def decide(self, stage: str, choice: str, because: str, **evidence) -> Decision:
        decision = Decision(stage, choice, because, evidence)
        self.decisions.append(decision)
        return decision

    @property
    def verdicts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.verdict] = counts.get(result.verdict, 0) + 1
        return counts

    @property
    def repairs(self) -> list:
        return [step for result in self.results for step in result.healed_steps]


@dataclass
class Budget:
    """Caps on the whole pipeline, not on any one stage."""

    explore_actions: int = 40
    explore_seconds: float = 180.0
    max_rounds: int = 2  # exploration attempts, including the first
    max_scenarios: int = 8
    max_seconds: float = 900.0


def addressable(gaps: tuple[Gap, ...], has_synthesizer: bool) -> tuple[Gap, ...]:
    """The gaps another round of exploration could actually close.

    The distinction this file exists for. A gap we have no mechanism to close is
    not a reason to spend budget -- it is a line in the report. Being explicit
    about which is which is the difference between deciding and looping.
    """
    open_gaps = []
    for gap in gaps:
        if gap.kind not in _EXPLORABLE:
            continue
        if "submit[invalid]" in gap.action and not has_synthesizer:
            # The synthesizer picks rejectable input, and without a model there
            # is nothing to pick it. Crawling cannot manufacture the mechanism.
            continue
        open_gaps.append(gap)
    return tuple(open_gaps)


def run(
    page: Page,
    target_url: str,
    budget: Budget | None = None,
    provider=None,
    synthesizer=None,
    credentials: Credentials | None = None,
    verify_against: tuple[str, ...] = (),
    on_event=None,
) -> Pipeline:
    """Explore, critique, re-plan if it would help, generate, run, report.

    `verify_against` re-runs the generated suite against further URLs after the
    baseline passes. It is how the demo shows healing and defect detection on
    one command: the agent wrote the suite against `?v=1` and nobody told it
    what changed in `?v=2` or `?bug=1`.
    """
    budget = budget or Budget()
    credentials = credentials or Credentials.from_env()
    deadline = time.monotonic() + budget.max_seconds
    pipe = Pipeline(target_url=target_url)

    def emit(level: str, message: str, surface: str | None = None) -> None:
        if on_event:
            on_event(level, message, surface)

    def announce(decision: Decision, surface: str | None = None) -> None:
        emit("decision", f"{decision.choice} -- {decision.because}", surface)

    # --- explore ---------------------------------------------------------
    explore_budget = CrawlBudget(
        max_actions=budget.explore_actions, max_seconds=budget.explore_seconds
    )
    world = crawl(page, target_url, explore_budget, credentials=credentials,
                  synthesizer=synthesizer)
    pipe.world = world
    pipe.rounds = 1
    announce(
        pipe.decide(
            "explore", f"mapped {len(world.states)} states",
            "walked the application from the entry URL until the frontier "
            "emptied or the budget ran out",
            states=len(world.states),
            transitions=sum(len(t) for t in world.transitions.values()),
            frontier_left=len(world.frontier()),
        ),
        surface="plan",
    )

    if len(world.states) < 2:
        pipe.stopped = "nothing to test"
        announce(
            pipe.decide(
                "stop", "stopped before generating",
                "the URL yielded fewer than two distinct states, so there are "
                "no transitions to compile into a test",
                states=len(world.states),
            )
        )
        return pipe

    # --- critique, and re-plan only if it could help ---------------------
    while True:
        pipe.gaps = prioritise(
            world, provider, on_event=lambda l, m: emit(l, m, "coverage")
        )
        open_gaps = addressable(pipe.gaps, synthesizer is not None)
        exhausted = not world.frontier()

        announce(
            pipe.decide(
                "critique", f"{len(pipe.gaps)} coverage gaps, {len(open_gaps)} "
                "closable by more exploration",
                "computed from the map: unexercised input partitions, ambiguous "
                "edges, untaken actions, and empty cells of the state table",
                gaps=len(pipe.gaps),
                addressable=len(open_gaps),
                frontier_left=len(world.frontier()),
            ),
            surface="coverage",
        )

        if not open_gaps:
            unclosable = len(pipe.gaps)
            announce(
                pipe.decide(
                    "replan", "proceeding to generation",
                    "no remaining gap can be closed by exploring again -- they "
                    "need a mechanism this run does not have (an input "
                    "synthesizer, credentials, or a change to state identity), "
                    "so another round would cost budget and close none of them",
                    gaps_remaining=unclosable,
                    synthesizer="configured" if synthesizer else "absent",
                ),
                surface="coverage",
            )
            break

        if exhausted:
            announce(
                pipe.decide(
                    "replan", "proceeding to generation",
                    "the frontier is empty -- every action the map offers has "
                    "been taken, so exploring again would retrace the same walk",
                    gaps_remaining=len(pipe.gaps),
                ),
                surface="coverage",
            )
            break

        if pipe.rounds >= budget.max_rounds or time.monotonic() > deadline:
            announce(
                pipe.decide(
                    "replan", "proceeding to generation",
                    f"{len(open_gaps)} gaps are still closable but the "
                    "exploration budget is spent; they are reported as remaining "
                    "rather than silently dropped",
                    rounds=pipe.rounds, max_rounds=budget.max_rounds,
                ),
                surface="coverage",
            )
            break

        # The one case where exploring again is the right answer: the crawl
        # stopped on budget, not on an empty frontier, and gaps remain that the
        # frontier can reach.
        explore_budget = CrawlBudget(
            max_actions=budget.explore_actions * 2,
            max_seconds=budget.explore_seconds,
        )
        announce(
            pipe.decide(
                "replan", "exploring again with a larger budget",
                f"the crawl stopped with {len(world.frontier())} actions still "
                f"unwalked and {len(open_gaps)} gaps the frontier can reach, so "
                "more exploration will close some of them",
                round=pipe.rounds + 1,
                actions=explore_budget.max_actions,
            ),
            surface="plan",
        )
        world = crawl(page, target_url, explore_budget, credentials=credentials,
                      synthesizer=synthesizer)
        pipe.world = world
        pipe.rounds += 1

    # --- generate --------------------------------------------------------
    pipe.plan = scenarios(world, limit=budget.max_scenarios)
    announce(
        pipe.decide(
            "generate", f"compiled {len(pipe.plan)} scenarios",
            "each is a path the crawler actually walked, and each assertion is "
            "an effect the application actually produced when it walked it",
            scenarios=len(pipe.plan),
            unhappy=sum(1 for s in pipe.plan if "nothing filled" in s.name
                        or "should reject" in s.name),
        ),
        surface="suite",
    )

    if not pipe.plan:
        pipe.stopped = "no scenarios"
        announce(
            pipe.decide("stop", "stopped before running",
                        "the map produced no compilable path")
        )
        return pipe

    # --- run, then re-verify against anything else we were given ---------
    for label, url in (("baseline", target_url), *((u, u) for u in verify_against)):
        baseline = url == target_url
        suite = pipe.plan if baseline else verifiable(pipe.plan)
        first = len(pipe.results)

        if not baseline and len(suite) < len(pipe.plan):
            announce(
                pipe.decide(
                    "verify",
                    f"re-verifying {len(suite)} of {len(pipe.plan)} scenarios "
                    f"against {url}",
                    "a scenario that navigates by link is not re-runnable against "
                    "a different base URL: a link carries an absolute destination, "
                    "so following it leaves the surface under test and any failure "
                    "would be attributed to the deploy rather than to the link",
                    skipped=len(pipe.plan) - len(suite),
                ),
                surface="suite",
            )

        for scenario in suite:
            if time.monotonic() > deadline:
                break
            result = replay(
                page, scenario, target_url=url, credentials=credentials,
                synthesizer=synthesizer,
                on_event=lambda l, m: emit(l, m, _surface_for(l, m)),
            )
            pipe.results.append(result)

        counts = _counts(pipe.results[first:])
        announce(
            pipe.decide(
                "run", f"{label}: " + ", ".join(f"{v} {k}" for k, v in counts.items()),
                _read_the_run(counts),
                **counts,
            ),
            surface="defect" if counts.get(DEFECT) else "heal",
        )

    pipe.stopped = "complete"
    announce(
        pipe.decide(
            "stop", "pipeline complete",
            "every generated scenario was executed and classified; what remains "
            "uncovered is listed in the report rather than left implicit",
            scenarios=len(pipe.plan),
            executed=len(pipe.results),
            gaps_remaining=len(pipe.gaps),
        ),
        surface="report",
    )
    return pipe


def verifiable(plan: tuple[Scenario, ...]) -> tuple[Scenario, ...]:
    """The scenarios that mean the same thing against a different base URL.

    Re-running a suite after a deploy is the whole point of having one, but two
    kinds of step do not survive being pointed somewhere new:

    **A link.** Its destination is absolute. `link:v2` from the entry is a
    different act depending on where the entry is, and against a base that is
    already v2 it correctly stays put -- which the classifier then reports as a
    defect, because staying put is not what it did when recorded. That is a true
    reading of a meaningless question.

    **A path that navigates first.** A scenario rooted deeper than the entry
    reaches its subject through the application's own navigation, which may not
    preserve whatever distinguishes the new base.

    Both reduce to: keep the scenarios that act where they land. On a real
    deploy -- where the whole application moves rather than a query parameter --
    this filter keeps nearly everything, because links then point at the new
    deploy too. Here it keeps the form scenarios, which is what the drift and
    defect knobs actually move.
    """
    return tuple(
        scenario
        for scenario in plan
        if len(scenario.steps) == 1
        and not any(
            step.action.startswith("link:") for step in scenario.steps
        )
    )


def _surface_for(level: str, message: str) -> str | None:
    if message.startswith("healed"):
        return "heal"
    if message.startswith(DEFECT) or message.startswith(ESCALATE):
        return "defect"
    return None


def _counts(results: list[Result]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    return counts


def _read_the_run(counts: dict[str, int]) -> str:
    """Say what the verdicts mean, not just how many there were.

    The brief asks the pipeline to distinguish a broken script from a genuine
    defect. `runner.py` does that per step; this turns the tally into the
    sentence a human needs, and refuses to smooth it over -- a defect is named
    as unrepairable and an escalation is named as unattributable.
    """
    if counts.get(DEFECT):
        return (
            f"{counts[DEFECT]} scenario(s) failed with the locator resolving and "
            "the click landing -- the application behaved differently from when "
            "it was recorded. Nothing here is repairable by healing"
        )
    if counts.get(ESCALATE):
        return (
            f"{counts[ESCALATE]} scenario(s) could not be attributed from one "
            "run -- either the repair retargeted the test or the app regressed. "
            "A human has to look"
        )
    if counts.get(HEALED):
        return (
            f"{counts[HEALED]} scenario(s) needed a repaired locator and then "
            "reached the recorded outcome, so the markup moved and the "
            "behaviour did not"
        )
    return "every scenario reached the outcome recorded when it was written"


def report(pipe: Pipeline) -> str:
    """The final test quality report. Must-have #6, and the demo's last screen.

    Everything the brief asks for by name -- scenarios covered, pass/fail
    outcomes, healer actions taken, coverage gaps remaining, untested flow risk
    -- plus the decision chain, because a report that says what happened without
    saying why it was attempted is not an account of an autonomous run.
    """
    lines = [
        "=" * 72,
        f"TEST QUALITY REPORT   {pipe.target_url}",
        "=" * 72,
        "",
        "HOW THE AGENT DECIDED",
    ]
    lines += [decision.render() for decision in pipe.decisions]

    lines += ["", "SCENARIOS COVERED"]
    if not pipe.plan:
        lines.append("  none")
    for scenario in pipe.plan:
        steps = " -> ".join(step.intent for step in scenario.steps)
        lines.append(f"  {scenario.name}")
        lines.append(f"      {steps}")

    lines += ["", "OUTCOMES"]
    if not pipe.results:
        lines.append("  nothing ran")
    for result in pipe.results:
        lines.append(
            f"  {result.verdict.upper():<9} {result.scenario.name}  "
            f"[{result.target_url}]"
        )
        for step in result.steps:
            if step.verdict != PASSED:
                lines.append(f"      {step.verdict}: {step.detail}")

    lines += ["", "HEALER ACTIONS"]
    if not pipe.repairs:
        lines.append("  none -- no locator needed repairing")
    for step in pipe.repairs:
        lines.append(f"  [{step.resolution.rung}] {step.step.action}")
        lines.append(f"      {step.resolution.detail}")
        lines.append(
            f"      verified: the repaired step then {step.detail.lower()}"
            if step.verdict == HEALED
            else f"      NOT verified: {step.detail}"
        )

    lines += ["", "COVERAGE GAPS REMAINING, AND WHAT THEY RISK"]
    lines.append(render_gaps(pipe.gaps))

    lines += [
        "",
        "-" * 72,
        f"stopped: {pipe.stopped}   exploration rounds: {pipe.rounds}   "
        f"scenarios: {len(pipe.plan)}   runs: {len(pipe.results)}",
        "No coverage percentage is reported. The gaps above are a prioritised",
        "list of real cells in a real table; their count is not a denominator.",
    ]
    return "\n".join(lines)


def fixture_variants(entry_url: str) -> tuple[str, ...]:
    """The extra targets to re-verify against, when the target is *our* SUT.

    `?v=1|2|3` and `?bug=1` are knobs on `web/app/sut/` and on nothing else.
    They were passed unconditionally, which is correct for the demo and wrong
    for every other URL: appended to a third-party app they are query parameters
    it ignores, so the suite is re-run against a byte-identical target and the
    report presents the passes as a drift check and a defect check.

    Measured against saucedemo on 2026-09-04, before this existed: 12 runs
    reported, 4 of which re-tested an unchanged app. Nothing failed, so nothing
    looked wrong -- which is what makes it worth a check rather than a comment.

    A real second target is a real deploy, so `main()` takes those on the
    command line. What this function decides is only the default.
    """
    parsed = urlsplit(entry_url)
    ours = parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.rstrip(
        "/"
    ).endswith("/sut")
    if not ours:
        return ()
    base = urlunsplit(parsed._replace(query="", fragment=""))
    return (f"{base}?v=2", f"{base}?bug=1")


def main(entry_url: str, verify_against: tuple[str, ...] = ()) -> int:
    """One command, no human between the stages. Needs `make dev`."""
    from playwright.sync_api import sync_playwright

    provider = None
    synthesizer = None
    try:
        from .explorer.synth import Synthesizer
        from .llm import load

        provider = load()
        synthesizer = Synthesizer(provider)
        print(f"model: {provider.name}\n")
    except Exception as error:
        print(f"no model configured ({type(error).__name__}) -- "
              "deterministic exploration and ranking\n")

    # Say it up front. A run with no second target cannot heal and cannot find a
    # defect, and a report that is silent about that reads like it looked.
    print(
        "re-verify against: " + (", ".join(verify_against) or
        "nothing -- pass further deploy URLs as extra arguments") + "\n"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        pipe = run(
            page, entry_url,
            provider=provider,
            synthesizer=synthesizer,
            verify_against=verify_against,
            on_event=lambda level, message, surface=None: print(
                f"  [{level}] {message}"
            ),
        )
        browser.close()

    print()
    print(report(pipe))
    return 0


if __name__ == "__main__":
    import sys

    #   python -m agents.pipeline <url> [verify-url ...]
    #
    # The extra URLs are further deploys of the same app to re-run the suite
    # against. Given none, `fixture_variants` supplies the SUT's own knobs when
    # the target *is* the SUT, and nothing at all otherwise.
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"
    targets = tuple(sys.argv[2:]) or fixture_variants(url)
    raise SystemExit(main(url, targets))
