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
                fields=(("textbox", "Card number"), ("textbox", "CVC")),
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

    print()
    return 0 if ok else 1


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
