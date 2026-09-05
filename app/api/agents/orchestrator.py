"""The colony: decide where ants go, run them, fold what they learn into the map.

    cd app/api && uv run python -m agents.orchestrator <url> ["natural language intent"]

This is the top of the exploration half of the system. A URL goes in; a world
map and a written account of the application come out, with no human between the
stages. Everything downstream -- the test plan, the coverage gaps, the healer --
reads what this produces.

**Two agents, not one, and the reason is context.** The orchestrator sees every
state and no action lists; an ant sees one state in full and the map as two
numbers (`tools.describe` vs `tools.brief`). A single agent doing both jobs would
have to carry both views, and the combined view grows without bound as the map
does -- which is exactly the failure that makes long-running browser agents
expensive and forgetful. Splitting the roles bounds both.

**What each is allowed to decide.** The orchestrator decides *where to look* and
*when to stop*. An ant decides *what to do* where it is sent. Neither decides
what a state **is** -- that is `state_key`, computed, identical on every run. So
the map's shape is deterministic even though the walk through it is not.

Prompt: `prompts/orchestrator.md`. That file is the tunable part.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from . import tools
from .ant import Report, explore, instructions
from .behavior import BehaviorModel, examine, synthesise
from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observer
from .explorer.crawler import Budget as CrawlBudget, autosave, crawl
from .explorer.synth import Synthesizer
from .explorer.worldmap import WorldMap
from .llm import Exchange, Provider, ToolResult, Transcript, load
from .shots import Shot
from .tracing import save_transcript, start as start_tracing

# Resolved from __file__ like `crawler.RUNS`, so it follows `api/` wherever
# that moves rather than depending on the caller's working directory.
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"


@dataclass(frozen=True)
class Budget:
    """Caps on the colony, not on any one ant.

    `max_waves` bounds orchestrator calls; `max_ants` bounds the expensive part.
    Both are needed: four waves of one ant and one wave of four ants cost the
    same in ants and very differently in wall-clock and in how much each
    decision was informed by the last.
    """

    max_waves: int = 6
    max_ants: int = 12
    ant_actions: int = 5
    max_seconds: float = 900.0

    # Which model drives the ants, when it should not be the one driving the
    # orchestrator. `None` means "the same one", which is what every existing
    # caller gets and what every measurement so far was taken against.
    #
    # Model choice belongs in Budget because it is the largest cost lever
    # there is -- larger than `max_ants`, which is what this class was written
    # to bound. Measured 2026-09-04 at 6,643 prompt tokens per ant call:
    # $0.089 for a full run on `qwen/qwen3-coder-next`, ~$3.42 on
    # `claude-opus-5`. Halving `max_ants` saves less than changing this string.
    ant_provider: str | None = None
    ant_model: str | None = None


@dataclass
class Exploration:
    """Everything one run produced. The artifact the rest of the pipeline reads."""

    world: WorldMap
    reports: list[Report] = field(default_factory=list)
    summary: str = ""
    flows: tuple[dict, ...] = ()
    gaps: tuple[str, ...] = ()
    stopped: str = "budget"  # covered | plateau | budget | error
    waves: int = 0
    transcript_path: str | None = None
    # The semantic layer this colony reasoned over. Kept beside the map rather
    # than folded into it: the map must be identical on every crawl of an
    # unchanged app, and this is an interpretation that will not be.
    behaviour: BehaviorModel = field(default_factory=BehaviorModel)
    # One line per non-ant agent the colony ran, in order. This is what makes
    # the loop a loop: it is fed back into the next `brief`, so the orchestrator
    # can see that a region it is about to test has already been tested.
    experiments: list[str] = field(default_factory=list)
    # Every verdict a healer dispatch produced, for the report.
    results: list = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"EXPLORATION stopped: {self.stopped} "
            f"after {self.waves} wave(s), {len(self.reports)} ant(s)",
            "",
        ]
        if self.summary:
            lines += ["WHAT THIS APPLICATION IS", f"  {self.summary}", ""]
        if self.flows:
            lines.append("FLOWS WORTH TESTING")
            for flow in self.flows:
                lines.append(f"  * {flow.get('name', '?')} -- {flow.get('why', '')}")
                path = flow.get("states") or []
                if path:
                    lines.append(
                        "      " + " -> ".join(str(s)[:8] for s in path)
                    )
            lines.append("")
        if self.behaviour.hypotheses:
            lines += [self.behaviour.render(), ""]
        if self.experiments:
            lines.append("WHAT THE COLONY RAN")
            lines += [f"  - {line}" for line in self.experiments]
            lines.append("")
        if self.gaps:
            lines.append("WHAT WE DID NOT REACH")
            lines += [f"  ! {gap}" for gap in self.gaps]
            lines.append("")
        lines += [self.world.summary()]
        return "\n".join(lines)



@dataclass
class Colony:
    """Everything a dispatched agent needs, gathered once instead of threaded.

    `run()` used to pass six arguments into `explore` and there was one kind of
    agent. With three, each wanting a different five of the same nine things,
    a context object is the difference between a dispatch table and a switch
    statement that grows a parameter every time an agent is added.
    """

    page: object
    world: WorldMap
    entry_url: str
    ant_provider: object
    credentials: object
    budget: "Budget"
    run_id: int | None = None
    shot: object = None
    synthesizer: object = None
    # What this colony's transcripts are filed as. Empty is the colony itself
    # ("orchestrator" and "ant"); `rescue.look` sets "healer", because the same
    # machinery serving a different stage of the pipeline should not be
    # indistinguishable from it in the record.
    filed_as: str = ""
    # Scenarios a generator dispatch compiled, keyed by the state it was sent
    # to. The healer reads this: it is the only channel between the two, and
    # it is why "generate here, then test here" is a plan the orchestrator can
    # make rather than an order the pipeline hardcodes.
    compiled: dict = field(default_factory=dict)
    # Every `runner.Result` a healer dispatch produced. The colony's evidence.
    verdicts: list = field(default_factory=list)
    # The semantic layer, once it exists. A generator dispatch reads it so that
    # "compile tests for checkout" can compile a *sequence* the colony believes
    # in, and not only the ranked single edges the map offers. None until
    # `synthesise` has run, and None for the whole run when no provider is
    # configured -- which `planner.plan` handles by planning from the map.
    behaviour: object = None


def _send_ant(colony: Colony, state: str, instruction: str, tag: str):
    """Explore. Returns the ant's `Report` -- the one agent with a rich result."""
    return explore(
        colony.page,
        colony.world,
        colony.ant_provider,
        entry_url=colony.entry_url,
        start_key=state,
        instruction=instruction,
        credentials=colony.credentials,
        budget=colony.budget.ant_actions,
        run_id=colony.run_id,
        shot=colony.shot,
        synthesizer=colony.synthesizer,
        role=colony.filed_as or "ant",
    )


