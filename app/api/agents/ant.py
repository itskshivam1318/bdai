"""One explorer ant: land somewhere, look around, act a few times, report, die.

    cd app/api && uv run python -m agents.ant <url> [instruction]

An ant is short-lived on purpose. It reads the shared world map, contributes to
it, and its context dies with it. That is the property that makes a colony
cheaper than a single long-running agent: the map grows without any one agent's
context growing, and no agent inherits another's confusion.

**What an ant is allowed to decide.** Which action to take, and what it means.
Nothing else. State identity, transition recording and evidence are computed by
`explorer/` underneath every `act` -- see `tools.py` for why that line is where
it is.

Prompt: `prompts/ant.md`. That file is the tunable part; this file is the
plumbing that runs it. The same markdown is shaped as a Claude Code subagent
definition, so it can be handed to a subagent that reaches these tools over a
CLI or MCP instead of in-process.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from . import tools
from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observation, Observer
from .explorer.worldmap import WorldMap
from .llm import Exchange, Provider, ToolResult, Transcript, load
from .tracing import save_transcript, start as start_tracing

PROMPTS = Path(__file__).parent / "prompts"

# Strips the YAML frontmatter that makes these files valid Claude Code subagent
# definitions. The frontmatter addresses the harness, not the model.
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def instructions(name: str) -> str:
    """Load a role's prompt. `name` is a file stem in `prompts/`."""
    return _FRONTMATTER.sub("", (PROMPTS / f"{name}.md").read_text()).strip()


@dataclass
class Report:
    """What an ant hands back. The orchestrator reads this, not the transcript."""

    start_key: str
    summary: str = ""
    branches: tuple[dict, ...] = ()
    uncertain: str = ""
    actions_taken: int = 0
    states_discovered: int = 0
    ended: str = "reported"  # reported | budget | stalled | stuck | error
    trail: list[str] = field(default_factory=list)
    transcript_path: str | None = None

    def render(self) -> str:
        lines = [
            f"ant @ {self.start_key[:8]}  "
            f"{self.actions_taken} action(s), "
            f"{self.states_discovered} new state(s), ended: {self.ended}"
        ]
        lines += [f"    {step}" for step in self.trail]
        if self.summary:
            lines += ["", f"  summary   {self.summary}"]
        if self.uncertain:
            lines.append(f"  uncertain {self.uncertain}")
        for branch in self.branches:
            lines.append(
                f"  branch    [{branch.get('priority', '?'):<6}] "
                f"{branch.get('action', '?')} -- {branch.get('why', '')}"
            )
        return "\n".join(lines)


def navigate(
    page: Page,
    observer: Observer,
    world: WorldMap,
    entry_url: str,
    target_key: str,
    credentials: Credentials,
) -> Observation | None:
    """Walk an ant to its assigned state. None if it could not be reached.

    Replay from the entry rather than `go_back()`, and *verify the landing* --
    a state can stop being reachable once data changes underneath it, and an ant
    that assumes it arrived would attribute its actions to the wrong node and
    corrupt the map for everyone.

    Deliberately a separate implementation from the one inside
    `explorer/crawler.py`. That file is under concurrent development by someone
    else; duplicating twenty lines is cheaper than two people editing one
    function. Collapse them once ownership is settled.
    """
    route = world.paths().get(target_key)
    if route is None:
        return None

    observer.start_window()
    page.goto(entry_url)
    observation = observer.observe()
    if world.record(observation) == target_key:
        return observation

    for step in route:
        observer.start_window()
        if not forms.perform(page, step, observation, credentials):
            return None
        observation = observer.observe()
        if world.record(observation) == target_key:
            return observation

    return None


