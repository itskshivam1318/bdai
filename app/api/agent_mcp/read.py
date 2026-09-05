"""Reading the map: what the app does, and which flows a change touches.

**The join.** A coding agent holds a diff and no behaviour; the map holds
behaviour and no files. Neither can answer "did I break something" alone. What
they share is the **accessible name** -- the thing a diff literally contains
(`- Place Order` / `+ Complete Purchase`) and the thing `statekey.normalize()`
keys states on. So `impact()` takes names, not paths, and the caller does the
source analysis it is already good at.

That is why there is no coverage instrumentation here. Mapping JS source ranges
back to files would let us take a file path instead of a name, at the cost of a
subsystem, and it would still be answering a question the client can answer
better -- it can read the diff, and we cannot.

Reads go through `store.load` rather than an endpoint because the console track
owns `GET /api/runs/{id}/map` and has not merged it yet. Reads are safe to take
locally: no rows are written, and the file is the same one the API opened.
"""

from __future__ import annotations

from sqlmodel import Session, select

from agents import generator, planner, runner
from agents.explorer import store
from agents.explorer.worldmap import WorldMap
from app.db import engine
from app.models import AppState

from .client import Console


def world_of(run_id: int) -> WorldMap:
    with Session(engine) as db:
        return store.load(run_id, db)


def mapped_runs() -> set[int]:
    """Run ids that actually produced states.

    Not every run is a mapped run. `verify` creates one to hang its timeline
    and its `TestCase` rows on, and it writes no `AppState` -- it replays a
    map, it does not build one.
    """
    with Session(engine) as db:
        # `select(AppState.run_id)` selects a *column*, so each row is the id
        # itself, not an AppState. Reading `.run_id` off it raises.
        return {
            run_id
            for run_id in db.exec(select(AppState.run_id).distinct())
            if run_id is not None
        }


def latest_run(console: Console, target_url: str | None = None) -> dict | None:
    """The newest run that has a map, so a defaulted `run_id` answers something.

    Tools take an optional `run_id` so an agent never has to know one. Without
    a default they would all have to call `sessions()` first, which is three
    round-trips to ask one question -- but a default that lands on a `verify`
    run returns an empty map, which is worse than asking.
    """
    mapped = mapped_runs()
    for run in console.runs():  # newest first, per `list_runs`
        if run["id"] not in mapped:
            continue
        if target_url and run.get("target_url") != target_url:
            continue
        return run
    return None


# `generator.scenarios` defaults to 8, which is a *demo* size: it is the
# number of .spec.ts files `make specs` should write, not the number of
# journeys an app has. Measured on saucedemo (19 states), the default
# returned 8 scenarios with **zero** of 3+ steps, while limit=40 returned 11
# with three -- including a four-step login -> item -> menu -> All Items
# journey. The map had the depth all along; the cap hid it. An agent asking
# what an app does wants the app's answer, not the demo's.
SCENARIO_LIMIT = 60


def map_of(run_id: int, include_snapshots: bool = False) -> dict:
    """The world map as an agent wants it: states, edges, and runnable flows.

    Snapshots are excluded by default. They are the largest thing in the store
    and an agent asking "what does this app do" wants the shape, not the
    accessibility dump of every page -- which would blow its context for no
    added answer.
    """
    world = world_of(run_id)
    scenarios = generator.scenarios(
        world, limit=SCENARIO_LIMIT, per_page=planner.share(world, SCENARIO_LIMIT)[1]
    )

    return {
        "run_id": run_id,
        # Headline only. `world.summary()` renders every state and its
        # actions as text below the counts -- the same data `states` and
        # `transitions` carry structurally, costing 21-49% of the payload
        # to say twice. Measured: the console crawl spent 56k of 115k
        # characters on the duplicate. The console still stores the full
        # rendering via `patch_run`; this is the agent-facing copy.
        "summary": world.summary().split("\n\n")[0],
        "states": [
            {
                "key": node.key,
                "title": node.title,
                "url": node.url,
                "label": node.label,
                "actions": list(node.actions),
                **(
                    {"snapshot": _snapshot(world, node.key)}
                    if include_snapshots
                    else {}
                ),
            }
            for node in world.states.values()
        ],
        "transitions": [
            {
                "from": key,
                "action": action,
                "to": edge.to_key,
                "mutating": edge.mutating,
                "self_loop": edge.self_loop,
            }
            for (key, action), edges in world.transitions.items()
            for edge in edges
        ],
        # Longest first: a multi-step journey says more about the app than a
        # one-click edge, and a caller that reads only the head of the list
        # should get the journeys.
        "flows": [
            {
                "name": scenario.name,
                "steps": [step.intent for step in scenario.steps],
            }
            for scenario in sorted(scenarios, key=lambda s: -len(s.steps))
        ],
        # Two edges with the same (state, action) landing apart means
        # `normalize()` collapsed behaviours that differ. Surfaced, not hidden:
        # it is the map telling the truth about its own resolution.
        "nondeterministic": [
            {"from": key, "action": action}
            for key, action in world.nondeterministic()
        ],
        "gaps": {key: list(actions) for key, actions in world.gaps().items()},
    }