def _send_generator(colony: Colony, state: str, instruction: str, tag: str) -> str:
    """Compile the paths through this state into runnable scenarios.

    Filtered to scenarios that actually pass through the state the orchestrator
    named, so "compile tests for checkout" does not silently return the login
    tests. A generator sent at a state no scenario reaches says so rather than
    reporting the whole suite -- an agent that answers a narrow question with a
    broad answer is worse than one that answers nothing.

    It plans through `planner.plan`, with the colony's own behavioural model as
    an input. Before that it called `generator.scenarios` directly, which meant
    the colony could believe "log in, add an item, reload, check it survived"
    and then compile only the ranked single edges -- the one agent that had a
    semantic model of the app was the one agent that never used it.
    """
    from .planner import plan

    every = plan(
        colony.world,
        colony.behaviour,
        limit=colony.budget.ant_actions * 4,
    )
    here = plan(
        colony.world,
        colony.behaviour,
        limit=colony.budget.ant_actions * 4,
        node=state,
    )
    colony.compiled[state] = here.scenarios
    if not here.scenarios:
        return (
            f"generator {tag}: no scenario passes through [{state[:8]}]. "
            f"{len(every)} exist elsewhere in the map; this region has no "
            "recorded transition worth compiling yet -- send an ant first."
        )
    names = "; ".join(scenario.name for scenario in here.scenarios[:4])
    return (
        f"generator {tag}: compiled {len(here)} scenario(s) through "
        f"[{state[:8]}] ({here.from_behaviour} from the behavioural model) "
        f"-- {names}"
    )


