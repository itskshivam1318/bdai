"""What the crawl did not cover, ranked — and never scored.

    cd app/api && uv run python -m agents.critic http://localhost:3000/sut

Must-have #3: *evaluate the plan for coverage gaps before passing it to the
Generator*. This is the stage the research is most emphatic about, and most of
what it says is a list of things not to do.

**The model does not find the gaps. It orders them.** `candidates()` computes
the whole list from the map with no model call at all, and the model's only
tool takes a permutation of what it was given. A gap it cannot point to is a
gap it cannot assert, because there is no field in which to write one. That is
the Rulers extractive-quote requirement (arXiv:2601.08654) enforced mechanically
rather than requested politely, and `prioritise()` counts and discards anything
invented.

Why so defensive. `docs/research/coverage-evaluation.md`:

- GPT-4 as a plan verifier has a **84.4% false-positive rate** — it waved
  through 38 of 45 invalid plans. A critic that accepts most bad artifacts
  manufactures confidence.
- On graph colouring, self-critique scored **1% against 16% for no iteration**,
  and scored *identically to deliberately fabricated feedback*. The loop was an
  expensive resampling scheme, not critique.
- A judge that reliably **ranks** by adequacy is achievable; one that reports
  "this plan is 82% complete" is not (arXiv:2603.14732). Hence `render()` prints
  a prioritised list and no percentage anywhere.

We are in the one configuration the research says works: the artifact under
review was **not written by a model**. `generator.py` compiles scenarios from
recorded transitions, so there is no self-preference bias to inherit and no
generator whose reasoning the critic could reproduce. The external,
non-fabricable signal is the crawl itself.

**One round.** Self-Refine caps at four and reports non-monotonic results past
two, attributing 61% of its failures to faulty feedback rather than to the
reviser. There is no loop here to be non-monotonic.

**Where the denominators come from.** ISTQB CTFL v4.0.1, which is the only
source in the field that gives formal coverage definitions:

    Equivalence Partitioning   partitions exercised / identified, and it
                               "must include invalid partitions" -- which is
                               exactly `submit[empty]` and `submit[invalid]`
                               sitting unexplored beside a `submit[valid]`
                               the crawler took
    0-switch coverage          valid transitions taken / offered -- our
                               `frontier()`, the actions a state offers and
                               nobody walked
    All transitions            includes the empty cells of the state table --
                               `worldmap.gaps()`, kept last and filtered,
                               because most cells of a real app's table are
                               meaningless rather than missing
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from .explorer.worldmap import WorldMap
from .llm import Tool, Transcript

_FORM_ACTION = re.compile(r"^submit\[(?P<mode>\w+)\]:(?P<descriptor>.+)$")

# The three input partitions our action grammar can express. `valid` is the
# happy path; the other two are the "not just happy paths" the brief asks for
# and the ones a crawl on a budget drops first.
_PARTITIONS = ("valid", "empty", "invalid")

# Deterministic priority per kind, lowest first. This ordering is the fallback
# when no model is configured, and the starting order the model is asked to
# improve on -- so it is a real ranking, not a placeholder.
#
#   partition   we know the form works, and we know nobody tested rejecting it.
#               The strongest kind of gap: a proven affordance with an
#               unexercised failure mode.
#   ambiguous   the map is actively wrong here, so every scenario routed
#               through this state inherits the error. Not coverage, but it
#               outranks coverage: fixing it changes what the other gaps mean.
#   untaken     offered and never walked. Ordinary shortfall, ranked by depth
#               so the shallow structure is closed before deep corridors.
#   unreachable the ISTQB empty cells. Last, because "you cannot submit the
#               checkout form from the settings page" is usually a fact about
#               the app rather than a missing test.
_KIND_RANK = {
    "unexercised-partition": 0,
    "ambiguous-edge": 1,
    "untaken-action": 2,
    "unreachable-action": 3,
}


@dataclass(frozen=True)
class Gap:
    """One thing the crawl did not cover, and the evidence that says so.

    `action` and `state_key` are verbatim from the map. They are the citation:
    a reader can look up either in `WorldMap` and find the row this came from,
    and the model reordering these may not write to them.
    """

    kind: str
    state_key: str
    where: str  # the state's title and path, for a human
    action: str  # verbatim from the map's action vocabulary
    why: str  # computed, never model-authored
    depth: int = 0
    risk: str = ""  # the one field a model may fill in

    @property
    def citation(self) -> tuple[str, str]:
        return self.state_key, self.action


def _where(world: WorldMap, key: str) -> str:
    """A human-readable location that is still unique.

    The state key leads, because on a single-page app every state shares one
    title and one path -- the SUT's ten states all render as "Acme Checkout
    (/sut)" -- and a report whose every line names the same place is unreadable
    even when each line is correct. The key is also the citation: it looks up.
    """
    node = world.states[key]
    path = urlparse(node.url).path or "/"
    return f"{key[:8]} {node.title or 'untitled'} ({path})"


def _forms(actions: tuple[str, ...]) -> dict[str, set[str]]:
    """Form descriptor -> the partitions this state offers for it."""
    found: dict[str, set[str]] = {}
    for action in actions:
        form = _FORM_ACTION.match(action)
        if form:
            found.setdefault(form.group("descriptor"), set()).add(form.group("mode"))
    return found


def candidates(world: WorldMap) -> tuple[Gap, ...]:
    """Every gap the map can prove, computed. No model is called here.

    This is the denominator, and it is the whole reason a critic is defensible
    at all: each item points at a row that exists. Nothing below infers a gap
    from what an application "ought" to have.
    """
    routes = world.paths()
    taken = {edge for edge, hits in world.transitions.items() if hits}
    ambiguous = set(world.nondeterministic())
    found: list[Gap] = []
    partitioned: set[tuple[str, str]] = set()

    for key, node in world.states.items():
        depth = len(routes.get(key, ()))
        where = _where(world, key)

        # 1. Input partitions. ISTQB EP: coverage "must include invalid
        #    partitions". A form the crawler successfully submitted, whose
        #    rejection paths nobody walked, is the most defensible gap we can
        #    state -- the affordance is proven and the failure mode is not.
        for descriptor, offered in _forms(node.actions).items():
            walked = {
                _FORM_ACTION.match(action).group("mode")
                for state, action in taken
                if state == key and _FORM_ACTION.match(action)
                and _FORM_ACTION.match(action).group("descriptor") == descriptor
            }
            if "valid" not in walked:
                continue  # the form itself was never made to work; that is a
                          # plain untaken action, reported below instead
            for mode in _PARTITIONS:
                if mode in walked or mode not in offered:
                    continue
                partitioned.add((key, f"submit[{mode}]:{descriptor}"))
                found.append(
                    Gap(
                        kind="unexercised-partition",
                        state_key=key,
                        where=where,
                        action=f"submit[{mode}]:{descriptor}",
                        why=(
                            f"{descriptor} submits successfully here, but its "
                            f"{mode}-input path was never taken -- so nothing "
                            "knows what this form rejects, or whether it rejects"
                        ),
                        depth=depth,
                    )
                )

        # 2. Ambiguous edges. Reported as a gap because everything downstream
        #    of a collapsed state is built on a map that is wrong, and no amount
        #    of extra coverage elsewhere repairs that.
        for from_key, action in ambiguous:
            if from_key != key:
                continue
            landings = {t.to_key for t in world.transitions[(from_key, action)]}
            found.append(
                Gap(
                    kind="ambiguous-edge",
                    state_key=key,
                    where=where,
                    action=action,
                    why=(
                        f"this action led to {len(landings)} different states on "
                        "different visits, so state identity collapsed two "
                        "behaviours here and any test routed through it is unsound"
                    ),
                    depth=depth,
                )
            )

        # 3. Offered and never walked. ISTQB 0-switch shortfall -- "the most
        #    widely used coverage criterion".
        #
        #    Skipping anything already reported as a partition gap: every
        #    unexercised partition is *also* an untaken action, and naming it
        #    under both kinds doubled the list with itself. The partition
        #    framing is strictly more informative -- it knows the form works --
        #    so that is the one that survives.
        for action in node.actions:
            if (key, action) in taken or (key, action) in partitioned:
                continue
            found.append(
                Gap(
                    kind="untaken-action",
                    state_key=key,
                    where=where,
                    action=action,
                    why="offered by this state and never taken, so where it "
                        "leads is unknown and nothing downstream of it is mapped",
                    depth=depth,
                )
            )

    # 4. The empty cells of the states x actions table. Restricted to form
    #    submissions: "this state does not offer link:Home" is a layout fact,
    #    while "this state has no way to submit the thing every other state can"
    #    is at least a question. Even so they rank last, and `render` says why.
    # An empty cell is only interesting when the action is *near-universal* and
    # this state is the exception. "Nine of ten states offer this and this one
    # does not" is a question; "two states offer it" is the ordinary variety of
    # an application. Without this, the SUT produced 63 correct cells nobody
    # could read, and on any real app the count grows with states x vocabulary.
    #
    # It also concedes something the ISTQB criterion assumes and a browser does
    # not provide: in a state machine you can *attempt* an invalid event, so
    # every empty cell is testable. In a web UI you cannot click a control that
    # is not rendered, so most empty cells are not attemptable at all.
    offering = Counter(
        action for node in world.states.values() for action in node.actions
    )
    threshold = max(2, len(world.states) // 2)

    for key, missing in world.gaps().items():
        # Only where this state has a form of its own. "The confirmation page
        # cannot submit the login form" is a fact about the layout; "this form
        # has no invalid-input path when every other form here does" is a
        # question.
        if not _forms(world.states[key].actions):
            continue
        for action in missing:
            if not _FORM_ACTION.match(action) or offering[action] < threshold:
                continue
            found.append(
                Gap(
                    kind="unreachable-action",
                    state_key=key,
                    where=_where(world, key),
                    action=action,
                    why=f"{offering[action]} of {len(world.states)} states offer "
                        "this and this one does not -- an ISTQB invalid-transition "
                        "cell, worth a test only if a user could plausibly expect "
                        "the action to be here",
                    depth=len(routes.get(key, ())),
                )
            )

    return tuple(sorted(found, key=lambda gap: (_KIND_RANK[gap.kind], gap.depth)))


# --- the model seam ------------------------------------------------------

PRIORITISE = Tool(
    name="prioritise",
    description=(
        "Reorder the candidate gaps from most to least worth testing next, and "
        "say what each one risks. You may omit a candidate you judge not worth "
        "testing. You may not add one: every entry must quote a candidate's "
        "`id` exactly as given."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ranked": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "the candidate's id, exactly as given",
                        },
                        "risk": {
                            "type": "string",
                            "description": (
                                "what goes untested if this gap stays open, in one "
                                "concrete sentence naming the user-visible "
                                "consequence -- not 'this is important'"
                            ),
                        },
                    },
                    "required": ["id", "risk"],
                },
            }
        },
        "required": ["ranked"],
    },
)


def brief(gaps: tuple[Gap, ...], summary: str, intent: str | None = None) -> str:
    """The candidate list as the model sees it. Ids are the only writable handle."""
    lines = [
        "The application, as explored:",
        "",
        summary,
        "",
        "Candidate coverage gaps, each computed from the map above:",
        "",
    ]
    for index, gap in enumerate(gaps):
        lines.append(f"  [{index}] {gap.kind} — {gap.action}")
        lines.append(f"       in: {gap.where}")
        lines.append(f"       why it is a candidate: {gap.why}")
    if intent:
        lines += ["", f"The person who started this run asked for: {intent}"]
    return "\n".join(lines)


def prioritise(
    world: WorldMap,
    provider=None,
    intent: str | None = None,
    instructions: str | None = None,
    on_event=None,
) -> tuple[Gap, ...]:
    """Rank the computed candidates. Returns them ordered, with risk filled in.

    With no provider this returns `candidates()` in its deterministic order,
    which is a real ranking and not a degraded mode -- the kind ordering encodes
    the ISTQB argument directly. The model is asked to do the one thing the
    research says judges are reliable at, which is ordering, and is structurally
    unable to do the thing they are unreliable at, which is inventing findings
    and attaching numbers to them.
    """
    found = candidates(world)
    if provider is None or not found:
        return found

    def emit(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    from .ant import instructions as load_instructions

    system = instructions or load_instructions("critic")
    transcript = Transcript(prompt=brief(found, world.summary(), intent))
    turn = provider.turn(system, transcript, [PRIORITISE])

    call = next((c for c in turn.calls if c.name == "prioritise"), None)
    if call is None:
        emit("warn", "critic did not rank; keeping the computed order")
        return found

    ranked: list[Gap] = []
    seen: set[int] = set()
    invented = 0

    for entry in call.arguments.get("ranked", []):
        index = entry.get("id")
        if not isinstance(index, int) or not 0 <= index < len(found) or index in seen:
            # The extractive-quote requirement, enforced rather than requested.
            invented += 1
            continue
        seen.add(index)
        gap = found[index]
        ranked.append(
            Gap(
                kind=gap.kind,
                state_key=gap.state_key,
                where=gap.where,
                action=gap.action,
                why=gap.why,
                depth=gap.depth,
                risk=str(entry.get("risk", "")).strip(),
            )
        )

    if invented:
        emit("warn", f"critic cited {invented} gaps that were not candidates; dropped")

    # Anything the model dropped is kept, after everything it ranked. Omission
    # is a judgement about priority, not a licence to delete evidence from the
    # report -- and a gap silently removed is exactly the failure the brief's
    # "coverage gaps remaining" line exists to prevent.
    ranked += [gap for index, gap in enumerate(found) if index not in seen]
    emit("decision", f"critic ranked {len(seen)} of {len(found)} candidate gaps")
    return tuple(ranked)


def render(gaps: tuple[Gap, ...], limit: int = 12) -> str:
    """The prioritised list. Deliberately carries no percentage.

    `research/README.md`, rule 1: *report a prioritised list of gaps, never a
    calibrated percentage.* A denominator exists here -- these are real cells of
    a real table -- but the cells are not equally meaningful, so dividing by
    their count would produce a number that looks calibrated and is not.
    """
    if not gaps:
        return "No coverage gaps found. Every action the map offers was taken."

    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap.kind] = counts.get(gap.kind, 0) + 1

    lines = [
        f"{len(gaps)} coverage gaps, most worth testing first",
        "  " + "  ".join(f"{kind}: {count}" for kind, count in counts.items()),
        "",
    ]
    for position, gap in enumerate(gaps[:limit], start=1):
        lines.append(f"{position:>3}. [{gap.kind}] {gap.action}")
        lines.append(f"     in {gap.where}")
        lines.append(f"     {gap.risk or gap.why}")
    if len(gaps) > limit:
        lines.append(f"     ... {len(gaps) - limit} more, in the same order")
    return "\n".join(lines)


def to_json(gaps: tuple[Gap, ...]) -> str:
    return json.dumps(
        [
            {
                "kind": g.kind, "action": g.action, "state_key": g.state_key,
                "where": g.where, "why": g.why, "risk": g.risk, "depth": g.depth,
            }
            for g in gaps
        ],
        indent=2,
    )


def main(entry_url: str) -> int:
    """Crawl, then rank what the crawl missed. Uses a model only if one is configured."""
    from playwright.sync_api import sync_playwright

    from .explorer.crawler import Budget, crawl

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        world = crawl(browser.new_page(), entry_url, Budget(max_actions=40, max_seconds=180))
        browser.close()

    try:
        from .llm import load

        provider = load()
        print(f"ranking with {provider.name}\n")
    except RuntimeError:
        provider = None
        print("no model configured -- deterministic ranking\n")

    print(render(prioritise(world, provider, on_event=lambda l, m: print(f"  [{l}] {m}"))))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"))
