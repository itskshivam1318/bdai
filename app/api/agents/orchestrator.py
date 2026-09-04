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

import sys
import time
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from . import tools
from .ant import Report, explore, instructions
from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observer
from .explorer.crawler import autosave
from .explorer.worldmap import WorldMap
from .llm import Exchange, Provider, ToolResult, Transcript, load
from .shots import Shot
from .tracing import save_transcript, start as start_tracing


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
        if self.gaps:
            lines.append("WHAT WE DID NOT REACH")
            lines += [f"  ! {gap}" for gap in self.gaps]
            lines.append("")
        lines += [self.world.summary()]
        return "\n".join(lines)


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
) -> Exploration:
    """Explore `entry_url` until the orchestrator is satisfied or the budget ends.

    `on_event(level, message)` is called at every decision point. It exists so
    the UI can stream what the colony is doing -- `models.Event`'s docstring
    already describes itself as "what the canvas streams to show reasoning", and
    an autonomous system that cannot show its reasoning is one nobody trusts.
    Defaults to printing.
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

    world = WorldMap(actions_of=lambda obs: forms.available_actions(page, obs))
    result = Exploration(world=world)

    observer = Observer(page)
    observer.start_window()
    page.goto(entry_url)
    entry_key = world.record(observer.observe())
    if shot is not None:
        world.attach_screenshot(entry_key, shot(entry_key))
    emit("info", f"entry {entry_url} -> state {entry_key[:8]}")

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
        )
    )
    ants_left = budget.max_ants

    for wave in range(budget.max_waves):
        if time.monotonic() > deadline or ants_left <= 0:
            break

        turn = provider.turn(system, transcript, tools.ORCHESTRATOR_TOOLS)

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

        for assignment in assignments:
            wanted = str(assignment.get("state", "")).strip()
            # The orchestrator sees 8-character ids; the map keys are 16.
            matches = [k for k in world.states if k.startswith(wanted)]
            if len(matches) != 1:
                rejected.append(
                    f"{wanted!r} is not a state in the map"
                    if not matches
                    else f"{wanted!r} is ambiguous"
                )
                continue

            instruction = str(assignment.get("instruction", "")).strip()
            emit("info", f"  ant -> {matches[0][:8]}: {instruction}")

            try:
                report = explore(
                    page,
                    world,
                    ant_provider,
                    entry_url=entry_url,
                    start_key=matches[0],
                    instruction=instruction,
                    credentials=credentials,
                    budget=budget.ant_actions,
                    run_id=run_id,
                    shot=shot,
                )
            except Exception as exc:
                # One ant dying must not take the colony with it. A transient
                # 503 from the provider killed a completed two-wave exploration
                # during development, and the map -- states, transitions, every
                # observation -- was lost with the stack frame, even though
                # nothing about it was wrong.
                #
                # The dead ant becomes a report like any other, so the
                # orchestrator can see what happened and decide whether to
                # retry that state, go elsewhere, or finish with what it has.
                emit("error", f"  ant died: {type(exc).__name__}: {exc}")
                report = Report(start_key=matches[0], ended="error")
                report.uncertain = (
                    f"this ant failed before reporting ({type(exc).__name__}); "
                    "its region is still unexplored"
                )
            ants_left -= 1
            wave_reports.append(report)
            emit(
                "info",
                f"  ant <- {report.actions_taken} action(s), "
                f"{report.states_discovered} new state(s), {report.ended}",
            )

        result.reports += wave_reports
        result.waves = wave + 1

        feedback = tools.brief(
            world,
            reports=wave_reports,
            waves_left=budget.max_waves - wave - 1,
            ants_left=ants_left,
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

    saved = autosave(
        world, entry_url, mode="colony", model=provider.model,
        stopped=result.stopped, waves=result.waves, ants=len(result.reports),
    )
    if saved:
        emit("info", f"map saved to {saved}")

    try:
        result.transcript_path = str(
            save_transcript(
                transcript, run_id=run_id, role="orchestrator", system=system
            )
        )
        emit("info", f"transcripts written to {result.transcript_path}")
    except Exception:
        pass

    if not result.summary:
        # Ran out of budget before the orchestrator chose to finish. Say so
        # rather than presenting a partial map as a complete account.
        result.stopped = "budget"
        result.gaps = result.gaps + (
            "Exploration was cut off by the budget before the orchestrator "
            "judged the map complete; unexplored actions remain.",
        )

    return result


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

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        result = run(
            page, entry_url, provider, intent=intent, credentials=credentials
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