def _send_healer(colony: Colony, state: str, instruction: str, tag: str) -> str:
    """Replay what was compiled here and report the verdicts.

    **This is the only agent whose answer is not the model's.** It runs the
    scenarios and `runner.run` classifies each step from two observable signals;
    nothing here is asked to judge. That is the deterministic floor the colony
    stands on: the orchestrator chooses *what* to test and the browser decides
    what happened.
    """
    from .runner import run as replay

    compiled = colony.compiled.get(state, ())
    if not compiled:
        return (
            f"healer {tag}: nothing compiled for [{state[:8]}] yet. Send a "
            "generator to this state before a healer."
        )

    counts: dict[str, int] = {}
    ran = 0
    for scenario in compiled[:3]:
        result = replay(
            colony.page,
            scenario,
            credentials=colony.credentials,
            synthesizer=colony.synthesizer,
        )
        colony.verdicts.append(result)
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
        ran += 1

    summary = ", ".join(f"{n} {verdict}" for verdict, n in sorted(counts.items()))
    detail = ""
    if counts.get("defect"):
        detail = (
            " -- a DEFECT here means the locator resolved and the application "
            "did something other than what it did when the crawler watched it. "
            "There is no repair to make, and healing it would manufacture a "
            "green run over a real bug."
        )
    return (
        f"healer {tag}: ran {ran} scenario(s) through [{state[:8]}] "
        f"-- {summary}{detail}"
    )


# Advertised in `tools.AGENT_KINDS` and handled here. A probe check asserts the
# two sets are equal, because an agent the model is offered and the runtime
# cannot run is a dispatch that burns a wave and reports nothing.
AGENTS = {
    "ant": _send_ant,
    "generator": _send_generator,
    "healer": _send_healer,
}


# How many unanswered questions one wave may add to the run's report. A filter
# that is right about the common case still needs a bound for the run where it
# is wrong -- the same argument as `ASSERTION_CAP`.
DOUBT_CAP = 5


def standing_doubts(reports, wave: int) -> tuple[tuple[str, str, str], ...]:
    """(ant tag, state key, doubt) for every ant that raised one. Pure.

    These reach the run's **report**, and deliberately not the orchestrator's
    prompt. Both halves were built; the second was measured and removed.

    **Why not the prompt.** The obvious move is to restate unanswered doubts
    every wave, the way `perished` is restated, and the argument looks
    identical: a fact mentioned in wave 2 competes with everything since. Two
    measurements from a peer session's saucedemo run say otherwise.

    First, a doubt is not lost. `transcript.exchanges` only ever appends, so
    wave 1's feedback -- reports and all -- is still in context at wave 3. The
    failure is dilution, not disappearance, and the decisive case shows
    restatement would not have fixed it: the wave-2 ant at 3cada78a asked
    "whether cb977164 is genuinely the inventory page", that doubt *was* in the
    wave-3 dispatch context by the mechanism above, and the orchestrator opened
    wave 3 with "the login door is cracked (standard_user -> 5a98e093 =
    inventory)" regardless. The refutation was present and was not heeded.

    Second, the volume is wrong for a standing list. All 12 ants in that run
    raised `uncertain`, and most of it reports the ant's own limits rather than
    a doubt about the application -- "I cannot read the rendered page content",
    "I never actually saw the field labels". Nine of the twelve cite a state
    key, so the citation rule that works in `behavior.admit` does not separate
    them here either. Restating a dozen of those under a heading calling each
    one worth settling would add noise to the exact context window whose
    signal-to-noise had already lost to a fabricated premise.

    `perished` is the precedent and the contrast: it is computed (`navigate`
    returned None), rare, and machine-checkable. A doubt is self-reported model
    prose and is apparently universal. The two do not belong in the same
    mechanism.

    What survives is the honest half: an unanswered question is a coverage gap
    of the most useful kind, because it names where to look, and it belongs in
    the report where a human reads it.

    **And only when it is the ant's whole output.** The volume problem does not
    disappear by moving one layer down -- `Exploration.gaps` renders as "WHAT
    WE DID NOT REACH", which in that saucedemo run is a single line, and folding
    in twelve self-reported limitations would make it thirteen in the section
    whose entire job is an honest account of the run's blind spots. A judge
    reads that section; at least the prompt had a model reading it.

    So the gate is `actions_taken == 0`: computed, not semantic. An ant that
    acted has told us something whether or not it was also uncertain. An ant
    that produced nothing but a doubt is reporting that the assignment was
    impossible, and that is a gap by any definition. Measured against the same
    12 ants -- every one of which took between one and four actions -- this
    carries none of them, and the section stays the one honest line it was.

    Capped as well as filtered, on the `ASSERTION_CAP` precedent: a filter that
    is right about the common case still has to be bounded for the run where it
    is wrong.
    """
    carried = tuple(
        (f"w{wave}a{index + 1}", report.start_key, report.uncertain.strip())
        for index, report in enumerate(reports)
        if getattr(report, "uncertain", "").strip()
        and not getattr(report, "actions_taken", 0)
    )
    return carried[:DOUBT_CAP]


