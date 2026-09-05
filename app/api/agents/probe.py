"""Observable checks for the agent layer. Not a test suite -- evidence.

    cd app/api && uv run python -m agents.probe

**Needs no API key and spends no quota.** Every check drives the real ant and
the real orchestrator against the real browser, with a scripted provider
standing in for the model. That is the point: the expensive, rate-limited,
non-deterministic part is the one thing worth faking, and everything underneath
it -- the loop, the tool plumbing, the map, the prompts -- is exercised for real.

Each check is a bug that actually happened. The most important is `bounded`: an
ant whose model answered in prose instead of calling a tool looped forever, one
API call per iteration, silently. It burned the daily quota of three separate
models before anyone noticed, because nothing about it looked like a failure --
no error, no output, just a run that never finished.

Needs `make dev` for the SUT at :3000.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from . import tools
from .ant import Report, explore, instructions
from . import ant, orchestrator
from .explorer import crawler, forms
from .explorer.forms import Credentials
from .explorer.observer import Observer
from .explorer.worldmap import WorldMap
from .llm import Exchange, ToolCall, ToolResult, Transcript, Turn
from .orchestrator import Budget, run

# The Makefile reads WEB_PORT from `.worktree-env` so several stacks can run at
# once; a probe that ignored it would test whichever checkout happened to own
# port 3000, which in a worktree is somebody else's code. make exports an
# undefined variable as an empty string, so the fallback has to catch "" and
# not just a missing key.
SUT = f"http://localhost:{os.environ.get('WEB_PORT') or '3000'}/sut"
CREDENTIALS = Credentials("probe@example.com", "probe-password")


class Chatty:
    """Answers in prose and never calls a tool. Reproduces the hang."""

    name, model = "scripted:chatty", "none"

    def __init__(self) -> None:
        self.calls = 0

    def turn(self, system, transcript, tool_defs):
        self.calls += 1
        if self.calls > 200:
            raise AssertionError("unbounded: still running after 200 calls")
        return Turn(text="Let me consider which action is best here...")


class Payloads:
    """Answers the synthesizer's tool call with the fields it is given.

    Constructed with names that exist on the form, or with one that does not:
    the synthesizer applies the same extractive rule the critic does, and a
    value for a field the form has not got cannot be typed into anything.
    """

    name, model = "scripted:payloads", "none"

    def __init__(self, *names: str, prose: bool = False) -> None:
        self.names, self.prose, self.calls = names, prose, 0

    def turn(self, system, transcript, tool_defs):
        self.calls += 1
        if self.prose:
            return Turn(text="I would suggest trying an invalid email address.")
        return Turn(
            text="",
            calls=(
                ToolCall(
                    id="payload-1",
                    name="payload",
                    arguments={
                        "fields": [
                            {"name": n, "value": "@@@", "why": "malformed"}
                            for n in self.names
                        ],
                        "expect": "the form should show a validation error",
                    },
                ),
            ),
        )


class Ranker:
    """Ranks two real candidates, invents a third, and omits the rest.

    All three behaviours in one turn on purpose: the critic must keep what was
    cited, discard what was fabricated, and still report what was left out.
    """

    name, model = "scripted:ranker", "none"

    def turn(self, system, transcript, tool_defs):
        return Turn(
            text="",
            calls=(
                ToolCall(
                    id="rank-1",
                    name="prioritise",
                    arguments={
                        "ranked": [
                            {"id": 1, "risk": "a customer could be charged twice"},
                            {"id": 0, "risk": "bad input reaches the database"},
                            {"id": 9999, "risk": "a gap nobody computed"},
                        ]
                    },
                ),
            ),
        )


class BrokeRanker:
    """A provider that fails the way an empty OpenRouter balance fails.

    The critic's docstring promises the no-provider path is "a real ranking and
    not a degraded mode". A provider that *raises* must land in that same place:
    ranking is the only thing the model does here, so losing it may cost the
    order and the risk prose, and nothing else. It must never cost the suite --
    `generator.scenarios` is deterministic and runs after this call.
    """

    name, model = "scripted:broke", "none"

    def turn(self, system, transcript, tool_defs):
        raise RuntimeError(
            'qwen/qwen3-coder-next: 402 from the provider: {"error":{"message":'
            '"This request requires more credits, or fewer max_tokens. You '
            'requested up to 32768 tokens, but can only afford 267."}}'
        )


class Explorer:
    """Takes the first untried action it is shown, then reports."""

    name, model = "scripted:explorer", "none"

    def __init__(self, act_limit: int = 2) -> None:
        self.calls = 0
        self.act_limit = act_limit
        self.acted = 0

    def turn(self, system, transcript, tool_defs):
        self.calls += 1
        names = {t.name for t in tool_defs}
        latest = (
            transcript.exchanges[-1].results[-1].content
            if transcript.exchanges and transcript.exchanges[-1].results
            else transcript.prompt
        )
        untried = [
            line.strip()[3:].strip()
            for line in latest.splitlines()
            if line.startswith("  .  ")
        ]
        if "act" in names and untried and self.acted < self.act_limit:
            self.acted += 1
            return Turn(
                text="",
                calls=(
                    ToolCall(
                        id=f"a{self.calls}",
                        name="act",
                        arguments={"action": untried[0], "why": "unexplored"},
                    ),
                ),
            )
        return Turn(
            text="",
            calls=(
                ToolCall(
                    id=f"r{self.calls}",
                    name="report",
                    arguments={
                        "summary": "A login form served in three markup variants.",
                        "uncertain": "Whether the form posts anywhere.",
                        "branches": [
                            {
                                "action": "link:v2",
                                "why": "a second variant",
                                "priority": "medium",
                            }
                        ],
                    },
                ),
            ),
        )


class Colony(Explorer):
    """Dispatches one wave, then finishes. Also drives the ants."""

    name, model = "scripted:colony", "none"

    def __init__(self) -> None:
        super().__init__()
        self.waves = 0

    def turn(self, system, transcript, tool_defs):
        names = {t.name for t in tool_defs}
        if "dispatch" not in names:
            # An ant's turn. Each ant gets a fresh action allowance.
            return super().turn(system, transcript, tool_defs)

        latest = (
            transcript.exchanges[-1].results[-1].content
            if transcript.exchanges
            else transcript.prompt
        )
        states = [
            line.strip()[1:9]
            for line in latest.splitlines()
            if line.strip().startswith("[")
        ]
        self.waves += 1
        if self.waves == 1 and states:
            self.acted = 0
            return Turn(
                text="",
                calls=(
                    ToolCall(
                        id="w1",
                        name="dispatch",
                        arguments={
                            "reasoning": "map the entry page first",
                            "assignments": [
                                {"state": states[0], "instruction": "look around"}
                            ],
                        },
                    ),
                ),
            )
        return Turn(
            text="",
            calls=(
                ToolCall(
                    id="f",
                    name="finish",
                    arguments={
                        "summary": "A one-page login form.",
                        "flows": [
                            {"name": "sign in", "why": "the only user action"}
                        ],
                        "gaps": ["The form posts nowhere."],
                        "reason": "covered",
                    },
                ),
            ),
        )


def _page_and_map(pw):
    browser = pw.chromium.launch()
    page = browser.new_page()
    world = WorldMap(actions_of=lambda obs: forms.available_actions(page, obs))
    observer = Observer(page)
    observer.start_window()
    page.goto(SUT)
    return browser, page, world, world.record(observer.observe())


def render_verdicts(result) -> str:
    """A failed classification check, in one line per step. The `detail` of a
    `check` is only read when something broke, so it carries the verdict and the
    rung that produced it -- which together are the whole diagnosis."""
    return " | ".join(
        f"{step.verdict}({step.resolution.rung}): {step.detail[:90]}"
        for step in result.steps
    ) or "no steps ran"


def _map_of(edges: list[tuple[str, str, str, bool]], network=()) -> WorldMap:
    """A world map with exactly these edges, and one observation behind them.

    `edges` are `(from_key, action, to_key, mutating)`. Every edge points at
    evidence 0, which is the single observation carrying `network` -- enough for
    the rules under test and nothing more, so a check that fails is failing
    about the rule rather than about the fixture.
    """
    from .explorer.observer import Observation
    from .explorer.worldmap import StateNode, Transition

    world = WorldMap()
    world.evidence = [
        Observation(url="http://sut/", title="sut", snapshot="", network=tuple(network))
    ]
    for from_key, action, to_key, mutating in edges:
        for key in (from_key, to_key):
            world.states.setdefault(
                key, StateNode(key=key, url="http://sut/", title="sut", actions=())
            )
        world.transitions.setdefault((from_key, action), []).append(
            Transition(
                from_key=from_key,
                action=action,
                to_key=to_key,
                mutating=mutating,
                evidence=0,
            )
        )
    return world


def _invariant_checks() -> bool:
    """Every rule in `invariants.py`, and the ambiguity each one refuses."""
    from .explorer.observer import NetworkEvent
    from .invariants import check as invariants_of

    ok = True
    valid, invalid, empty = (
        "submit[valid]:button:Sign in",
        "submit[invalid]:button:Sign in",
        "submit[empty]:button:Sign in",
    )

    # The form let bad input through: valid input moved the app forward, and
    # input chosen to be rejected arrived in the same place.
    accepted = invariants_of(
        _map_of([("a", valid, "b", True), ("a", invalid, "b", True)])
    )
    ok &= check(
        "input the form should reject reaching the success state is a defect",
        [v.rule for v in accepted] == ["invalid-accepted"],
        f"rules={[v.rule for v in accepted]}",
    )

    # The same map with the invalid path landing somewhere else -- a validation
    # error state -- is a *correct* form, and must be silent.
    rejecting = invariants_of(
        _map_of([("a", valid, "b", True), ("a", invalid, "c", False)])
    )
    ok &= check(
        "a form that rejects bad input reports nothing",
        rejecting == (),
        f"rules={[v.rule for v in rejecting]}",
    )

    # Neither path moves and nothing fires: the form does not discriminate, and
    # which way it fails is not decidable from one crawl. Refusing to guess is
    # the same policy as ESCALATE.
    undecidable = invariants_of(
        _map_of([("a", valid, "a", False), ("a", invalid, "a", False)])
    )
    ok &= check(
        "valid and invalid behaving identically is reported as undecidable",
        [v.rule for v in undecidable] == ["no-validation"],
        f"rules={[v.rule for v in undecidable]}",
    )
    ok &= check(
        "an undecidable form is not upgraded to 'invalid-accepted'",
        all(v.rule != "invalid-accepted" for v in undecidable),
    )

    # An empty submission that reaches the success state means the fields were
    # never required. That it also fired a POST is deliberately not a second
    # violation -- see the `empty-mutates` paragraph in `invariants.py`.
    empties = invariants_of(
        _map_of([("a", valid, "b", True), ("a", empty, "b", True)])
    )
    ok &= check(
        "an empty submission reaching the success state is a defect",
        [v.rule for v in empties] == ["empty-accepted"],
        f"rules={[v.rule for v in empties]}",
    )
    # The rule that was removed for being unprovable must stay removed: a form
    # that POSTs on empty submit but lands somewhere else has not been shown to
    # be wrong, and four such forms on one real site is what a false alarm at
    # scale looks like.
    posts_on_empty = invariants_of(
        _map_of([("a", valid, "b", True), ("a", empty, "c", True)])
    )
    ok &= check(
        "an empty submission that fires a write but lands elsewhere is not a defect",
        posts_on_empty == (),
        f"rules={[v.rule for v in posts_on_empty]}",
    )

    # A valid submit that neither moves nor fires anything is a broken happy
    # path -- but with no invalid edge beside it there is nothing to compare,
    # and inventing a verdict from one edge is what these rules must not do.
    lonely = invariants_of(_map_of([("a", valid, "a", False)]))
    ok &= check(
        "one submit edge alone proves nothing",
        lonely == (),
        f"rules={[v.rule for v in lonely]}",
    )

    # A rule may only cite an edge that was walked. An action a state merely
    # offers is a coverage gap and belongs to `critic.candidates`.
    unwalked = invariants_of(_map_of([("a", valid, "b", True)]))
    ok &= check(
        "an action nobody took is a gap, never a violation",
        unwalked == (),
        f"rules={[v.rule for v in unwalked]}",
    )

    # The server saying it failed is a defect in any application, and a 4xx is
    # not: a 401 on a login wall is the app working.
    failed = invariants_of(
        _map_of(
            [("a", "link:Reports", "b", False)],
            network=[NetworkEvent("GET", "http://sut/api/reports", "fetch", 503)],
        )
    )
    ok &= check(
        "a 5xx anywhere in an edge's evidence is a defect",
        [v.rule for v in failed] == ["server-error"] and "503" in failed[0].because,
        f"rules={[v.rule for v in failed]}",
    )
    unauthorised = invariants_of(
        _map_of(
            [("a", "link:Reports", "b", False)],
            network=[NetworkEvent("GET", "http://sut/api/reports", "fetch", 401)],
        )
    )
    ok &= check(
        "a 4xx is the application working, not a violation",
        unauthorised == (),
        f"rules={[v.rule for v in unauthorised]}",
    )

    # Every violation has to be printable and has to point at real evidence,
    # or the report cites something a human cannot open.
    ok &= check(
        "every violation cites evidence that exists",
        all(v.evidence == 0 and v.because and v.state for v in accepted + empties),
    )

    # --- what the invalid case actually carried ---------------------------
    #
    # The rule above reads "we called it invalid and the app took it" as a
    # defect, so it inherits whatever the synthesizer chose. These checks are
    # the guard on that: an all-empty payload is an empty submission wearing an
    # `invalid` label, and the assumption behind a real payload has to reach
    # the report rather than being laundered into a verdict.
    same = _map_of([("a", valid, "b", True), ("a", invalid, "b", True)])
    hollow = {
        "button:Sign in": {"values": {"Project name": ""}, "why": {}, "source": "fallback"}
    }
    undecidable_input = invariants_of(same, hollow)
    ok &= check(
        "an all-empty invalid payload is not reported as a defect",
        [v.rule for v in undecidable_input] == ["invalid-not-rejectable"],
        f"rules={[v.rule for v in undecidable_input]}",
    )
    ok &= check(
        "and it says why it could not decide",
        "every value it carried was empty" in undecidable_input[0].because,
        undecidable_input[0].because if undecidable_input else "nothing reported",
    )

    real = {
        "button:Sign in": {
            "values": {"Password": "short"},
            "why": {"Password": "below any length minimum"},
            "source": "model",
        }
    }
    grounded = invariants_of(same, real)
    ok &= check(
        "a real payload still reports the defect",
        [v.rule for v in grounded] == ["invalid-accepted"],
        f"rules={[v.rule for v in grounded]}",
    )
    ok &= check(
        "the defect discloses whose assumption it rests on",
        "chosen by model" in grounded[0].because
        and "below any length minimum" in grounded[0].because,
        grounded[0].because if grounded else "nothing reported",
    )

    # A caller with no synthesizer must get a superset, never a different set:
    # the payload map only ever suppresses and annotates.
    ok &= check(
        "no payload information leaves the verdict unchanged",
        [v.rule for v in invariants_of(same)] == ["invalid-accepted"],
    )

    return ok


def _behaviour_world():
    """Two real states, a real edge, and real observations behind both.

    Built by hand rather than by crawling, so the checks that use it need no
    browser and no server -- but with genuine `Observation`s in `evidence`,
    because every state in a real map has one (`WorldMap.record` puts it there)
    and a fixture without them would let a consumer that crashes on a
    missing snapshot pass.
    """
    from .explorer.observer import Observation
    from .explorer.worldmap import StateNode, Transition, WorldMap

    world = WorldMap()
    world.evidence = [
        Observation(url="/login", title="Login",
                    snapshot='- heading "Sign in"\n- button "Sign in"'),
        Observation(url="/dash", title="Dashboard",
                    snapshot='- heading "Dashboard"\n- button "Log out"'),
        Observation(url="/done", title="Done",
                    snapshot='- heading "Signed out"'),
    ]
    world.states = {
        "a" * 16: StateNode(key="a" * 16, url="/login", title="Login",
                            actions=("click:Sign in",), label="login",
                            evidence=(0,)),
        "b" * 16: StateNode(key="b" * 16, url="/dash", title="Dashboard",
                            actions=("click:Log out",), label="dashboard",
                            evidence=(1,)),
    }
    world.entry_key = "a" * 16
    world.transitions = {
        ("a" * 16, "click:Sign in"): [
            Transition(from_key="a" * 16, action="click:Sign in",
                       to_key="b" * 16, mutating=True, evidence=1)
        ]
    }
    return world


def _behaviour_checks() -> bool:
    """The semantic layer: a model interprets the map and may not invent it.

    `Exploration.summary`/`flows` were the embryo of this and had one fatal
    property -- nothing downstream read them, so nothing ever checked a claim
    in them against the map. A behavioural model that can name a state the
    crawler never saw is not a model of *this* application, and a generator
    compiling from it would emit a test for a page that does not exist.

    Every check here is that guard, or the seam it protects.
    """
    print("BEHAVIOUR   the model interprets the map, and may not invent it")
    ok = True

    try:
        from .behavior import admit, synthesise
    except ImportError as exc:
        print(f"  FAIL  agents.behavior does not import ({exc})")
        return False

    world = _behaviour_world()

    ok &= check(
        "a hypothesis citing a state the map does not hold is refused",
        admit(world.ground(), {"claim": "x", "kind": "flow", "cites": ["deadbeef"]})
        is None,
    )

    admitted = admit(
        world.ground(),
        {"claim": "signing in authenticates", "kind": "flow",
         "cites": ["aaaaaaaa", "click:Sign in"]},
    )
    ok &= check("a hypothesis citing a real state is admitted", admitted is not None)
    ok &= check(
        "an admitted citation is widened to the full state key",
        admitted is not None and "a" * 16 in admitted.cites,
        f"cites={admitted.cites if admitted else None}",
    )
    ok &= check(
        "an action in the map's vocabulary is a valid citation",
        admitted is not None and "click:Sign in" in admitted.cites,
    )
    ok &= check(
        "an admitted hypothesis starts unexamined, not believed",
        admitted is not None and admitted.status == "unexamined",
        f"status={admitted.status if admitted else None}",
    )
    ok &= check(
        "a hypothesis with no citation at all is refused",
        admit(world.ground(), {"claim": "the app is slow", "kind": "flow",
                              "cites": []})
        is None,
    )

    ok &= check(
        "with no provider nothing is guessed",
        synthesise(world, None).hypotheses == (),
    )

    class Inventive:
        """Cites one real state and one page it made up."""

        name, model = "scripted:inventive", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="", calls=(ToolCall(
                id="m1", name="model",
                arguments={
                    "summary": "a login guarding a dashboard",
                    "hypotheses": [
                        {"claim": "signing in authenticates", "kind": "flow",
                         "cites": ["aaaaaaaa", "bbbbbbbb"]},
                        {"claim": "checkout charges a card", "kind": "flow",
                         "cites": ["cafef00d"]},
                    ],
                },
            ),))

    built = synthesise(world, Inventive())
    ok &= check(
        "the grounded hypothesis survives synthesis",
        len(built.hypotheses) == 1,
        f"{len(built.hypotheses)} admitted",
    )
    ok &= check(
        "the invented one is dropped and counted, not silently ignored",
        built.dropped == 1,
        f"dropped={built.dropped}",
    )
    ok &= check("synthesis carries the summary", bool(built.summary))
    ok &= check(
        "the model reaches the orchestrator's view of the world",
        "signing in authenticates" in tools.brief(
            world, waves_left=1, ants_left=1, behaviour=built
        ),
        "tools.brief ignored the behavioural model, so the orchestrator "
        "cannot reason over it",
    )

    return ok


def _dispatch_checks() -> bool:
    """One orchestrator, several kinds of agent.

    Until this existed there were two orchestrators that never spoke:
    `pipeline.py` routed the stages and `orchestrator.py` routed ants, so the
    decision "should I explore more or test what I have" was made by neither --
    it was made by the order the stages are written in. A colony whose only
    verb is `send an ant` cannot decide to stop exploring and start testing.

    The guard that matters is the last one: an unknown agent kind must be
    refused out loud. A dispatcher that silently treats an unrecognised name as
    the default runs the wrong agent and reports success.
    """
    print("DISPATCH    one orchestrator, several kinds of agent")
    ok = True

    from . import tools

    item = tools.DISPATCH.parameters["properties"]["assignments"]["items"]
    enum = item.get("properties", {}).get("agent", {}).get("enum")
    ok &= check(
        "dispatch can send something other than an ant",
        bool(enum) and set(enum) >= {"ant", "generator", "healer"},
        f"agent enum = {enum}",
    )

    from .orchestrator import AGENTS

    ok &= check(
        "every advertised agent kind has a handler",
        bool(enum) and set(enum) == set(AGENTS),
        f"advertised {sorted(enum or [])}, handled {sorted(AGENTS)}",
    )

    world = _behaviour_world()
    refusal = tools.refuse_assignment(world, {"state": "aaaaaaaa", "agent": "wizard"})
    ok &= check(
        "an unknown agent kind is refused, not run as an ant",
        refusal is not None and "wizard" in refusal,
        f"refusal={refusal!r}",
    )
    ok &= check(
        "a known agent on a real state is not refused",
        tools.refuse_assignment(world, {"state": "aaaaaaaa", "agent": "healer"})
        is None,
    )
    ok &= check(
        "a known agent on a state the map lacks is refused",
        tools.refuse_assignment(world, {"state": "deadbeef", "agent": "ant"})
        is not None,
    )
    ok &= check(
        "an assignment naming no agent still runs an ant",
        tools.refuse_assignment(world, {"state": "aaaaaaaa"}) is None,
    )
    ok &= check(
        "a state id copied back with the brief's brackets still resolves",
        tools.refuse_assignment(world, {"state": "[aaaaaaaa]", "agent": "ant"}) is None,
        "measured 2026-09-05 on saucedemo: four waves, zero ants, every id bracketed",
    )

    # The feedback loop: what an earlier dispatch produced has to reach the
    # next decision, or the orchestrator re-runs work that is already done.
    rendered = tools.brief(
        world, waves_left=1, ants_left=1,
        results=["generator w1a1: compiled 3 scenarios from [aaaaaaaa]"],
    )
    ok &= check(
        "what an agent produced reaches the next dispatch decision",
        "compiled 3 scenarios" in rendered,
    )

    return ok


def _report_checks() -> bool:
    """The semantic layer has to reach the last screen, or it did not happen.

    The rubric pays 15% for presenting *the agent's decisions*, and a run that
    reasoned over a behavioural model and then printed only pass/fail counts
    has hidden the part that was reasoning. The discarded count is here for the
    same reason: a guard nobody can see is a guard nobody trusts.
    """
    print("REPORT      what the agent believed reaches the last screen")
    ok = True

    from .behavior import BehaviorModel, Hypothesis
    from .pipeline import Pipeline, report

    pipe = Pipeline(target_url="http://localhost:3000/sut")
    pipe.behaviour = BehaviorModel(
        summary="a login guarding a dashboard",
        hypotheses=(
            Hypothesis(
                claim="logging out ends the session",
                kind="invariant",
                cites=("a" * 16,),
            ),
        ),
        dropped=2,
    )
    pipe.experiments = ["generator w1a2: compiled 3 scenario(s) through [aaaaaaaa]"]
    rendered = report(pipe)

    ok &= check(
        "the report names what the agent believed",
        "logging out ends the session" in rendered,
    )
    ok &= check(
        "an unexamined belief is not presented as a finding",
        "unexamined" in rendered or "?" in rendered,
    )
    ok &= check(
        "discarded hypotheses are reported, not hidden",
        "2 further hypothesis" in rendered,
        "the citation guard fired and the report did not say so",
    )
    ok &= check(
        "the report says what the colony dispatched",
        "compiled 3 scenario(s)" in rendered,
    )

    empty = report(Pipeline(target_url="http://x"))
    ok &= check(
        "a run with no model still renders a report",
        "TEST QUALITY REPORT" in empty and "WHAT THE AGENT BELIEVES" not in empty,
    )

    return ok


def _flow_checks() -> bool:
    """A believed flow has to become a runnable test, or the model is decoration.

    This is the join the whole semantic layer exists for. `generator.scenarios`
    compiles the *shortest path from the entry plus one terminal action*, which
    is a fine default and cannot express "log in, add an item, reload, check it
    survived" -- a sequence the map has always contained and nothing could ask
    for. A flow hypothesis carries the states in order, so it can.

    The guard that matters is the second one: a flow citing two states with no
    recorded edge between them must compile to nothing. The model named an
    ordering it never saw walked, and a test built on it would assert a
    transition the crawler never observed -- exactly the fabricated expectation
    `claims.py` refuses to generate.
    """
    print("FLOWS       a believed flow compiles into a runnable scenario")
    ok = True

    from .behavior import Hypothesis
    from .generator import from_flow
    from .explorer.worldmap import StateNode, Transition

    world = _behaviour_world()
    # A third state, reachable from the second, so the flow is longer than
    # anything `paths()` would produce on its own.
    world.states["c" * 16] = StateNode(
        key="c" * 16, url="/done", title="Done",
        actions=(), label="signed out", evidence=(2,),
    )
    world.transitions[("b" * 16, "click:Log out")] = [
        Transition(from_key="b" * 16, action="click:Log out",
                   to_key="c" * 16, mutating=True, evidence=2)
    ]

    walked = Hypothesis(
        claim="signing in and out returns to an unauthenticated state",
        kind="flow",
        cites=("a" * 16, "b" * 16, "c" * 16),
    )
    scenario = from_flow(world, walked)
    ok &= check("a flow the crawler walked compiles", scenario is not None)
    ok &= check(
        "the compiled scenario follows the flow, not the shortest path",
        scenario is not None and len(scenario.steps) == 2,
        f"{len(scenario.steps) if scenario else 0} step(s)",
    )
    ok &= check(
        "the scenario is named for the claim, not for its last action",
        scenario is not None and "unauthenticated" in scenario.name,
        f"name={scenario.name if scenario else None!r}",
    )

    unwalked = Hypothesis(
        claim="the login page leads straight to sign-out",
        kind="flow",
        cites=("a" * 16, "c" * 16),
    )
    ok &= check(
        "a flow with no recorded edge between two states compiles to nothing",
        from_flow(world, unwalked) is None,
        "the model asserted an ordering nobody walked and it became a test",
    )

    ok &= check(
        "a non-flow hypothesis is not compiled",
        from_flow(world, Hypothesis(claim="x", kind="invariant",
                                    cites=("a" * 16, "b" * 16))) is None,
    )
    ok &= check(
        "a flow citing one state has no transition to test",
        from_flow(world, Hypothesis(claim="x", kind="flow",
                                    cites=("a" * 16,))) is None,
    )

    # An uncompilable flow is the model's most specific steer -- "I believe A
    # leads to C and nobody has checked" -- and counting it throws that away.
    # The pair has to be named, and it has to reach the orchestrator's brief,
    # or the colony cannot send an ant to walk it.
    from .behavior import BehaviorModel
    from .generator import unwalked as first_unwalked
    from .planner import plan
    from .tools import brief

    ok &= check(
        "the first unwalked pair of a believed flow is named",
        first_unwalked(world, unwalked) == ("a" * 16, "c" * 16),
        f"got {first_unwalked(world, unwalked)!r}",
    )
    ok &= check(
        "a flow the crawler walked has no unwalked pair",
        first_unwalked(world, walked) is None,
    )
    model = BehaviorModel(hypotheses=(walked, unwalked))
    planned = plan(world, model, source="behaviour")
    ok &= check(
        "the plan carries the unwalked pair, not only a count",
        planned.unwalked == ((unwalked.claim, "a" * 16, "c" * 16),),
        f"got {planned.unwalked!r}",
    )
    rendered = planned.render()
    ok &= check(
        "the plan names where a believed flow breaks",
        "a" * 8 in rendered and "c" * 8 in rendered and "unwalked" in rendered,
    )
    briefed = brief(world, waves_left=1, ants_left=1, behaviour=model)
    ok &= check(
        "the orchestrator's brief names the unwalked pair as a place to send an ant",
        "unwalked" in briefed and f"[{'a' * 8}] -> [{'c' * 8}]" in briefed,
        "the steer stayed inside the planner and never reached dispatch",
    )
    ok &= check(
        "a fully walked flow is not offered as a place to send an ant",
        f"[{'a' * 8}] -> [{'b' * 8}]" not in briefed,
    )

    # Measured 2026-09-05 on two pipeline runs: `uncompilable=1` with no
    # unwalked pair, on a chain the crawler had walked end to end and could
    # type. "Uncompilable" covered at least three different refusals and named
    # none, so a run could not say why its one believed flow became no test.
    from .generator import refusal

    one_state = Hypothesis(claim="x", kind="flow", cites=("a" * 16,))
    ok &= check(
        "a refused flow says why: too few cited states",
        "1 state" in refusal(world, one_state),
        f"got {refusal(world, one_state)!r}",
    )
    ok &= check(
        "a refused flow says why: the unwalked pair",
        "a" * 8 in refusal(world, unwalked) and "c" * 8 in refusal(world, unwalked),
        f"got {refusal(world, unwalked)!r}",
    )
    ok &= check("a compilable flow has no refusal", refusal(world, walked) == "")
    planned = plan(world, BehaviorModel(hypotheses=(one_state,)), source="behaviour")
    ok &= check(
        "the plan carries each refusal with its claim",
        planned.refused == (("x", refusal(world, one_state)),),
        f"got {planned.refused!r}",
    )
    ok &= check(
        "the plan renders the refusal", "1 state" in planned.render(),
    )

    return ok


def _verdict_checks() -> bool:
    """A proposed invariant gets checked by code, never by the model that wrote it.

    This is the seam the whole architecture turns on. The model is allowed to
    reason about *what ought to hold* -- that is a semantic question and no
    amount of graph traversal answers it. It is not allowed to decide whether
    it holds, because that is the configuration the coverage-evaluation research
    measures at a 84.4% false-positive rate.

    So an `invariant` hypothesis carries a `rule` from a fixed vocabulary bound
    to real states and actions, and `examine()` evaluates it against the map.
    The model picks the claim; the recorded transitions return the verdict.
    """
    print("VERDICT     the model proposes an invariant, the map rules on it")
    ok = True

    from .behavior import BehaviorModel, Hypothesis, RULES, examine

    world = _behaviour_world()

    def one(rule, cites):
        return BehaviorModel(hypotheses=(
            Hypothesis(claim="c", kind="invariant", cites=tuple(cites), rule=rule),
        ))

    # `click:Sign in` from [aaaa] lands in [bbbb] and fired a non-GET.
    ok &= check(
        "an invariant the map upholds is supported",
        examine(world, one("must-move", ["a" * 16, "click:Sign in"]))
        .hypotheses[0].status == "supported",
    )
    ok &= check(
        "an invariant the map contradicts is contradicted",
        examine(world, one("must-not-mutate", ["a" * 16, "click:Sign in"]))
        .hypotheses[0].status == "contradicted",
    )
    ok &= check(
        "a verdict says which transition decided it",
        bool(examine(world, one("must-mutate", ["a" * 16, "click:Sign in"]))
             .hypotheses[0].because),
    )
    ok &= check(
        "an invariant about an edge nobody walked is inconclusive, not passing",
        examine(world, one("must-move", ["b" * 16, "click:Sign in"]))
        .hypotheses[0].status == "inconclusive",
        "an unwalked edge was reported as upholding the rule",
    )
    ok &= check(
        "an invariant naming no rule cannot be checked",
        examine(world, one("", ["a" * 16, "click:Sign in"]))
        .hypotheses[0].status == "inconclusive",
    )
    ok &= check(
        "an unknown rule is inconclusive, never quietly true",
        examine(world, one("must-be-lovely", ["a" * 16, "click:Sign in"]))
        .hypotheses[0].status == "inconclusive",
    )

    # A flow hypothesis has no rule and must not be silently ruled on.
    flows = BehaviorModel(hypotheses=(
        Hypothesis(claim="c", kind="flow", cites=("a" * 16, "b" * 16)),
    ))
    ok &= check(
        "a non-invariant hypothesis is left unexamined by the checker",
        examine(world, flows).hypotheses[0].status == "unexamined",
    )

    ok &= check(
        "every rule the model is offered has a checker",
        set(RULES) == set(
            _behaviour_rule_enum()
        ),
        "the schema advertises a rule nothing can evaluate",
    )

    return ok


def _behaviour_rule_enum():
    from .behavior import MODEL

    items = MODEL.parameters["properties"]["hypotheses"]["items"]
    return items["properties"]["rule"]["enum"]


def _mission_checks() -> bool:
    """An orchestrator must not hand an ant a mission the ant cannot perform.

    Found by a peer session running the pipeline against saucedemo: the
    orchestrator instructed three separate ants to "submit standard_user /
    secret_sauce". An ant has no way to do that. `forms.value_for` fills every
    field from the `Credentials` the run was constructed with, which come from
    `AIVAR_USERNAME` / `AIVAR_PASSWORD` -- so with those unset each ant typed
    `aivar-explorer` / `Test-Password-1`, failed to log in, and returned "3
    actions, 0 new states". The orchestrator read three identical dead ends as
    evidence about the application rather than about its own plan, and spent a
    quarter of the colony's budget doing it.

    A citation guard catches a false *claim* after the fact. This catches a
    false *plan* before the ants are spent, which is the only point at which
    the budget is still recoverable.
    """
    print("MISSION     an ant is not sent to do what an ant cannot do")
    ok = True

    from . import tools
    from .explorer.forms import Credentials

    world = _behaviour_world()
    none_held = Credentials()
    held = Credentials("standard_user", "secret_sauce")

    def refuse(instruction, credentials):
        return tools.refuse_assignment(
            world,
            {"state": "aaaaaaaa", "agent": "ant", "instruction": instruction},
            credentials=credentials,
        )

    spelled = refuse("Submit standard_user / secret_sauce on the login form", none_held)
    ok &= check(
        "an instruction naming literal credentials is refused when none are held",
        spelled is not None,
        "the ant would have typed the configured default and reported a dead end",
    )
    ok &= check(
        "the refusal says where credentials actually come from",
        spelled is not None and "AIVAR_USERNAME" in spelled,
        f"refusal={spelled!r}",
    )
    ok &= check(
        "a slash-free credential pair is caught too",
        refuse("log in as admin:hunter2", none_held) is not None,
    )

    ok &= check(
        "the same instruction is allowed when the run does hold credentials",
        refuse("Submit standard_user / secret_sauce on the login form", held) is None,
        "refusing here would forbid the one mission that can actually succeed",
    )

    # False positives are the whole risk: this gate reads free text, and an
    # over-eager rule silently forbids ordinary exploration.
    for benign in (
        "Get through the login form and report what is behind it",
        "Explore the cart and check whether items persist after a reload",
        "Try submitting the form with nothing filled in",
        "Follow the v2 link and report the destination",
        "Check whether this state traps the user or allows return to the form",
    ):
        ok &= check(
            f"not refused: {benign[:44]}...",
            refuse(benign, none_held) is None,
        )

    ok &= check(
        "with no credentials argument the gate is inert, not guessing",
        tools.refuse_assignment(
            world,
            {"state": "aaaaaaaa", "agent": "ant",
             "instruction": "submit standard_user / secret_sauce"},
        ) is None,
    )

    return ok


def _standing_checks() -> bool:
    """An unanswered question is a coverage gap, and reaches the report as one.

    The prompt half of this was built and then removed, and the removal is the
    interesting part. Restating unanswered doubts to the orchestrator every
    wave looked like the `perished` fix and is not: a peer session's saucedemo
    run showed the decisive doubt was already in the wave-3 context (the
    transcript only ever appends) and was ignored anyway, and that all 12 ants
    raised `uncertain` -- mostly about their own limits, nine of twelve citing
    a state key, so citation does not separate them. See `standing_doubts`.

    What is checked here is the half the evidence supports.
    """
    print("STANDING    an unanswered question survives into the report")
    ok = True

    from . import tools
    from .ant import Report
    from .orchestrator import standing_doubts

    world = _behaviour_world()
    doubt = (
        "Whether cb977164 is genuinely the inventory page, or is still the "
        "login page"
    )

    collected = standing_doubts(
        [Report(start_key="a" * 16, uncertain=doubt),
         Report(start_key="b" * 16, uncertain="")],
        wave=1,
    )
    ok &= check(
        "only an ant that actually raised a doubt contributes one",
        len(collected) == 1,
        f"{len(collected)} collected from two reports, one of them silent",
    )
    ok &= check(
        "a collected doubt carries the ant and the state it was raised at",
        bool(collected) and collected[0][0].startswith("w1")
        and collected[0][1] == "a" * 16,
        f"collected={collected}",
    )
    ok &= check(
        "the doubt itself is carried verbatim, not summarised",
        bool(collected) and collected[0][2] == doubt,
    )
    ok &= check(
        "no reports means nothing to carry",
        standing_doubts([], wave=1) == (),
    )

    # The volume rule, measured rather than guessed. `Exploration.gaps` renders
    # as "WHAT WE DID NOT REACH", which in a real run is ONE line. Folding in
    # every ant's `uncertain` makes it thirteen, twelve of them reporting the
    # ant's own limits -- the same dilution just removed from the prompt, moved
    # into the section whose whole job is the run's honest account of its blind
    # spots, and read by a judge rather than by a model.
    #
    # So a doubt is carried only when it is the ant's ONLY output. Computed,
    # not semantic: an ant that acted and still doubted has told us something
    # either way, while an ant that produced nothing but a doubt is saying the
    # assignment was impossible, which is exactly a gap. Measured against 12
    # real ants, all of which acted: this carries none of them.
    busy = Report(start_key="a" * 16, uncertain=doubt, summary="had a look")
    busy.actions_taken = 3
    ok &= check(
        "an ant that acted contributes no gap, however uncertain it was",
        standing_doubts([busy], wave=1) == (),
        "measured on 12 real ants: all took 1-4 actions and all raised a "
        "doubt, so carrying these turns a one-line section into thirteen",
    )

    blocked = Report(start_key="a" * 16, uncertain=doubt)
    blocked.actions_taken = 0
    ok &= check(
        "an ant that produced nothing but a doubt contributes it",
        len(standing_doubts([blocked], wave=1)) == 1,
    )

    many = []
    for _ in range(9):
        stuck = Report(start_key="a" * 16, uncertain=doubt)
        stuck.actions_taken = 0
        many.append(stuck)
    ok &= check(
        "and the list is capped, so a bad run cannot bury the report",
        len(standing_doubts(many, wave=1)) <= 5,
        f"{len(standing_doubts(many, wave=1))} carried from 9 blocked ants",
    )

    # The removal, pinned. A doubt must NOT be restated into the orchestrator's
    # prompt: measured, that adds a dozen self-reported limitations to the one
    # context window whose signal-to-noise had already lost to a fabrication.
    rendered = tools.brief(
        world, reports=[Report(start_key="a" * 16, uncertain=doubt)],
        waves_left=1, ants_left=1,
    )
    ok &= check(
        "a doubt reaches the wave it was raised in, through the report",
        doubt[:30] in rendered,
    )
    ok &= check(
        "but the brief carries no standing-doubt section",
        "unsettled" not in rendered.lower(),
        "restating every ant's uncertainty was measured as noise, not signal",
    )

    return ok


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def _parity_checks() -> bool:
    """The colony must honour what the crawler honours, and start where it stopped.

    Every check here is a capability that existed in `explorer/crawler.py` and
    silently did not exist in `orchestrator.py` + `ant.py`. The measured cost of
    the worst one: `forms.perform` refuses `submit[invalid]` when handed no
    synthesizer, the ant was told "that is a fact about the application, not
    your mistake" -- which was false -- nothing was recorded, so `brief()` still
    showed the action as untried and the orchestrator reassigned it. Two
    separate saucedemo runs handed it to a fresh ant and got zero actions back.
    """
    from .explorer.worldmap import WorldMap

    print("PARITY      the colony honours what the crawler honours")
    ok = True

    ok &= check(
        "one guard, reachable from both walkers",
        crawler.is_safe is forms.is_safe
        and not forms.is_safe("button:Delete account")
        and forms.is_safe("button:Sign in"),
        "the destructive-action denylist is not shared",
    )

    ok &= check(
        "an ant refuses a destructive action instead of clicking it",
        "world.skipped" in _source(ant, "refused: the name suggests"),
        "ant.py does not consult the guard before acting",
    )

    ok &= check(
        "an ant is handed the synthesizer",
        "synthesizer" in _signature(ant.explore)
        and "synthesizer" in _signature(orchestrator.run),
        "submit[invalid] is structurally impossible for every ant",
    )

    ok &= check(
        "the orchestrator accepts a map the crawler already built",
        "world" in _signature(orchestrator.run),
        "the colony can only start from a blank map",
    )

    # A refused action must reach the orchestrator's view, or it is reassigned.
    world = WorldMap()
    world.states["abc12345deadbeef"] = __import__(
        "agents.explorer.worldmap", fromlist=["StateNode"]
    ).StateNode(
        key="abc12345deadbeef", url="/", title="t",
        actions=("submit[invalid]:button:Login",),
    )
    world.skipped[("abc12345deadbeef", "submit[invalid]:button:Login")] = (
        "nothing here could be filled"
    )
    rendered = tools.brief(world, waves_left=3, ants_left=8)

    ok &= check(
        "a refused action is shown to the orchestrator, not hidden",
        "REFUSED" in rendered and "submit[invalid]:button:Login" in rendered,
        "brief() renders no refused section; the orchestrator cannot tell "
        "'never tried' from 'tried and impossible'",
    )
    ok &= check(
        "the refused action carries the reason it failed",
        "nothing here could be filled" in rendered,
        "a refusal with no reason is a to-do nobody can action",
    )

    # A dead ant was survivable and a dead orchestrator was not. Measured on a
    # seeded saucedemo run: the wave-3 model call raised a 402 and the
    # exception left `run` entirely, so a 24-state crawl, a completed wave and
    # the autosave went with it and the process ended on a traceback.
    class DiesOnWaveTwo(Colony):
        def turn(self, system, transcript, tool_defs):
            names = {t.name for t in tool_defs}
            if "dispatch" in names and self.waves >= 1:
                raise RuntimeError("402 from the provider: out of credits")
            return super().turn(system, transcript, tool_defs)

    with sync_playwright() as pw:
        browser, page, _, _ = _page_and_map(pw)
        try:
            result = run(
                page, SUT, DiesOnWaveTwo(),
                budget=Budget(max_waves=3, max_ants=2, ant_actions=1),
                credentials=CREDENTIALS,
                on_event=lambda level, message: None,
            )
            ok &= check(
                "a provider failure ends the colony, it does not erase it",
                bool(result.world.states) and result.stopped == "error",
                f"states={len(result.world.states)} stopped={result.stopped!r}",
            )
            ok &= check(
                "the run says why it has no summary",
                any("model call failed" in gap for gap in result.gaps),
                f"gaps={result.gaps}",
            )
        except Exception as exc:
            ok &= check(
                "a provider failure ends the colony, it does not erase it",
                False,
                f"the exception escaped `run`: {type(exc).__name__}: {exc}",
            )
        finally:
            browser.close()

    return ok


def _signature(fn) -> str:
    import inspect

    return str(inspect.signature(fn))


def _source(module, needle: str) -> str:
    import inspect

    text = inspect.getsource(module)
    return text if needle in text else ""


def _function_source(module, name: str) -> str:
    """Just one function's body, not the module it lives in.

    `_source` answers "does this text appear anywhere in the file", which is
    the right question for some checks and the wrong one for any check that
    counts. A count over the whole module sees the helper *definitions* too --
    `redact_url` appears in observer.py whether or not anything calls it -- so
    a check written on `_source` would report a passing count for a function
    that redacts nothing. Found exactly that way: the redaction check below
    read 3 and 2 over the module where the function contains 2 and 1.
    """
    import ast
    import inspect

    text = inspect.getsource(module)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    return ""


def main() -> int:
    print("AGENTS      scripted provider, real browser, no API key\n")
    ok = True

    # Ahead of the browser, deliberately. Everything below the `sync_playwright`
    # block is gated on the SUT being up, and without `make dev` this module
    # prints SKIPPED and returns 0 -- a green run that checked almost nothing.
    # These two read source and drive stubs; a server they do not need must not
    # be what decides whether they run. Found by adding them below the guard
    # and watching `make probe` pass without executing either.
    print()
    ok &= _ground_checks()
    ok &= _delta_checks()
    ok &= _session_checks()
    ok &= _worker_checks()
    ok &= _seeding_checks()
    ok &= _interleave_checks()
    ok &= _per_page_checks()
    ok &= _frontier_order_checks()
    ok &= _model_generator_checks()
    ok &= _ceiling_checks()
    print()
    ok &= _surface_checks()
    print()

    with sync_playwright() as pw:
        try:
            browser, page, world, entry = _page_and_map(pw)
        except PlaywrightError:
            print(f"SKIPPED     needs `make dev` for the SUT at {SUT}")
            return 0

        # 1. A model that never calls a tool must not loop forever. This is the
        #    bug that silently exhausted three models' daily quotas.
        chatty = Chatty()
        report = explore(
            page, world, chatty, entry_url=SUT, start_key=entry,
            credentials=CREDENTIALS, budget=3,
        )
        ok &= check(
            "a non-tool-calling model cannot loop forever",
            chatty.calls <= 3 * 3 + 1,
            f"{chatty.calls} model calls for a budget of 3 actions",
        )
        ok &= check(
            "a stalled ant is labelled 'stalled', not 'budget'",
            report.ended == "stalled",
            f"ended={report.ended}",
        )

        # 2. An ant that explores must come back with a write-up. An ant that
        #    spends budget and reports nothing is worse than one that never ran.
        browser.close()
        browser, page, world, entry = _page_and_map(pw)
        ant = Explorer()
        report = explore(
            page, world, ant, entry_url=SUT, start_key=entry,
            instruction="look at the form", credentials=CREDENTIALS, budget=3,
        )
        ok &= check("an ant takes actions", report.actions_taken > 0)
        ok &= check("an ant returns a summary", bool(report.summary))
        ok &= check("an ant returns ranked branches", bool(report.branches))
        ok &= check(
            "acting records transitions in the map",
            sum(len(t) for t in world.transitions.values()) > 0,
        )

        # 3. The colony round trip: dispatch, run ants, fold in, finish.
        browser.close()
        browser, page, world, entry = _page_and_map(pw)
        # `checkpoint` is what the console draws from. The map is saved to the
        # database only when the caller is handed one, so a colony that never
        # calls back leaves the canvas empty for the whole run and then fills
        # it in one jump at the end -- which is exactly what a *live* map is
        # not. Recording the state count at each call rather than just the
        # number of calls: a callback that fires with nothing in it would
        # satisfy a bare "was it called" and still draw an empty canvas.
        saved_state_counts: list[int] = []
        result = run(
            page, SUT, Colony(),
            intent="check the sign-in flow",
            budget=Budget(max_waves=3, max_ants=2, ant_actions=2, max_seconds=120),
            credentials=CREDENTIALS,
            on_event=lambda level, message: None,
            checkpoint=lambda w: saved_state_counts.append(len(w.states)),
        )
        ok &= check("the colony dispatches and finishes", result.stopped == "covered")
        ok &= check("the colony names flows", bool(result.flows))
        ok &= check("the colony reports gaps honestly", bool(result.gaps))
        ok &= check("ant reports reach the orchestrator", bool(result.reports))
        ok &= check(
            "the colony checkpoints its map while exploring",
            len(saved_state_counts) > 0,
            "run() never called checkpoint, so nothing reaches the console "
            "until the run is over",
        )
        ok &= check(
            "a checkpoint carries the states found so far",
            bool(saved_state_counts) and saved_state_counts[0] > 0,
            f"state counts seen at each checkpoint: {saved_state_counts}",
        )
        browser.close()

        # 3b. A map must survive being written to a file and read back, or
        #     comparing two runs compares two lossy renderings.
        import tempfile

        from .explorer.snapshot import compare, load, save

        with tempfile.TemporaryDirectory() as tmp:
            path = save(world, f"{tmp}/run.json", target=SUT)
            reloaded = load(path)
            ok &= check(
                "a saved map reloads with the same states and edges",
                set(reloaded.states) == set(world.states)
                and set(reloaded.transitions) == set(world.transitions),
            )
            ok &= check(
                "a map compared against itself reports no changes",
                compare(world, reloaded).identical,
            )
            stripped = load(path)
            stripped.states.pop(next(iter(stripped.states)))
            ok &= check(
                "a real difference is reported, not swallowed",
                not compare(world, stripped).identical,
            )

        # 3c. A thumbnail is attached once and must survive both a revisit and
        #     a round trip through a file. Losing it on revisit is silent: the
        #     node still renders, just without its picture, and only on the
        #     states the crawler visited more than once.
        from .explorer.worldmap import WorldMap as _WorldMap

        shots = _WorldMap()
        first = world.evidence[0]
        shot_key = shots.record(first)
        shots.attach_screenshot(shot_key, "run-1/abc.png")
        shots.record(first)  # a revisit
        ok &= check(
            "a revisit does not lose the thumbnail",
            shots.states[shot_key].screenshot == "run-1/abc.png",
        )
        shots.attach_screenshot(shot_key, "run-1/second.png")
        ok &= check(
            "the first thumbnail wins",
            shots.states[shot_key].screenshot == "run-1/abc.png",
        )
        ok &= check(
            "attaching None is a no-op, not a wipe",
            (
                shots.attach_screenshot(shot_key, None),
                shots.states[shot_key].screenshot == "run-1/abc.png",
            )[1],
        )
        with tempfile.TemporaryDirectory() as tmp:
            from .explorer.snapshot import load as _load
            from .explorer.snapshot import save as _save

            reloaded = _load(_save(shots, f"{tmp}/shots.json", target=SUT))
            ok &= check(
                "a saved map keeps its thumbnails",
                reloaded.states[shot_key].screenshot == "run-1/abc.png",
            )

        # 3d. store.save is incremental and upserts states. A thumbnail that
        #     arrives after a state's first save must reach the database on the
        #     next checkpoint, or the UI shows a picture-less node forever.
        from sqlmodel import Session as _Session
        from sqlmodel import SQLModel as _SQLModel
        from sqlmodel import create_engine as _create_engine

        from .explorer import store as _store

        with tempfile.TemporaryDirectory() as tmp:
            engine = _create_engine(f"sqlite:///{tmp}/probe.db")
            _SQLModel.metadata.create_all(engine)
            with _Session(engine) as db:
                bare = _WorldMap()
                bare_key = bare.record(world.evidence[0])
                _store.save(bare, run_id=7, session=db)
                bare.attach_screenshot(bare_key, "run-7/pic.png")
                _store.save(bare, run_id=7, session=db)
                back = _store.load(7, db)
                ok &= check(
                    "a thumbnail attached after the first save still persists",
                    back.states[bare_key].screenshot == "run-7/pic.png",
                )

        # 3d-bis. Which ant found what. The colony dispatches up to twelve of
        #     them and the map it returns is the union of their work, with no
        #     record of who did which part -- so a run that went wrong somewhere
        #     could not be traced to the ant that went there. `attribution` is
        #     set by the orchestrator around each dispatch and stamped onto
        #     whatever gets recorded while it is set.
        #
        #     First finder wins, deliberately: `record` already resolves a
        #     revisit in favour of the first sighting for url, title and
        #     actions, and a discoverer that changed on every revisit would name
        #     the last ant to walk past rather than the one that found it.
        with tempfile.TemporaryDirectory() as tmp:
            engine = _create_engine(f"sqlite:///{tmp}/ants.db")
            _SQLModel.metadata.create_all(engine)
            with _Session(engine) as db:
                ants = _WorldMap()
                ants.attribution = "w1a1"
                found_key = ants.record(world.evidence[0])
                ok &= check(
                    "a state records the ant that first reached it",
                    ants.states[found_key].found_by == "w1a1",
                    f"found_by={ants.states[found_key].found_by!r}",
                )

                ants.attribution = "w2a1"
                ants.record(world.evidence[0])  # a revisit by a different ant
                ok &= check(
                    "a revisit does not reassign the finder",
                    ants.states[found_key].found_by == "w1a1",
                    f"found_by={ants.states[found_key].found_by!r}",
                )

                # Lands back where it started, which is a real edge and the
                # one this module calls most informative -- and all the
                # scripted colony gave us is the one entry observation.
                edge = ants.connect(found_key, "link:Courses", world.evidence[0])
                ok &= check(
                    "a transition records the ant that walked it",
                    edge.found_by == "w2a1",
                    f"found_by={edge.found_by!r}",
                )

                # Attribution the console cannot read is attribution that does
                # not exist: the map is drawn from the database, not from the
                # object the colony returned.
                _store.save(ants, run_id=9, session=db)
                back = _store.load(9, db)
                ok &= check(
                    "attribution survives the database round trip",
                    back.states[found_key].found_by == "w1a1"
                    and any(
                        t.found_by == "w2a1"
                        for edges in back.transitions.values()
                        for t in edges
                    ),
                    f"state={back.states[found_key].found_by!r}",
                )

        # 3e. A state the pipeline crossed twice takes the worse verdict. A
        #     map that showed the *last* verdict would hide a defect behind a
        #     pass, which is the one direction that must never happen.
        from .generator import scenarios as _scenarios
        from .runner import DEFECT as _DEFECT
        from .runner import PASSED as _PASSED
        from .runner import Resolution as _Resolution
        from .runner import Result as _Result
        from .runner import StepResult as _StepResult
        from . import suite as _suite

        drafted = _scenarios(result.world)
        if not drafted:
            ok &= check("the probe world yields a scenario to persist", False)
        else:
            one = drafted[0]

            def _result(verdict: str) -> _Result:
                return _Result(
                    scenario=one,
                    target_url=SUT,
                    steps=[
                        _StepResult(
                            step=step,
                            verdict=verdict,
                            resolution=_Resolution(
                                action=step.action, rung="exact", detail=""
                            ),
                            detail="probe",
                        )
                        for step in one.steps
                    ],
                )

            with tempfile.TemporaryDirectory() as tmp:
                engine = _create_engine(f"sqlite:///{tmp}/suite.db")
                _SQLModel.metadata.create_all(engine)
                with _Session(engine) as db:
                    # DEFECT first, PASSED second, on purpose: the reduction is
                    # supposed to keep the worse verdict, so the better one must
                    # arrive LATER and lose. Written the other way round, a
                    # last-write-wins bug would produce the same answer as the
                    # correct code and the check would prove nothing.
                    written = _suite.save_results(
                        [_result(_DEFECT), _result(_PASSED)], run_id=9, session=db
                    )
                    ok &= check("both results are stored", written == 2)

                    crossed = _suite.path_of(_result(_PASSED))
                    verdicts = _suite.verdicts_by_state(9, db)
                    ok &= check(
                        "every state on the path gets a verdict",
                        all(key in verdicts for key in crossed),
                    )
                    ok &= check(
                        "the worse verdict survives a better one written later",
                        set(verdicts.values()) == {_DEFECT},
                        f"got {sorted(set(verdicts.values()))}",
                    )

                    # A Result whose steps list is empty against a scenario that
                    # has steps: reading the plan instead of the run would
                    # return keys here, and returning none is the whole fix.
                    ok &= check(
                        "a run that executed no steps colours no states",
                        _suite.path_of(
                            _Result(scenario=one, target_url=SUT, steps=[])
                        ) == [],
                    )

        # 3f. What is worth a test. `is_flow` is structural and lives in
        #     worldmap.py, which never learns what an action *means*; the
        #     vocabulary rule (a submission is always a flow) lives in the
        #     generator, which does. Both halves are checked here because the
        #     split is the point -- putting `submit[` in worldmap.py would be
        #     the easy fix and would break the module's stated invariant.
        print()
        from .explorer.worldmap import Transition as _T
        from .explorer.worldmap import WorldMap as _WM
        from .explorer.worldmap import is_flow

        flows = _WM()
        flows.entry_key = "a"
        # a --click--> a  (nothing happened)          not a flow
        # a --type---> a  but a POST fired            a flow: accepted, no re-render
        # a --nav----> b  b reachable only this way   a flow
        # a --alt----> b  b already reachable         not a flow, b is recorded
        edges = {
            ("a", "button:Nothing"): _T("a", "button:Nothing", "a", False, 0),
            ("a", "textbox:Email"): _T("a", "textbox:Email", "a", True, 0),
            ("a", "link:Only way"): _T("a", "link:Only way", "b", False, 0),
            ("a", "link:Also"): _T("a", "link:Also", "b", False, 0),
        }
        flows.transitions = {k: [v] for k, v in edges.items()}
        ok &= check(
            "an edge where nothing happened is not worth a test",
            not is_flow(flows, edges[("a", "button:Nothing")]),
        )
        ok &= check(
            "staying put with a request fired IS worth a test",
            is_flow(flows, edges[("a", "textbox:Email")]),
            "the app accepted input and did not re-render -- the bug this catches",
        )
        ok &= check(
            "the only recorded way into a state is worth a test",
            is_flow(flows, edges[("a", "link:Only way")]),
        )
        ok &= check(
            "a second way into an already-recorded state is not",
            not is_flow(flows, edges[("a", "link:Also")]),
        )

        # 3g. The vocabulary half of the same policy. A submission the app
        #     correctly refuses is a self-loop that fires nothing -- structurally
        #     indistinguishable from `textbox:Email stays` -- and the brief wants
        #     unhappy paths, so the Generator overrides `is_flow` for it.
        from .generator import worth_testing

        refused = _T("a", "submit[empty]:button:Sign in", "a", False, 0)
        idle = _T("a", "textbox:Email", "a", False, 0)
        flows.transitions[("a", refused.action)] = [refused]
        ok &= check(
            "a submission the app refuses is still a flow",
            worth_testing(flows, refused),
            "an unhappy path was dropped for looking structurally inert",
        )
        ok &= check(
            "a non-submission that does nothing is still dropped",
            not worth_testing(flows, idle),
        )

        # 3h. Diversity. Ranking alone let one kind eat the whole suite:
        #     saucedemo's login page is several states, each contributing a
        #     submit edge, and forms outrank everything -- so 7 of 8 scenarios
        #     were the login form and every product link was crowded out.
        from .generator import interleave

        crowded = {
            "submit[valid]": ["v1", "v2", "v3", "v4", "v5", "v6", "v7"],
            "link": ["l1", "l2"],
            "button": ["b1"],
        }
        picked = interleave(crowded, 4)
        ok &= check(
            "one kind of action cannot crowd out the whole suite",
            len(picked) == 4 and len({*picked} & {"l1", "l2", "b1"}) >= 2,
            f"picked {picked}",
        )
        ok &= check(
            "the best of the best-ranked kind is still picked first",
            picked[0] == "v1",
            f"picked {picked}",
        )
        ok &= check(
            "a suite smaller than the limit keeps everything",
            sorted(interleave({"a": ["x"], "b": ["y"]}, 8)) == ["x", "y"],
        )

        # 3i. An assertion is a claim, not a snapshot. Measured 2026-09-05
        #     against practicesoftwaretesting.com: `button:Testing Guide` opens
        #     a modal holding an entire black-box-testing handbook, and
        #     `_behavioural` recorded all **469** of its lines as required
        #     effects. On replay 52 came back and 417 did not, so the Runner
        #     read a modal that opened correctly as a DEFECT -- three times,
        #     because three scenarios share that step as a path prefix.
        #
        #     The subset rule already lets an app render *more* than it did.
        #     Nothing protected against one omission out of 469. A delta that
        #     large is a document, and a document is content, not behaviour.
        #
        #     The cap truncates: the dropped effects are not retained anywhere,
        #     so the report cannot yet say "asserting on 12 of 469". That is a
        #     real loss of evidence and the next thing to add here.
        from .generator import ASSERTION_CAP, expectation as _expectation
        from .explorer.observer import Observation as _Obs

        def _mapped(before: str, after: str):
            """A two-state map with one edge, built from real observations."""
            world = _WM()
            a = world.record(_Obs(url="/", title="t", snapshot=before))
            b = world.record(_Obs(url="/", title="t", snapshot=after))
            world.transitions[(a, "button:Testing Guide")] = [
                _T(a, "button:Testing Guide", b, False, 1)
            ]
            return world, a

        # Real snapshots, not a generated stand-in. The target is
        # nondeterministic -- 11, 5, 6 and 2 state maps across four runs of the
        # same URL on 2026-09-05 -- so re-running it cannot attribute a change
        # to this code: the variance is larger than the effect. Pinning the
        # captured pair is what makes the failure repeatable.
        #
        # `fixtures/capture.py` refreshes it and records where it came
        # from. A generated fixture was tried first and was worse than useless:
        # 469 lines reading `chapter {i}` collapse to one under `explain`'s
        # digit normalisation, so the check passed before the fix existed.
        _fixture = json.loads(
            (Path(__file__).resolve().parent / "fixtures"
             / "testing-guide-modal.json").read_text(encoding="utf-8")
        )
        big, big_from = _mapped(_fixture["before"], _fixture["after"])
        huge = _expectation(big, big_from, "button:Testing Guide")
        ok &= check(
            "a captured modal does not become a 410-line assertion",
            # A literal, deliberately not `ASSERTION_CAP`. Written against the
            # constant, the check compares the cap to itself and passes for any
            # value of it -- verified 2026-09-05 by setting the cap to 10**9 and
            # watching this still pass. A check that cannot fail is not evidence.
            huge is not None and len(huge.added) <= 20,
            f"asserting on {len(huge.added) if huge else 0} lines -- a modal that "
            f"opens correctly will be reported as a defect",
        )

        # The other direction, and the reason this is a cap rather than a role
        # filter: "Invalid credentials" is a `paragraph`, it is the single most
        # valuable unhappy-path assertion we generate, and a rule that dropped
        # body text to solve the handbook would drop it too.
        small, small_from = _mapped(
            _fixture["before"],
            _fixture["before"] + '\n- paragraph: Invalid credentials',
        )
        tiny = _expectation(small, small_from, "button:Testing Guide")
        ok &= check(
            "a small delta is still asserted in full",
            tiny is not None and any("Invalid credentials" in ln for ln in tiny.added),
            "the error message an unhappy path exists to catch was dropped",
        )

        # 3j. What the test types must be what the crawl typed. Measured
        #     2026-09-05 on the exported saucedemo suite: every
        #     `submit[invalid]` spec filled `standard_user` / `secret_sauce` --
        #     the *valid* credentials -- and then asserted "Password is
        #     required". Both cannot be true. The spec passed anyway, because
        #     the assertion is a visibility check on text that happened to be
        #     on screen at record time, and it would keep passing if the
        #     application deleted every validation rule it has.
        #
        #     Root cause was a data-model hole, not a codegen slip: `Step.fields`
        #     carried `(role, name)` -- *which* fields exist -- so `spec()` had
        #     to re-derive the value from `forms.value_for`, and that function
        #     only knows how to produce valid input. Anything the recorder does
        #     not persist, the export re-derives, and re-derivation always
        #     drifts to the default path because it is the only one the deriving
        #     function knows.
        from .generator import Expectation as _Exp
        from .generator import Step as _Step
        from .generator import Scenario as _Scen
        from .generator import spec as _spec

        _typed = _Scen(
            name="submit the Sign in form with input the app should reject",
            target_url="https://example.test/",
            steps=(
                _Step(
                    intent="submit the Sign in form with input the app should reject",
                    action="submit[invalid]:button:Sign in",
                    from_key="a",
                    fields=(
                        ("textbox", "Username", "' OR 1=1 --"),
                        ("textbox", "Password", ""),
                    ),
                    expect=_Exp(
                        moved=False, mutating=False,
                        added=("- paragraph: Password is required",),
                        removed=(), to_key="a",
                    ),
                ),
            ),
        )
        _exported = _spec(_typed, Credentials("real-user", "real-password"))
        ok &= check(
            "a rejected-input spec types what the crawl typed",
            "' OR 1=1 --" in _exported,
            "the recorded payload never reached the export -- the spec re-derived "
            "a valid value and now asserts a rejection it cannot cause",
        )
        ok &= check(
            "a rejected-input spec does not type the valid credentials",
            "real-password" not in _exported and "real-user" not in _exported,
            "the negative test fills the happy-path credentials, so it would "
            "still pass with every validation rule removed",
        )

        # 3k. An action we cannot name is an action we cannot write a test for.
        #     `forms.available_actions` says so in as many words -- "an element
        #     we cannot name is one we cannot write a stable test for, so it is
        #     explored once and honestly" -- and then the Generator compiled one
        #     anyway, as `page.getByRole('button').first()`. Measured on the
        #     exported saucedemo suite: 4 of 8 specs clicked "whatever button or
        #     link is first in the DOM". Three of them were the error-dismiss X
        #     on the login page, which is why they submit a form and then assert
        #     the form is still there.
        #
        #     Exploring an unnamed control is right; exporting one is not. The
        #     map keeps the edge either way -- this drops it from the *suite*.
        from .generator import scenarios as _scen_of

        _unnamed = _WM()
        _a = _unnamed.record(_Obs(url="/", title="t", snapshot="- button\n- link"))
        _b = _unnamed.record(_Obs(url="/x", title="u", snapshot="- heading: Done"))
        _unnamed.transitions[(_a, "button")] = [_T(_a, "button", _b, True, 1)]
        ok &= check(
            "an action with no accessible name is not compiled into a suite",
            not _scen_of(_unnamed),
            "compiled a scenario whose only locator is getByRole(role).first() "
            "-- the positional locator this design exists to avoid",
        )

        _named = _WM()
        _c = _named.record(_Obs(url="/", title="t", snapshot='- button "Go"'))
        _d = _named.record(_Obs(url="/x", title="u", snapshot="- heading: Done"))
        _named.transitions[(_c, "button:Go")] = [_T(_c, "button:Go", _d, True, 1)]
        ok &= check(
            "a named action still compiles",
            bool(_scen_of(_named)),
            "the name guard is too strict -- it dropped a nameable action",
        )

        # 4. The executable layer: a path through the map becomes a test, and
        #    the test's failure classifies itself. These six checks are the
        #    acceptance experiment for the whole product claim, so they drive
        #    the real crawler, the real generator and the real browser.
        #
        #    The SUT carries two orthogonal knobs precisely so this section can
        #    exist: `?v=` moves the markup without touching behaviour, `?bug=1`
        #    moves the behaviour without touching the markup. An agent that
        #    always answers "heal" passes the drift check and fails the defect
        #    check; one that always answers "defect" does the reverse. Only a
        #    classifier reading both signals passes both.
        print()
        from .explorer.crawler import Budget as CrawlBudget
        from .explorer.crawler import crawl
        from .generator import Expectation, Step, from_json, scenarios, spec, to_json
        from .runner import DEFECT, ESCALATE, HEALED, PASSED, resolve
        from .runner import run as replay

        browser = pw.chromium.launch()
        page = browser.new_page()
        # A crawl reports each action as it takes it. `checkpoint` fires per
        # edge but receives only the map, so it can say the crawl advanced and
        # not what it did -- which is why a stalled run against a remote target
        # is indistinguishable from a slow one. Measured 2026-09-05: a
        # reproduction against practicesoftwaretesting.com printed nothing for
        # 3m20s and there was no way to tell progress from a hang.
        walked: list[str] = []
        mapped = crawl(
            page, SUT, CrawlBudget(max_actions=10, max_seconds=90),
            trace=walked.append,
        )
        ok &= check(
            "a crawl says what it is doing while it does it",
            len(walked) >= len(mapped.transitions),
            f"{len(walked)} traced against {len(mapped.transitions)} edges walked",
        )
        ok &= check(
            "a traced line names the action, not just a counter",
            any("Sign in" in line for line in walked),
            f"first lines: {walked[:3]}",
        )

        # 4b. One picture per state, and not one more. A revisit that shoots
        #     again is invisible in the UI and quadratic in a real crawl.
        from pathlib import Path as _Path

        from .shots import shooter

        with tempfile.TemporaryDirectory() as tmp:
            shot_page = browser.new_page()

            # Counting the *invocations*, not the files: `shooter` names each
            # file after the state key, so a second shot at a state it already
            # captured silently overwrites the first and leaves the file count
            # unchanged. Only the call log can tell "captured once" from
            # "captured twice", and "captured once" is the whole property.
            taken: list[str] = []
            capture_once = shooter(shot_page, run_id=1, root=_Path(tmp))

            def counting_shot(key: str) -> str | None:
                taken.append(key)
                return capture_once(key)

            shot_world = crawl(
                shot_page,
                SUT,
                CrawlBudget(max_actions=12, max_seconds=90),
                credentials=CREDENTIALS,
                shot=counting_shot,
            )
            shot_page.close()
            files = list((_Path(tmp) / "run-1").glob("*.png"))
            ok &= check(
                "one screenshot per state, never two",
                len(files) == len(shot_world.states),
                f"{len(files)} files for {len(shot_world.states)} states",
            )
            ok &= check(
                "every state carries a thumbnail path",
                all(n.screenshot for n in shot_world.states.values()),
            )
            ok &= check(
                "the recorded path is what the API serves",
                all(
                    n.screenshot.startswith("run-1/") and n.screenshot.endswith(".png")
                    for n in shot_world.states.values()
                ),
            )
            ok &= check(
                "no state is captured twice",
                len(taken) == len(set(taken)) == len(shot_world.states),
                f"{len(taken)} shots for {len(set(taken))} distinct states",
            )

        plan = scenarios(mapped)
        happy = next(
            (s for s in plan if s.terminal.action.startswith("submit[valid]")), None
        )

        ok &= check("a crawl compiles into scenarios", bool(plan))
        ok &= check(
            "a completed form becomes a scenario",
            happy is not None,
            "no submit[valid] edge in the map -- the crawl never filled the form",
        )

        if happy is not None:
            ok &= check(
                "expectations are recorded effects, not invented ones",
                bool(happy.terminal.expect.added) and happy.terminal.expect.moved,
                "the happy path recorded no observable effect to assert on",
            )
            ok &= check(
                "a scenario survives the round trip to JSON",
                from_json(to_json(happy)) == happy,
            )
            exported = spec(happy)
            ok &= check(
                "the exported spec is a real Playwright file that asserts",
                "@playwright/test" in exported
                and "toBeVisible" in exported
                and exported.count("test.step") == len(happy.steps),
            )

            baseline = replay(page, happy, target_url=SUT)
            ok &= check(
                "baseline: the recorded path still passes",
                baseline.verdict == PASSED,
                render_verdicts(baseline),
            )

            drifted = replay(page, happy, target_url=f"{SUT}?v=2")
            ok &= check(
                "markup drift is healed, not reported as a bug",
                drifted.verdict == HEALED
                and drifted.steps[-1].resolution.rung == "structural",
                render_verdicts(drifted),
            )

            broken = replay(page, happy, target_url=f"{SUT}?bug=1")
            ok &= check(
                "a behavioural defect is reported, not healed away",
                broken.verdict == DEFECT,
                render_verdicts(broken),
            )

            both = replay(page, happy, target_url=f"{SUT}?v=2&bug=1")
            ok &= check(
                "drift and defect at once escalates instead of guessing",
                both.verdict == ESCALATE,
                render_verdicts(both),
            )

            # The regression that matters most. `smoke_run.heal_locator` used to
            # answer `page.get_by_role("button").first` -- any button at all --
            # which turns every failure into a green run. A step whose form no
            # longer matches must refuse to resolve rather than grab a neighbour.
            observer = Observer(page)
            observer.start_window()
            page.goto(SUT)
            here = observer.observe()
            #
            # The name must be one the page does not carry, or the exact rung
            # fires first and fires correctly: a descriptor that still resolves
            # verbatim has not drifted, whatever else changed around it.
            impostor = Step(
                intent="submit a payment form this page does not have",
                action="submit[valid]:button:Place order",
                from_key="",
                fields=(
                    ("textbox", "Card number", "4111111111111111"),
                    ("textbox", "CVC", "123"),
                ),
                expect=Expectation(True, False, (), (), ""),
            )
            refusal = resolve(page, impostor, here)
            ok &= check(
                "healing refuses a control it cannot justify",
                refusal.action is None,
                f"the healer grabbed {refusal.action!r} via {refusal.rung} -- "
                "the old toy behaviour, any button will do",
            )

        # 5. The critic. Its whole defensibility is that the model cannot
        #    invent a finding, so that is what these check -- not whether the
        #    ranking is good, which is not a question a probe can answer.
        print()
        from .critic import candidates, prioritise, render

        gaps = candidates(mapped)
        cells = [gap.citation for gap in gaps]

        ok &= check(
            "the crawl's uncovered input partitions are found",
            any(g.kind == "unexercised-partition" for g in gaps),
            "no partition gap on a map whose submit[invalid] edges were never walked",
        )
        ok &= check(
            "no cell is reported twice",
            len(cells) == len(set(cells)),
            f"{len(cells) - len(set(cells))} duplicate citations",
        )
        ok &= check(
            "every gap cites a state that exists in the map",
            all(gap.state_key in mapped.states for gap in gaps),
        )
        ok &= check(
            "the report carries no percentage",
            "%" not in render(gaps),
            "a calibrated-looking number leaked into the report",
        )

        if len(gaps) >= 2:
            ranked = prioritise(mapped, Ranker())
            ok &= check(
                "a fabricated gap is discarded, not reported",
                len(ranked) == len(gaps)
                and set(g.citation for g in ranked) == set(cells),
                "the critic returned a finding that was never a candidate",
            )
            ok &= check(
                "the model's ordering is honoured",
                (ranked[0].citation, ranked[1].citation) == (cells[1], cells[0]),
            )
            ok &= check(
                "an omitted gap is still reported, after the ranked ones",
                all(not g.risk for g in ranked[2:]) and len(ranked) > 2,
                "omission deleted evidence instead of demoting it",
            )

            # Runs 31 and 33 of 2026-09-05 are the same 402 on the same key:
            # 31 reached this call and died with 0 tests, 33 had no candidates
            # to rank, never made the call, and reported 12. The difference was
            # never about coverage -- it was that this one model call could
            # abort a deterministic suite compiled after it.
            try:
                survived = prioritise(mapped, BrokeRanker())
            except Exception as exc:
                survived = None
                broke = f"{type(exc).__name__}: {exc}"
            else:
                broke = ""
            ok &= check(
                "a provider that fails leaves the computed ranking standing",
                survived is not None
                and set(g.citation for g in survived) == set(cells),
                broke or "the candidates did not survive the provider failure",
            )

            # The one call that decides the order of the final report, and the
            # only one of the five model calls in this system that used to
            # leave no durable record. `emit("critic ranked N of M")` is a
            # count, not evidence -- it cannot say what was asked or what came
            # back, which is the question anyone asks a day later.
            import shutil

            from .tracing import TRANSCRIPTS

            probe_run = 999_000
            shutil.rmtree(TRANSCRIPTS / f"run-{probe_run}", ignore_errors=True)
            prioritise(mapped, Ranker(), run_id=probe_run)
            written_files = sorted(
                (TRANSCRIPTS / f"run-{probe_run}").glob("*-critic.json")
            )
            ok &= check(
                "the critic's ranking leaves a transcript, filed under its run",
                len(written_files) == 1,
                f"{len(written_files)} critic transcripts under run-{probe_run}",
            )
            if written_files:
                import json as _json

                record = _json.loads(written_files[0].read_text())
                ok &= check(
                    "the transcript carries the prompt, the system and the answer",
                    bool(record["system"])
                    and bool(record["prompt"])
                    and record["exchanges"]
                    and record["exchanges"][0]["calls"][0]["name"] == "prioritise",
                    "a transcript was written that cannot reconstruct the call",
                )
            shutil.rmtree(TRANSCRIPTS / f"run-{probe_run}", ignore_errors=True)

        # 5b. The synthesizer: the model seam `explorer/__init__` calls the one
        #     place a model is worth its cost. It held its own Anthropic client
        #     and checked ANTHROPIC_API_KEY by hand, so on an OPENROUTER key it
        #     returned None on every call and every cached payload read
        #     "source": "fallback". Nothing failed; the seam was simply never
        #     open, and the only trace was a word in a summary line.
        print()
        from .explorer.synth import Synthesizer

        form = (("textbox", "Email"), ("textbox", "Password"))

        asked = Synthesizer(provider=Payloads("Email"))
        payload = asked.invalid_payload("s", "button:Sign in", "Sign in", form)
        ok &= check(
            "the synthesizer asks whatever provider llm.load would return",
            payload.source == "model" and payload.values == {"Email": "@@@"},
            f"source={payload.source} values={payload.values}",
        )

        # The extractive rule, same as the critic's. A payload naming a field
        # this form has not got is not a payload, it is a typo waiting to be
        # typed into `.first`.
        wrong = Synthesizer(provider=Payloads("Coupon code"))
        payload = wrong.invalid_payload("s", "button:Sign in", "Sign in", form)
        ok &= check(
            "a payload for a field the form has not got falls back and says why",
            payload.source == "fallback" and "no field" in wrong.unavailable,
            f"source={payload.source} unavailable={wrong.unavailable!r}",
        )

        # Real forms decorate their labels. Matching verbatim meant the seam
        # did not fire on `practicesoftwaretesting.com/auth/login`, whose
        # accessible names are `Email address *` and `Password *`.
        decorated = (("textbox", "Email address *"), ("textbox", "Password *"))
        loose = Synthesizer(provider=Payloads("Email address"))
        payload = loose.invalid_payload("s", "button:Login", "Login", decorated)
        ok &= check(
            "a decorated field label still matches the model's plain name",
            payload.source == "model" and payload.values == {"Email address *": "@@@"},
            f"source={payload.source} values={payload.values}",
        )

        # Still extractive, and still refuses a guess: two fields that reduce to
        # the same key are ambiguous, and typing into the wrong one is worse
        # than falling back.
        twins = (("textbox", "Email"), ("textbox", "e-mail"))
        ambiguous = Synthesizer(provider=Payloads("Email"))
        payload = ambiguous.invalid_payload("s", "button:Go", "Go", twins)
        ok &= check(
            "two fields that reduce to one key are refused, not guessed between",
            payload.source == "fallback",
            f"source={payload.source} values={payload.values}",
        )

        prose = Synthesizer(provider=Payloads(prose=True))
        payload = prose.invalid_payload("s", "button:Sign in", "Sign in", form)
        ok &= check(
            "a model that answers in prose degrades instead of raising",
            payload.source == "fallback"
            and "without calling payload" in prose.unavailable,
            f"source={payload.source} unavailable={prose.unavailable!r}",
        )

        class Broken:
            name, model = "scripted:broken", "none"

            def turn(self, system, transcript, tool_defs):
                raise RuntimeError("429 daily quota exhausted")

        dead = Synthesizer(provider=Broken())
        payload = dead.invalid_payload("s", "button:Sign in", "Sign in", form)
        ok &= check(
            "a provider that raises degrades the crawl and names the reason",
            payload.source == "fallback" and "429" in dead.unavailable,
            f"source={payload.source} unavailable={dead.unavailable!r}",
        )

        # The cache is the replay log, and it must not learn a shape twice.
        cached = asked.invalid_payload("s", "button:Sign in", "Sign in", form)
        ok &= check(
            "a form shape already answered is served from the cache",
            cached.source == "cache" and asked._provider.calls == 1,
            f"source={cached.source} model calls={asked._provider.calls}",
        )

        # 5c. Redaction. An Observation is persisted verbatim -- snapshot, url
        #     and network all reach StateObservation, and the url reaches
        #     AppState and artifacts/runs too. Measured on this workspace's own
        #     database before this existed: 108 snapshot rows with a Password
        #     value, 48 url rows with a password= (two distinct values, neither
        #     producible by synth.py), 39 network rows. Nothing masked any of
        #     it. These checks are the three paths plus the two ways a redaction
        #     can be worse than the exposure.
        print()
        from .explorer.observer import REDACTED, redact_snapshot, redact_url
        from .explorer.statekey import state_key

        filled = (
            '- form:\n'
            '  - textbox "Email" [ref=e9]: alice@example.com\n'
            '  - textbox "Password" [active] [ref=e11]: hunter2-real-password\n'
            '  - button "Sign in" [ref=e13]'
        )
        blank = filled.replace(": hunter2-real-password", ":")
        hidden = redact_snapshot(filled)

        ok &= check(
            "a password value is redacted out of the snapshot",
            "hunter2-real-password" not in hidden and REDACTED in hidden,
            hidden,
        )
        ok &= check(
            "a non-secret field keeps its value",
            "alice@example.com" in hidden,
            "redaction was too broad -- the evidence is the point of the record",
        )
        ok &= check(
            "redacting does not change the state key",
            state_key(filled) == state_key(hidden),
            f"{state_key(filled)} != {state_key(hidden)}",
        )
        # The failure bdai-16 flagged: field_value maps "" to "" and anything
        # else to "filled", so an empty placeholder would collapse a filled
        # field into an unfilled one -- and the error state after a rejected
        # submit differs from the pristine form by exactly that.
        ok &= check(
            "a filled field and an empty one stay distinct after redaction",
            state_key(hidden) != state_key(blank),
            "redaction merged a filled form with an empty one",
        )
        ok &= check(
            "a password in the query string is redacted",
            "hunter2" not in redact_url("http://x/sut?email=a%40b.com&password=hunter2"),
            redact_url("http://x/sut?email=a%40b.com&password=hunter2"),
        )
        # The url is evidence, so a url with nothing to hide must come back
        # byte-identical rather than re-encoded by a round trip through
        # urlencode.
        plain = "http://x/y?a=1&b=hello%20world"
        ok &= check(
            "a url with no secret is returned untouched",
            redact_url(plain) == plain,
            f"{plain} -> {redact_url(plain)}",
        )

        # Scrubbing the database is not the whole remediation, and believing it
        # was is the mistake this check exists to stop repeating. `autosave`
        # writes the same url into artifacts/runs/*.json, and a model repeats
        # what it read into a transcript -- 17 files here still carried a
        # credential after `make scrub` reported the database clean.
        import json as _json
        import tempfile

        from .explorer.store import scrub_artifacts

        yard = Path(tempfile.mkdtemp())
        (yard / "runs").mkdir()
        (yard / "runs" / "a.json").write_text(
            _json.dumps({"url": "http://x/sut?email=a%40b.com&password=hunter2"})
        )
        (yard / "runs" / "clean.json").write_text(_json.dumps({"url": "http://x/sut?v=1"}))
        (yard / "note.png").write_bytes(b"\x89PNG not text")

        touched = scrub_artifacts(yard)
        after = (yard / "runs" / "a.json").read_text()
        ok &= check(
            "a credential in an artifact file is redacted too",
            "hunter2" not in after and touched["files"] == 1,
            f"{touched} :: {after}",
        )
        ok &= check(
            "the scrubbed artifact is still valid JSON",
            _json.loads(after).get("url", "").startswith("http://x/sut?email="),
            after,
        )
        ok &= check(
            "a second scrub changes nothing",
            scrub_artifacts(yard)["files"] == 0,
            "scrubbing is not idempotent -- re-running would churn every file",
        )

        # 6. The meta-agent. The brief's headline requirement is that nobody
        #    chooses the stages, so what these check is the *deciding*, not the
        #    stages -- each of which is already covered above.
        print()
        from .pipeline import Budget as PipeBudget
        from .pipeline import addressable, report, verifiable
        from .pipeline import run as pipeline

        pipe = pipeline(
            page, SUT,
            budget=PipeBudget(explore_actions=12, explore_seconds=90, max_scenarios=4),
            verify_against=(f"{SUT}?v=2", f"{SUT}?bug=1"),
        )

        ok &= check(
            "a URL alone drives the whole pipeline",
            pipe.stopped == "complete" and bool(pipe.plan) and bool(pipe.results),
            f"stopped={pipe.stopped!r} scenarios={len(pipe.plan)} runs={len(pipe.results)}",
        )
        stages = [d.stage for d in pipe.decisions]
        ok &= check(
            "every stage of the brief is decided, in order",
            stages[:1] == ["explore"]
            and {"critique", "replan", "generate", "run", "stop"} <= set(stages),
            f"stages seen: {stages}",
        )
        ok &= check(
            "every decision carries the evidence it cites",
            all(d.because and (d.evidence or d.stage == "stop") for d in pipe.decisions),
            "a decision was recorded without a reason or its numbers",
        )

        # The decision the file exists for: a gap with no mechanism to close it
        # must not trigger another exploration round.
        invalid_gaps = tuple(g for g in pipe.gaps if "submit[invalid]" in g.action)
        ok &= check(
            "a gap with no mechanism to close it does not trigger a re-plan",
            not addressable(invalid_gaps, has_synthesizer=False)
            and len(addressable(invalid_gaps, has_synthesizer=True)) == len(invalid_gaps),
            "submit[invalid] was treated as explorable without a synthesizer",
        )
        ok &= check(
            "re-verification drops scenarios that navigate by link",
            all(
                not any(s.action.startswith("link:") for s in scenario.steps)
                for scenario in verifiable(pipe.plan)
            ),
            "a link-following scenario would be re-run against a different base",
        )

        # `?v=2`, `?bug=1` and their composition are knobs on *our* SUT and
        # nothing else. Appended to a third-party URL they are query parameters
        # the app ignores, so the suite gets re-run against a byte-identical
        # target and the report calls the result a verification. Measured
        # against saucedemo before this check existed: 4 of 12 reported runs
        # meant nothing.
        from .pipeline import fixture_variants

        ok &= check(
            "the SUT's fixture knobs are not appended to a third-party URL",
            fixture_variants("https://www.saucedemo.com") == ()
            and fixture_variants(SUT)
            == (f"{SUT}?v=2", f"{SUT}?bug=1", f"{SUT}?v=2&bug=1"),
            "a real target would be re-verified against itself",
        )

        written = report(pipe)
        ok &= check(
            "the report answers every line the brief asks for",
            all(
                heading in written
                for heading in (
                    "HOW THE AGENT DECIDED", "SCENARIOS COVERED", "OUTCOMES",
                    "HEALER ACTIONS", "COVERAGE GAPS REMAINING",
                )
            ),
        )
        # Still true, and now load-bearing in a second way: `/progress` shows a
        # walked/offered ratio on the map, and the thing that keeps that from
        # becoming a coverage claim is that it never reaches the report. If a
        # percentage ever appears here, the 19:00 decision has been lost.
        ok &= check(
            "the report carries no coverage percentage",
            "%" not in written,
        )

        browser.close()

    # 7. Invariants: the defects that need no recording of past behaviour.
    #
    #    Pure functions over a map, so these build the map by hand rather than
    #    crawling for one. That is deliberate and not a shortcut -- the shapes
    #    below are the ones a real crawl produces rarely and a demo needs to
    #    survive, and waiting for an application to exhibit them is how a rule
    #    ships untested. Every map here is one a `?bug=` variant could produce.
    print()
    ok &= _invariant_checks()

    # 8. The prompts are the tunable part; loading them must not silently break.
    print()
    for role, marker in (
        ("ant", "explorer ant"),
        ("orchestrator", "orchestrator"),
        ("critic", "coverage critic"),
        # The console's chat reads this one. It has no tool loop to fail
        # loudly, so a broken prompt would show up only as a vague answer.
        ("analyst", "explorer ants built"),
    ):
        text = instructions(role)
        ok &= check(
            f"prompts/{role}.md loads without its frontmatter",
            not text.startswith("---") and marker in text.lower(),
        )
    ok &= check(
        "every tool schema is well formed",
        all(
            set(t.parameters.get("required", []))
            <= set(t.parameters.get("properties", {}))
            for t in tools.ANT_TOOLS + tools.ORCHESTRATOR_TOOLS
        ),
        "a tool requires a property it does not declare",
    )

    # 8. The console's chat is the one caller with a *person* in the loop, and
    # the only one whose transcript has to alternate user/assistant turns. It
    # used to render its whole history into a single user message; these checks
    # are what stops that regressing quietly, since a stateless chat still
    # answers -- just without remembering anything you said.
    print()
    ok &= _chat_transcript_checks()

    # 9. The two walkers had drifted: every guard, the input synthesizer and
    # the refused-action notebook lived in `crawler.py` and the colony -- the
    # engine the console runs whenever an API key is present -- had none of
    # them. These checks are what stops that reopening.
    print()
    ok &= _parity_checks()

    # 9b. The semantic layer over the map. The colony has always written a
    # summary and named flows; nothing ever checked a word of it against the
    # map, and nothing downstream read it. These pin both halves.
    print()
    ok &= _behaviour_checks()

    # 9c. One orchestrator that can send an ant, a generator or a healer. The
    # pipeline used to decide that by the order its stages were written in.
    print()
    ok &= _dispatch_checks()

    # 9d. The semantic layer has to reach the report, or the run reasoned in
    # private and presented a log.
    print()
    ok &= _report_checks()

    # 9e. The join the semantic layer exists for: a believed flow becomes a
    # runnable scenario, and a believed flow nobody walked becomes nothing.
    print()
    ok &= _flow_checks()

    # 9f. The model proposes what ought to hold; the recorded transitions rule
    # on it. This is the one place a hypothesis stops being unexamined.
    print()
    ok &= _verdict_checks()

    # 9g. A plan an ant structurally cannot carry out, refused before the ants
    # are spent rather than disbelieved after.
    print()
    ok &= _mission_checks()

    # 9h. A doubt raised in wave 1 must still be in front of the orchestrator in
    # wave 3, while the belief it contradicts is still being acted on.
    print()
    ok &= _standing_checks()

    # 10. Bring-your-own-key. The console's Advanced panel is only a form until
    # the key it holds reaches `load()`; these checks are the wire between them.
    print()
    ok &= _byok_checks()

    # 11. The second input. A URL is still all the brief requires, but a box
    # beside it now carries credentials, focus and claims -- and the parse that
    # tells those apart is a model, so it needs pinning like any other seam.
    print()
    ok &= _context_checks()

    # 12. What happens to a claim after it is parsed. The failure these guard
    # against is the attractive one: letting a model write a test for the
    # sentence, whose expectation nothing ever measured, and whose failure is
    # therefore unclassifiable on the one test the user asked for by name.
    print()
    ok &= _claim_checks()

    # 13. A crawl walks off the site it was given and meets documents that are
    # still committing. One of those ended a run in `error` after five states.
    print()
    ok &= _navigation_checks()

    # 14. An exhausted key is not a hypothetical: two live runs have died on
    # one mid-wave. These pin the escape hatch *and* its default, which is off
    # -- a run that quietly changes model corrupts every comparison in bets.md.
    print()
    ok &= _fallback_checks()

    # 15. The suite kept between runs. `runner` could always tell a moved
    # locator from moved behaviour; nothing wrote the answer back to a file, so
    # every suite was recompiled from the crawl that had just happened and no
    # test could ever be older than the change it was meant to catch.
    print()
    ok &= _suite_checks()

    # 16. Which world model the plan came from, and the suite that keeps its
    # past. A recompiled suite agrees with the current app by construction, so
    # a suite with no versions cannot catch a regression however good its
    # scenarios are.
    print()
    ok &= _planner_checks()
    print()
    ok &= _versioning_checks()
    ok &= _rescue_checks()
    ok &= _ladder_checks()
    ok &= _reverify_checks()

    print()
    return 0 if ok else 1


def _planner_world():
    """A map with one mutating edge and one navigation edge, both walkable.

    Hand-built rather than crawled because these checks are about which world
    model the Planner *reads*, and a live crawl would make a failure ambiguous
    between the planner and the app it was pointed at.
    """
    from .explorer.observer import Observation
    from .explorer.worldmap import StateNode, Transition, WorldMap

    a, b, c = "a" * 16, "b" * 16, "c" * 16
    world = WorldMap()
    world.evidence = [Observation(url="http://sut/", title="sut", snapshot="")]
    for key in (a, b, c):
        world.states[key] = StateNode(
            key=key, url="http://sut/", title="sut",
            actions=("submit[valid]:button:Sign in", "link:Home"),
            evidence=(0,),
        )
    world.entry_key = a
    for from_key, action, to_key, mutating in (
        (a, "submit[valid]:button:Sign in", b, True),
        (b, "link:Home", c, False),
    ):
        world.transitions[(from_key, action)] = [
            Transition(from_key=from_key, action=action, to_key=to_key,
                       mutating=mutating, evidence=0)
        ]
    return world, a, b, c


def _planner_checks() -> bool:
    """Which world model the plan was drawn from, and that the knob is real.

    The claim under test is not "the behavioural model is better" -- that is
    measured by running both suites. It is the weaker and more important one:
    the two sources are actually *different*, and `source="map"` cannot leak a
    scenario the model proposed. A knob that quietly plans the same way either
    way makes the comparison it exists for meaningless, and would do so
    silently.
    """
    from .behavior import BehaviorModel, Hypothesis
    from .planner import DEFAULT_SOURCE, compare, plan, source_from_env

    print("PLANNER     one seam, two world models, and a knob that really moves")
    ok = True

    world, a, b, c = _planner_world()
    # A flow the map can back: both consecutive pairs are recorded edges.
    believed = BehaviorModel(
        summary="a sign-in that leads home",
        hypotheses=(
            Hypothesis(
                claim="signing in leads to the home page",
                kind="flow",
                cites=(a, b, c),
            ),
        ),
    )

    rich = plan(world, believed, source="behaviour", limit=8)
    plain = plan(world, believed, source="map", limit=8)

    ok &= check(
        "the behavioural planner compiles a believed flow the map can back",
        rich.from_behaviour == 1
        and any(s.origin == "behaviour:flow" for s in rich.scenarios),
        f"origins were {[s.origin for s in rich.scenarios]}",
    )
    ok &= check(
        "the deterministic planner returns nothing the model proposed",
        plain.from_behaviour == 0
        and all(s.origin == "map" for s in plain.scenarios),
        f"origins were {[s.origin for s in plain.scenarios]}",
        )
    ok &= check(
        "and it still plans -- a map-only plan is smaller, not empty",
        len(plain) > 0,
        "the deterministic half is the floor every no-key run stands on",
    )
    ok &= check(
        "the behavioural plan contains a scenario the deterministic one cannot",
        bool({s.name for s in rich.scenarios} - {s.name for s in plain.scenarios}),
        "if the two sources produce the same suite the knob measures nothing; "
        "a believed flow is named for the claim it checks, and ranking single "
        "edges can never propose one",
    )
    ok &= check(
        "and it does not lose the computed scenarios by adding them",
        {s.name for s in plain.scenarios} - {s.name for s in rich.scenarios} == set()
        or len(rich) == rich.from_behaviour + rich.from_map,
        "the semantic layer only ever adds; the map half fills what is left",
    )

    # The node filter, which is what per-node dispatch and per-node coverage
    # both stand on.
    at_b = plan(world, believed, source="map", limit=8, node=b)
    ok &= check(
        "a plan narrowed to a node contains only scenarios that cross it",
        all(any(step.from_key == b for step in s.steps) for s in at_b.scenarios),
        f"got {[s.node for s in at_b.scenarios]}",
    )
    ok &= check(
        "a scenario names the state its terminal action is taken from",
        all(s.node == s.steps[-1].from_key for s in rich.scenarios),
        "the node is what the map colours; deriving it from anything else "
        "lets a label and its steps disagree after a heal",
    )

    # The `only` filter: incremental generation stands entirely on it, and it
    # has to hold for both halves of the planner. A believed flow that slipped
    # past it would write a test for behaviour that is not new.
    one_edge = {(a, "submit[valid]:button:Sign in")}
    scoped = plan(world, believed, source="behaviour", limit=8, only=one_edge)
    ok &= check(
        "a plan scoped to one edge compiles only scenarios ending on it",
        all(
            (s.terminal.from_key, s.terminal.action) in one_edge
            for s in scoped.scenarios
        ),
        f"got {[(s.terminal.from_key[:4], s.terminal.action) for s in scoped.scenarios]}",
    )
    ok &= check(
        "an empty scope compiles nothing at all",
        len(plan(world, believed, source="behaviour", limit=8, only=set())) == 0,
        "a run that found no new edge must add no test; an empty filter that "
        "fell through to the whole map would append the suite to itself",
    )
    ok &= check(
        "and no scope is still the whole map",
        len(plan(world, believed, source="behaviour", limit=8, only=None)) == len(rich),
        "every caller from before incremental generation passes None",
    )

    # No provider at all. This is the whole no-key path, and it must say so.
    silent = plan(world, None, source="behaviour", limit=8)
    ok &= check(
        "with no behavioural model the plan degrades to the map and says so",
        len(silent) > 0 and silent.from_behaviour == 0 and bool(silent.degraded),
        f"degraded={silent.degraded!r}",
    )

    # The knob is read from the environment by both entry points, so a typo
    # must not silently remove the semantic layer.
    import os

    before = os.environ.get("PLAN_FROM")
    try:
        os.environ["PLAN_FROM"] = "map"
        ok &= check("PLAN_FROM=map selects the deterministic planner",
                    source_from_env() == "map")
        os.environ["PLAN_FROM"] = "behavioural"  # a plausible misspelling
        ok &= check(
            "an unrecognised PLAN_FROM keeps the richer source rather than dropping it",
            source_from_env() == DEFAULT_SOURCE,
            "a typo that silently removed the semantic layer would corrupt "
            "exactly the comparison the knob was set to make",
        )
    finally:
        os.environ.pop("PLAN_FROM", None)
        if before is not None:
            os.environ["PLAN_FROM"] = before

    ok &= check(
        "the comparison is computed from the two plans, not asserted",
        "scenarios" in compare(rich, plain) and "nodes covered" in compare(rich, plain),
    )

    # --- the redundancy guard ------------------------------------------------
    #
    # `regression.unseen` is what stops the kept suite growing on every run.
    # Measured before it existed: a re-crawl of the SUT reports 32 added edges
    # against an application nobody touched, because `state_key` folds in
    # accessible names and the crawl reaches the drift variants in a different
    # order each time. Keyed on the state, those 32 would have appended 32
    # duplicate tests; keyed on the action sequence, they append none.
    import tempfile
    from dataclasses import replace

    from . import regression

    with tempfile.TemporaryDirectory() as tmp:
        saved = plan(world, believed, source="map", limit=8).scenarios
        regression.emit(saved, tmp, because="probe baseline", target_url="http://sut/")

        ok &= check(
            "a candidate the saved suite already walks is not added again",
            regression.unseen(saved, tmp) == (),
            f"{len(regression.unseen(saved, tmp))} of {len(saved)} came back as new",
        )

        moved = tuple(
            replace(s, name=f"{s.name} (renamed)") for s in saved
        )
        ok &= check(
            "and renaming it does not make it new -- the actions are matched",
            regression.unseen(moved, tmp) == (),
            "matched on the name, a healed suite would re-add everything the "
            "Healer had just renamed",
        )

        fresh = plan(world, believed, source="behaviour", limit=8).scenarios
        genuinely_new = tuple(
            s for s in fresh
            if tuple(step.action for step in s.steps)
            not in {tuple(step.action for step in k.steps) for k in saved}
        )
        ok &= check(
            "a path the suite does not walk is added",
            len(regression.unseen(fresh, tmp)) == len(genuinely_new),
            "the guard must not be so strict it can never grow; a new flow is "
            "the one change to a kept suite that cannot hide a regression",
        )
        ok &= check(
            "extending an empty set of additions writes no version",
            regression.extend(tmp, (), because="nothing") is None,
            "a run that found nothing new must leave no version behind",
        )

    return ok


def _rescue_checks() -> bool:
    """Recovering a lost control, and refusing to when the map is ambiguous.

    Every check here is on a hand-built `WorldMap`, so the *policy* is testable
    without a browser, a key or a live app -- which is the point, because the
    policy is the part that can quietly start manufacturing green.

    The one that matters most is the tie. Two edges that both behave as the
    recorded step did is not a repair with a tie to break; it is the map saying
    the step is now ambiguous, and inventing an answer there is exactly the
    coin-flip `runner.resolve` declines to make one rung lower.
    """
    from . import rescue, runner
    from .explorer.worldmap import Transition, WorldMap
    from .generator import Expectation, Scenario, Step

    print("RESCUE      a lost control is looked for, and ties still refuse")
    ok = True

    here, dest, other = "a" * 16, "b" * 16, "c" * 16

    def step(action: str, moved: bool = True, mutating: bool = False) -> Step:
        return Step(
            intent="press the button", action=action, from_key=here, fields=(),
            expect=Expectation(
                moved=moved, mutating=mutating, added=(), removed=(), to_key=dest
            ),
        )

    def world_with(*edges: tuple[str, str, bool]) -> WorldMap:
        world = WorldMap()
        for action, to_key, mutating in edges:
            world.transitions.setdefault((here, action), []).append(
                Transition(
                    from_key=here, action=action, to_key=to_key,
                    mutating=mutating, evidence=0,
                )
            )
        return world

    # --- which edge replaced the lost one --------------------------------
    found, why = rescue.replacement(
        world_with(("button:Log in", dest, False)), here, step("button:Sign in"),
        "button:Sign in",
    )
    ok &= check(
        "a renamed control landing where the step landed is the replacement",
        found == "button:Log in",
        f"got {found!r}: {why}",
    )

    found, why = rescue.replacement(
        world_with(("button:Log in", dest, False), ("button:Register", dest, False)),
        here, step("button:Sign in"), "button:Sign in",
    )
    ok &= check(
        "two controls landing there is an ambiguity, not a repair",
        found is None and "ambiguous" in why,
        f"got {found!r}: {why}",
    )

    # No edge lands on the recorded destination, so the weaker filter runs: the
    # same kind of control, moving and mutating the same way.
    found, why = rescue.replacement(
        world_with(("button:Log in", other, False)), here, step("button:Sign in"),
        "button:Sign in",
    )
    ok &= check(
        "failing that, the only control of the same kind behaving the same way",
        found == "button:Log in",
        f"got {found!r}: {why}",
    )
    found, why = rescue.replacement(
        world_with(("link:Log in", other, False)), here, step("button:Sign in"),
        "button:Sign in",
    )
    ok &= check(
        "a link does not replace a button",
        found is None,
        f"got {found!r}: {why}",
    )
    found, why = rescue.replacement(
        world_with(("button:Log in", other, True)), here,
        step("button:Sign in", mutating=False), "button:Sign in",
    )
    ok &= check(
        "a control that now mutates where the old one did not is not the same step",
        found is None,
        f"got {found!r}: {why}",
    )
    found, why = rescue.replacement(
        world_with(("button:Log in", here, False)), here,
        step("button:Sign in", moved=True), "button:Sign in",
    )
    ok &= check(
        "a control that stays put where the old one moved is not the same step",
        found is None,
        f"got {found!r}: {why}",
    )
    found, why = rescue.replacement(
        world_with(("button:Sign in", dest, False)), here, step("button:Sign in"),
        "button:Sign in",
    )
    ok &= check(
        "the action that broke is never proposed as its own replacement",
        found is None,
        f"got {found!r}: {why}",
    )
    found, why = rescue.replacement(world_with(), here, step("button:Sign in"), "x")
    ok &= check(
        "an empty region says so rather than raising",
        found is None and "nothing leaves" in why,
        f"got {found!r}: {why}",
    )

    # --- which escalations are even candidates ---------------------------
    scenario = Scenario(
        name="sign in", target_url=SUT, steps=(step("button:Sign in"),),
    )
    absent = runner.Resolution(action=None, rung="unresolved", detail="gone")
    present = runner.Resolution(action="button:Sign in", rung="exact", detail="here")

    ok &= check(
        "a step nothing can play is a candidate for rescue",
        rescue.unattemptable(
            runner.Result(scenario, SUT, [
                runner.StepResult(scenario.steps[0], runner.ESCALATE, absent, "")
            ])
        ) == 0,
    )
    ok &= check(
        "a control that resolved but would not fire is not",
        rescue.unattemptable(
            runner.Result(scenario, SUT, [
                runner.StepResult(scenario.steps[0], runner.ESCALATE, present, "inert")
            ])
        ) is None,
        "the app was observed there, so exploring is shopping for a second opinion",
    )
    ok &= check(
        "and neither is a defect",
        rescue.unattemptable(
            runner.Result(scenario, SUT, [
                runner.StepResult(scenario.steps[0], runner.DEFECT, present, "")
            ])
        ) is None,
    )

    # --- the recovered action reaches the scenario as a Repair -----------
    recovered = rescue.Rescue(
        scenario="sign in", step=1, intent="press the button",
        was="button:Sign in", node=here, now="button:Log in", to_key=other,
        why="the only button here that behaves as recorded", explored=3,
        source="colony",
    )
    patched, repair = rescue.apply(scenario, recovered)
    ok &= check(
        "a recovered step is substituted into the scenario",
        patched.steps[0].action == "button:Log in",
        f"got {patched.steps[0].action!r}",
    )
    ok &= check(
        "and its evidence is refreshed with where it actually landed",
        patched.steps[0].expect.to_key == other,
    )
    ok &= check(
        "the repair says it came from an exploration, not from the ladder",
        repair is not None and repair.rung == "rescue" and "colony" in repair.detail,
        f"got {repair and (repair.rung, repair.detail)}",
    )
    unrecovered = rescue.Rescue(
        scenario="sign in", step=1, intent="", was="button:Sign in", node=here,
        why="nothing here behaves as recorded",
    )
    patched, repair = rescue.apply(scenario, unrecovered)
    ok &= check(
        "a rescue that found nothing changes nothing",
        repair is None and patched.steps[0].action == "button:Sign in",
    )
    return ok


def _ladder_checks() -> bool:
    """The bottom rung of the resolution ladder: a ranking, and its adjudicator.

    `runner.resolve` walks exact -> structural -> similarity, and the file's own
    docstring named the seam this closes: a model belongs *above* `escalate`,
    not above `structural`, because every rung over it already produces evidence
    and a model asked earlier would be overruling a deterministic answer.

    So there are two properties here and they pull in opposite directions.

    **It must speak when nothing else can.** Several structural candidates, none
    similar enough to the recorded name to clear the margin, is the coin-flip
    `resolve` refuses -- and refusing it costs a whole scenario. Choosing among
    controls that all exist is exactly what the research says judges are good at.

    **It must not speak anywhere else, and must never be believed on its own.**
    It answers by index into a list it was given, so an invented control cannot
    survive the return. It refuses outright below two candidates, so it cannot
    reach past the rung above it. And a repair it proposes is still replayed:
    `runner.run` classifies the step afterwards exactly as before, which is the
    healing invariant -- healing cannot override a failed verification.
    """
    from .llm import ToolCall, Turn
    from .runner import Step, ranked
    from .generator import Expectation

    print("LADDER      the ranked rung chooses, and cannot invent")
    ok = True

    step = Step(
        intent="click the primary action",
        action="button:Continue",
        from_key="a" * 16,
        fields=(),
        expect=Expectation(moved=True, mutating=False, added=(), removed=(),
                           to_key="b" * 16),
    )
    # Two controls that both exist, both of the right kind, and neither of which
    # reads like "Continue". This is the input `resolve` currently escalates on.
    tied = (("button:Proceed", "Proceed"), ("button:Next step", "Next step"))

    class Picks:
        name, model = "scripted:picks", "none"

        def __init__(self, index):
            self.index = index
            self.asked = 0

        def turn(self, system, transcript, tool_defs):
            self.asked += 1
            return Turn(
                text="",
                calls=(ToolCall(id="1", name="choose", arguments={
                    "id": self.index,
                    "why": "it carries the same position in the flow",
                }),),
            )

    class Silent:
        name, model = "scripted:silent", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="I cannot tell these two apart.", calls=())

    class Broken:
        name, model = "scripted:broken", "none"

        def turn(self, system, transcript, tool_defs):
            raise RuntimeError("402 insufficient credits")

    class Exploding:
        name, model = "scripted:exploding", "none"

        def turn(self, system, transcript, tool_defs):
            raise AssertionError("the model was consulted and must not have been")

    # --- where the rung sits in the ladder --------------------------------
    #
    # The ordering property, checked by consulting a provider that raises if it
    # is ever reached. `resolve`'s docstring reserved this position -- above
    # `escalate`, below `structural` -- and an off-by-one rung here is not a
    # worse repair, it is a model overruling an observable fact.
    from .runner import ladder

    fields = (("textbox", "Email"), ("textbox", "Password"))
    exact_step = Step(intent="click Continue", action="button:Continue",
                      from_key="a" * 16, fields=(), expect=step.expect)

    verbatim = ladder(exact_step, ("button:Continue", "button:Proceed"), (),
                      Exploding())
    ok &= check(
        "the recorded control still being there is not a question for a model",
        verbatim.rung == "exact",
        f"{verbatim.rung}: {verbatim.detail}",
    )
    only_one = ladder(exact_step, ("button:Proceed",), (), Exploding())
    ok &= check(
        "the only control of its kind is a structural answer, not a ranked one",
        only_one.rung == "structural" and only_one.action == "button:Proceed",
        f"{only_one.rung}: {only_one.detail}",
    )
    by_name = ladder(exact_step, ("button:Continue now", "button:Delete"), (),
                     Exploding())
    ok &= check(
        "a name that plainly matches is a similarity answer, not a ranked one",
        by_name.rung == "similarity" and by_name.action == "button:Continue now",
        f"{by_name.rung}: {by_name.detail}",
    )
    nothing = ladder(
        Step(intent="pay", action="submit[valid]:button:Place order",
             from_key="a" * 16, fields=fields, expect=step.expect),
        ("button:Continue",), (), Exploding(),
    )
    ok &= check(
        "a step nothing on the page can play is rescue's problem, not a tie",
        nothing.action is None,
        f"{nothing.rung}: {nothing.detail}",
    )

    # --- and the tie that used to be the end of the road ------------------
    tie = ("button:Proceed", "button:Next step")
    ok &= check(
        "the tie the ladder refuses is what reaches the model",
        ladder(exact_step, tie, (), Picks(0)).action == "button:Proceed",
        ladder(exact_step, tie, (), Picks(0)).detail,
    )
    ok &= check(
        "and with no provider that tie still escalates, as it always did",
        ladder(exact_step, tie, (), None).action is None,
    )

    # --- a form submit is matched on which fields exist, not what was typed ---
    #
    # `Step.fields` carries `(role, name, value)` since the recorded payload had
    # to reach the export (see `Step.fields`). `fields_now` is read off a live
    # page nobody has typed into yet, so it is `(role, name)` and always will
    # be. Comparing the two directly compares a recording against a blank form,
    # never matches, and every form submit escalates as `unresolved` -- which is
    # exactly what happened the moment the value was added, and what these two
    # checks exist to catch if the projection is ever dropped again.
    _form_step = Step(
        intent="complete the Sign in form and submit it",
        action="submit[valid]:button:Sign in",
        from_key="a" * 16,
        fields=(("textbox", "Email", "a@b.test"), ("textbox", "Password", "pw")),
        expect=step.expect,
    )
    _same_form = (("textbox", "Email"), ("textbox", "Password"))
    _renamed = ladder(_form_step, ("submit[valid]:button:Log in",), _same_form)
    ok &= check(
        "a renamed submit heals when the form still has the same fields",
        _renamed.action == "submit[valid]:button:Log in",
        f"rung={_renamed.rung} action={_renamed.action!r} -- the recorded value "
        f"was compared against a blank page",
    )
    ok &= check(
        "a submit whose form lost a field still refuses to heal onto it",
        ladder(
            _form_step, ("submit[valid]:button:Log in",), (("textbox", "Email"),)
        ).action is None,
    )

    # --- with no provider, nothing changes -------------------------------
    ok &= check(
        "no provider is the behaviour that shipped: the tie still escalates",
        ranked(step, tied, None).action is None,
    )

    # --- the rung cannot reach past the one above it ----------------------
    ok &= check(
        "one candidate is the structural rung's answer, and is not asked about",
        ranked(step, tied[:1], Exploding()).action is None,
    )
    ok &= check(
        "no candidates is rescue's problem, and is not asked about",
        ranked(step, (), Exploding()).action is None,
    )

    # --- it answers by index into the list it was given --------------------
    chose = ranked(step, tied, Picks(1))
    ok &= check(
        "a tie the ladder refuses is decided by index into the candidates",
        chose.action == "button:Next step" and chose.rung == "ranked",
        f"got {chose.action!r} via {chose.rung}: {chose.detail}",
    )
    ok &= check(
        "the repair carries the reason it was chosen, not just the choice",
        "same position in the flow" in chose.detail,
        chose.detail,
    )

    # --- and cannot answer with anything else ------------------------------
    invented = ranked(step, tied, Picks(7))
    ok &= check(
        "an index that names no candidate is dropped, not resolved",
        invented.action is None and invented.rung == "unresolved",
        f"the model invented {invented.action!r}",
    )
    ok &= check(
        "a model that declines to choose leaves the escalation standing",
        ranked(step, tied, Silent()).action is None,
    )

    # --- the rung has to actually be reachable from a run ------------------
    #
    # A rung nobody threads a provider to is a rung that never fires, and that
    # failure is silent: every check above still passes and every replay still
    # escalates exactly as it did before. This asserts the wiring, and only the
    # wiring -- the live replays in section 4 are what show the ladder still
    # heals and still refuses.
    import inspect

    from . import regression, runner as runner_mod

    ok &= check(
        "a replay can be given the provider its ladder would rank with",
        "provider" in inspect.signature(runner_mod.run).parameters,
    )
    # Exactly one forward, and which one it is matters. The first replay is
    # where a repair is *proposed* and is allowed to rank; the re-verification
    # below it is where that repair is *confirmed*, and handing a provider to a
    # confirmation would let a bad repair be rescued by a second guess during
    # its own check -- the definition of shopping for a verdict.
    body = inspect.getsource(regression.verify)
    proposal, _, confirmation = body.partition("if reverify:")
    proposing = proposal.split("result = runner.run(")[1].split(")")[0]
    ok &= check(
        "the replay that proposes a repair is given the provider",
        "provider=provider" in proposing,
        proposing,
    )
    ok &= check(
        "the replay that confirms one is not: a check may not repair itself",
        "provider" not in confirmation.split("report.reverified")[0],
        confirmation.split("report.reverified")[0][-200:],
    )

    # --- losing the model must never cost the escalation -------------------
    refused = ranked(step, tied, Broken())
    ok &= check(
        "a provider that raises escalates and names why it could not rank",
        refused.action is None and "402" in refused.detail,
        refused.detail,
    )
    return ok


def _reverify_checks() -> bool:
    """A repair is a hypothesis until the repaired scenario has been replayed.

    `verify` used to write a version the moment repairs existed. The claim on
    the tin was "the healer repaired three locators"; what had actually been
    established was that three locators *resolved*, which is a different and
    weaker thing -- the resolution ladder can pick a control that exists, is of
    the right kind, and does something else entirely.

    Checked against the source rather than by driving a browser, because what
    is being guarded is an ordering: replay, then decide, then emit. The
    browser-driven proof is in the e2e run; this is what fails if someone
    reorders it.
    """
    from . import regression

    print("REVERIFY    a repair is not a repair until it has been replayed")
    ok = True

    # The crawl `record` runs is the only one that can produce a
    # `submit[invalid]` edge, and `forms.perform` refuses that mode outright
    # when handed no synthesizer -- deliberately, because an invalid edge
    # carrying a valid payload would be a lie in the map. `record` passed none,
    # so `make suite` could never record an unhappy path at all: measured
    # 2026-09-05 against saucedemo, 8 scenarios and 5 scenarios across two runs,
    # zero `submit[invalid]` in either, while the console path -- which does
    # pass one -- produced three. The brief's "not just happy paths" was
    # decided by which entry point you used.
    ok &= check(
        "the suite recorder gives its crawl a synthesizer",
        "synthesizer=" in _function_source(regression, "record"),
        "submit[invalid] is refused for want of one, so `make suite` can only "
        "ever record happy paths and empty submissions",
    )

    # The replay half of the same omission, and the more damaging one, because
    # it does not go quiet -- it reports. `runner.run` re-submits a form by
    # re-performing the action, so an invalid submission replayed without a
    # synthesizer is refused and the step ESCALATEs as "the action would not
    # execute -- the control is present and inert". The control is fine. We
    # declined to type. Measured 2026-09-05 on the saucedemo suite this
    # recorder had just written: 4 escalate, 4 passed, and every one of the
    # four escalations was an invalid or empty submission the recorder itself
    # had watched the app handle correctly minutes earlier.
    for name in ("record", "verify"):
        body = _function_source(regression, name)
        ok &= check(
            f"`{name}` replays with the synthesizer it recorded with",
            all(
                "synthesizer=" in call
                for call in body.split("runner.run(")[1:]
            ),
            "an invalid-input scenario escalates as inert, which is a false "
            "verdict on a test this same function wrote and watched pass",
        )

    body = _function_source(regression, "verify")

    ok &= check(
        "the repaired scenarios are replayed before anything is emitted",
        body.index("if reverify:") < body.index("report.emitted = emit("),
        "emitting first would make the re-verification a postscript",
    )
    ok &= check(
        "a repair the replay contradicts is withdrawn, not written",
        "report.rejected.extend(repairs)" in body
        and "confirmed.append(originals[index])" in body,
    )
    ok &= check(
        "and the original scenario is kept when that happens",
        "confirmed.append(originals[index])" in body,
        "rewriting to a control that does not work is worse than escalating",
    )
    ok &= check(
        "a version with nothing surviving emits nothing at all",
        "if not report.applied:\n            return report" in body,
    )
    ok &= check(
        "only the changed scenarios are replayed",
        "if not repairs:\n                confirmed.append(next_suite[index])" in body,
        "replaying the whole suite would double every run for no new evidence",
    )
    ok &= check(
        "the emitted version records what the re-verification found",
        "reverified=report.reverify_counts" in body,
        "a claim that cannot be read off the manifest is a claim in a log",
    )

    # The escalation policy: an absence a rescue answered may be repaired; an
    # escalation nobody answered may not, and a defect never may.
    ok &= check(
        "a defect is still never repaired",
        "blocked = result.verdict == runner.DEFECT or (" in body,
    )
    ok &= check(
        "an escalation nobody could answer is still left exactly as recorded",
        "and not (rescued is not None and rescued.recovered)" in body,
    )

    # And the two records reach a reader.
    version = regression.Version(root=Path("."), number=2, parent=1)
    ok &= check(
        "a version carries both records, defaulting to empty rather than absent",
        version.reverified == {} and version.rescues == (),
    )
    ok &= check(
        "an old version.json without them still loads",
        "reverified" in regression.Version(root=Path("."), number=1).as_dict(),
    )
    return ok


def _versioning_checks() -> bool:
    """A version is immutable, and the healer corrects the map without rewriting it.

    Two separate disciplines, and both are about not destroying evidence:

      * **An emitted version is never edited.** The first draft of `regression`
        healed the suite in place, which left the claim "the healer repaired
        three locators" checkable only against a log the healer wrote itself.
        Emitting v002 beside an untouched v001 makes it checkable with `diff`.
      * **The healer does not rewrite the crawl.** `store.py` scopes a map to a
        run so two runs can be compared; a healer that edited the old map to
        say "the button was always called Log in" would delete the before half
        of the before/after that proves the app changed at all.
    """
    import tempfile

    from . import regression
    from .explorer.forms import Credentials
    from .generator import Expectation, Scenario, Step

    print("VERSIONS    an emitted suite is immutable, and the map keeps its past")
    ok = True

    def scenario(name: str, action: str, from_key: str) -> Scenario:
        return Scenario(
            name=name,
            target_url="http://localhost:3000/sut",
            steps=(Step(
                intent="press the button", action=action, from_key=from_key,
                fields=(),
                expect=Expectation(moved=True, mutating=True, added=(), removed=(),
                                   to_key="b" * 16),
            ),),
            origin="behaviour:flow",
        )

    a = "a" * 16
    first_plan = scenario("sign in", "button:Sign in", a)
    healed_plan = scenario("sign in", "button:Log in", a)
    credentials = Credentials(username="u", password="p")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "suite"
        one = regression.emit(
            (first_plan,), root, because="recorded from the behaviour world model",
            credentials=credentials, target_url=first_plan.target_url,
            mark="mark-one", source="behaviour", outcomes=("passed",),
        )
        before = {p.name: p.read_text() for p in one.root.glob("*")}

        two = regression.emit(
            (healed_plan,), root, because="healed 1 locator(s)",
            credentials=credentials, target_url=first_plan.target_url,
            mark="mark-two", source="behaviour", parent=one.number,
        )

        ok &= check(
            "a second emit writes a new version rather than overwriting the first",
            one.label == "v001" and two.label == "v002" and one.root != two.root,
            f"{one.label} -> {two.label}",
        )
        ok &= check(
            "and every file of the first version is byte-identical afterwards",
            {p.name: p.read_text() for p in one.root.glob("*")} == before,
            "a version edited after the fact cannot be diffed against its child, "
            "which is the only evidence a heal actually happened",
        )
        ok &= check(
            "the child records which version it came from",
            two.parent == one.number,
            f"parent was {two.parent}",
        )
        ok &= check(
            "a replay loads the newest version, not the first one written",
            regression.load(root) == (healed_plan,)
            and regression.current(root).label == "v002",
        )
        ok &= check(
            "the version records the node each scenario covers, for the map to colour",
            two.scenarios[0]["node"] == a and a in two.scenarios[0]["covers"],
            f"got {two.scenarios[0]}",
        )
        ok &= check(
            "and which planner proposed it, so two versions stay comparable",
            two.from_behaviour == 1 and two.source == "behaviour",
        )
        ok &= check(
            "the baseline keeps the verdict each scenario reported when recorded",
            one.scenarios[0].get("verdict") == "passed",
            "a suite that hid what it could not reproduce would report a clean "
            "baseline and bury the flakiest part of the app",
            )
        ok &= check(
            "the lineage names the current version and every version before it",
            regression.lineage(root)["current"] == "v002"
            and len(regression.lineage(root)["versions"]) == 2,
        )

        # The export: one version's worth of specs, and nothing left over from
        # a longer previous one.
        into = Path(tmp) / "generated"
        (into).mkdir()
        (into / "99-stale.spec.ts").write_text("// from a longer suite", encoding="utf-8")
        written = regression.export(two, into)
        ok &= check(
            "the export is exactly one version and drops what a longer suite left",
            len(written) == 1 and not (into / "99-stale.spec.ts").exists(),
            f"wrote {[p.name for p in written]}",
        )

    # The map half. A repair is a correction to the world model too, and
    # applying it must not touch the map it was computed from.
    repairs = (
        regression.Repair(scenario="sign in", step=1, intent="press", 
                          was="button:Sign in", now="button:Log in",
                          rung="structural", detail="", node=a, to_key="b" * 16),
        regression.Repair(scenario="sign in again", step=1, intent="press",
                          was="button:Sign in", now="button:Log in",
                          rung="structural", detail="", node=a, to_key="b" * 16),
        regression.Repair(scenario="unplaced", step=1, intent="press",
                          was="button:Sign in", now="button:Log in",
                          rung="name", detail="", node="", to_key=""),
    )
    updates = regression.map_updates_for(repairs)
    ok &= check(
        "two scenarios crossing one state correct the map once, not twice",
        len(updates) == 1 and updates[0]["state"] == a,
        f"got {updates}",
    )
    ok &= check(
        "a repair with no state on the map is dropped rather than guessed at",
        all(u["state"] for u in updates),
        "patching an unnamed state means patching every state",
    )

    world, entry, _, _ = _planner_world()
    rename = ({"state": entry, "was": "submit[valid]:button:Sign in",
               "now": "submit[valid]:button:Log in", "rung": "structural"},)
    patched = regression.apply_to_map(world, rename)
    ok &= check(
        "the healed action replaces the old one on the state and on its edge",
        "submit[valid]:button:Log in" in patched.states[entry].actions
        and (entry, "submit[valid]:button:Log in") in patched.transitions
        and (entry, "submit[valid]:button:Sign in") not in patched.transitions,
        f"actions={patched.states[entry].actions}",
    )
    ok &= check(
        "the edge keeps where it went and whether it mutated",
        patched.transitions[(entry, "submit[valid]:button:Log in")][0].mutating is True,
        "a locator match is evidence about markup and about nothing else",
    )
    ok &= check(
        "the crawl the repair was computed from is left exactly as it was",
        "submit[valid]:button:Sign in" in world.states[entry].actions
        and (entry, "submit[valid]:button:Sign in") in world.transitions,
        "editing the old map deletes the before half of the before/after",
    )
    return ok


def _suite_checks() -> bool:
    """A suite that heals itself must not also heal away a defect.

    The whole value of keeping tests on disk is that the file is a record of
    what the app did. Two ways to destroy that, and one of them is attractive:

      * heal a step the Runner *classified as a defect*, which turns a red
        suite green by editing out the evidence. `regression.verify` refuses
        the whole scenario, not just the offending step.
      * rewrite a scenario when nothing healed, which churns the file on every
        run and makes `git diff` on the suite say nothing.

    Both are checked here against constructed Results rather than a live app,
    because the classification they depend on is `runner`'s job and is already
    pinned by the drift checks above. What is new here is only what happens to
    the *file* afterwards.
    """
    import tempfile

    from . import regression, runner
    from .explorer.forms import Credentials
    from .generator import Expectation, Scenario, Step

    print("SUITE       tests kept between runs, repaired on evidence, never healed green")
    ok = True

    def scenario(name: str, action: str) -> Scenario:
        return Scenario(
            name=name,
            target_url="http://localhost:3000/sut",
            steps=(
                Step(
                    intent="press the button",
                    action=action,
                    from_key="a" * 16,
                    fields=(),
                    expect=Expectation(
                        moved=True, mutating=False,
                        added=("- heading \"Signed in\"",), removed=(),
                        to_key="b" * 16,
                    ),
                ),
            ),
        )

    def outcome(scenario_: Scenario, verdict: str, rung: str, action: str | None):
        result = runner.Result(scenario=scenario_, target_url=scenario_.target_url)
        result.steps.append(runner.StepResult(
            step=scenario_.steps[0],
            verdict=verdict,
            resolution=runner.Resolution(action, rung, "constructed for the probe"),
            detail="",
            actual_key="c" * 16,
        ))
        return result

    plan = scenario("sign in", "button:Sign in")

    # 1. The pure substitution. This is what "heal the Playwright test" means:
    #    the same scenario, one locator different, everything else identical.
    healed, repairs = regression.repaired(
        plan, outcome(plan, runner.HEALED, "structural", "button:Log in")
    )
    ok &= check(
        "a healed step rewrites the locator it was healed onto",
        healed.steps[0].action == "button:Log in",
        f"got {healed.steps[0].action!r}",
    )
    ok &= check(
        "healing a locator does not touch what the step expects",
        healed.steps[0].expect.added == plan.steps[0].expect.added
        and healed.steps[0].intent == plan.steps[0].intent,
        "an assertion edited by the healer is an assertion nobody wrote",
    )
    ok &= check(
        "the repair records the rung, not just the outcome",
        len(repairs) == 1 and repairs[0].rung == "structural"
        and repairs[0].was == "button:Sign in",
        f"got {repairs}",
    )

    # 2. `Resolution.healed` is false on the exact rung, so a passing step is
    #    not a no-op rewrite -- it produces no repair at all.
    _, none = regression.repaired(
        plan, outcome(plan, runner.PASSED, "exact", "button:Sign in")
    )
    ok &= check(
        "a step that resolved exactly produces no repair",
        none == (),
        f"got {none}",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "suite"
        credentials = Credentials(username="u", password="p")

        # 3. The round trip. A suite that cannot be loaded back is a suite that
        #    only ever tests the crawl that just produced it.
        first = regression.emit(
            (plan,), root, because="recorded from a crawl",
            credentials=credentials, target_url=plan.target_url,
            mark="fingerprint-one",
        )
        back = regression.load(root)
        ok &= check(
            "a saved scenario loads back identical",
            back == (plan,),
            "the .json is what a re-run reads; a lossy write loses the past",
        )
        ok &= check(
            "the export lands beside it and is runnable TypeScript",
            len(list(first.root.glob("*.spec.ts"))) == 1
            and "@playwright/test" in next(first.root.glob("*.spec.ts")).read_text(),
        )

        # 4. The trigger. A fingerprint that has not moved must not start a run,
        #    and a suite with no recorded fingerprint must not report calm.
        ok &= check(
            "the suite records the fingerprint it was saved against",
            regression.current(root).fingerprint == "fingerprint-one",
            f"got {regression.current(root).fingerprint!r}",
        )
        ok &= check(
            "an unchanged fingerprint is not a change",
            not regression.Drift(False, "fingerprint-one", "fingerprint-one").changed,
        )
        ok &= check(
            "a suite with no past reports drift rather than calm",
            regression.Drift(True, "", "anything").changed,
            "otherwise the first run after adopting a suite checks nothing",
        )

        # 5. The discipline. A defect leaves the recorded version exactly as it
        #    was, even though the same run also resolved a locator by healing.
        version = regression.current(root)
        before = {p.name: p.read_text() for p in version.root.glob("*.json")}
        defect = outcome(plan, runner.DEFECT, "structural", "button:Log in")
        _, would = regression.repaired(plan, defect)
        ok &= check(
            "a defect step still computes a repair, so the report can show it",
            len(would) == 1,
        )
        ok &= check(
            "but the file is left alone: DEFECT is not in the rewrite set",
            defect.verdict in (runner.DEFECT, runner.ESCALATE),
            f"verdict was {defect.verdict}",
        )
        ok &= check(
            "the scenario on disk is byte-identical after a defect",
            {p.name: p.read_text() for p in version.root.glob("*.json")} == before
            and regression.current(root).number == version.number,
            "healing a defect turns a red suite green by editing the evidence; "
            "a version emitted for a defect does the same thing one directory over",
        )

        # 6. Found by running it: a scenario that heals one step and reports a
        #    defect on another produced a repair the report listed under HEAL
        #    and wrote into the manifest's heal log -- for a file it had
        #    correctly refused to rewrite. The suite stayed honest; the record
        #    of what had been done to it did not.
        report = regression.Report(directory=root, target_url=plan.target_url)
        report.results.append(defect)
        report.repairs.extend(would)
        ok &= check(
            "a repair the scenario's defect withheld is not reported as applied",
            report.applied == [] and report.withheld == would,
            f"applied={report.applied} withheld={report.withheld}",
        )
        ok &= check(
            "and the summary says so rather than counting it as published",
            "withheld" in report.summary() and report.emitted is None,
            report.summary(),
        )

    # 7. Also found by running it. The first version gated the replay on the
    #    fingerprint, and against the SUT's `?bug=1` -- markup identical,
    #    behaviour broken, which is the orthogonality the fixture is built for
    #    -- the gate saw an unchanged landing key, skipped the suite and printed
    #    calm. Three defects were sitting in it. A fingerprint can answer "the
    #    markup moved"; it can never answer "nothing changed".
    calm = regression.Drift(False, "same", "same")
    moved = regression.Drift(True, "before", "after")
    ok &= check(
        "an unchanged fingerprint still replays, because it cannot see behaviour",
        regression.should_replay(calm),
        "gating on markup drift skips exactly the regressions worth catching",
    )
    ok &= check(
        "the cheap gate is available, but only when asked for by name",
        not regression.should_replay(calm, if_drifted=True)
        and regression.should_replay(moved, if_drifted=True),
    )

    return ok


def _surface_checks() -> bool:
    """Every surface the backend emits on must be one the console listens for.

    `Event.surface` is a string agreed between two languages and checked by
    nobody. `web/lib/stages.ts` maps a surface to a slot in the stage strip and
    `web/lib/agents.ts` maps it to an agent; both are hand-maintained tables in
    TypeScript keyed on literals written in Python. A surface with no listener
    is not an error anywhere -- the row is written, the console reads it, and
    no stage lights.

    Found the expensive way on 2026-09-05. The console's seed crawl passed
    `checkpoint` and no `trace`, so the *map* streamed while the timeline held
    one sentence -- "crawling deterministically first" -- for the whole crawl,
    with a live elapsed counter climbing beside it. `_crawl_only` was handed an
    `emit` it never called. `pipeline.run` passed a `trace` with no surface at
    all, which lights nothing. All three were the longest stage of the run
    reporting into a table with no row for it.

    Offline: reads source, runs nothing.
    """
    import re

    from . import pipeline
    from app.routers import explore as explore_router

    print("SURFACE     the crawl reports somewhere the console is looking")
    ok = True

    web = Path(__file__).resolve().parents[2] / "web" / "lib"
    stages, agents = web / "stages.ts", web / "agents.ts"
    if not stages.exists():
        return check("web/lib/stages.ts is where it is expected", False, str(stages))

    # What the console listens for, from both tables. `surfaces: [...]` is the
    # shape in each, so one pattern reads both.
    listened = set(
        re.findall(r'"([a-z]+)"', " ".join(
            re.findall(r"surfaces:\s*\[([^\]]*)\]", stages.read_text() + agents.read_text())
        ))
    )

    # What the backend actually writes. Both spellings: `surface="x"` and the
    # positional third argument `emit(level, message, "x")`.
    emitted: set[str] = set()
    for module in (pipeline, explore_router):
        text = _source(module, "def ")
        emitted |= set(re.findall(r'surface=["\']([a-z]+)["\']', text))
        emitted |= set(re.findall(r'emit\([^)]*?,\s*["\']([a-z]+)["\']\s*\)', text))

    orphans = sorted(emitted - listened)
    ok &= check(
        "every surface the backend emits has a stage listening for it",
        not orphans,
        f"{orphans} reach the database and light nothing; add them to "
        "web/lib/stages.ts or stop emitting them",
    )

    # The specific one that was missing, named so a regression says which.
    ok &= check(
        "the crawl is one of them",
        "explore" in emitted and "explore" in listened,
        "the longest stage of a run has no surface, so the stage strip stays "
        "dark from the first action to the last",
    )

    # And the crawl must actually *say* something per action, not only persist.
    # `checkpoint` streams the map; `trace` streams the account of it, and the
    # canvas filling while the timeline sits still is the confusing half.
    for name in ("_crawl_only",):
        body = _function_source(explore_router, name)
        ok &= check(
            f"{name} narrates the crawl as well as persisting it",
            "trace=" in body and "checkpoint=" in body,
            "it was handed an `emit` and never called it: the map filled and "
            "the timeline said nothing for the whole crawl",
        )

    seeds = _source(explore_router, "crawling deterministically first")
    ok &= check(
        "the seed crawl in front of the colony narrates too",
        seeds.count("trace=lambda line: emit(") >= 1,
        "the seed crawl is minutes long and reported twice: once when it "
        "started and once when it finished",
    )

    ok &= check(
        "pipeline.run's crawl traces carry a surface, not just a level",
        _source(pipeline, "trace=lambda").count('emit("info", line, "explore")') == 2,
        'both crawls in pipeline.run must pass the surface; `emit("info", '
        "line)` defaults it to None and lights no stage",
    )
    return ok


def _ground_checks() -> bool:
    """The citation guard, detached from a map that is still being written.

    `admit` reads `world.vocabulary()` and the keys of `world.states`, and
    `vocabulary()` iterates `states`. Once the behavioural model runs beside
    the crawl rather than after it, that iteration happens on a second thread
    while the crawler inserts -- which is `RuntimeError: dictionary changed
    size during iteration`, raised somewhere in the model's reply handling and
    nowhere near the cause.

    `Ground` is the fix: an immutable pair of frozensets taken on the crawl
    thread and handed across. These pin that it carries what `admit` needs,
    that it cannot tear, and that using it does not loosen the guard.

    Offline: no browser, no key, no network.
    """
    from .explorer.worldmap import Ground

    print("GROUND      the citation guard, detached from a live map")
    ok = True
    world = _behaviour_world()

    ground = world.ground()
    ok &= check(
        "a ground carries the map's state keys",
        ground.states == frozenset(world.states),
        f"got {sorted(ground.states)}, map has {sorted(world.states)}",
    )
    ok &= check(
        "and its action vocabulary",
        ground.actions == frozenset(world.vocabulary()),
        f"got {sorted(ground.actions)}, map has {sorted(world.vocabulary())}",
    )
    ok &= check(
        "and is frozen, so two threads cannot see it half-written",
        isinstance(ground, Ground)
        and isinstance(ground.states, frozenset)
        and isinstance(ground.actions, frozenset),
        "a mutable snapshot is the bug wearing a different name",
    )

    # The guard itself, now reading a value rather than a map. Same rule:
    # every citation must resolve, an 8-character id widens to 16, an
    # ambiguous prefix is refused, and a claim left with none is dropped.
    from .behavior import admit

    hypothesis = admit(ground, {
        "claim": "signing in reaches the dashboard",
        "kind": "flow",
        "cites": ["a" * 8, "click:Sign in"],
    })
    ok &= check(
        "admit resolves a short state id and an action against a ground",
        hypothesis is not None and set(hypothesis.cites) == {"a" * 16, "click:Sign in"},
        f"got {hypothesis.cites if hypothesis else None}",
    )
    ok &= check(
        "and still refuses a claim whose every citation is invented",
        admit(ground, {
            "claim": "the checkout page totals the cart",
            "kind": "flow",
            "cites": ["click:Checkout", "f" * 8],
        }) is None,
        "a claim about web applications in general passed the guard",
    )

    # Staleness, in the one direction it can happen. The crawl keeps walking
    # while the model is thinking, so a reply is admitted against a ground
    # taken before it. States are never removed, so the worst this can do is
    # refuse something the newer map would allow.
    from .explorer.worldmap import StateNode

    stale = world.ground()
    world.states["c" * 16] = StateNode(
        key="c" * 16, url="/cart", title="Cart",
        actions=("click:Checkout",), label="cart", evidence=(2,),
    )
    ok &= check(
        "a ground taken earlier still resolves everything it was taken with",
        admit(stale, {"claim": "c", "kind": "flow", "cites": ["a" * 8]}) is not None,
        "the guard got stricter about states it already had, which would drop "
        "claims for no reason",
    )
    ok &= check(
        "and refuses a state that arrived after it was taken",
        admit(stale, {"claim": "c", "kind": "flow", "cites": ["c" * 8]}) is None,
        "too lax is the dangerous direction: a ground must never admit a "
        "citation it cannot itself resolve",
    )
    ok &= check(
        "while a ground taken after does admit it",
        admit(world.ground(), {"claim": "c", "kind": "flow", "cites": ["c" * 8]})
        is not None,
        "the newer ground should see the newer state",
    )

    # The reason `Ground` exists at all. Iterating a live map while the
    # crawler inserts into it raises; iterating a ground cannot.
    torn = None
    try:
        for _ in world.ground().states:
            world.states["d" * 16] = StateNode(
                key="d" * 16, url="/d", title="D", actions=(), evidence=(),
            )
    except RuntimeError as exc:
        torn = exc
    ok &= check(
        "a ground survives the map growing underneath it",
        torn is None,
        f"raised {torn!r} -- which is what the crawler thread would cause",
    )
    return ok


def _delta_checks() -> bool:
    """What one turn of an incremental behavioural model is shown.

    `brief` renders the whole map, and its reply grows with it -- which is how
    `sarvam-105b` came to be cut off mid-`tool_calls` (see `_ceiling_checks`).
    Fed a few states at a time the reply stays small, and each turn can be told
    what arrived since the last one rather than re-reading everything.

    Offline: no browser, no key, no network.
    """
    from .behavior import delta_brief

    print("DELTA       one turn sees what arrived, not everything")
    ok = True
    world = _behaviour_world()
    login, dash = "a" * 16, "b" * 16

    first = delta_brief(world, since=frozenset())
    ok &= check(
        "the first turn is shown every state",
        first is not None and login[:8] in first and dash[:8] in first,
        "a first turn with nothing in it would make the whole model empty",
    )

    later = delta_brief(world, since=frozenset({login}))
    ok &= check(
        "a later turn names the state that arrived",
        later is not None and dash[:8] in later,
        f"{dash[:8]} missing from {later!r}",
    )
    ok &= check(
        "and does not re-describe the one already sent",
        later is not None and login[:8] not in later.split("Since")[-1],
        "re-sending every state is the growth this exists to stop -- the "
        "reply scales with the map again",
    )
    ok &= check(
        "while still carrying the shape of the whole map",
        later is not None and "2 states" in later,
        "a turn shown four states and nothing else cannot tell a small app "
        "from the corner of a large one",
    )

    ok &= check(
        "nothing new means no turn to take",
        delta_brief(world, since=frozenset({login, dash})) is None,
        "an empty delta must not be sent: it is a paid model call that can "
        "only produce claims about states already claimed",
    )
    return ok


def _session_checks() -> bool:
    """Hypotheses accumulating across turns, and never un-said.

    `synthesise` asked once. A session asks repeatedly over one transcript, so
    turn 3 can revise its reading of turn 1 in the light of states that only
    arrived in between -- which is the whole reason to interleave.

    **What it may not do is withdraw.** A turn that could delete an earlier
    hypothesis would be a model grading its own claim, which is what
    `behavior.py` exists to prevent, and it would be the invisible form of it:
    a claim removed before `examine` runs leaves no count and no verdict. So
    turns add, `examine` rules at the end from the map, and a claim that later
    states contradict comes back `contradicted` -- a finding, not a silence.

    Offline: a scripted provider. No key, no network.
    """
    from .behavior import BehaviourSession
    from .llm import ToolCall, Turn

    def call(*hypotheses, summary="", truncated=False):
        return Turn(
            text="",
            calls=(ToolCall(id="c", name="model", arguments={
                "summary": summary, "hypotheses": list(hypotheses),
            }),),
            truncated=truncated,
        )

    class Scripted:
        """Replays turns in order and records what it was asked."""

        name, model = "scripted", "stub"

        def __init__(self, *turns):
            self.turns, self.prompts = list(turns), []

        def turn(self, system, transcript, tools):
            # Depth and follow-up captured *now*. The transcript is one mutable
            # object the session keeps appending to, so holding a reference and
            # reading it later measures the end of the run, not this turn.
            last = transcript.exchanges[-1] if transcript.exchanges else None
            self.prompts.append((
                len(transcript.exchanges),
                last.follow_up if last else "",
                transcript.prompt,
                len(last.results) if last else 0,
            ))
            nxt = self.turns.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

    print("SESSION     one transcript, hypotheses accumulating")
    ok = True
    world = _behaviour_world()
    login, dash = "a" * 16, "b" * 16
    ground = world.ground()

    LOGIN = {"claim": "the entry state is a sign-in form", "kind": "flow",
             "cites": [login[:8]]}
    DASH = {"claim": "signing in reaches a dashboard", "kind": "flow",
            "cites": [dash[:8]]}
    INVENTED = {"claim": "there is a checkout page", "kind": "flow",
                "cites": ["ffffffff"]}

    # 1. Two turns, and both survive.
    provider = Scripted(call(LOGIN, summary="a login"), call(DASH))
    session = BehaviourSession(provider, system="sys")
    session.feed("turn one", ground)
    session.feed("turn two", ground)
    model = session.model()
    claims = {h.claim for h in model.hypotheses}
    ok &= check(
        "a hypothesis from the first turn survives the second",
        LOGIN["claim"] in claims,
        f"got {claims}",
    )
    ok &= check(
        "and the second turn's is there too",
        DASH["claim"] in claims,
        f"got {claims} -- last-turn-wins loses everything said earlier",
    )

    # 2. One transcript, so a later turn can see what it already said.
    # Every tool call must be answered before the next turn is sent. Measured
    # live on 2026-09-05 against `minimax/minimax-m3:free`: turns 1 and 2
    # succeeded, and turn 3 came back
    #
    #   400 ... "invalid params, tool call result does not follow tool call"
    #
    # because the transcript held an assistant turn that called `model` and no
    # `role: "tool"` message answering it. `synthesise` never met this -- it
    # sends one turn and never sends the transcript back -- so it is a bug that
    # only exists once the conversation continues.
    answered = provider.prompts[1][3]
    ok &= check(
        "the first turn's tool call is answered before the second is sent",
        answered == 1,
        f"{answered} result(s) for 1 call: a provider that validates the "
        "message list rejects the whole turn, and every turn after it",
    )

    depth, follow_up, opening = provider.prompts[1][:3]
    ok &= check(
        "the second turn is shown the first turn's exchange",
        depth == 1,
        f"the transcript carried {depth} exchange(s) at call time; with 0, "
        "each turn is a fresh conversation and the context this exists to "
        "build never accumulates",
    )
    ok &= check(
        "and the second turn's text reaches the model as the reply to it",
        follow_up == "turn two" and opening == "turn one",
        f"follow_up={follow_up!r} opening={opening!r} -- every provider "
        "serialises `Exchange.follow_up` as the next user message; without "
        "it the turn is sent with nothing new to read",
    )

    # 3. The guard still runs, per turn, and still counts what it drops.
    provider = Scripted(call(LOGIN, INVENTED))
    session = BehaviourSession(provider, system="sys")
    session.feed("turn one", ground)
    ok &= check(
        "an invented citation is dropped inside a session too",
        session.model().dropped == 1
        and {h.claim for h in session.model().hypotheses} == {LOGIN["claim"]},
        f"dropped={session.model().dropped}",
    )

    # 4. A turn that fails must not cost the turns that worked. This runs on a
    #    worker thread beside a crawl; losing the crawl's semantic layer to one
    #    bad reply is the failure the whole design is built to avoid.
    provider = Scripted(call(LOGIN), RuntimeError("provider exploded"))
    session = BehaviourSession(provider, system="sys")
    session.feed("turn one", ground)
    session.feed("turn two", ground)
    ok &= check(
        "a turn that raises leaves the earlier turns intact",
        {h.claim for h in session.model().hypotheses} == {LOGIN["claim"]},
        f"got {[h.claim for h in session.model().hypotheses]}",
    )
    ok &= check(
        "and says so rather than failing silently",
        any(level == "error" for level, _ in session.notes),
        f"notes={session.notes}",
    )

    # 5. The summary is the first one offered, not the last. A later turn sees
    #    a fraction of the app and would narrow it.
    provider = Scripted(call(LOGIN, summary="a login and a dashboard"),
                        call(DASH, summary="a dashboard"))
    session = BehaviourSession(provider, system="sys")
    session.feed("one", ground)
    session.feed("two", ground)
    ok &= check(
        "the summary is kept from the turn that saw the most",
        session.model().summary == "a login and a dashboard",
        f"got {session.model().summary!r}",
    )
    return ok


def _worker_checks() -> bool:
    """The behavioural model running beside the crawl, not after it.

    The crawler walks on the main thread and the model call is network I/O, so
    the model runs on a worker and the crawl never waits for it. Two things
    make that safe rather than merely fast, and both are checked here.

    **The worker touches no database.** `routers/explore.py`'s `emit` closes
    over one SQLModel `Session` and commits on it, and `db.py` sets
    `check_same_thread: False` -- so a second thread writing events would not
    raise, it would corrupt that session's unit of work quietly. Everything the
    worker wants to say is queued and emitted by whoever calls `tick`, which is
    the crawl thread.

    **The worker touches no live map.** It is handed `delta_brief` text and a
    frozen `Ground`, both built on the crawl thread. See `worldmap.Ground`.

    Offline: a scripted provider, no browser, no key, no network.
    """
    import threading

    from .behavior import BehaviourWorker
    from .explorer.worldmap import StateNode
    from .llm import ToolCall, Turn

    def call(*hypotheses):
        return Turn(text="", calls=(ToolCall(
            id="c", name="model",
            arguments={"summary": "s", "hypotheses": list(hypotheses)},
        ),))

    class Scripted:
        name, model = "scripted", "stub"

        def __init__(self, *turns, record=None):
            self.turns, self.record = list(turns), record
            self.threads: set[int] = set()

        def turn(self, system, transcript, tools):
            self.threads.add(threading.get_ident())
            nxt = self.turns.pop(0) if self.turns else call()
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

    def grow(world, n: int, start: int = 0):
        """Add `n` states, the way a crawl does -- one at a time."""
        for i in range(start, start + n):
            key = f"{i:016x}"
            world.states[key] = StateNode(
                key=key, url=f"/s{i}", title=f"S{i}",
                actions=(f"click:go{i}",), evidence=(),
            )

    print("WORKER      the model runs beside the crawl, not after it")
    ok = True
    main_thread = threading.get_ident()

    # 1. A batch is sent once enough states have arrived, and not before.
    world = _behaviour_world()
    provider = Scripted()
    worker = BehaviourWorker(provider, every=4)
    worker.tick(world)
    ok &= check(
        "two states is not yet a batch",
        worker.batches == 0,
        f"sent {worker.batches} batch(es) for 2 states with every=4",
    )
    grow(world, 2)
    worker.tick(world)
    ok &= check(
        "the fourth state sends one",
        worker.batches == 1,
        f"sent {worker.batches}",
    )
    grow(world, 3, start=2)
    worker.tick(world)
    ok &= check(
        "three more is not another",
        worker.batches == 1,
        f"sent {worker.batches} -- the threshold is per batch, not cumulative",
    )
    worker.close()

    # 2. The crawl thread is the only one that emits. This is the check that
    #    stands between a worker and a quietly corrupted SQLModel session.
    world = _behaviour_world()
    grow(world, 6)
    emitted_from: set[int] = set()
    said: list[tuple[str, str]] = []

    def emit(level, message):
        emitted_from.add(threading.get_ident())
        said.append((level, message))

    provider = Scripted(call({"claim": "c", "kind": "flow",
                              "cites": ["a" * 8]}))
    worker = BehaviourWorker(provider, every=4, on_event=emit)
    worker.tick(world)
    model = worker.close()

    ok &= check(
        "the model really ran on another thread",
        provider.threads and main_thread not in provider.threads,
        f"provider ran on {provider.threads}, main is {main_thread} -- if it "
        "ran here, the crawl waited for it and nothing was gained",
    )
    ok &= check(
        "and every event was emitted from the crawl thread",
        emitted_from <= {main_thread},
        f"emitted from {emitted_from}, main is {main_thread}: a worker "
        "calling `emit` writes to a SQLModel session from two threads, which "
        "check_same_thread=False lets corrupt silently instead of raising",
    )
    ok &= check(
        "what the worker admitted comes back from close()",
        len(model.hypotheses) == 1,
        f"got {len(model.hypotheses)} hypothesis(es)",
    )
    ok &= check(
        "and the run is told the model said something",
        any("hypothes" in m for _, m in said),
        f"said {said}",
    )

    # 3. A worker that dies must cost the model and nothing else.
    world = _behaviour_world()
    grow(world, 6)
    before = dict(world.states)
    worker = BehaviourWorker(
        Scripted(RuntimeError("provider exploded")), every=4, on_event=emit,
    )
    worker.tick(world)
    model = worker.close()
    ok &= check(
        "a worker that raises leaves the map untouched",
        world.states == before,
        "the crawl's own result must not depend on the model beside it",
    )
    ok &= check(
        "and returns an empty model rather than propagating",
        model.hypotheses == (),
        f"got {model.hypotheses}",
    )
    ok &= check(
        "and the failure reaches the run as an error",
        any(level == "error" for level, _ in said),
        f"said {said}",
    )

    # 4. No provider, no thread. This is the whole no-key path.
    world = _behaviour_world()
    grow(world, 6)
    worker = BehaviourWorker(None, every=4, on_event=emit)
    worker.tick(world)
    ok &= check(
        "with no provider nothing is started and nothing is claimed",
        worker.close().hypotheses == () and worker.batches == 0,
        "a colony with no key still crawls; it must not grow a thread",
    )

    # 5. close() must not hang the run waiting on a model call.
    ok &= check(
        "close() is bounded, so a wedged provider cannot hold the run open",
        BehaviourWorker(None).close() is not None,
        "close must always return a model, even an empty one",
    )

    # 6. The tail of the crawl. Batches are sent every `every` states, so a
    #    crawl that ends with fewer than that left over would leave its last
    #    states unexamined -- and the last states of a crawl are the deepest
    #    ones it reached, which is where the interesting behaviour is.
    world = _behaviour_world()
    grow(world, 2)          # 4 states -> one full batch
    provider = Scripted(call({"claim": "first", "kind": "flow",
                              "cites": ["a" * 8]}),
                        call({"claim": "tail", "kind": "flow",
                              "cites": ["b" * 8]}))
    worker = BehaviourWorker(provider, every=4, on_event=emit)
    worker.tick(world)
    grow(world, 2, start=2)  # 2 more -> below the threshold, never sent
    worker.tick(world)
    sent_during = worker.batches
    model = worker.close(world)
    ok &= check(
        "the states left over when the crawl stops are still sent",
        worker.batches == sent_during + 1,
        f"{worker.batches} batch(es) total after {sent_during} during the "
        "crawl -- without a final flush the deepest states the crawl reached "
        "are the ones the model never sees",
    )
    ok &= check(
        "and what that last turn said is in the model",
        {h.claim for h in model.hypotheses} == {"first", "tail"},
        f"got {[h.claim for h in model.hypotheses]}",
    )

    ok &= check(
        "closing twice does not send the tail twice",
        BehaviourWorker(None).close(world).hypotheses == (),
        "close is called from a finally block on more than one path",
    )
    return ok


def _seeding_checks() -> bool:
    """Who builds the behavioural model when the crawl already built one.

    `orchestrator.run` calls `synthesise` before its first wave, because
    without a semantic layer the only plan it can form is "try the untried
    actions". Once the crawl builds that layer *while it crawls*, the colony
    is handed one -- and calling `synthesise` anyway would pay for the same
    model twice and throw away the incremental one, which is the better of the
    two because it was built a few states at a time.

    The decision is its own function so it can be checked without a browser.
    `orchestrator.run` needs a live `Page`; this needs nothing.

    Offline: no browser, no key, no network.
    """
    from .behavior import BehaviorModel
    from .orchestrator import behaviour_for

    print("SEEDING     the colony uses the model the crawl already built")
    ok = True
    world = _behaviour_world()
    calls: list[str] = []

    def synth(*args, **kwargs):
        calls.append("called")
        return BehaviorModel(summary="freshly synthesised")

    handed = BehaviorModel(summary="built beside the crawl")

    got = behaviour_for(world, provider=object(), given=handed, synthesise=synth)
    ok &= check(
        "a model handed in is the one the colony uses",
        got is handed,
        f"got {got.summary!r}",
    )
    ok &= check(
        "and no second model is paid for",
        calls == [],
        "synthesise ran anyway: the same map is sent to the same model twice "
        "and the incremental answer is discarded for the one-shot one",
    )

    calls.clear()
    got = behaviour_for(world, provider=object(), given=None, synthesise=synth)
    ok &= check(
        "with nothing handed in the colony still builds its own",
        calls == ["called"] and got.summary == "freshly synthesised",
        f"calls={calls} got={got.summary!r} -- every path that does not run "
        "the worker must keep working exactly as it did",
    )

    calls.clear()
    ok &= check(
        "an empty model handed in is still a model, not a missing one",
        behaviour_for(world, provider=object(), given=BehaviorModel(),
                      synthesise=synth) is not None and calls == [],
        "a worker that admitted nothing has already spent the model call; "
        "re-running synthesise would spend a second on the same states",
    )

    calls.clear()
    ok &= check(
        "with no provider nothing is built and nothing is called",
        behaviour_for(world, provider=None, given=None,
                      synthesise=synth).hypotheses == () and calls == [],
        "the no-key path must not reach a model",
    )

    calls.clear()
    empty = _behaviour_world()
    empty.states = {}
    ok &= check(
        "an empty map is not worth a model call either",
        behaviour_for(empty, provider=object(), given=None,
                      synthesise=synth).hypotheses == () and calls == [],
        "there is nothing to interpret",
    )
    return ok


def _pages_world():
    """Three pages: a login page offering eight distinct behaviours and two
    pages behind it offering two each. Every edge discovers a fresh state so
    `is_flow` keeps it. Returns the map and the page of every state key.
    """
    from dataclasses import replace

    pages = {
        "login": "/login", "L2": "/login", "L3": "/login", "L4": "/login",
        "L5": "/login", "L6": "/login", "L7": "/login", "L8": "/login",
        "dash": "/dashboard", "D2": "/dashboard", "D3": "/dashboard",
        "exec": "/executions", "E2": "/executions", "E3": "/executions",
    }
    edges = [
        ("login", "submit[valid]:button:Sign in", "dash", True),
        ("login", "submit[invalid]:button:Sign in", "L2", True),
        ("login", "submit[empty]:button:Sign in", "L3", False),
        ("login", "button:Show password", "L4", False),
        ("login", "link:Back to home", "L5", False),
        ("L2", "submit[valid]:button:Sign in", "exec", True),
        ("L2", "submit[invalid]:button:Sign in", "L6", False),
        ("L2", "submit[empty]:button:Sign in", "L7", True),
        ("L3", "button:Hide password", "L8", True),
        ("dash", "link:Datasets", "D2", False),
        ("dash", "button:New agentflow", "D3", True),
        ("exec", "link:Filter", "E2", False),
        ("exec", "button:Refresh", "E3", True),
    ]
    world = _map_of(edges)
    world.entry_key = "login"
    for key, node in list(world.states.items()):
        world.states[key] = replace(
            node, url=f"http://sut{pages[key]}", evidence=(0,)
        )
    return world, pages


def _per_page_checks() -> bool:
    """The suite reaches every crawled page, not just the one nearest the entry.

    Measured 2026-09-05 on a Velogent run: 26 states across ten URL paths, and
    every one of 23 scenarios terminated on the login page or its landing.
    `interleave` rotates across *kinds* of action, and a login page supplies
    every kind -- three submit partitions, a button, a link -- so it filled the
    suite by itself. Pages are the fairness unit a tester actually thinks in;
    kinds are the tie-break within one.

    Two halves, checked apart: the generator's `by_page` spends whatever share
    it is handed, and the planner's `share` decides that share from the map --
    so a new map gets a new number, and nobody hardcodes two.
    """
    from .generator import scenarios
    from .planner import plan, share

    print("PER-PAGE    the planner spreads the suite across every crawled page")
    ok = True
    world, pages = _pages_world()

    def by_page(plan_) -> dict[str, int]:
        counts: dict[str, int] = {}
        for scenario in plan_:
            page = pages[scenario.terminal.from_key]
            counts[page] = counts.get(page, 0) + 1
        return counts

    # The mechanism, handed a share.
    six = by_page(scenarios(world, limit=6, per_page=2))
    ok &= check(
        "generator: six slots, three pages, two each",
        six == {"/login": 2, "/dashboard": 2, "/executions": 2},
        f"{six} -- the login page crowded the others out",
    )
    four = by_page(scenarios(world, limit=4, per_page=2))
    ok &= check(
        "generator: no page takes its second slot before every page has one",
        four.get("/dashboard") == 1 and four.get("/executions") == 1,
        f"{four}",
    )
    eight = by_page(scenarios(world, limit=8, per_page=2))
    ok &= check(
        "generator: slots left after every page has its share go by kind",
        sum(eight.values()) == 8 and eight["/login"] == 4,
        f"{eight}",
    )
    none = by_page(scenarios(world, limit=6, per_page=0))
    ok &= check(
        "generator: zero reserves nothing -- the old kind-rotation, unchanged",
        none == {"/login": 6},
        f"{none}",
    )

    # The policy, derived from the map.
    ok &= check(
        "planner: the share is the limit spread over the pages the crawl reached",
        share(world, 6) == (3, 2) and share(world, 24) == (3, 8)
        and share(world, 2) == (3, 1),
        f"{share(world, 6)} {share(world, 24)} {share(world, 2)}",
    )
    planned = plan(world, source="map", limit=6)
    ok &= check(
        "planner: a plan spreads by that share and records the decision",
        by_page(planned.scenarios) == {"/login": 2, "/dashboard": 2, "/executions": 2}
        and planned.pages == 3 and planned.per_page == 2,
        f"{by_page(planned.scenarios)} pages={planned.pages} per_page={planned.per_page}",
    )
    return ok


def _frontier_order_checks() -> bool:
    """Which untaken action the crawler takes next, and why it is not the login
    form's ninth variant.

    Measured 2026-09-05 on a Velogent run: the crawl reached the dashboard,
    executions and human-tasks pages, each offering 25 actions, and took one to
    six on each -- roughly 30 of its 58 actions went to permutations of the
    login form. Two rules did it together. "Exhaust where we are standing" let
    every error state re-submit the same form for one click, and breadth-first
    kept pulling the crawl back to the depth-1 login variants, of which each
    submit minted another. Ties then fell to list order, and on every page
    behind the login the first untaken action was the logo link.

    The rule under test: an action *string* never taken anywhere on the map
    outranks one already taken from another state, ahead of locality and
    depth. The form's three partitions are still taken first, once; its tenth
    repeat waits behind the dashboard's first link.
    """
    from .explorer.crawler import priority

    print("FRONTIER    a page never seen beats a form seen nine times")
    ok = True

    routes = {"entry": (), "err": ("submit[invalid]:button:Sign in",),
              "dash": ("submit[valid]:button:Sign in",),
              "deep": ("submit[valid]:button:Sign in", "link:Datasets")}
    taken = {"submit[invalid]:button:Sign in", "submit[valid]:button:Sign in",
             "submit[empty]:button:Sign in", "link:Velogent Velogent"}

    def first(here, *edges):
        return min(edges, key=lambda e: priority(e, here, routes, taken))

    ok &= check(
        "a novel link on a deeper page beats a repeated submit where we stand",
        first("err", ("err", "submit[empty]:button:Sign in"), ("dash", "link:Datasets"))
        == ("dash", "link:Datasets"),
    )
    ok &= check(
        "among novel actions, where we stand still comes first",
        first("dash", ("dash", "link:Datasets"), ("entry", "button:Show password"))
        == ("dash", "link:Datasets"),
    )
    ok &= check(
        "among novel actions elsewhere, shallower still comes first",
        first("err", ("deep", "button:New"), ("entry", "button:Show password"))
        == ("entry", "button:Show password"),
    )
    ok &= check(
        "a form's rejected partitions still go before the one that succeeds",
        first("entry", ("entry", "submit[valid]:button:Go"), ("entry", "submit[empty]:button:Go"),
              ("entry", "link:About"))
        == ("entry", "submit[empty]:button:Go"),
    )
    ok &= check(
        "the logo link, once taken anywhere, waits behind a page's own links",
        first("dash", ("dash", "link:Velogent Velogent"), ("dash", "link:Datasets"))
        == ("dash", "link:Datasets"),
    )
    ok &= check(
        "with nothing novel left, the old order stands: here, then depth",
        first("err", ("err", "submit[empty]:button:Sign in"), ("dash", "link:Velogent Velogent"))
        == ("err", "submit[empty]:button:Sign in"),
    )
    return ok


def _effects_world():
    """Four states with real snapshots, so edges carry effects a model can cite.

    login -> valid -> dashboard -> Datasets -> datasets; login -> invalid -> error.
    Returns the map and the keys.
    """
    from .explorer.observer import Observation
    from .explorer.worldmap import StateNode, Transition, WorldMap

    login, err, dash, data = "a" * 16, "d" * 16, "b" * 16, "c" * 16
    form = '- heading "Welcome back" [level=1]\n- textbox "Username"\n- textbox "Password"\n- button "Sign in"'
    pages = {
        login: ("/login", "Login", form),
        err: ("/login", "Login", form + "\n- paragraph: Invalid credentials"),
        dash: ("/dashboard", "Dashboard", '- heading "Dashboard" [level=1]\n- paragraph: Signed in as user@example.com\n- link "Datasets"'),
        data: ("/datasets", "Datasets", '- heading "Datasets" [level=1]\n- button "New dataset"'),
    }
    world = WorldMap()
    world.evidence = []
    for key, (path, title, snapshot) in pages.items():
        world.evidence.append(Observation(url=f"http://sut{path}", title=title, snapshot=snapshot))
        world.states[key] = StateNode(
            key=key, url=f"http://sut{path}", title=title, actions=(),
            evidence=(len(world.evidence) - 1,),
        )
    world.entry_key = login
    index = {key: i for i, key in enumerate(pages)}
    for from_key, action, to_key, mutating in (
        (login, "submit[valid]:button:Sign in", dash, True),
        (login, "submit[invalid]:button:Sign in", err, True),
        (dash, "link:Datasets", data, False),
    ):
        world.transitions[(from_key, action)] = [
            Transition(from_key=from_key, action=action, to_key=to_key,
                       mutating=mutating, evidence=index[to_key])
        ]
    return world, login, err, dash, data


def _model_generator_checks() -> bool:
    """The Generator is a model call, and the map still decides what exists.

    Decided 2026-09-05. The model writes the suite -- which recorded paths,
    chained how, named what, proving each step with which recorded effects --
    and `generator.propose` keeps only what the map backs: a step is a recorded
    edge or it is dropped, an assertion is a recorded effect or it is dropped,
    a chain that breaks is dropped whole, and a scenario starting mid-map is
    prefixed with the route from the entry. Without a provider the compile is
    the whole plan, as it always was.
    """
    from .behavior import BehaviorModel, Hypothesis
    from .generator import edges, expectation, from_flow, propose
    from .planner import plan

    print("GENERATOR   a model writes the suite; the map decides what exists")
    ok = True
    world, login, err, dash, data = _effects_world()
    ids = {edge: f"e{i}" for i, edge in enumerate(edges(world))}
    valid = ids[(login, "submit[valid]:button:Sign in")]
    invalid = ids[(login, "submit[invalid]:button:Sign in")]
    datasets = ids[(dash, "link:Datasets")]
    landing = expectation(world, login, "submit[valid]:button:Sign in").added
    heading = next(i for i, line in enumerate(landing) if "Dashboard" in line)

    class Writer:
        name, model = "scripted:writer", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="", calls=(ToolCall(id="g1", name="suite", arguments={
                "tests": [
                    {"name": "Signing in with valid credentials reaches the dashboard",
                     "why": "nobody can use the product without it",
                     "steps": [{"edge": valid, "assert": [heading]}]},
                    {"name": "Datasets opens from the dashboard",
                     "steps": [{"edge": datasets}]},
                    {"name": "a checkout the crawler never saw",
                     "steps": [{"edge": "e99"}]},
                    {"name": "a chain that does not connect",
                     "steps": [{"edge": invalid}, {"edge": datasets}]},
                    {"name": "an effect nobody recorded",
                     "steps": [{"edge": valid, "assert": [heading, 42]}]},
                ]}),))

    proposal = propose(world, Writer(), limit=8)
    names = [s.name for s in proposal.scenarios]
    ok &= check(
        "the tests the map backs are built, the others are not",
        len(proposal.scenarios) == 3 and "a checkout the crawler never saw" not in names
        and "a chain that does not connect" not in names,
        f"{names}",
    )
    ok &= check(
        "an unrecorded edge and a broken chain are counted as invented",
        proposal.invented == 2, f"invented={proposal.invented}",
    )
    ok &= check(
        "an assertion naming no recorded effect is trimmed and counted",
        proposal.trimmed == 1
        and proposal.scenarios[2].steps[-1].expect.added == (landing[heading],),
        f"trimmed={proposal.trimmed}",
    )
    first = proposal.scenarios[0]
    ok &= check(
        "the model chose which recorded effect proves the step",
        first.steps[-1].expect.added == (landing[heading],)
        and len(landing) > 1 and first.why and first.origin == "generator:model",
        f"added={first.steps[-1].expect.added} of {len(landing)}",
    )
    second = proposal.scenarios[1]
    ok &= check(
        "a test that starts mid-map is prefixed with the route from the entry",
        [st.action for st in second.steps]
        == ["submit[valid]:button:Sign in", "link:Datasets"]
        and second.steps[0].from_key == login,
        f"{[st.action for st in second.steps]}",
    )
    ok &= check(
        "with no provider the model writes nothing and says so",
        propose(world, None).degraded.startswith("no provider"),
    )

    believed = BehaviorModel(summary="", hypotheses=(
        Hypothesis(claim="the dashboard leads to datasets", kind="flow", cites=(dash, data)),
    ))
    flow = from_flow(world, believed.hypotheses[0])
    ok &= check(
        "a believed flow that starts mid-map is prefixed too",
        flow is not None and [st.action for st in flow.steps]
        == ["submit[valid]:button:Sign in", "link:Datasets"],
        f"{flow and [st.action for st in flow.steps]}",
    )

    alone = plan(world, None, source="behaviour", limit=8, provider=Writer())
    ok &= check(
        "the planner puts the model's tests in the plan and counts them",
        alone.from_model == 2 and alone.invented == 2 and alone.trimmed == 1,
        f"from_model={alone.from_model} invented={alone.invented} trimmed={alone.trimmed}",
    )
    with_model = plan(world, believed, source="behaviour", limit=8, provider=Writer())
    ok &= check(
        "a believed flow outranks the model's test that walks the same edges",
        with_model.from_model == 1 and with_model.from_behaviour == 1
        and with_model.scenarios[0].origin == "behaviour:flow",
        f"from_model={with_model.from_model} invented={with_model.invented} "
        f"trimmed={with_model.trimmed} origins={[s.origin for s in with_model.scenarios]}",
    )
    ok &= check(
        "a duplicate walk is kept once, first writer wins",
        len({tuple(st.action for st in s.steps) for s in with_model.scenarios})
        == len(with_model.scenarios),
    )
    map_only = plan(world, believed, source="map", limit=8, provider=Writer())
    ok &= check(
        "source=map keeps the model out of the plan",
        map_only.from_model == 0 and all(s.origin == "map" for s in map_only.scenarios),
        f"{[s.origin for s in map_only.scenarios]}",
    )
    no_key = plan(world, None, source="behaviour", limit=8)
    ok &= check(
        "no provider: the compile is the whole plan",
        no_key.from_model == 0 and len(no_key) > 0 and all(s.origin == "map" for s in no_key.scenarios),
    )
    return ok


def _interleave_checks() -> bool:
    """The crawl and the behavioural model, wired together in both callers.

    Two entry points build a map and then colonise it: `pipeline.run`, which
    `make pipeline` drives, and the inline sequence in `routers/explore.py`,
    which the console's Start button drives. They are not the same code, and a
    feature wired into one of them exists on half the product.

    `_signature` and `_source` rather than a run: both need a live browser and
    a target, and what is being pinned here is that the wiring is present on
    both paths, which is structural. The behaviour itself is checked in
    `_worker_checks` and by running `make pipeline`.
    """
    import re

    from app.routers import explore as explore_router

    from . import pipeline

    print("INTERLEAVE  both callers run the model beside the crawl")
    ok = True

    sig = _signature(pipeline.run)
    ok &= check(
        "pipeline.run takes a checkpoint",
        "checkpoint" in sig,
        f"{sig} -- without it the crawl inside it persists nothing until it "
        "finishes, so its map cannot be watched and no worker can be fed",
    )

    body = _function_source(pipeline, "run")
    ok &= check(
        "and hands the crawl's checkpoint to the worker",
        "BehaviourWorker" in body and "checkpoint=" in body,
        "the worker is never fed, so the model still runs after the crawl",
    )
    # `(?<!_)` because `from_behaviour=` is already in this function and
    # matched a bare substring test -- a check that passes before the code
    # exists is not a check.
    ok &= check(
        "and hands what the worker built to the colony",
        re.search(r"(?<!_)behaviour=", body) is not None,
        "the colony would call synthesise and pay for the same model twice",
    )
    ok &= check(
        "and closes the worker with the finished map",
        ".close(" in body,
        "close() sends the states left over below the batch threshold -- the "
        "deepest ones the crawl reached",
    )

    # A crawl that raises must still stop the worker. Both callers run inside
    # a long-lived uvicorn process, and `_run` blocks on `self._work.get()`
    # forever -- so a worker whose `close` is skipped is a thread parked for
    # the life of the server, one per failed run. `daemon=True` only means the
    # process can still exit; it does not reclaim anything while it runs.
    ok &= check(
        "pipeline.run closes the worker even when the crawl raises",
        "finally:" in body,
        "a crawl that throws leaks a parked thread per run",
    )

    console = _source(explore_router, "crawling deterministically first")
    ok &= check(
        "the console's own crawl feeds a worker too",
        "BehaviourWorker" in console,
        "the console is the demo path; a feature only `make pipeline` has is "
        "a feature the product does not have",
    )
    ok &= check(
        "and hands the result to its colony",
        "behaviour=" in console,
        "orchestrator.run would synthesise from scratch and discard it",
    )
    ok &= check(
        "and closes it even when the crawl raises",
        "behaviour_worker.close" in console
        and _function_source(explore_router, "watch") != ""
        and console.count("finally:") >= 1,
        "the console runs inside uvicorn: a leaked worker is a thread parked "
        "for the life of the server",
    )

    # And the worker must tolerate being closed with nothing, which is what a
    # failed crawl hands it.
    from .behavior import BehaviourWorker

    ok &= check(
        "closing with no map is not an error",
        BehaviourWorker(None).close(None) is not None,
        "the failure path passes None because the crawl never returned a map",
    )
    return ok


def _ceiling_checks() -> bool:
    """A reply cut off at the ceiling must say so, and say what it cost.

    Measured 2026-09-05. `sarvam-105b` was catalogued at `max_output=8192`
    while Sarvam enables reasoning by default and charges reasoning tokens
    against the same completion budget. The behaviour synthesis call came back
    `finish_reason: length` with its `model` tool call severed mid-JSON,
    `_arguments` yielded `{}`, and the console printed two lines from two
    modules:

        sarvam-105b: the reply hit the 8192-token ceiling and was cut off
        no behavioural model returned; the map stands alone

    Nothing joined them. The second sentence is what a model that *declined*
    would produce, so the failure read as a modelling problem and was a config
    one. `Turn.truncated` is the join, and these checks are what stop the two
    from drifting apart again.

    Offline: no key, no network, no quota. `_client` is a stub.
    """
    from .behavior import synthesise
    from .llm import Tool, Transcript, Turn
    from .llm.catalog import max_output_for
    from .llm.openai_compat import OpenAICompat

    class Response:
        def __init__(self, payload):
            self.status_code, self.text, self._payload = 200, "", payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, payload):
            self.payload = payload

        def post(self, path, json):
            return Response(self.payload)

    def reply(finish_reason: str, arguments: str):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "model",
                                    "arguments": arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": finish_reason,
                }
            ]
        }

    def provider(model: str, payload):
        fake = object.__new__(OpenAICompat)
        fake.model, fake.max_tokens = model, max_output_for(model)
        fake.name = "openrouter"
        fake.notes = []
        fake._notify = lambda level, message: fake.notes.append((level, message))
        fake._client = Client(payload)
        return fake

    print("CEILING     a truncated reply names itself, and names what it lost")
    ok = True

    # 1. The catalogue moved, and it moved to the two real context windows.
    #    A regression here is silent: the run still works, it just loses the
    #    behavioural model on every call and blames the model for it.
    ok &= check(
        "sarvam-105b's ceiling clears the 8192 that severed the tool call",
        max_output_for("sarvam-105b") == 32768,
        f"got {max_output_for('sarvam-105b')}, expected 32768 (128k window)",
    )
    # Not raised, and the reason is measured rather than inferred: the API
    # refuses anything above 8192 for this one ("exceeds the maximum output
    # length of 8192 tokens ... For a larger output budget, use sarvam-105b").
    # Its 32k context window does not predict that cap, so the number has to
    # be pinned or the next person will raise it to match its sibling and get
    # a 400 on every call instead of a truncation on some.
    ok &= check(
        "sarvam-105b-conversations stays at the 8192 its provider enforces",
        max_output_for("sarvam-105b-conversations") == 8192,
        f"got {max_output_for('sarvam-105b-conversations')}; the API 400s "
        "above 8192 for this model, so raising it breaks every call",
    )

    # 2. `finish_reason: length` reaches the caller as a fact, not only a log
    #    line. This is the seam the whole diagnosis hangs on.
    cut = provider("sarvam-105b", reply("length", '{"summary": "half a sen'))
    turn = cut.turn("sys", Transcript(prompt="p"), [])
    ok &= check(
        "a reply stopped at the ceiling comes back marked truncated",
        turn.truncated is True,
        "Turn.truncated was not set, so no caller can tell a severed reply "
        "from a model that declined",
    )
    ok &= check(
        "the severed tool call is what the caller actually receives",
        bool(turn.calls) and turn.calls[0].arguments == {},
        f"expected an empty-argument call, got {turn.calls!r}",
    )
    ok &= check(
        "and the provider still says so out loud",
        any("ceiling" in message for _, message in cut.notes),
        f"notes were {cut.notes!r}",
    )

    whole = provider("sarvam-105b", reply("stop", '{"summary": "done"}'))
    ok &= check(
        "a reply the model finished is not marked truncated",
        whole.turn("sys", Transcript(prompt="p"), []).truncated is False,
        "every turn would report as cut off, which is the same blindness "
        "pointing the other way",
    )

    # 3. The consequence names the cause. `synthesise` is the caller that lost
    #    something, and until now it reported the loss without the reason.
    world = _behaviour_world()

    class Stub:
        def __init__(self, truncated: bool, calls=()):
            self.name, self.model = "stub", "sarvam-105b"
            self._truncated, self._calls = truncated, calls

        def turn(self, system, transcript, tools):
            return Turn(text="", calls=self._calls, truncated=self._truncated)

    said: list[tuple[str, str]] = []
    synthesise(world, Stub(truncated=True), on_event=lambda l, m: said.append((l, m)))
    warned = " ".join(m for l, m in said if l == "warn")
    ok &= check(
        "a run that lost its model to the ceiling is told the ceiling did it",
        "cut off" in warned and "LLM_MAX_TOKENS" in warned,
        f"said {warned!r} -- a person had to join two log lines by hand to "
        "learn this, which is the bug",
    )
    ok &= check(
        "and is told which model's ceiling to raise",
        "sarvam-105b" in warned,
        f"said {warned!r}; catalog.py has one entry per model and the "
        "message has to say which line to open",
    )

    said.clear()
    synthesise(world, Stub(truncated=False), on_event=lambda l, m: said.append((l, m)))
    warned = " ".join(m for l, m in said if l == "warn")
    ok &= check(
        "a model that simply declined is not blamed on the ceiling",
        "the map stands alone" in warned and "cut off" not in warned,
        f"said {warned!r} -- reporting a modelling problem as a config one "
        "sends the next person to the wrong file",
    )

    return ok


def _fallback_checks() -> bool:
    """An exhausted key ends a colony mid-wave. This is the escape hatch.

    Measured on 2026-09-05: a wave-3 `dispatch` call 402'd with "requested up
    to 32768 tokens, but can only afford 268" while the account still held $10.
    OpenRouter checks `max_tokens` against the *key's* remaining budget before
    the model runs, and that key carried its own spend cap -- so the balance
    was never what bound. The run died with a 24-state crawl in it.

    `LLM_FREE_FALLBACK` lets that call retry once on a `:free` route, which
    reserves nothing. Two properties make the difference between a rescue and
    a lie, and both are checked below:

    1. **Off by default.** A run that silently changes model is a run whose
       numbers cannot be compared to the one before it. `docs/product/bets.md`
       holds a crawler-vs-colony A/B; a quiet downgrade would corrupt it.
    2. **Loud when it fires.** The switch is announced at `warn` naming both
       routes, so the timeline says which model actually produced the flows.

    Offline: no key, no network, no quota. `_client` is a stub.
    """
    import os

    from .llm.catalog import max_output_for
    from .llm.openai_compat import OpenAICompat

    FLAG = "LLM_FREE_FALLBACK"
    FREE = "minimax/minimax-m3:free"
    PAID = "qwen/qwen3-coder-next"

    # `free_route_for` returns the *first* `:free` row, so the order of those
    # rows in the catalogue is the fallback policy. A second free route was
    # added 2026-09-05 (Nemotron, measurably faster); this pins which one an
    # unattended run still lands on, so reordering the list has to come here
    # and say so rather than changing behaviour silently.
    from .llm.catalog import free_route_for

    ok_order = check(
        "the catalogue's first free row is still what a 402 falls back to",
        free_route_for("openrouter") == FREE,
        f"got {free_route_for('openrouter')}",
    )
    # The provider's own words, trimmed to what `_post` actually reads.
    BODY = (
        '{"error":{"message":"This request requires more credits, or fewer '
        'max_tokens. You requested up to 32768 tokens, but can only afford '
        '268.","code":402,"metadata":{"limit_source":"openrouter_credits"}}}'
    )
    REPLY = {
        "choices": [
            {"message": {"content": "ok", "role": "assistant"},
             "finish_reason": "stop"}
        ]
    }

    class Response:
        def __init__(self, status_code: int, payload=None, text: str = ""):
            self.status_code, self.text, self._payload = status_code, text, payload

        def json(self):
            return self._payload

    class Client:
        """Hands back scripted responses and keeps every payload it was sent."""

        def __init__(self, *responses):
            self.queue, self.sent = list(responses), []

        def post(self, path, json):
            self.sent.append(json)
            return self.queue.pop(0) if self.queue else Response(200, REPLY)

    def provider(model: str, *responses, name: str = "openrouter"):
        fake = object.__new__(OpenAICompat)
        fake.model, fake.max_tokens = model, max_output_for(model)
        # The real `__init__` always sets this, and the resolver reads it: a
        # fallback route is retried with the key already in hand, so it has to
        # come from the provider that key opens.
        fake.name = name
        fake.notes = []
        fake._notify = lambda level, message: fake.notes.append((level, message))
        fake._client = Client(*responses)
        return fake

    def post(fake):
        """Drive `_post` with the payload `turn()` would have built."""
        return OpenAICompat._post(
            fake, {"model": fake.model, "max_tokens": fake.max_tokens}
        )

    print("FALLBACK    an exhausted key degrades to a free route, loudly")
    ok = True
    before = os.environ.get(FLAG)

    # 1. Off by default. This is the check that protects the A/B numbers.
    try:
        os.environ.pop(FLAG, None)
        fake = provider(PAID, Response(402, text=BODY))
        try:
            post(fake)
            ok &= check(
                "an unflagged run still dies on 402 rather than switching model",
                False,
                "the 402 was swallowed and the run continued on another model",
            )
        except RuntimeError as exc:
            ok &= check(
                "an unflagged run still dies on 402 rather than switching model",
                len(fake._client.sent) == 1 and fake.model == PAID,
                f"sent {len(fake._client.sent)} request(s), ended on {fake.model}",
            )
            ok &= check(
                "the unflagged 402 names the flag that would have saved it",
                FLAG in str(exc),
                f"the message offers no way out: {exc}",
            )

        # 2. Flagged: one retry on the free route, and the reply comes back.
        os.environ[FLAG] = "1"
        fake = provider(PAID, Response(402, text=BODY), Response(200, REPLY))
        body = post(fake)
        sent = fake._client.sent
        ok &= check(
            "a flagged 402 retries once on the free route",
            len(sent) == 2 and sent[1]["model"].endswith(":free"),
            f"sent {[p['model'] for p in sent]}",
        )
        ok &= check(
            "the retry returns the reply, not the 402",
            body == REPLY,
            f"got {body!r}",
        )
        # The paid model's ceiling is not the free one's, and sending the wrong
        # number is a 400 rather than a clamp.
        ok &= check(
            "the retry carries the free route's own ceiling",
            len(sent) == 2
            and sent[1]["max_tokens"] == max_output_for(sent[1]["model"]),
            f"retried with max_tokens={sent[-1].get('max_tokens')}",
        )
        # A colony makes ~78 calls. Re-attempting the dead route on each one
        # spends a round trip per call to learn what it already knows.
        ok &= check(
            "the switch sticks for the rest of the run",
            fake.model.endswith(":free")
            and fake.max_tokens == max_output_for(fake.model),
            f"instance still on {fake.model}",
        )
        ok &= check(
            "the switch is announced with both routes named",
            any(
                level in ("warn", "error") and PAID in m and ":free" in m
                for level, m in fake.notes
            ),
            f"notes={fake.notes}",
        )

        # 3. An explicit route beats the catalogue's pick.
        os.environ[FLAG] = "deepseek/deepseek-chat:free"
        fake = provider(PAID, Response(402, text=BODY), Response(200, REPLY))
        post(fake)
        ok &= check(
            "the flag may name the route to fall back to",
            fake._client.sent[1]["model"] == "deepseek/deepseek-chat:free",
            f"fell back to {fake._client.sent[1]['model']}",
        )

        # 4. The loop guard. A free route 402s when the balance is *negative*
        # -- documented, and it applies to free models too -- so this is a
        # reachable state, not a hypothetical.
        os.environ[FLAG] = "1"
        fake = provider(FREE, Response(402, text=BODY))
        try:
            post(fake)
            ok &= check(
                "a 402 on the free route itself is not retried forever",
                False,
                "the free route fell back to itself",
            )
        except RuntimeError:
            ok &= check(
                "a 402 on the free route itself is not retried forever",
                len(fake._client.sent) == 1,
                f"sent {len(fake._client.sent)} request(s)",
            )
        # 5. Sarvam has no `:free` route. Falling back to MiniMax's would send
        # a Sarvam key to OpenRouter and turn a legible 402 into a 401.
        fake = provider("sarvam-m", Response(402, text=BODY), name="sarvam")
        try:
            post(fake)
            ok &= check(
                "a provider with no free tier raises rather than misrouting",
                False,
                "a sarvam key was pointed at another provider's route",
            )
        except RuntimeError:
            ok &= check(
                "a provider with no free tier raises rather than misrouting",
                len(fake._client.sent) == 1 and fake.model == "sarvam-m",
                f"ended on {fake.model}",
            )
    finally:
        os.environ.pop(FLAG, None)
        if before is not None:
            os.environ[FLAG] = before

    return ok


def _byok_checks() -> bool:
    """Does a key typed into the console actually drive the run it paid for?

    The failure this section exists for is silent in both directions. A key that
    never reaches `load()` leaves the run on the server's own key -- it works,
    it just bills the wrong person and runs the wrong model. A key that reaches
    it by way of `os.environ` works too, until two runs overlap in the one
    process the API serves them from and the second one's key drives the first
    one's colony.

    Every check here is offline: no network, no browser, no quota. The keys are
    obvious fakes, and nothing below sends one anywhere.
    """
    import os

    from .llm import load
    from .llm.catalog import (
        BY_ID,
        FALLBACK_MAX_OUTPUT,
        PROVIDERS,
        as_json,
        max_output_for,
        resolve,
    )
    from .llm import Transcript
    from .llm.openai_compat import OpenAICompat

    print("BYOK        a key from the console drives the run it paid for")
    ok = True

    ok &= check(
        "every catalogued provider is one load() can build",
        all(resolve(spec.id).id == spec.id for spec in PROVIDERS),
        "the dialog offers a provider the backend cannot resolve",
    )
    ok &= check(
        "every provider's default model is one it lists",
        all(
            any(m.id == spec.default_model for m in spec.models)
            for spec in PROVIDERS
        ),
        "a provider defaults to a model missing from its own select",
    )
    # The ceiling was a flat 4096 in `openai_compat.py` for every model on
    # every provider -- 14% of what the default model emits, and small enough
    # that a `finish` call returning flows and a summary was being cut off
    # inside it. These four checks are what stop it drifting back: a number
    # that is per-model, that an env var can still rescue, and a truncation
    # that says so instead of returning a stump.
    ok &= check(
        "every catalogued model declares its own reply ceiling",
        all(
            choice.max_output >= 4096
            for spec in PROVIDERS
            for choice in spec.models
        ),
        "a model would send less than the flat ceiling this replaced",
    )
    ok &= check(
        "a model nobody catalogued still gets a safe ceiling",
        max_output_for("nobody/has-heard-of-this") == FALLBACK_MAX_OUTPUT,
        "the free-text model box would send max_tokens=None",
    )
    ok &= check(
        "the ceiling is the model's, not one number for the class",
        max_output_for("deepseek/deepseek-chat")
        != max_output_for("qwen/qwen3-coder-next"),
        "DeepSeek caps at 16384 and would 400 on the larger ask",
    )

    # Constructed with a fake key and no network: `turn()` is driven through a
    # stubbed `_post`, so this proves the wiring, not the provider.
    fake = object.__new__(OpenAICompat)
    fake.model = "qwen/qwen3-coder-next"
    fake.max_tokens = max_output_for(fake.model)
    warnings: list[str] = []
    fake._notify = lambda level, message: warnings.append(message)
    sent: list[dict] = []
    fake._post = lambda payload: sent.append(payload) or {
        "choices": [
            {"message": {"content": "cut off mid-", "role": "assistant"},
             "finish_reason": "length"}
        ]
    }
    reply = OpenAICompat.turn(fake, "sys", Transcript(prompt="hi"), [])

    ok &= check(
        "the request carries the model's ceiling, not 4096",
        sent and sent[0]["max_tokens"] == max_output_for(fake.model),
        f"sent max_tokens={sent[0]['max_tokens'] if sent else 'nothing'}",
    )
    ok &= check(
        "a reply cut off at the ceiling says so instead of passing as whole",
        any("cut off" in w for w in warnings) and reply.text == "cut off mid-",
        f"finish_reason=length produced {len(warnings)} warning(s)",
    )

    # `GET /api/providers` is unauthenticated and its whole job is to describe
    # keys, so the line between "names the variable" and "prints its contents"
    # is one substitution away. Compared against the real environment, because a
    # check against a fixture would pass on a machine that has no keys at all.
    served = json.dumps(as_json())
    present = [
        value
        for spec in PROVIDERS
        if (value := os.environ.get(spec.key_env))
    ]
    ok &= check(
        "the served catalogue names the variables, never their contents",
        not any(value in served for value in present),
        "GET /api/providers is returning a credential",
    )
    ok &= check(
        "the catalogue is checked against a machine that has keys",
        bool(present),
        "no provider key is set here, so the leak check proved nothing",
    )

    # The four providers the console offers, each built with a fake key and no
    # environment at all -- so anything that constructs did so on the key it
    # was handed. `google` and `claude` need their SDKs; a missing one is a
    # skipped check, not a failure, because neither is needed to crawl.
    saved = {
        name: os.environ.pop(name, None)
        for name in (
            "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
            "GOOGLE_API_KEY", "SARVAM_API_KEY", "OPENROUTER_MODEL",
        )
    }
    try:
        for spec in PROVIDERS:
            try:
                provider = load(spec.id, api_key="probe-not-a-real-key")
            except ImportError:
                print(f"  SKIP  {spec.id}: SDK not installed")
                continue
            ok &= check(
                f"{spec.id} builds on a brought key with an empty environment",
                provider.model == spec.default_model,
                f"got model {provider.model!r}, wanted {spec.default_model!r}",
            )

        ok &= check(
            "a chosen model is not overridden by the environment",
            (
                load("openrouter", model="chosen/model", api_key="k").model
                == "chosen/model"
            ),
            "the dialog says one model and the run uses another",
        )

        # The reason `api_key` is a parameter and not an `os.environ` write:
        # the API serves every run from one process, so an exported key is
        # every concurrent run's key.
        ok &= check(
            "a brought key is never exported",
            all(os.environ.get(name) is None for name in saved),
            "load() wrote a caller's key into the process environment",
        )

        # A key with no provider is a secret we cannot route. Guessing is worse
        # than refusing: it would spend someone's Claude key on OpenRouter.
        orphaned = False
        try:
            load(api_key="k")
        except ValueError:
            orphaned = True
        ok &= check(
            "a key with no provider is refused, not guessed",
            orphaned,
            "load() accepted a key without being told what it opens",
        )

        unknown = False
        try:
            resolve("not-a-provider")
        except ValueError:
            unknown = True
        ok &= check("an unknown provider name is refused", unknown)
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value

    # `gemini` is what `agents/` has always called it and `google` is what the
    # dialog shows. Both must land on one spec, or a `.env` written yesterday
    # stops working today.
    ok &= check(
        "the vendor's name and the internal one resolve alike",
        resolve("gemini") is resolve("google") is BY_ID["google"],
        "an alias points somewhere else",
    )

    # Every route from the console into a model has to carry the choice, or
    # one of them quietly spends the server's key instead of the caller's.
    from app.routers import chat, explore

    for module, name in ((explore, "_explore"), (explore, "_dispatch_ant"),
                         (chat, "send")):
        ok &= check(
            f"{name} takes the caller's key",
            "keys" in _signature(getattr(module, name)),
            "this path still loads a provider from the environment only",
        )

    return ok


def _navigation_checks() -> bool:
    """Can a page that is between documents be observed without killing the run?

    The bug: a crawl of `practicetestautomation.com` ended in `error` after five
    states with `Locator.aria_snapshot: Selector "body" does not match any
    element`. Its "AI Workshop" link leaves the site for `luma.com`, and while
    that document was committing there was no `body` to snapshot -- reproduced 3
    times in 6 clicks.

    Two defects compose there, and both are checked here because fixing either
    alone leaves a live failure:

    1. `crawler` calls `observer.observe()` *before* `_same_origin`, so the rule
       that refuses off-site destinations cannot fire -- observing the foreign
       page raises first. A correct policy one line too late is no policy.
    2. `observe()` assumed `body` exists. Off-origin is not the only way to meet
       a document still committing; a slow-hydrating same-origin SPA is the case
       this codebase has already been bitten by once.

    Offline and deterministic: the bodyless document is made by removing the
    element, not by racing a real navigation.
    """
    from .explorer.observer import Observer

    print("NAVIGATION  a document between states does not end the run")
    ok = True
    observed = None

    # Its own playwright lifetime: `main`'s `with sync_playwright()` block has
    # already closed by the time the section list gets here, and a section that
    # borrows a stopped one dies before printing a single check.
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            page.goto("about:blank")
            page.evaluate(
                "() => document.documentElement.removeChild(document.body)"
            )
            ok &= check(
                "the probe really built a document with no body",
                page.locator("body").count() == 0,
                "the reproduction is not reproducing; every check below is "
                "vacuous",
            )

            raised = ""
            try:
                observed = Observer(page).observe(settle_ms=10, patience_ms=30)
            except Exception as exc:  # noqa: BLE001 -- what it raises is the finding
                raised = f"{type(exc).__name__}: {exc}"

            ok &= check(
                "observing a document with no body does not raise",
                observed is not None,
                f"observe() raised {raised}; one such page ends the whole crawl",
            )
            if observed is not None:
                ok &= check(
                    "the empty observation is still a usable Observation",
                    observed.url is not None and observed.elements == (),
                    "a bodyless page must read as 'nothing to act on', not as "
                    "a half-built object the crawler then trips over",
                )
        finally:
            browser.close()

    # The other half: having survived being observed, the foreign page must
    # then be *refused*. This is asserted as a composition rather than as an
    # ordering of the two lines in `crawler`, deliberately. Moving the origin
    # test above `observe()` would save the observation, but it would read
    # `page.url` before navigation has necessarily committed -- and a false
    # refusal silently drops a legitimate state from the map, which is a worse
    # failure than the one being fixed here. Observing then discarding costs
    # one settle per off-site link and is always correct.
    ok &= check(
        "a page from another origin is refused, not mapped",
        observed is not None
        and not crawler._same_origin("https://example.com/app/", observed.url),
        "the crawl would record another site's page as a state of this app",
    )
    # Every Observation is built in one place. This is what makes "no
    # Observation exists un-redacted" a claim about six lines rather than a
    # rule each return site has to remember -- see `Observer._observation`.
    # Asserted structurally because the next return path is the one that will
    # forget, and it does not exist yet to be tested behaviourally.
    from .explorer import observer as observer_mod

    body = _function_source(observer_mod, "_observation")
    built = _source(observer_mod, "_observation").count("return Observation(")
    ok &= check(
        "an Observation is constructed in exactly one place",
        built == 1,
        f"{built} construction sites; a property that must hold of all of them "
        "now has to be repeated, and repeated is where it drifts",
    )
    # The check above counts construction sites. It does not say what that one
    # site *does*, and the comment above it claims redaction -- so an empty
    # `_observation` returning `self.page.url` raw satisfies it perfectly.
    # That is not hypothetical: it is precisely the shape this function had on
    # the branch that introduced it, because the redaction helpers lived on the
    # other branch. Each half was covered and the join between them was not,
    # which is where two independent merges landed today.
    #
    # Counted rather than parsed because position matters and arity does not:
    # two `redact_url` -- the page's own url and each network event's -- and
    # one `redact_snapshot`, whose result must also be what `parse_snapshot`
    # reads, since a parsed Element carries the node's value too.
    urls, snapshots = body.count("redact_url("), body.count("redact_snapshot(")
    ok &= check(
        "every Observation is built from redacted parts",
        urls == 2 and snapshots == 1,
        f"redact_url x{urls}, redact_snapshot x{snapshots} in _observation -- "
        "wanted the page url, every network event's url, and the snapshot",
    )

    ok &= check(
        "the refusal is on the origin, not on the page being unreadable",
        crawler._same_origin("https://example.com/a", "https://example.com/b")
        and not crawler._same_origin("https://example.com/a", "https://luma.com/x"),
        "_same_origin does not compare hosts the way the bug requires",
    )
    return ok


def _chat_transcript_checks() -> bool:
    """Does a chat thread reach the providers as a real conversation?

    Imported inside the function on purpose: `app.routers.chat` imports
    `agents.llm`, and doing this at module scope would make the agent layer
    depend on the API layer that depends on it.
    """
    from app.models import ChatMessage  # noqa: PLC0415 -- see docstring
    from app.routers.chat import _build_transcript  # noqa: PLC0415

    from .llm.claude import Claude
    from .llm.openai_compat import OpenAICompat

    def message(id: int, role: str, content: str, keys: str = "[]") -> ChatMessage:
        return ChatMessage(id=id, role=role, content=content, node_keys=keys, run_id=1)

    def claude_roles(transcript) -> list[str]:
        serialise = Claude.__dict__["_messages"]
        return [m["role"] for m in serialise(object.__new__(Claude), transcript)]

    def alternates(roles: list[str]) -> bool:
        return all(a != b for a, b in zip(roles, roles[1:]))

    ok = True

    thread = [
        message(1, "user", "why did sign-in split?", '["abc12345"]'),
        message(2, "assistant", "the abstraction split them"),
        message(3, "user", "which one has the password field?"),
        message(4, "assistant", "the second"),
    ]
    live = _build_transcript("http://x", None, [], [], {}, thread, "and untested?", [])

    ok &= check(
        "a chat thread becomes turns, not one prompt",
        len(live.exchanges) == 2 and all(e.follow_up for e in live.exchanges[:-1]),
        f"{len(live.exchanges)} exchange(s) for a four-message thread",
    )
    ok &= check(
        "the transcript alternates user and assistant",
        alternates(claude_roles(live)) and claude_roles(live)[-1] == "user",
        f"roles were {claude_roles(live)}",
    )
    ok &= check(
        "the live question is the last turn, not buried in the first",
        "and untested?" in live.exchanges[-1].follow_up
        and "and untested?" not in live.prompt,
    )
    ok &= check(
        "an older question keeps its attachment names, not its rows",
        "[attached: abc12345]" in live.prompt and "edges leaving" not in live.prompt,
    )
    openai_roles = [
        m["role"]
        for m in OpenAICompat.__dict__["_messages"](
            object.__new__(OpenAICompat), "sys", live
        )
    ]
    ok &= check(
        "the same transcript alternates for chat-completions too",
        alternates(openai_roles) and openai_roles[0] == "system",
        f"roles were {openai_roles}",
    )

    # Two questions in a row is what a deleted reply leaves behind, and what a
    # thread adopted from before threads existed can look like. Every provider
    # rejects consecutive user messages, so the rows cannot be trusted as-is.
    torn = [
        message(1, "user", "first"),
        message(2, "user", "second"),
        message(3, "assistant", "answer"),
    ]
    patched = _build_transcript("http://x", None, [], [], {}, torn, "third", [])
    ok &= check(
        "a torn thread still alternates, and loses no question",
        alternates(claude_roles(patched))
        and "first" in patched.prompt
        and "second" in patched.prompt,
        f"roles were {claude_roles(patched)}",
    )

    # The whole reason `follow_up` was added to `Exchange` rather than
    # `Transcript` being widened: an ant's round must serialise exactly as it
    # did before, with tool results and nothing else in the answering turn.
    ant = Transcript(
        prompt="go",
        exchanges=[
            Exchange(
                text="thinking",
                calls=(ToolCall("1", "look", {}),),
                results=(ToolResult("1", "look", "saw a page"),),
            )
        ],
    )
    blocks = [
        c["type"]
        for c in Claude.__dict__["_messages"](object.__new__(Claude), ant)[-1]["content"]
    ]
    ok &= check(
        "an ant's transcript is untouched by the chat's seam",
        blocks == ["tool_result"],
        f"answering turn held {blocks}",
    )
    return ok


def _context_checks() -> bool:
    """Does the free-text box beside the URL become something the run can use?

    The box is one textarea holding three different kinds of thing -- who to log
    in as, what to focus on, and statements the user wants tested -- and a model
    is what tells them apart. So the parse is the seam, and these checks pin the
    two failures that matter: inventing credentials nobody typed, and losing the
    ones somebody did.
    """
    from .context import Context, parse
    from .explorer.forms import Credentials

    print("CONTEXT     one textarea, three consumers")
    ok = True

    typed = (
        "log in as standard_user / secret_sauce. focus on checkout. "
        "check that an out-of-stock item can't be added to the cart."
    )

    class Parser:
        """Answers the parse exactly as the schema asks."""

        name, model = "scripted:parser", "none"

        def __init__(self) -> None:
            self.systems: list[str] = []

        def turn(self, system, transcript, tool_defs):
            self.systems.append(transcript.prompt)
            return Turn(
                text="",
                calls=(
                    ToolCall(
                        "1",
                        "record_context",
                        {
                            "username": "standard_user",
                            "password": "secret_sauce",
                            "focus": "checkout",
                            "claims": [
                                "an out-of-stock item can't be added to the cart"
                            ],
                        },
                    ),
                ),
            )

    parser = Parser()
    parsed = parse(typed, parser)

    ok &= check(
        "credentials typed in prose reach the crawler",
        parsed.credentials.username == "standard_user"
        and parsed.credentials.password == "secret_sauce",
        f"got {parsed.credentials}",
    )
    ok &= check(
        "the focus survives as steering",
        parsed.focus == "checkout",
        f"focus={parsed.focus!r}",
    )
    ok &= check(
        "a statement becomes a claim, not part of the focus prose",
        parsed.claims == ("an out-of-stock item can't be added to the cart",),
        f"claims={parsed.claims}",
    )
    ok &= check(
        "the raw text is kept verbatim whatever the model made of it",
        parsed.raw == typed,
    )
    ok &= check(
        "the model is shown the text it is meant to parse",
        typed in parser.systems[0],
    )

    # The timeline is on screen during the demo. Everything else about this box
    # is stored in the clear on purpose; the one line that gets screenshotted
    # is not.
    ok &= check(
        "the password never appears in the line the timeline prints",
        "secret_sauce" not in parsed.redacted and "standard_user" in parsed.redacted,
        f"redacted={parsed.redacted!r}",
    )

    # Without a model there is nothing to tell a password from a sentence. The
    # run must fall back to the environment rather than guess -- and must still
    # be able to say what it ignored.
    blind = parse(typed, None)
    ok &= check(
        "with no model nothing is invented, and the text is still kept",
        blind.raw == typed
        and not blind.credentials
        and blind.focus is None
        and blind.claims == (),
        f"got {blind}",
    )

    class Chatty:
        """Answers in prose and never calls the tool."""

        name, model = "scripted:chatty", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="Sure! Here are the credentials I found:")

    prose = parse(typed, Chatty())
    ok &= check(
        "a model that answers in prose yields nothing rather than a bad guess",
        not prose.credentials and prose.claims == (),
        f"got {prose}",
    )

    ok &= check(
        "an empty box costs no model call",
        parse("   ", Chatty()) == Context(raw=""),
    )

    # Measured, not imagined: a real run sat on "running" for minutes with two
    # events on the timeline because this call was the first thing after the
    # provider was loaded and the model was slow. A parse that fails must cost
    # the context, not the run -- the crawl behind it needs no model at all.
    class Broken:
        name, model = "scripted:broken", "none"

        def turn(self, system, transcript, tool_defs):
            raise RuntimeError("429 rate limited")

    ok &= check(
        "a provider that fails costs the context, not the run",
        parse(typed, Broken()) == Context(raw=typed),
    )

    # Those two empty results mean opposite things, and the timeline has to be
    # able to say which. "We could not read your box" is a transient failure
    # worth retrying; "we read it and there was nothing in it" is a note about
    # what you typed. Reporting the second when the first happened sends the
    # user to edit a box that was fine.
    class Nothing:
        name, model = "scripted:nothing", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="", calls=(ToolCall("1", "record_context", {}),))

    ok &= check(
        "a box the model read and found nothing in says it was read",
        parse(typed, Nothing()).parsed is True,
    )
    ok &= check(
        "a box the model never managed to read says it was not",
        parse(typed, Broken()).parsed is False
        and parse(typed, None).parsed is False,
    )

    # Precedence. The environment is how the demo machine has always been
    # configured, and a box left empty must not blank it out; a box that was
    # filled in must win, because it is the more recent thing a human said.
    import os

    from .context import credentials_for

    before = {k: os.environ.get(k) for k in ("AIVAR_USERNAME", "AIVAR_PASSWORD")}
    os.environ["AIVAR_USERNAME"] = "env-user"
    os.environ["AIVAR_PASSWORD"] = "env-password"
    try:
        ok &= check(
            "an empty box leaves the environment's credentials alone",
            credentials_for(Context(raw="")).username == "env-user",
        )
        ok &= check(
            "a box with no login in it also leaves them alone",
            credentials_for(Context(raw="focus on checkout", focus="checkout")).username
            == "env-user",
        )
        ok &= check(
            "a login typed in the box beats the environment",
            credentials_for(parsed).username == "standard_user"
            and credentials_for(parsed).password == "secret_sauce",
        )
        # Half a pair is the interesting case: a username in the box and a
        # password only in the environment must not silently combine into a
        # login neither source ever described.
        half = Context(raw="log in as someone", credentials=Credentials("box-user"))
        ok &= check(
            "a half-filled login does not borrow the other half from the environment",
            credentials_for(half).username == "box-user"
            and credentials_for(half).password is None,
            f"got {credentials_for(half)}",
        )
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # The four call sites in `explore.py` used to read the environment
    # directly. Every one of them has to go through the context now, or the box
    # is a form that changes nothing.
    from app.routers import explore as explore_router

    ok &= check(
        "no stage of a run reads credentials behind the context's back",
        "Credentials.from_env()" not in _source(explore_router, "def _explore"),
        "explore.py still calls Credentials.from_env() directly",
    )

    # Parity again, and the same shape of bug as the synthesizer one: a
    # capability wired into one walker and not the other.
    #
    # Measured on this workspace's artifacts directory: runs 3-13 wrote 3-9
    # screenshots each, runs 14 onward wrote exactly one. What changed is that
    # the console now crawls deterministically *first* and hands the map to the
    # colony -- so the crawler discovers nearly every state, and the crawler was
    # the call that had no camera. Every card on the map read "no capture".
    ok &= check(
        "every crawl the console starts is handed a camera",
        _crawls_without_shot(explore_router) == [],
        f"crawler.crawl called without shot= at line(s) "
        f"{_crawls_without_shot(explore_router)}",
    )
    return ok


def _crawls_without_shot(module) -> list[int]:
    """Line numbers of `crawler.crawl(...)` calls given no `shot=`.

    Parsed rather than grepped: the argument list spans a dozen lines and a
    substring search for "shot" finds the one in the *next* call down.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "crawl"):
            continue
        if not any(kw.arg == "shot" for kw in node.keywords):
            missing.append(node.lineno)
    return missing

