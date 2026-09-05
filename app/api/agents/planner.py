"""Decide what is worth testing, from one world model or from two.

    cd app/api && uv run python -m agents.planner http://localhost:3000/sut

The Planner is the brief's first sub-agent, and until now it had no file. Its
work was four lines inside `pipeline.py`'s generate stage, which meant two
things were true and both were bad: the colony's `_send_generator` could not
reach the same decision (it compiled raw map edges and never saw a hypothesis),
and there was nowhere to stand to ask whether the semantic layer earns its cost.

**One question this file exists to answer.** The crawler produces a factual map
with no interpretation in it; `behavior.py` produces an interpretation of that
map. Does the second make better Playwright tests than the first, or does it
just make more of them? That is not answerable by argument, so `source` is a
knob rather than a constant:

    source="map"        the deterministic world model alone. No provider, no
                        key, no interpretation -- `scenarios()` ranking every
                        edge the crawler walked.
    source="behaviour"  the same, preceded by every flow the colony believed
                        in and the map could back.

Both runs record their provenance on every scenario (`Scenario.origin`), so the
comparison is a count over the saved suite rather than a claim in a report.
`compare()` prints it.

**The behavioural half can only ever add.** `from_flow` returns None the moment
a consecutive pair of cited states has no edge the crawler recorded, so a
believed flow cannot introduce an expectation nobody observed. The computed
suite then fills the remaining slots. This ordering is deliberate: `scenarios()`
ranks single edges and structurally cannot propose a sequence, so "log in, add
an item, reload, check it survived" enters the plan through the semantic layer
or not at all.

**What this file does not do.** It does not explore -- that is the crawler and
the ants, and both run before anything here has an input. It does not write
files or run a browser. It reads two in-memory models and returns scenarios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .generator import (
    Scenario, from_flow, page_of, propose, refusal, scenarios, unwalked,
)

# The two world models a plan can be built from. `behaviour` is a superset:
# it starts with the believed flows and then fills from the map.
SOURCES = ("map", "behaviour")

DEFAULT_SOURCE = "behaviour"


@dataclass(frozen=True)
class Plan:
    """The scenarios to test, and an account of where each came from.

    The counts are not decoration. `pipeline.report` prints them as the
    Planner's decision, and `compare()` is the A/B they make possible -- a suite
    whose `from_behaviour` is 0 on an app with a rich behavioural model is
    telling you the model cited orderings the crawler never walked, which is a
    fact about the model rather than about the app.
    """

    scenarios: tuple[Scenario, ...]
    source: str
    from_behaviour: int = 0
    from_map: int = 0
    # Believed flows the map could not back, so nothing was compiled for them.
    uncompilable: int = 0
    # Of those, the ones that broke on a pair nobody walked: (claim, from_key,
    # to_key). Where the colony should send an ant next -- `tools.brief` offers
    # the same pairs to the orchestrator.
    unwalked: tuple[tuple[str, str, str], ...] = ()
    # Every uncompilable flow with the reason `from_flow` refused it:
    # (claim, reason). A superset of `unwalked`, in prose.
    refused: tuple[tuple[str, str], ...] = ()
    # Set when the plan was narrowed to one state key.
    node: str = ""
    # Why the requested source was not the source used, if it was not.
    degraded: str = ""
    # How the limit was spread: distinct URL paths the crawl reached, and the
    # slots reserved for each before any page took more. See `share`.
    pages: int = 0
    per_page: int = 0
    # Scenarios the Generator's model wrote over the map (`generator.propose`),
    # and what of its writing the map refused: steps naming no recorded edge,
    # assertions naming no recorded effect.
    from_model: int = 0
    invented: int = 0
    trimmed: int = 0

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self):
        return iter(self.scenarios)

    @property
    def nodes(self) -> tuple[str, ...]:
        """Every state key this plan claims to cover, deduplicated."""
        seen: dict[str, None] = {}
        for scenario in self.scenarios:
            for key in scenario.covers:
                seen.setdefault(key, None)
        return tuple(seen)

    @property
    def unhappy(self) -> int:
        """Scenarios whose terminal action is a rejection path.

        Counted off the action grammar rather than the name: `submit[empty]`
        and `submit[invalid]` are in the map because `forms.available_actions`
        put them there, so "not just happy paths" is measurable without asking
        a model whether a test feels negative.
        """
        return sum(
            1
            for s in self.scenarios
            if s.steps
            and (
                s.terminal.action.startswith("submit[empty]")
                or s.terminal.action.startswith("submit[invalid]")
            )
        )

    def render(self) -> str:
        lines = [
            f"PLAN        {len(self.scenarios)} scenario(s) from `{self.source}`"
            + (f" at [{self.node[:8]}]" if self.node else ""),
            f"            {self.from_behaviour} believed, {self.from_map} computed, "
            f"{len(self.nodes)} node(s), {self.unhappy} unhappy path(s)",
        ]
        if self.degraded:
            lines.append(f"            degraded: {self.degraded}")
        if self.uncompilable:
            lines.append(
                f"            {self.uncompilable} believed flow(s) named an "
                "ordering nobody walked and were not compiled"
            )
        for claim, from_key, to_key in self.unwalked:
            lines.append(
                f"            unwalked: [{from_key[:8]}] -> [{to_key[:8]}]  {claim}"
            )
        for claim, why in self.refused:
            lines.append(f"            refused: {why}  ({claim[:60]})")
        for scenario in self.scenarios:
            lines.append(
                f"  [{scenario.node[:8]}] {scenario.name}  ({scenario.origin})"
            )
        return "\n".join(lines)



def share(world, limit: int) -> tuple[int, int]:
    """How many crawled pages there are, and how many slots each is owed.

    The Planner's allocation decision, derived from the map on every run and
    never a constant: the limit divided evenly across the distinct URL paths
    the crawl reached, never below one. Ten pages under a 24-scenario cap get
    two each; three pages under eight get two; one page gets the whole suite.

    Measured 2026-09-05 on a Velogent run before this existed: 26 states over
    ten paths, and all 23 scenarios terminated on the login page, because the
    generator's kind-rotation was the only fairness rule and a login page
    offers every kind of action there is. The generator's `by_page` is the
    mechanism that spends this; the number is decided here, where the map is
    read as a whole.
    """
    pages = len({page_of(world, key) for key in world.states})
    return pages, max(1, limit // max(pages, 1))


def source_from_env(default: str = DEFAULT_SOURCE) -> str:
    """`PLAN_FROM=map` runs the deterministic planner. Anything else is ignored.

    An unrecognised value falls back rather than raising, and the fallback is
    the richer source: the knob exists to *remove* the semantic layer for a
    measurement, so a typo that silently removed it would corrupt exactly the
    comparison it was set to make.
    """
    requested = os.environ.get("PLAN_FROM", "").strip().lower()
    return requested if requested in SOURCES else default


def plan(
    world,
    behaviour=None,
    *,
    source: str = DEFAULT_SOURCE,
    limit: int = 8,
    node: str = "",
    only: set[tuple[str, str]] | None = None,
    per_page: int | None = None,
    provider=None,
    run_id: int | None = None,
    on_event=None,
    intent: str | None = None,
) -> Plan:
    """What to test, best first, capped at `limit`.

    `node` narrows the plan to scenarios that pass through one state key --
    what the colony's per-node dispatch needs, and what makes a suite's
    coverage of a region answerable. The filter runs before the cap, so asking
    for a node does not return the whole map's best eight and then discard
    seven of them.

    `provider` is the Generator's model (decided 2026-09-05, see
    `generator.propose`). With one, and `source="behaviour"`, the model writes
    scenarios over the map and they rank ahead of the compiled ones; the
    compile still fills what the model left, and is the whole plan when there
    is no provider or the model's writing named nothing the map recorded.
    `source="map"` keeps the model out of the plan entirely -- the A/B knob.

    `only` narrows it to scenarios whose *terminal* edge is one of a given set
    of `(from_key, action)` pairs -- what incremental generation needs, so a
    run that found one new flow compiles a test for that flow rather than
    recompiling the suite. It is applied to both halves of the planner by the
    same rule: a believed flow qualifies when the edge it ends on qualifies.
    """
    if source not in SOURCES:
        source = DEFAULT_SOURCE

    believed: list[Scenario] = []
    uncompilable = 0
    missing: list[tuple[str, str, str]] = []
    refused: list[tuple[str, str]] = []
    degraded = ""

    written: list[Scenario] = []
    invented = trimmed = 0
    if source == "behaviour":
        hypotheses = tuple(behaviour.of_kind("flow")) if behaviour else ()
        for hypothesis in hypotheses:
            scenario = from_flow(world, hypothesis)
            if scenario is None:
                uncompilable += 1
                pair = unwalked(world, hypothesis)
                if pair is not None:
                    missing.append((hypothesis.claim, *pair))
                refused.append((hypothesis.claim, refusal(world, hypothesis)))
                continue
            believed.append(scenario)

        # The Generator's model (decided 2026-09-05, `generator.propose`). It
        # writes over the map whether or not the colony believed anything, so
        # it is asked before the source is judged.
        proposal = propose(
            world, provider, limit, intent=intent, on_event=on_event, run_id=run_id
        )
        written = list(proposal.scenarios)
        invented, trimmed = proposal.invented, proposal.trimmed

        if not believed and not written:
            # Nothing model-derived reached the plan. The map alone is still a
            # plan, and saying which happened is the difference between a
            # smaller suite and a broken one.
            #
            # The source is *demoted to what actually happened*, not left at
            # what was asked for. A version stamped `behaviour` with nothing
            # model-derived in it would sit in the comparison as though the
            # semantic layer had been given its chance and added nothing, which
            # is the one wrong answer the A/B can produce.
            reasons = []
            if behaviour is None or not getattr(behaviour, "hypotheses", ()):
                reasons.append("no behavioural model")
            elif not hypotheses:
                reasons.append("the colony named no flow to compile")
            else:
                reasons.append("no believed flow was one the map could back")
            if proposal.degraded:
                reasons.append(proposal.degraded)
            degraded = (
                "asked for behaviour; " + "; ".join(reasons)
                + "; the plan is the deterministic map alone"
            )
            source = "map"

    pages, owed = share(world, limit)
    if per_page is None:
        per_page = owed
    computed = list(
        scenarios(world, limit=max(limit, 8), only=only, per_page=per_page)
    )

    # Deduplicated on the action sequence, not the name: a believed flow, a
    # model-written scenario and a computed one can walk the same edges under
    # different names, and running two would double-count the coverage they
    # prove. First writer keeps it: believed, then the model, then the compile.
    ordered: list[Scenario] = []
    seen: set[tuple[str, ...]] = set()
    for scenario in believed + written + computed:
        walk = tuple(step.action for step in scenario.steps)
        if walk in seen:
            continue
        seen.add(walk)
        ordered.append(scenario)

    if only is not None:
        # `scenarios()` already honoured this for the computed half; the
        # believed half has not been filtered yet, and one rule applied to the
        # whole ordered list is easier to reason about than two.
        ordered = [
            s
            for s in ordered
            if s.steps and (s.terminal.from_key, s.terminal.action) in only
        ]

    if node:
        ordered = [
            s for s in ordered if any(step.from_key == node for step in s.steps)
        ]

    chosen = tuple(ordered[:limit])
    return Plan(
        scenarios=chosen,
        source=source,
        from_behaviour=sum(1 for s in chosen if s.origin.startswith("behaviour")),
        from_map=sum(1 for s in chosen if s.origin == "map"),
        from_model=sum(1 for s in chosen if s.origin == "generator:model"),
        invented=invented,
        trimmed=trimmed,
        uncompilable=uncompilable,
        unwalked=tuple(missing),
        refused=tuple(refused),
        node=node,
        degraded=degraded,
        pages=pages,
        per_page=per_page,
    )


# ------------------------------------------------------------------ the A/B


def compare(with_behaviour: Plan, map_only: Plan) -> str:
    """The two planners side by side, on measurable properties only.

    Every row here is counted off the plans themselves. There is deliberately
    no row for "quality": whether the extra scenarios are worth their cost is
    answered by running both suites -- `regression` records defects found and
    heals needed per version -- and not by a table written at planning time.
    """
    rows = [
        ("scenarios", len(with_behaviour), len(map_only)),
        ("nodes covered", len(with_behaviour.nodes), len(map_only.nodes)),
        ("unhappy paths", with_behaviour.unhappy, map_only.unhappy),
        ("multi-step", _multi(with_behaviour), _multi(map_only)),
        ("from behaviour", with_behaviour.from_behaviour, map_only.from_behaviour),
    ]
    width = max(len(name) for name, _, _ in rows)
    lines = [
        f"{'':<{width}}  {'behaviour':>10}  {'map only':>10}",
        f"{'':-<{width}}  {'':->10}  {'':->10}",
    ]
    lines += [f"{name:<{width}}  {a:>10}  {b:>10}" for name, a, b in rows]

    only = {s.name for s in with_behaviour} - {s.name for s in map_only}
    if only:
        lines.append("")
        lines.append("only the behavioural planner proposed:")
        lines += [f"  {name}" for name in sorted(only)]
    return "\n".join(lines)


def _multi(p: Plan) -> int:
    """Scenarios longer than one action after the route to their start.

    The claim for the semantic layer is that it can express a sequence, so this
    is the row that would show it doing so. A computed scenario's length is its
    shortest route plus one terminal action; a believed flow's length is
    whatever the model claimed and the map backed.
    """
    return sum(1 for s in p.scenarios if len(s.steps) > 1)


def main(entry_url: str) -> int:
    """Crawl once, plan both ways, print the comparison.

    One crawl feeds both plans on purpose. Crawling twice would let map
    nondeterminism -- a form the synthesizer filled differently, an edge the
    budget cut -- show up as a difference between the planners.
    """
    from playwright.sync_api import sync_playwright

    from .explorer.crawler import Budget, crawl
    from .explorer.forms import Credentials

    credentials = Credentials.from_env()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(ignore_https_errors=True)
        world = crawl(page, entry_url, Budget(max_actions=40, max_seconds=180),
                      credentials=credentials)
        behaviour = None
        try:
            from .behavior import synthesise
            from .llm import load

            behaviour = synthesise(world, load())
        except Exception as exc:  # no key, exhausted key, provider refused
            print(f"BEHAVIOUR   unavailable ({type(exc).__name__}: {exc})")
        browser.close()

    rich = plan(world, behaviour, source="behaviour")
    plain = plan(world, behaviour, source="map")
    print(rich.render())
    print()
    print(plain.render())
    print()
    print(compare(rich, plain))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"))