def ant_provider_for(budget: Budget, provider: Provider) -> Provider:
    """Which model drives the ants. Called once, before the first wave.

    The two roles in a colony are not the same job. The orchestrator makes ~6
    calls a run and each one decides where up to 12 ants go, so a bad call
    wastes a whole wave. An ant makes ~60 calls doing mechanical
    click-and-observe against a budget that already bounds its damage. That
    asymmetry is the whole argument for splitting them -- a strong model where
    judgement compounds, a cheap one where it does not.

    The cost of the split, and the reason it is a decision rather than a
    default: `llm/__init__.py` opens by saying the point of the neutral
    transcript is that `agent.md` "has to mean the same thing to Claude, to
    Gemini, and to a Claude Code subagent handed the same file". A mixed run is
    no longer a clean provider A/B -- when a run goes badly you can no longer
    tell which of the two models to blame, and the transcript diff that would
    have told you now compares two things that differ in more than one way.

    Measured, per full run: one cheap model $0.089, one Opus $3.42, and a
    strong orchestrator over cheap ants ~$0.35 -- roughly 112, 3, and 28 runs
    against a $10 cap.

    TODO(shivam): the policy. `budget.ant_provider` already carries the static
    answer; what is unwritten is whether the answer should be static at all.
    The adaptive version has evidence available to it that the static one
    ignores -- `Report.ended` is already "stalled" when an ant achieved
    nothing, so a colony could start every ant cheap and promote only the
    states where cheap ants keep stalling. That spends the strong model on the
    regions that actually resisted, rather than on a guess made before the
    first observation. It also means two models inside one run, which is the
    A/B cost above paid twice over.
    """
    if budget.ant_provider is None and budget.ant_model is None:
        return provider
    return load(provider=budget.ant_provider, model=budget.ant_model)


