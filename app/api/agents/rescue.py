"""When a step cannot be attempted at all, go and look again.

    cd app/api && uv run python -m agents.rescue http://localhost:3000/sut

`runner.py` classifies a replayed step from two observations, and one of its
verdicts is deliberately a refusal: **ESCALATE** means *no control here plays
the recorded part*, so there is nothing to observe and nothing to classify.
`regression.verify` correctly leaves such a scenario alone -- rewriting a test
whose premise has vanished is how a suite starts describing an app that never
existed.

But "a human has to say what this step now means" is only true if nobody looks.
The map the suite was recorded against is old by construction; the control the
step wanted may well still exist under a name and a shape nothing in the old
map can reach. So this module does the one thing `verify` structurally cannot:
**it explores the region that moved, and asks the fresh map what replaced it.**

Three properties hold it together, and each is one way this could otherwise
start manufacturing green.

**It only fires on the unattemptable step.** `runner.py` escalates for three
different reasons and only one of them is an absence: `resolution.action is
None`. The other two -- a control that is present and inert, and a repair whose
outcome also changed -- are cases where the app *was* observed and something
was wrong with what it did. Re-exploring those would be looking for a second
opinion about evidence we already have, which is the definition of shopping for
a verdict.

**The replacement must be an edge the crawler walked.** Not a model's proposal,
not a similar-looking name: an action recorded in the fresh map, from the state
the scenario is actually standing on, whose observed effect matches what the
step recorded. The colony may explore -- deciding *where to look* is judgement
and this is exactly the kind of thing an ant is for -- but what it produces is
a map, and only the map may answer.

**One candidate or none.** Two edges that both match is not a repair with a tie
to break; it is the map saying the recorded step is now ambiguous, which is
precisely the question a human is better at. This is `runner.resolve`'s
`_CLEAR_MARGIN` discipline one layer up, and it is the reason this file can be
allowed to write to a suite at all.

What comes back is a `Repair` in the same shape `regression.repaired` produces,
so it flows into `emit` through the path that already exists -- and, since the
same commit, through the re-verification that path now performs. A rescue that
is wrong is caught by the replay before the version is declared.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace

from playwright.sync_api import Page

from . import runner
from .explorer import forms
from .explorer.crawler import Budget as CrawlBudget
from .explorer.crawler import crawl
from .explorer.forms import Credentials
from .explorer.statekey import state_key
from .explorer.worldmap import WorldMap
from .generator import Scenario

# Deliberately small. This is a *region*, not an app: the scenario has already
# walked to the state that moved, and what we need is what leaves it. A budget
# big enough to re-map the application would turn every escalation into a
# second crawl, and the run that provoked it into the slowest thing in the
# product.
REGION = CrawlBudget(max_states=8, max_actions=12, max_seconds=60, max_depth=2)


@dataclass(frozen=True)
class Rescue:
    """One attempt to recover an unattemptable step, and what came of it.

    Written whether or not it succeeded, and `why` is the useful half either
    way: "nothing in the fresh map lands where this step landed" is a finding
    about the application, not a failure of this module.
    """

    scenario: str
    step: int
    intent: str
    was: str
    node: str
    #: The action found, or None when the region could not answer.
    now: str | None = None
    to_key: str = ""
    why: str = ""
    #: How many states the aimed exploration saw.
    explored: int = 0
    #: `colony` when ants were dispatched, `crawl` when it was deterministic.
    source: str = "crawl"

    @property
    def recovered(self) -> bool:
        return self.now is not None

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario, "step": self.step, "intent": self.intent,
            "was": self.was, "node": self.node, "now": self.now,
            "to_key": self.to_key, "why": self.why, "explored": self.explored,
            "source": self.source,
        }


def unattemptable(result: runner.Result) -> int | None:
    """The index of the step that could not be attempted, if that is why this failed.

    None for every other escalation, and that is the whole gate. `runner.py`
    reaches ESCALATE three ways and only this one is an *absence* -- the other
    two observed the app and found its behaviour ambiguous, which is a question
    about the application rather than about the locator.
    """
    for index, outcome in enumerate(result.steps):
        if outcome.verdict == runner.ESCALATE and outcome.resolution.action is None:
            return index
    return None


def land(
    page: Page,
    scenario: Scenario,
    index: int,
    *,
    target_url: str,
    credentials: Credentials | None = None,
    synthesizer=None,
) -> str | None:
    """Drive the browser to the state the failing step is taken from.

    By replaying the scenario's own prefix through the Runner rather than by
    navigating to a recorded URL: a state key is not a URL, the prefix may be
    the only way to reach this screen at all (it usually is -- the step after a
    login is the common case), and re-deriving the route would be a second
    implementation of the thing that just ran.

    Returns the URL landed on, or None when the prefix itself no longer works.
    A prefix that has broken is not a rescue -- it is an earlier escalation that
    has not been reached yet, and pretending otherwise would explore whatever
    page the browser happened to be left on.
    """
    if index == 0:
        page.goto(target_url)
        return page.url

    prefix = replace(scenario, steps=scenario.steps[:index])
    outcome = runner.run(
        page, prefix, target_url=target_url,
        credentials=credentials, synthesizer=synthesizer,
    )
    if outcome.verdict in {runner.DEFECT, runner.ESCALATE}:
        return None
    return page.url


def look(
    page: Page,
    url: str,
    *,
    intent: str,
    credentials: Credentials | None = None,
    provider=None,
    budget: CrawlBudget | None = None,
    on_event=None,
    run_id: int | None = None,
) -> tuple[WorldMap, str]:
    """Map the region around `url`. Deterministically, then with ants if we can.

    The order is the architecture and it is the same one the whole system uses:
    the crawl produces the substrate, and judgement is only ever applied to
    something that already exists. An unseeded colony sent at a single screen
    would spend its budget rediscovering the buttons on it.

    The colony is optional and its failure is not the rescue's failure -- a
    spent key must not turn a recoverable step into an escalation, because the
    crawl on its own answers the common case (a renamed control still reachable
    from the same screen).
    """
    def say(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    world = crawl(page, url, budget or REGION, credentials=credentials)
    say("info", f"rescue: crawled {len(world.states)} state(s) around {url}")

    if provider is None:
        return world, "crawl"

    try:
        from .orchestrator import Budget as ColonyBudget
        from .orchestrator import run as explore

        exploration = explore(
            page, url, provider,
            intent=intent,
            budget=ColonyBudget(max_waves=1, max_ants=2, ant_actions=4),
            credentials=credentials,
            world=world,
            on_event=lambda level, message: say(level, f"rescue: {message}"),
            # Without this the wave writes its transcripts to
            # `artifacts/transcripts/adhoc/`, and the console lists only
            # `run-<id>/` -- so the Healer's one model-backed step, the one that
            # decides what replaced a lost control, was the single stage of the
            # pipeline whose reasoning could not be read back. It ran, it was
            # recorded, and it was filed where nothing looks.
            run_id=run_id,
            # Filed as the Healer, not as the colony: this wave belongs to a
            # scenario that could not be replayed, and grouping it with the
            # exploration would put the answer to "what did the Healer do"
            # under the wrong agent.
            filed_as="healer",
        )
        say("info", f"rescue: colony left {len(exploration.world.states)} state(s)")
        return exploration.world, "colony"
    except Exception as exc:
        say("warn", f"rescue: colony skipped ({type(exc).__name__}: {exc})")
        return world, "crawl"


def _role(action: str) -> str:
    """`button` from `button:Sign in`, `submit[valid]` from a form action."""
    return action.split(":", 1)[0] if ":" in action else action


def replacement(
    world: WorldMap, here: str, step, was: str
) -> tuple[str | None, str]:
    """Which action in the fresh map now does what `step` recorded. Or none.

    Three filters, narrowing, and the order is the strength of the evidence.

    **Same destination.** An edge landing on the state key the step expected is
    the strongest thing the map can say: the control was renamed and the screen
    it leads to did not change. Nothing else needs to match.

    **Same kind, same effect.** Failing that, an edge of the same role -- a link
    replaced by a link -- whose observed `mutating` flag and whose movement
    agree with what was recorded. A button that now submits where the old one
    navigated is not the same step wearing a new name.

    **Never the one that broke.** The recorded action is excluded explicitly. It
    is not in the fresh map by construction (the Runner could not resolve it),
    but excluding it costs a line and makes that a property rather than a
    coincidence.

    Ties refuse. Two edges that both qualify is the map reporting an ambiguity,
    and inventing a tie-break here would be exactly the coin-flip
    `runner.resolve` declines to make one rung lower.
    """
    edges = [
        edge
        for (from_key, _), group in world.transitions.items()
        if from_key == here
        for edge in group
        if edge.action != was
    ]
    if not edges:
        return None, f"nothing leaves {here[:8]} in the fresh map"

    exact = [e for e in edges if e.to_key == step.expect.to_key]
    if len(exact) == 1:
        return exact[0].action, (
            f"{exact[0].action!r} lands on {step.expect.to_key[:8]}, where this "
            f"step landed when it was recorded"
        )
    if len(exact) > 1:
        return None, (
            f"{len(exact)} actions now land on {step.expect.to_key[:8]} -- the "
            "step is ambiguous, which is a question for a human"
        )

    kind = _role(was)
    alike = [
        e
        for e in edges
        if _role(e.action) == kind
        and e.mutating == step.expect.mutating
        and (e.to_key != here) == step.expect.moved
    ]
    if len(alike) == 1:
        return alike[0].action, (
            f"{alike[0].action!r} is the only {kind} here that "
            f"{'moves' if step.expect.moved else 'stays put'} and "
            f"{'fires' if step.expect.mutating else 'fires no'} a mutation, as "
            "this step recorded"
        )
    if len(alike) > 1:
        return None, (
            f"{len(alike)} {kind} actions here behave as this step recorded -- "
            "the step is ambiguous, which is a question for a human"
        )
    return None, (
        f"the region has {len(edges)} edge(s) and none of them behaves as this "
        f"step recorded ({kind}, "
        f"{'moves' if step.expect.moved else 'stays put'}, "
        f"{'mutating' if step.expect.mutating else 'non-mutating'})"
    )


def attempt(
    page: Page,
    scenario: Scenario,
    result: runner.Result,
    *,
    target_url: str,
    credentials: Credentials | None = None,
    synthesizer=None,
    provider=None,
    budget: CrawlBudget | None = None,
    on_event=None,
    run_id: int | None = None,
) -> Rescue | None:
    """Recover one escalated scenario, or say why it could not be.

    None means this result was never a candidate -- it passed, it healed, it is
    a defect, or it escalated for a reason exploring cannot address. A `Rescue`
    with `recovered=False` means it *was* a candidate and the region was looked
    at and had no answer, which is a different and more useful thing to report.
    """
    index = unattemptable(result)
    if index is None:
        return None

    step = result.steps[index].step
    def say(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    say("info",
        f"rescue: {scenario.name!r} step {index + 1} cannot be attempted "
        f"({step.action!r}) -- exploring the region it was taken from")

    url = land(
        page, scenario, index, target_url=target_url,
        credentials=credentials, synthesizer=synthesizer,
    )
    if url is None:
        return Rescue(
            scenario=scenario.name, step=index + 1, intent=step.intent,
            was=step.action, node=step.from_key,
            why="the steps before it no longer work either, so this is an "
                "earlier failure rather than a lost control",
        )

    world, source = look(
        page, url,
        intent=(
            f"The saved test needs to {step.intent!r} from this screen, and the "
            f"control it recorded ({step.action}) is gone. Find what a user "
            "would now do here instead."
        ),
        credentials=credentials, provider=provider, budget=budget, on_event=on_event,
        run_id=run_id,
    )

    # Where we are standing *now*, not where the suite thinks we are. The whole
    # premise is that this screen moved, so its key has probably moved with it,
    # and looking the old key up in the fresh map would find nothing.
    here = world.entry_key or ""
    if not here:
        return Rescue(
            scenario=scenario.name, step=index + 1, intent=step.intent,
            was=step.action, node=step.from_key, explored=len(world.states),
            source=source, why="the region could not be observed at all",
        )

    action, why = replacement(world, here, step, step.action)
    outcome = Rescue(
        scenario=scenario.name, step=index + 1, intent=step.intent,
        was=step.action, node=step.from_key, now=action, why=why,
        explored=len(world.states), source=source,
        to_key=next(
            (
                edge.to_key
                for (from_key, act), group in world.transitions.items()
                if from_key == here and act == action
                for edge in group
            ),
            "",
        ) if action else "",
    )
    say("decision" if action else "warn", f"rescue: {scenario.name!r} -- {why}")
    return outcome


def apply(scenario: Scenario, rescued: Rescue):
    """The scenario with the recovered action substituted in, plus the Repair.

    Returns `(scenario, Repair | None)`. Shaped like `regression.repaired` on
    purpose: a rescue is a repair whose evidence came from an exploration rather
    than from the resolution ladder, and everything downstream -- the version's
    `heals` log, the map corrections, the re-verification -- should not have to
    know which.
    """
    from .regression import Repair

    if not rescued.recovered or rescued.now is None:
        return scenario, None
    index = rescued.step - 1
    if index >= len(scenario.steps):
        return scenario, None

    steps = list(scenario.steps)
    before = steps[index]
    expect = before.expect
    if rescued.to_key:
        expect = replace(expect, to_key=rescued.to_key)
    steps[index] = replace(before, action=rescued.now, expect=expect)
    return replace(scenario, steps=tuple(steps)), Repair(
        scenario=scenario.name,
        step=rescued.step,
        intent=rescued.intent,
        was=rescued.was,
        now=rescued.now,
        rung="rescue",
        detail=f"[{rescued.source}, {rescued.explored} state(s)] {rescued.why}",
        node=rescued.node,
        to_key=rescued.to_key,
    )


def main(entry_url: str) -> int:
    """Replay the kept suite and try to recover whatever escalates."""
    from playwright.sync_api import sync_playwright

    from . import regression

    directory = regression.directory_for(entry_url)
    version = regression.current(directory)
    if version is None:
        print(f"no suite recorded for {entry_url}")
        return 1

    credentials = Credentials.from_env()
    provider = None
    try:
        from .llm import load

        provider = load()
        print(f"PROVIDER    {provider.name} / {provider.model}")
    except Exception as exc:
        print(f"PROVIDER    none ({type(exc).__name__}) -- deterministic rescue only")

    print(f"SUITE       {version.render()}\n")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(ignore_https_errors=True)
        for scenario in regression.load(version):
            result = runner.run(
                page, scenario, target_url=entry_url, credentials=credentials
            )
            print(f"  {result.verdict.upper():9s} {scenario.name}")
            rescued = attempt(
                page, scenario, result, target_url=entry_url,
                credentials=credentials, provider=provider,
                on_event=lambda level, message: print(f"            {message}"),
            )
            if rescued:
                print(f"            -> {rescued.now or 'not recovered'}: {rescued.why}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"))
