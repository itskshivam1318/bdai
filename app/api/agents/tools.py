"""What an ant can see and do, and how the world is described to it.

The division this module enforces is the one the whole design rests on:

    the ant decides       which action to take
    the code observes     what state that landed in

An ant never tells the map what it saw. It acts, and the act is recorded by
`state_key` and `WorldMap` underneath it. If an ant could write "this looks like
the dashboard" into the map instead of a digest, two ants would describe one page
two ways, the map would grow duplicates, and it would stop being a map. What the
ant contributes is *understanding* -- the summary, the ranked branches, the
things it could not tell -- which is exactly the part code cannot produce.

`describe()` is the ant's entire view of the world, so it is the most
prompt-sensitive function here. It has to fit in a context window beside the
transcript, which is why it renders the current state in full and the rest of
the colony's knowledge as two numbers.
"""

from __future__ import annotations

from .explorer.observer import Observation
from .explorer.worldmap import WorldMap
from .llm import Tool

ACT = Tool(
    name="act",
    description=(
        "Take one action from the state you are standing in. Returns the state "
        "you land in and whether the colony has been there before. Use an action "
        "string exactly as it appears in the state description -- do not invent one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "An action string copied verbatim from the state description.",
            },
            "why": {
                "type": "string",
                "description": "One short sentence: what you expect this to reveal.",
            },
        },
        "required": ["action", "why"],
        "additionalProperties": False,
    },
)

REPORT = Tool(
    name="report",
    description=(
        "Finish your assignment and hand your findings to the colony. Call this "
        "before your action budget runs out, not after."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "What this region of the application is, in a QA engineer's "
                    "language. What a user comes here to accomplish."
                ),
            },
            "branches": {
                "type": "array",
                "description": "Actions you did not take that someone else should.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "why": {
                            "type": "string",
                            "description": "Why this specifically is worth an ant.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["action", "why", "priority"],
                    "additionalProperties": False,
                },
            },
            "uncertain": {
                "type": "string",
                "description": (
                    "Anything you could not tell. Plain 'I do not know what X "
                    "does' is more useful than a confident guess."
                ),
            },
        },
        "required": ["summary", "branches"],
        "additionalProperties": False,
    },
)

ANT_TOOLS = [ACT, REPORT]


DISPATCH = Tool(
    name="dispatch",
    description=(
        "Send a wave of ants. Each runs from the state you name, explores on its "
        "own, and reports back before you are asked again. Send between one and "
        "four; they cost time and money, and two sent to neighbouring states will "
        "retrace each other."
    ),
    parameters={
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "description": "A state id from the map, as shown (8 characters).",
                        },
                        "instruction": {
                            "type": "string",
                            "description": (
                                "What you want from this ant, in one sentence. "
                                "'Get through the login form' beats 'explore'."
                            ),
                        },
                    },
                    "required": ["state", "instruction"],
                    "additionalProperties": False,
                },
            },
            "reasoning": {
                "type": "string",
                "description": "Why this wave, and not somewhere else. One or two sentences.",
            },
        },
        "required": ["assignments", "reasoning"],
        "additionalProperties": False,
    },
)

FINISH = Tool(
    name="finish",
    description=(
        "Stop exploring. Call this when the map covers the application's real "
        "work, when a whole wave taught you nothing new, or when the budget is "
        "nearly spent -- not merely because unexplored actions remain."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What this application is and what a user does with it.",
            },
            "flows": {
                "type": "array",
                "description": (
                    "Sequences a user accomplishes, named as a QA engineer would. "
                    "'Log in', 'add an item and check out'. Not 'view the header'."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "states": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "State ids the flow passes through, in order.",
                        },
                        "why": {
                            "type": "string",
                            "description": "Why this flow matters to a user.",
                        },
                    },
                    "required": ["name", "why"],
                    "additionalProperties": False,
                },
            },
            "gaps": {
                "type": "array",
                "description": (
                    "What was not reached, and the risk of not knowing. Be "
                    "concrete: 'no ant completed a purchase, so nothing "
                    "downstream of payment is mapped'."
                ),
                "items": {"type": "string"},
            },
            "reason": {
                "type": "string",
                "enum": ["covered", "plateau", "budget"],
                "description": "Which stopping condition you are invoking.",
            },
        },
        "required": ["summary", "flows", "gaps", "reason"],
        "additionalProperties": False,
    },
)

ORCHESTRATOR_TOOLS = [DISPATCH, FINISH]


