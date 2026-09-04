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

import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from . import tools
from .ant import Report, explore, instructions
from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observer
from .explorer.worldmap import WorldMap
from .llm import ToolCall, Turn
from .orchestrator import Budget, run

SUT = "http://localhost:3000/sut"
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


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


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
        result = run(
            page, SUT, Colony(),
            intent="check the sign-in flow",
            budget=Budget(max_waves=3, max_ants=2, ant_actions=2, max_seconds=120),
            credentials=CREDENTIALS,
            on_event=lambda level, message: None,
        )
        ok &= check("the colony dispatches and finishes", result.stopped == "covered")
        ok &= check("the colony names flows", bool(result.flows))
        ok &= check("the colony reports gaps honestly", bool(result.gaps))
        ok &= check("ant reports reach the orchestrator", bool(result.reports))
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

        # 3d. What is worth a test. `is_flow` is structural and lives in
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

        # 3e. The vocabulary half of the same policy. A submission the app
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
        mapped = crawl(page, SUT, CrawlBudget(max_actions=10, max_seconds=90))
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

        # `?v=2` and `?bug=1` are knobs on *our* SUT and nothing else. Appended
        # to a third-party URL they are query parameters the app ignores, so the
        # suite gets re-run against a byte-identical target and the report calls
        # the result a verification. Measured against saucedemo before this
        # check existed: 4 of 12 reported runs meant nothing.
        from .pipeline import fixture_variants

        ok &= check(
            "the SUT's fixture knobs are not appended to a third-party URL",
            fixture_variants("https://www.saucedemo.com") == ()
            and fixture_variants(SUT) == (f"{SUT}?v=2", f"{SUT}?bug=1"),
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
        ok &= check(
            "the report carries no coverage percentage",
            "%" not in written,
        )

        browser.close()

    # 7. The prompts are the tunable part; loading them must not silently break.
    print()
    for role, marker in (
        ("ant", "explorer ant"),
        ("orchestrator", "orchestrator"),
        ("critic", "coverage critic"),
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

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