def explore(
    page: Page,
    world: WorldMap,
    provider: Provider,
    *,
    entry_url: str,
    start_key: str,
    instruction: str | None = None,
    credentials: Credentials | None = None,
    budget: int = 5,
    run_id: int | None = None,
) -> Report:
    """Run one ant to completion.

    `budget` is actions, not model calls -- an ant that spends a turn thinking
    and then reports has cost one call and zero actions. Five is enough to cross
    a login form and look around behind it, and small enough that a confused ant
    is cheap.
    """
    credentials = credentials or Credentials.from_env()
    observer = Observer(page)
    report = Report(start_key=start_key)

    here = navigate(page, observer, world, entry_url, start_key, credentials)
    if here is None:
        report.ended = "stuck"
        report.uncertain = "could not reach the assigned state; it may no longer exist"
        return report

    here_key = start_key
    known_before = set(world.states)

    transcript = Transcript(
        prompt=tools.describe(
            world, here_key, here, budget_left=budget, instruction=instruction
        )
    )

    # Turns, not actions. An ant that answers in prose takes a turn and no
    # action, so a loop bounded only by `budget` never has to end: every nudge
    # costs an API call and makes no progress, silently, until the day's quota
    # is gone. That is not hypothetical -- it burned a run to 245 seconds with
    # no output and is the likeliest reason two models hit their daily caps.
    #
    # Three turns per action is generous. An ant that cannot call a tool in that
    # many attempts is not going to.
    turns = 0
    max_turns = budget * 3

    while report.actions_taken < budget and turns < max_turns:
        turns += 1
        turn = provider.turn(instructions("ant"), transcript, tools.ANT_TOOLS)

        if turn.done:
            # No tool call. The ant said something without doing anything --
            # nudge by replaying the state, rather than ending the assignment on
            # a stray sentence. Bounded by `max_turns` above.
            transcript.exchanges.append(
                Exchange(text=turn.text, opaque=turn.opaque)
            )
            transcript.exchanges.append(
                Exchange(
                    text="",
                    results=(
                        ToolResult(
                            call_id="nudge",
                            name="act",
                            content="Call act() or report(). "
                            + tools.describe(
                                world,
                                here_key,
                                here,
                                budget_left=budget - report.actions_taken,
                            ),
                        ),
                    ),
                )
            )
            continue

        results: list[ToolResult] = []
        finished = False

        for call in turn.calls:
            if call.name == "report":
                report.summary = str(call.arguments.get("summary", ""))
                report.uncertain = str(call.arguments.get("uncertain", ""))
                branches = call.arguments.get("branches") or []
                report.branches = tuple(b for b in branches if isinstance(b, dict))
                finished = True
                break

            action = str(call.arguments.get("action", "")).strip()
            node = world.states.get(here_key)

            if not node or action not in node.actions:
                results.append(
                    ToolResult(
                        call_id=call.id,
                        name=call.name,
                        content=(
                            f"{action!r} is not an action available here. Copy one "
                            f"verbatim from the list.\n\n"
                            + tools.describe(
                                world,
                                here_key,
                                here,
                                budget_left=budget - report.actions_taken,
                            )
                        ),
                    )
                )
                continue

            observer.start_window()
            if not forms.perform(page, action, here, credentials):
                report.trail.append(f"{action}  (would not run)")
                results.append(
                    ToolResult(
                        call_id=call.id,
                        name=call.name,
                        content=(
                            "That action could not be performed -- the element "
                            "did not resolve, or a form had nothing to fill. "
                            "That is a fact about the application, not your "
                            "mistake. Try something else."
                        ),
                    )
                )
                # The page may have half-changed; re-anchor before continuing.
                here = navigate(
                    page, observer, world, entry_url, here_key, credentials
                ) or here
                continue

            after = observer.observe()
            report.actions_taken += 1

            from_key = here_key
            to_key = world.connect(from_key, action, after).to_key
            report.trail.append(f"{action}  -> {to_key[:8]}")
            here, here_key = after, to_key

            results.append(
                ToolResult(
                    call_id=call.id,
                    name=call.name,
                    content=tools.outcome(
                        world,
                        from_key,
                        action,
                        after,
                        to_key,
                        budget_left=budget - report.actions_taken,
                    ),
                )
            )

        transcript.exchanges.append(
            Exchange(
                text=turn.text,
                calls=turn.calls,
                results=tuple(results),
                opaque=turn.opaque,
            )
        )

        if finished:
            break
    else:
        # Out of actions and it never reported. Everything it learned is in its
        # transcript and about to be thrown away, so ask once more with only
        # `report` offered -- an ant that explored well and forgot to write up
        # is worse than useless to the orchestrator, because it consumed budget
        # and returned an empty finding.
        # Two different endings. "budget" means it explored until it ran out of
        # actions, which is ordinary. "stalled" means it kept answering in prose
        # without calling a tool -- a prompt problem, not an exploration one,
        # and worth telling apart in the timeline.
        report.ended = "budget" if report.actions_taken >= budget else "stalled"
        transcript.exchanges.append(
            Exchange(
                text="",
                results=(
                    ToolResult(
                        call_id="final",
                        name="act",
                        content=(
                            "You are out of actions. Call report() now with "
                            "what you understood and the branches you did not "
                            "take. This is your last turn."
                        ),
                    ),
                ),
            )
        )
        try:
            final = provider.turn(
                instructions("ant"), transcript, [tools.REPORT]
            )
            for call in final.calls:
                if call.name == "report":
                    report.summary = str(call.arguments.get("summary", ""))
                    report.uncertain = str(call.arguments.get("uncertain", ""))
                    report.branches = tuple(
                        b
                        for b in (call.arguments.get("branches") or [])
                        if isinstance(b, dict)
                    )
        except Exception as exc:  # a lost write-up is not a lost crawl
            report.uncertain = f"ran out of actions and could not write up: {exc}"

    report.states_discovered = len(set(world.states) - known_before)

    # Always, even on failure. An ant that went wrong is the transcript most
    # worth reading, and it is the one a "save on success" branch would drop.
    try:
        report.transcript_path = str(
            save_transcript(
                transcript,
                run_id=run_id,
                role="ant",
                system=instructions("ant"),
                label=start_key[:8],
            )
        )
    except Exception:
        # Losing the write-up must never lose the exploration.
        pass

    return report


def main(entry_url: str, instruction: str | None = None) -> int:
    traces = start_tracing()
    provider = load()
    if traces:
        print(f"PHOENIX     {traces}")
    credentials = Credentials.from_env()
    print(f"ANT         {provider.name} / {provider.model}")
    print(f"TARGET      {entry_url}")
    print(
        "CREDENTIALS "
        + (credentials.username or "none set (AIVAR_USERNAME/AIVAR_PASSWORD)")
    )
    print()

    world = WorldMap(actions_of=None)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        world.actions_of = lambda obs: forms.available_actions(page, obs)

        observer = Observer(page)
        observer.start_window()
        page.goto(entry_url)
        entry_key = world.record(observer.observe())

        report = explore(
            page,
            world,
            provider,
            entry_url=entry_url,
            start_key=entry_key,
            instruction=instruction,
            credentials=credentials,
        )
        browser.close()

    print(report.render())
    print()
    print(world.summary())
    return 0


if __name__ == "__main__":
    sys.exit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut",
            sys.argv[2] if len(sys.argv) > 2 else None,
        )
    )