def run(
    page,
    entry_url: str,
    provider: Provider,
    *,
    intent: str | None = None,
    budget: Budget | None = None,
    credentials: Credentials | None = None,
    on_event=None,
    run_id: int | None = None,
    shot: Shot | None = None,
    checkpoint=None,
    synthesizer=None,
    world: WorldMap | None = None,
    filed_as: str = "",
    experiments: list[str] | None = None,
) -> Exploration:
    """Explore `entry_url` until the orchestrator is satisfied or the budget ends.

    `on_event(level, message)` is called at every decision point. It exists so
    the UI can stream what the colony is doing -- `models.Event`'s docstring
    already describes itself as "what the canvas streams to show reasoning", and
    an autonomous system that cannot show its reasoning is one nobody trusts.
    Defaults to printing.

    `checkpoint(world)` is the same idea for the map rather than the reasoning:
    it is called at the end of every wave, so a caller that persists the map can
    draw it as it grows. Without it the colony's map exists only in memory until
    `run` returns, and a console watching a ten-minute exploration has an empty
    canvas for ten minutes and a finished graph in the eleventh. Optional, and
    never allowed to end a run -- see the call site.
    """
    budget = budget or Budget()
    credentials = credentials or Credentials.from_env()
    emit = on_event or (lambda level, message: print(f"[{level}] {message}"))
    deadline = time.monotonic() + budget.max_seconds

    # Resolved once rather than per ant: a colony dispatches up to 12 of them
    # and constructing a client 12 times would hide a config error behind the
    # first ant's stack trace instead of failing before the first wave.
    ant_provider = ant_provider_for(budget, provider)
    if ant_provider is not provider:
        emit(
            "info",
            f"orchestrator on {provider.model}, ants on {ant_provider.model}",
        )

    # A map handed in is a crawl that already happened. The colony's first
    # wave then decides where *judgement* is needed rather than what the
    # application is -- measured on saucedemo, waves 1-4 of an unseeded run go
    # entirely on rediscovering structure `explorer.crawler` produces in 124
    # seconds for nothing, and the budget is gone before `finish` is reached.
    #
    # `actions_of` is rebound either way: it closes over a page, and the page
    # that crawled is not the page the ants will walk.
    seeded = world is not None
    world = world or WorldMap()
    world.actions_of = lambda obs: forms.available_actions(page, obs)
    result = Exploration(world=world)
    # What was already run against this app before the colony started -- on a
    # second run, the kept suite's verdicts. Seeded into the same list the
    # colony's own generator and healer dispatches append to, so the first
    # brief already says which saved tests failed and where, and the first
    # wave can be aimed at that region rather than at the untried count.
    result.experiments = list(experiments or ())

    observer = Observer(page)
    observer.start_window()
    page.goto(entry_url)
    entry_key = world.record(observer.observe())
    if shot is not None:
        world.attach_screenshot(entry_key, shot(entry_key))
    if seeded:
        emit(
            "info",
            f"seeded with {len(world.states)} state(s), "
            f"{sum(len(t) for t in world.transitions.values())} transition(s) "
            f"and {len(world.skipped)} refused action(s) from the crawler",
        )
    emit("info", f"entry {entry_url} -> state {entry_key[:8]}")

    colony = Colony(
        page=page,
        world=world,
        entry_url=entry_url,
        ant_provider=ant_provider,
        credentials=credentials,
        budget=budget,
        run_id=run_id,
        shot=shot,
        synthesizer=synthesizer,
        filed_as=filed_as,
    )

    # The semantic layer, built once from the seeded map before any agent is
    # sent. This is the step that makes the orchestrator's job reasoning rather
    # than graph traversal: without it the only thing it can see is which
    # actions are untried, so the only plan it can form is "try them". With it
    # the brief carries claims -- and an unexamined claim names what an agent
    # would be *for*.
    #
    # It uses the orchestrator's own provider, not the ants': this is one call
    # and it decides where the whole colony looks.
    if world.states:
        result.behaviour = synthesise(
            world, provider, run_id=run_id,
            on_event=lambda level, message: emit(level, message),
        )
        # The generator dispatch plans from it. Set here rather than passed to
        # `Colony(...)` above because it does not exist until this call returns,
        # and a colony built without it would have to be rebuilt.
        colony.behaviour = result.behaviour

    system = instructions("orchestrator")
    if intent:
        # Natural-language intent is a "good to have" in the brief ("focus on
        # checkout and authentication"). It steers dispatch without touching the
        # prompt file, so the file stays the same for every run.
        system += f"\n\n## What the user asked for\n\n{intent.strip()}"

    transcript = Transcript(
        prompt=tools.brief(
            world,
            waves_left=budget.max_waves,
            ants_left=budget.max_ants,
            behaviour=result.behaviour,
            results=result.experiments,
            credentials=credentials,
        )
    )
    ants_left = budget.max_ants

    # States an ant was sent to and could not reach. A state is a place the
    # colony *stood*, not a place it can necessarily stand again: `navigate`
    # replays the shortest path from the entry and verifies the landing, and on
    # an app that carries data the replay stops reproducing the state the
    # moment the data moves underneath it. saucedemo's inventory page is the
    # measured case -- `Add to cart` re-keys it, so every state recorded behind
    # one is gone as soon as the cart is not empty.
    #
    # `navigate` returning None is correct and the ant reporting `stuck` is
    # correct. What was wrong is that nothing carried the fact back up: the
    # orchestrator sees the map, the map still lists the state, and it kept
    # spending ants on it. Measured 2026-09-05 against saucedemo: 4 of 12 ants
    # ended `stuck`, all of them in the last two waves, all on inventory states
    # a `Add to cart` had already destroyed -- a third of the colony's budget
    # spent arriving nowhere.
    perished: set[str] = set()

    # Every doubt any ant raised, accumulated across waves for the report. Not
    # fed back into the prompt -- see `standing_doubts` for the measurements
    # that decided that.
    unsettled: list[tuple[str, str, str]] = []

    for wave in range(budget.max_waves):
        if time.monotonic() > deadline or ants_left <= 0:
            break

        try:
            turn = provider.turn(system, transcript, tools.ORCHESTRATOR_TOOLS)
        except Exception as exc:
            # The same argument the dead-ant handler below makes, one level up
            # and previously unmade. A provider failure here -- a 402, a
            # transient 503 -- propagated out of `run` and took the whole
            # exploration with it: the map, the wave that had already reported,
            # the autosave, the summary. Measured 2026-09-05: a seeded run lost
            # a 24-state crawl and a completed first wave to a credits error on
            # the wave-3 call, and the process exited with a traceback rather
            # than with the map it was holding.
            #
            # Breaking out rather than retrying, because the two failures worth
            # surviving are opposite: a rate limit wants a wait this loop
            # cannot afford, and an exhausted balance never recovers. Both are
            # better answered by ending the run with what it has.
            emit("error", f"orchestrator call failed: {type(exc).__name__}: {exc}")
            result.stopped = "error"
            result.gaps = result.gaps + (
                f"The colony stopped early: the model call failed ({exc}). "
                "The map below is what had been walked when it did.",
            )
            break

        if turn.done:
            emit("warn", "orchestrator said nothing actionable; stopping")
            break

        call = turn.calls[0]

        if call.name == "finish":
            result.summary = str(call.arguments.get("summary", ""))
            result.flows = tuple(
                f for f in (call.arguments.get("flows") or []) if isinstance(f, dict)
            )
            result.gaps = tuple(str(g) for g in (call.arguments.get("gaps") or []))
            result.stopped = str(call.arguments.get("reason", "covered"))
            emit("decision", f"finished: {result.stopped} -- {result.summary[:120]}")
            break

        assignments = [
            a for a in (call.arguments.get("assignments") or []) if isinstance(a, dict)
        ][:ants_left]
        emit("decision", f"wave {wave + 1}: {call.arguments.get('reasoning', '')}")

        wave_reports: list[Report] = []
        rejected: list[str] = []

        for ant_index, assignment in enumerate(assignments):
            wanted = str(assignment.get("state", "")).strip()
            # One gate for the state id and the agent name, shared with the
            # probe so an agent advertised in the schema and missing from
            # `AGENTS` fails a check rather than a demo.
            refusal = tools.refuse_assignment(
                world, assignment, credentials=credentials
            )
            if refusal is not None:
                rejected.append(refusal)
                continue

            matches = [k for k in world.states if k.startswith(wanted)]
            kind = str(assignment.get("agent", "ant")).strip() or "ant"

            # Only an ant has to *stand* in the state. A generator reads the
            # map and never touches the browser; a healer replays from the
            # entry URL and builds its own route. Refusing those on a perished
            # state would forbid testing exactly the regions the colony changed
            # by exploring them -- which is most of the interesting ones.
            if kind == "ant" and matches[0] in perished:
                # Refused before the ant is built, not after it dies. The ant
                # would cost a model call and a page load to rediscover what
                # the last one already established.
                rejected.append(
                    f"{wanted!r} can no longer be reached -- an earlier ant "
                    "tried and could not get back to it. Something the colony "
                    "did has changed the application underneath that state. "
                    "Send this ant somewhere reachable instead"
                )
                continue

            instruction = str(assignment.get("instruction", "")).strip()
            tag = f"w{wave + 1}a{ant_index + 1}"
            emit("info", f"  {kind} {tag} -> {matches[0][:8]}: {instruction}")

            # Everything this agent records is stamped with it. Set here rather
            # than passed into the handler, because the recording happens deep
            # in `ant.py` and `forms.py` and threading an identity down would
            # put the colony's dispatch structure into modules that have no ants.
            world.attribution = tag
            try:
                outcome = AGENTS[kind](colony, matches[0], instruction, tag)
            except Exception as exc:
                # One agent dying must not take the colony with it. A
                # transient 503 from the provider killed a completed two-wave
                # exploration during development, and the map -- states,
                # transitions, every observation -- was lost with the stack
                # frame, even though nothing about it was wrong.
                #
                # The dead agent becomes an ordinary result, so the
                # orchestrator can see what happened and decide whether to
                # retry that state, go elsewhere, or finish with what it has.
                # A failed healer is reported as *untested*, never as passing:
                # that distinction is the difference between a suite and a
                # dashboard.
                emit("error", f"  {kind} died: {type(exc).__name__}: {exc}")
                if kind == "ant":
                    outcome = Report(start_key=matches[0], ended="error")
                    outcome.uncertain = (
                        f"this ant failed before reporting ({type(exc).__name__}); "
                        "its region is still unexplored"
                    )
                else:
                    outcome = (
                        f"{kind} {tag}: failed on [{matches[0][:8]}] "
                        f"({type(exc).__name__}: {exc}). That region is "
                        "untested, not passing."
                    )
            finally:
                # Anything recorded between ants -- the orchestrator's own
                # bookkeeping, a later crawl -- belongs to no ant, and a stale
                # tag would quietly credit it to the last one that ran.
                world.attribution = None

            ants_left -= 1

            if not isinstance(outcome, Report):
                # A generator or a healer. Its result is one line, and that line
                # is the whole feedback loop: it goes into `result.experiments`,
                # which `brief()` renders on every subsequent wave, so the next
                # dispatch decision is made knowing what the last one produced.
                result.experiments.append(str(outcome))
                emit("info", f"  {outcome}")
                continue

            report = outcome
            if report.ended == "stuck":
                # The one ending that says something about the *map* rather
                # than about the ant. Recorded so the next wave cannot repeat
                # it; not removed from the map, because the state was really
                # observed and its transitions are still evidence.
                perished.add(matches[0])
                emit(
                    "warn",
                    f"  {matches[0][:8]} is no longer reachable -- not "
                    f"assigning further ants to it",
                )

            wave_reports.append(report)
            emit(
                "info",
                f"  ant <- {report.actions_taken} action(s), "
                f"{report.states_discovered} new state(s), {report.ended}",
            )

        result.reports += wave_reports
        result.waves = wave + 1
        unsettled += standing_doubts(wave_reports, wave=wave + 1)

        # The wave is the honest unit: ants mutate `world` as they go, but only
        # here is it a settled account of what the colony knows. Failure to save
        # must not kill an exploration that is otherwise fine -- the map is
        # still in memory and the final save at the end of `run` gets another
        # attempt -- so this is reported and stepped over, like a dead ant.
        if checkpoint is not None:
            try:
                checkpoint(world)
            except Exception as exc:
                emit("error", f"  checkpoint failed: {type(exc).__name__}: {exc}")

        feedback = tools.brief(
            world,
            reports=wave_reports,
            waves_left=budget.max_waves - wave - 1,
            ants_left=ants_left,
            behaviour=result.behaviour,
            results=result.experiments,
            credentials=credentials,
        )
        if perished:
            # Stated every wave, not once. The orchestrator's context is the
            # transcript, and a fact mentioned in wave 2 competes with
            # everything since; a standing list is what stops wave 5 proposing
            # what wave 4 already learned was impossible.
            feedback = (
                "these states are recorded but NO LONGER REACHABLE -- the "
                "colony changed the application and cannot get back to them. "
                "Do not assign ants here: "
                + ", ".join(sorted(k[:8] for k in perished))
                + "\n\n"
                + feedback
            )

        if rejected:
            feedback = (
                "some assignments were refused: "
                + "; ".join(rejected)
                + "\nuse the state ids exactly as listed.\n\n"
                + feedback
            )

        transcript.exchanges.append(
            Exchange(
                text=turn.text,
                calls=(call,),
                opaque=turn.opaque,
                results=(
                    ToolResult(call_id=call.id, name=call.name, content=feedback),
                ),
            )
        )

    result.results = colony.verdicts

    # Carried into the run's own account. An unanswered question is a coverage
    # gap of the most useful kind -- it names where to look -- and reporting it
    # only to the model that could have acted on it wastes it.
    result.gaps = result.gaps + tuple(
        f"unsettled by {tag} at [{key[:8]}]: {doubt}" for tag, key, doubt in unsettled
    )

    # Ruled on at the end rather than at synthesis, because the map is bigger
    # now: an invariant about an edge no ant had walked when it was proposed is
    # `inconclusive` then and decidable now. This is the cheapest verdict in the
    # system -- it reads recorded transitions and makes no call of any kind.
    if result.behaviour.hypotheses:
        result.behaviour = examine(world, result.behaviour)
        broken = [
            h for h in result.behaviour.hypotheses if h.status == "contradicted"
        ]
        for hypothesis in broken:
            emit(
                "decision",
                f"invariant broken -- {hypothesis.claim}: {hypothesis.because}",
            )

    saved = autosave(
        world, entry_url, mode="colony", model=provider.model,
        stopped=result.stopped, waves=result.waves, ants=len(result.reports),
    )
    if saved:
        emit("info", f"map saved to {saved}")

    try:
        result.transcript_path = str(
            save_transcript(
                transcript, run_id=run_id, role=filed_as or "orchestrator",
                system=system,
            )
        )
        emit("info", f"transcripts written to {result.transcript_path}")
    except Exception:
        pass

    if not result.summary and result.stopped != "error":
        # Ran out of budget before the orchestrator chose to finish. Say so
        # rather than presenting a partial map as a complete account. An error
        # is excluded: it is also a run with no summary, and relabelling it
        # "budget" would hide the reason it has none.
        result.stopped = "budget"
        result.gaps = result.gaps + (
            "Exploration was cut off by the budget before the orchestrator "
            "judged the map complete; unexplored actions remain.",
        )

    return result