def _snapshot(world: WorldMap, key: str) -> str:
    node = world.states.get(key)
    if node is None or not node.evidence:
        return ""
    return world.evidence[node.evidence[0]].snapshot


def _matches(name: str, needles: list[str]) -> bool:
    """Containment in either direction, case-folded.

    A diff says `Email`; the map says `Email address`. A diff says
    `Complete Purchase`; the map says `Complete Purchase`. Requiring equality
    misses the first, and requiring the query to contain the map value misses
    it the other way round -- so accept both and let precision come from the
    caller passing the strings it actually changed.
    """
    haystack = name.casefold()
    return any(
        needle.casefold() in haystack or haystack in needle.casefold()
        for needle in needles
        if needle.strip()
    )


def impact_of(run_id: int, names: list[str]) -> dict:
    """Which recorded flows touch these user-visible strings.

    Two tiers, because they are not the same claim and collapsing them makes
    the answer useless. Measured against the SUT: renaming one button matched
    8 of 8 flows when every match counted equally, which tells a caller
    nothing.

      acts      the flow operates this control -- clicks it, or fills it.
                Rename it and this flow's locator has to be re-resolved.
      observes  the control only appears in the flow's expected state delta,
                usually as a line that *disappears* when the flow acts. Every
                flow starting on the page holding that control matches this
                way, so on its own it is close to no evidence.

    `affected` is the acts tier. `observing` is reported separately rather
    than dropped: a flow that expects a heading to appear is genuinely
    affected when you delete that heading, even though it never acts on it.
    """
    world = world_of(run_id)
    scenarios = generator.scenarios(
        world, limit=SCENARIO_LIMIT, per_page=planner.share(world, SCENARIO_LIMIT)[1]
    )

    affected, observing = [], []
    for scenario in scenarios:
        acts, observes = [], []
        for index, step in enumerate(scenario.steps, start=1):
            _, role, name = runner._parts(step.action)
            if _matches(name, names) or _matches(role, names):
                acts.append({"step": index, "intent": step.intent,
                             "why": f"acts on {step.action}"})
            for field_role, field_name, _ in step.fields:
                if _matches(field_name, names):
                    acts.append({"step": index, "intent": step.intent,
                                 "why": f"fills {field_role}:{field_name}"})
            for line in step.expect.added + step.expect.removed:
                if _matches(line, names):
                    observes.append({"step": index, "intent": step.intent,
                                     "why": f"expects {line.strip()}"})

        entry = {
            "flow": scenario.name,
            "steps": len(scenario.steps),
            "target_url": scenario.target_url,
            "acts": acts,
            "observes": observes,
        }
        if acts:
            affected.append(entry)
        elif observes:
            observing.append(entry)

    named = {e["flow"] for e in affected} | {e["flow"] for e in observing}
    return {
        "run_id": run_id,
        "names": names,
        # Verify these. The flow acts on a control you changed.
        "affected": affected,
        # Weaker. Verify after `affected` if the change removed something.
        "observing": observing,
        # An honest empty answer. Nothing matching is a real result -- the
        # change is in code no recorded flow exercises -- but it is also what
        # an unmapped area looks like, and those need different responses.
        "unaffected_flows": [
            scenario.name for scenario in scenarios if scenario.name not in named
        ],
    }
