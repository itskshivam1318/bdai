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

import os
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

# The Makefile reads WEB_PORT from `.worktree-env` so several stacks can run at
# once; a probe that ignored it would test whichever checkout happened to own
# port 3000, which in a worktree is somebody else's code.
SUT = f"http://localhost:{os.environ.get('WEB_PORT', '3000')}/sut"
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

        # 4b. One picture per state, and not one more. A revisit that shoots
        #     again is invisible in the UI and quadratic in a real crawl.
        from pathlib import Path as _Path

        from .shots import shooter

        with tempfile.TemporaryDirectory() as tmp:
            shot_page = browser.new_page()
            shot_world = crawl(
                shot_page,
                SUT,
                CrawlBudget(max_actions=12, max_seconds=90),
                credentials=CREDENTIALS,
                shot=shooter(shot_page, run_id=1, root=_Path(tmp)),
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

        browser.close()

    # 5. The prompts are the tunable part; loading them must not silently break.
    print()
    for role, marker in (("ant", "explorer ant"), ("orchestrator", "orchestrator")):
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