def brief(
    world: WorldMap,
    *,
    reports: list | None = None,
    waves_left: int,
    ants_left: int,
) -> str:
    """The orchestrator's view: the whole map, coarsely, plus the last wave.

    The mirror image of `describe()`. An ant sees one state in full and the map
    as two numbers; the orchestrator sees every state and no action lists at
    all. Neither could do the other's job with the other's view, which is the
    argument for them being two agents rather than one -- a single agent would
    have to carry both views at once, and the combined thing grows without bound
    as the map does.

    Untried counts are shown per state because they are what a dispatch decision
    turns on: a state with 23 untried actions is a frontier, and one with none is
    finished territory.

    **Refused actions are shown separately, and they are the point of a seeded
    run.** An action the deterministic walker offered, tried, and could not
    perform is not frontier -- it is the exact place determinism ran out and
    judgement is needed. Without this section the orchestrator cannot tell "not
    yet tried" from "tried and impossible", so it reassigns the impossible one
    wave after wave: measured on saucedemo, `submit[invalid]` was handed to a
    fresh ant in two separate runs and returned zero actions both times.

    **Known limit, and it binds before the others do.** This renders one line
    per state, so the orchestrator's prompt grows linearly with the map. That
    is fine at 21 states and is the unbounded-context problem at 200 -- the
    same failure the ant/orchestrator split exists to prevent, relocated. A
    seeded run reaches that size much sooner than an unseeded one, so ranking
    or bucketing states here is the next thing this function needs.
    """
    edges = sum(len(taken) for taken in world.transitions.values())
    lines = [
        f"world map: {len(world.states)} states, {edges} transitions, "
        f"{len(world.frontier())} actions never tried",
        "",
        "states:",
    ]

    for key, node in world.states.items():
        tried = sum(
            1 for action in node.actions if (key, action) in world.transitions
        )
        untried = len(node.actions) - tried
        title = (node.label or node.title or "")[:28]
        lines.append(
            f"  [{key[:8]}] {title:<30} {tried:>2} tried, {untried:>3} untried"
        )

    if world.skipped:
        lines += [
            "",
            f"{len(world.skipped)} action(s) OFFERED BUT REFUSED -- these were "
            "tried and could not be done. They are not frontier; sending an ant "
            "to repeat one costs a wave and finds nothing. They are where a "
            "human or a cleverer approach is needed:",
        ]
        for (key, action), why in list(world.skipped.items())[:12]:
            lines.append(f"  [{key[:8]}] {action}  --  {why}")
        if len(world.skipped) > 12:
            lines.append(f"  ... and {len(world.skipped) - 12} more")

    if reports:
        lines += ["", "what the last wave found:"]
        for report in reports:
            lines += ["", report.render()]
    else:
        lines += ["", "no ants have reported yet -- this is the first wave."]

    lines += [
        "",
        f"budget: {waves_left} wave(s) left, {ants_left} ant(s) left",
    ]
    return "\n".join(lines)


def describe(
    world: WorldMap,
    key: str,
    observation: Observation,
    *,
    budget_left: int,
    instruction: str | None = None,
) -> str:
    """The ant's whole view of the world. Everything it knows comes from here.

    Deliberately asymmetric: the current state is rendered in full because the
    ant is about to act on it, and the rest of the map is two numbers because
    the ant cannot act on it and does not need it. That asymmetry is what keeps
    an ant's context small no matter how large the map grows -- the property
    that makes many short-lived ants cheaper than one long-lived agent.

    Each action is marked with where it is already known to lead, so an ant can
    tell "nobody has tried this" from "this goes somewhere we have been". That
    single distinction is most of what makes an ant's choice better than random.
    """
    node = world.states.get(key)
    actions = node.actions if node else ()
    seen_before = node is not None and len(node.evidence) > 1

    lines = [
        f"state    {key[:8]}   {'known' if seen_before else 'NEW'}",
        f"url      {observation.url}",
        f"title    {observation.title}",
        f"colony   {len(world.states)} states mapped, "
        f"{len(world.frontier())} actions never tried",
        f"budget   {budget_left} action(s) left before you must report",
    ]

    if instruction:
        lines += ["", f"your assignment: {instruction}"]

    lines += ["", "actions available here:"]

    if not actions:
        lines.append("  (none -- this state offers nothing to do; report and stop)")

    for action in actions:
        taken = world.transitions.get((key, action))
        if not taken:
            lines.append(f"  .  {action}")
            continue
        destinations = {t.to_key for t in taken}
        if destinations == {key}:
            where = "tried: stays here"
        else:
            where = "tried: -> " + ", ".join(d[:8] for d in sorted(destinations))
        lines.append(f"  x  {action}   ({where})")

    return "\n".join(lines)


def outcome(
    world: WorldMap,
    from_key: str,
    action: str,
    observation: Observation,
    to_key: str,
    *,
    budget_left: int,
) -> str:
    """What `act` returns: where the ant landed, and whether that is news.

    `is_new` is the signal the ant is being asked to optimise for, so it is
    stated first and in words rather than left implicit in a digest the ant
    would have to compare by eye.
    """
    node = world.states[to_key]
    first_visit = len(node.evidence) <= 1
    mutating = bool(observation.mutating_calls)

    header = (
        "landed in a NEW state"
        if first_visit
        else "landed in a state the colony already knew"
    )
    if to_key == from_key:
        header = "nothing changed -- you are still in the same state"

    lines = [header]
    if mutating:
        lines.append(
            "this action changed data on the server (a write request fired)"
        )

    lines += ["", describe(world, to_key, observation, budget_left=budget_left)]
    return "\n".join(lines)


__all__ = ["ACT", "ANT_TOOLS", "REPORT", "describe", "outcome"]