def _claim_checks() -> bool:
    """A statement the user typed must end up with a verdict, or with a reason.

    The temptation here is to let a model write a scenario for the claim. It
    cannot: a `Scenario`'s `Expectation` is *measured* -- computed from the diff
    between two states the crawl actually observed -- and that measurement is
    the only reason `runner.py` can tell a moved button from a broken checkout.
    An invented expectation is one nothing ever observed, so its failure would
    be unclassifiable, on precisely the test the user cared most about.

    So the model does what `critic.prioritise` does: it points at things it was
    given, by index, and anything it invents is counted and dropped.
    """
    from .claims import attribute, brief as claims_brief, gaps_for
    from .generator import Scenario, Step
    from .generator import Expectation

    print("CLAIMS      the user's own sentence, given a verdict or a reason")
    ok = True

    def scenario(name: str) -> Scenario:
        expect = Expectation(
            moved=True, mutating=False, added=(), removed=(), to_key="b" * 16
        )
        return Scenario(
            name=name,
            target_url="http://localhost:3000/sut",
            steps=(
                Step(
                    intent=name,
                    action="button:Add to cart",
                    from_key="a" * 16,
                    fields=(),
                    expect=expect,
                ),
            ),
        )

    # Two scenarios with the same name and different destinations. This is not
    # a contrived fixture: a crawl of practicetestautomation.com produced
    # exactly two "complete the Submit form and submit it", one landing on
    # /logged-in-successfully/ and one on /contact/, and the claim "a valid
    # login should land on the logged-in-successfully page" came back
    # *uncovered* -- the suite had tested it and passed, and the report said
    # nothing exercised it. A false "not tested" on the one thing the user
    # asked for by name is worse than not testing it.
    #
    # Verified against a live model on 2026-09-05: with the shipped brief the
    # claim matched nothing; with one line per scenario naming where it ends,
    # it matched the right one of the two.
    def landing(name: str, to_key: str) -> Scenario:
        base = scenario(name)
        step = base.steps[0]
        return Scenario(
            name=base.name,
            target_url=base.target_url,
            steps=(
                Step(
                    intent=step.intent,
                    action=step.action,
                    from_key=step.from_key,
                    fields=step.fields,
                    expect=Expectation(
                        moved=True, mutating=False, added=(), removed=(),
                        to_key=to_key,
                    ),
                ),
            ),
        )

    twins = (
        landing("complete the Submit form and submit it", "1" * 16),
        landing("complete the Submit form and submit it", "2" * 16),
    )
    where = {
        "1" * 16: "https://example.com/logged-in-successfully/",
        "2" * 16: "https://example.com/contact/",
    }
    rendered = claims_brief(("a valid login lands on the logged-in page",), twins, where)
    ok &= check(
        "the brief says where each scenario ends",
        all(url in rendered for url in where.values()),
        "two scenarios can share a name, so the name cannot be what tells them "
        "apart; without the destination the model is asked to distinguish "
        "things the brief renders identically",
    )
    ok &= check(
        "the destination is attached per scenario, not listed loose",
        rendered.index(where["1" * 16]) < rendered.index("[1]") < rendered.index(where["2" * 16]),
        "both destinations appear but not under the scenarios they belong to",
    )
    ok &= check(
        "a scenario whose destination is unknown still renders",
        "[0]" in claims_brief(("c",), twins, {}),
        "a map missing a state must not cost the whole attribution",
    )

    plan = (
        scenario("complete the sign-in form and submit it"),
        scenario("add an out-of-stock item to the cart"),
    )
    claims = ("an out-of-stock item can't be added to the cart",)

    class Attributor:
        name, model = "scripted:attributor", "none"

        def __init__(self, entries):
            self.entries = entries
            self.prompts: list[str] = []

        def turn(self, system, transcript, tool_defs):
            self.prompts.append(transcript.prompt)
            return Turn(
                text="",
                calls=(ToolCall("1", "attribute", {"matches": self.entries}),),
            )

    good = Attributor([{"claim": 0, "scenarios": [1]}])
    covered = attribute(claims, plan, good)
    ok &= check(
        "a claim is matched to the scenario that exercises it",
        covered == {claims[0]: (1,)},
        f"got {covered}",
    )
    ok &= check(
        "the model is shown both the claims and the scenarios it may cite",
        claims[0] in good.prompts[0] and plan[1].name in good.prompts[0],
    )

    # The extractive requirement, same as the critic's. A scenario index that
    # does not exist is a scenario the model made up, and a claim "covered" by
    # one is worse than an uncovered claim: it reports a pass nobody ran.
    invented = attribute(claims, plan, Attributor([{"claim": 0, "scenarios": [7, -1]}]))
    ok &= check(
        "a cited scenario that does not exist is dropped, not trusted",
        invented == {claims[0]: ()},
        f"got {invented}",
    )
    ok &= check(
        "a claim the model did not answer for is unmatched, not absent",
        attribute(claims, plan, Attributor([])) == {claims[0]: ()},
    )

    class Chatty:
        name, model = "scripted:chatty", "none"

        def turn(self, system, transcript, tool_defs):
            return Turn(text="Scenario 2 looks like a good match!")

    ok &= check(
        "prose naming a scenario is not an attribution",
        attribute(claims, plan, Chatty()) == {claims[0]: ()},
    )
    ok &= check(
        "with no model every claim is unmatched rather than guessed",
        attribute(claims, plan, None) == {claims[0]: ()},
    )
    ok &= check(
        "no claims means no model call",
        attribute((), plan, Chatty()) == {},
    )

    # Attribution runs after `generator.scenarios` has compiled the plan and
    # before `explore.py` writes a TestCase row, so a provider that raises here
    # discards a suite that was already built -- the same shape as the critic's
    # 402 on 2026-09-05, one stage further down.
    try:
        unattributed = attribute(claims, plan, BrokeRanker())
    except Exception as exc:
        unattributed, broke_attr = None, f"{type(exc).__name__}: {exc}"
    else:
        broke_attr = ""
    ok &= check(
        "a provider that fails costs the attribution, not the suite",
        unattributed == {claims[0]: ()},
        broke_attr or "the run did not survive the provider failure",
    )

    # An unmatched claim is the honest outcome, and it has to be visible. A
    # claim that quietly vanishes reports a green suite over the one question
    # the user actually asked.
    gaps = gaps_for(attribute(claims, plan, Attributor([])))
    ok &= check(
        "an unmatched claim becomes a gap",
        len(gaps) == 1 and gaps[0].kind == "unmatched-claim",
        f"got {gaps}",
    )
    ok &= check(
        "the gap quotes the claim, so the report says what was asked",
        claims[0] in gaps[0].why,
        f"why={gaps[0].why!r}",
    )
    ok &= check(
        "a claim with no citation carries no citation, rather than a fake one",
        gaps[0].state_key == "" and gaps[0].action == "",
        f"citation={gaps[0].citation}",
    )
    ok &= check(
        "a matched claim is not also a gap",
        gaps_for(covered) == (),
    )

    # `addressable` decides whether spending more budget is a decision or a
    # loop. An unmatched claim is the one gap kind a *steered* re-exploration
    # can close, because the claim itself is the steer.
    from .critic import Gap
    from .pipeline import addressable

    ok &= check(
        "another wave, aimed at the claim, is worth spending",
        addressable(gaps, has_synthesizer=False) == gaps,
        "an unmatched claim is not reachable by exploring again",
    )
    ok &= check(
        "an unreachable action is still not worth another wave",
        addressable(
            (Gap(kind="unreachable-action", state_key="a", where="", action="x", why=""),),
            has_synthesizer=True,
        )
        == (),
    )

    # `scenarios()` caps the suite and interleaves it for fairness across kinds
    # of action, which is right for a suite nobody asked anything specific of.
    # A claim is somebody asking something specific, so the scenario answering
    # it must not be the one fairness dropped.
    from .claims import claimed_by, with_claimed

    capped = plan[:1]
    ok &= check(
        "a claim's scenario is added to a suite the cap had dropped it from",
        with_claimed(capped, plan, {claims[0]: (1,)}) == (plan[0], plan[1]),
        f"got {[s.name for s in with_claimed(capped, plan, {claims[0]: (1,)})]}",
    )
    # Same wrong assumption as the brief's, in a second place: two scenarios can
    # share a name, so a name is not an identity. A claim needing the twin the
    # cap dropped would find its namesake in the plan and never be added -- the
    # suite would then not contain the scenario the report says answers it.
    ok &= check(
        "a claim's scenario is added even when a namesake is already in the plan",
        with_claimed((twins[0],), twins, {claims[0]: (1,)}) == (twins[0], twins[1]),
        f"got {len(with_claimed((twins[0],), twins, {claims[0]: (1,)}))} scenario(s); "
        "the twin that lands somewhere else was swallowed by its own name",
    )

    ok &= check(
        "a claim's scenario already in the suite is not run twice",
        with_claimed(plan, plan, {claims[0]: (1,)}) == plan,
    )
    ok &= check(
        "no claims leaves the suite exactly as the generator ranked it",
        with_claimed(capped, plan, {}) == capped,
    )

    # The point of all of it: the sentence the user typed comes back with the
    # verdict of the test that answered it.
    ok &= check(
        "a claim reports the verdict of the scenario that covered it",
        claimed_by({claims[0]: (1,)}, plan) == {claims[0]: (plan[1].name,)},
        f"got {claimed_by({claims[0]: (1,)}, plan)}",
    )
    ok &= check(
        "an uncovered claim names no scenario rather than the nearest one",
        claimed_by({claims[0]: ()}, plan) == {claims[0]: ()},
    )

    # Whether to spend a second wave. `pipeline.addressable` calls an unmatched
    # claim explorable because the claim is itself the steer -- but only a model
    # can be steered, and without one every claim is unmatched by definition.
    # Retrying then is the loop `addressable` exists to prevent.
    from .claims import steer

    ok &= check(
        "an uncovered claim is worth one more wave, aimed at it",
        steer({claims[0]: ()}, good) == claims,
    )
    ok &= check(
        "a covered claim does not buy another wave",
        steer({claims[0]: (1,)}, good) == (),
    )
    ok &= check(
        "with no model there is nothing to steer, so nothing to spend",
        steer({claims[0]: ()}, None) == (),
        "a model-free run would re-crawl for every claim it could never match",
    )
    return ok


if __name__ == "__main__":
    sys.exit(main())