def seed_map(page, entry_url: str, credentials, synthesizer, budget=None):
    """Walk the app deterministically, and hand the colony what it found.

    The whole argument for the hybrid in one function. Measured on saucedemo at
    equal action budgets: the crawler reaches 21 states for nothing in 124s,
    the colony reaches 16 for ~$0.09 in ~1400s. An unseeded colony spends its
    first four waves rebuilding what this returns, and in three complete runs
    never had budget left to call `finish` -- so it produced no summary and no
    flows, which is the only thing it can do that the crawler cannot.

    The refused actions matter more than the states. `world.skipped` is the
    list of things determinism tried and could not do -- login walls, forms
    with no fillable field -- and that is precisely the work worth spending a
    model on. `tools.brief` renders it for the orchestrator.
    """
    return crawl(
        page,
        entry_url,
        budget or CrawlBudget(),
        credentials=credentials,
        synthesizer=synthesizer,
    )


def main(entry_url: str, intent: str | None = None) -> int:
    traces = start_tracing()
    provider = load()
    if traces:
        print(f"PHOENIX     {traces}")
    credentials = Credentials.from_env()

    print(f"COLONY      {provider.name} / {provider.model}")
    print(f"TARGET      {entry_url}")
    print(
        "CREDENTIALS "
        + (credentials.username or "none set (AIVAR_USERNAME/AIVAR_PASSWORD)")
    )
    if intent:
        print(f"INTENT      {intent}")
    print()

    # Seeding is the default because the hybrid is the product; `SEED=0` turns
    # it off, which is what the crawler-vs-colony A/B needs to stay runnable.
    seed = os.environ.get("SEED", "1") != "0"
    synthesizer = Synthesizer(cache_path=ARTIFACTS / "invalid-payloads.json")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Test and staging targets routinely serve self-signed or expired certs;
        # refusing them would make the agent useless on its own target market. The
        # run still reports that transport security was not verified -- see
        # `_tls_warning`.
        page = browser.new_page(ignore_https_errors=True)

        world = None
        if seed:
            print("SEED        crawling deterministically first...")
            world = seed_map(page, entry_url, credentials, synthesizer)
            print(
                f"SEED        {len(world.states)} states, "
                f"{sum(len(t) for t in world.transitions.values())} transitions, "
                f"{len(world.skipped)} refused -- handing to the colony\n"
            )

        result = run(
            page, entry_url, provider, intent=intent, credentials=credentials,
            synthesizer=synthesizer, world=world,
        )
        browser.close()

    print()
    print(result.render())
    return 0 if result.world.states else 1


if __name__ == "__main__":
    sys.exit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut",
            sys.argv[2] if len(sys.argv) > 2 else None,
        )
    )
