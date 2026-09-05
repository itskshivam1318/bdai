"""The behavioural model: what states exist, and what moves between them.

This is the artifact the whole pipeline reads and writes. The brief names three
sub-agents; all three are operations on this object:

    Planner    the transitions ARE the test plan
    Generator  a path through the graph compiles to a test
    Healer     re-observe, compare keys, and the failure classifies itself

Nothing here calls a model. States are `state_key()` digests, edges are element
descriptors, and every method below is a pure function over two dicts. The model
seams are named at the bottom of this docstring and all of them are optional.

**Why transitions map to a list.** `transitions[(from, action)]` holds a *list*
of `Transition`, not one. Doing the same thing from the same state should land
in the same place; when it does not, the projection in `statekey.py` threw away
something that mattered, and the two successors are the proof. Storing a list
makes that structurally visible -- `nondeterministic()` is a `len() > 1` filter
over a dict we already keep -- instead of something a separate reconciler has to
go hunting for. It is also the whole of "contradiction handling": a contradiction
in a computed map is a nondeterministic edge.

**Two different kinds of "we have not tried this".** They are not the same and
the distinction is worth the two methods:

    frontier()  actions a state OFFERS and we have not taken. Drives the crawl.
                Empty means the crawl is done.

    gaps()      actions the app has SOMEWHERE that this state does not offer.
                Never in the frontier, because you cannot click what is not
                there. These are ISTQB's invalid-transition cells -- the empty
                cells of a states x actions table -- and they are the closest
                thing this field has to a computable "missing error states"
                denominator. See `docs/research/coverage-evaluation.md`.

The model seams, none of which exist yet and none of which block anything:
naming a state for humans, ranking `gaps()` by plausibility, and choosing where
to explore when the frontier stalls.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlparse

from .observer import Observation
from .statekey import state_key


@dataclass(frozen=True)
class StateNode:
    """One behavioural state, identified by what `normalize()` kept.

    `url` and `title` are the *first* ones seen, and are descriptive only --
    never identity. Two routes can be one state (`/products/1` and
    `/products/2`) and one route can be two (`/checkout` before and after a
    validation error). Reading identity off the URL is the mistake this whole
    module exists to avoid.
    """

    key: str
    url: str
    title: str
    actions: tuple[str, ...]  # element descriptors, e.g. 'button:Sign in'
    label: str | None = None  # human name. A model seam; None until asked.
    evidence: tuple[int, ...] = ()  # indices into WorldMap.evidence
    # Path to one screenshot, relative to the artifacts dir. Taken the first
    # time we stood in this state and never retaken -- see `attach_screenshot`.
    screenshot: str | None = None
    # Which ant first stood here, e.g. "w2a1" -- wave 2, ant 1. None when
    # nothing was attributing: the model-free crawler, or a direct `record`.
    # First finder wins; see `record`.
    found_by: str | None = None


@dataclass(frozen=True)
class Transition:
    """Taking one action from one state landed in another. Or the same one.

    `to_key == from_key` is not a failure and not a self-loop to filter out. It
    is the single most informative edge in the graph: the app was asked to do
    something and stayed put. Combined with `mutating` it separates the two
    cases the Healer has to tell apart --

        stayed put, no request fired    the click did nothing (or a guard fired)
        stayed put, request fired       the app accepted it and did not re-render
    """

    from_key: str
    action: str
    to_key: str
    mutating: bool
    evidence: int  # index into WorldMap.evidence
    # The ant that took this action. Unlike a state, an edge has exactly one
    # walker, so there is no first-wins question here.
    found_by: str | None = None
    # `(role, name, value)` for every field typed to cross this edge, from
    # `forms.Performed`. Empty for an action that types nothing, which is most
    # of them.
    #
    # **The value has to live here and not be re-derived.** It is chosen at
    # crawl time by the synthesizer (for `submit[invalid]`) or by
    # `forms.value_for` (for `submit[valid]`), and only one of those two is
    # reachable from an exported spec. When the Generator re-derived it, every
    # `submit[invalid]` test typed the *valid* credentials and then asserted the
    # rejection they cannot cause -- see `forms.Performed`.
    typed: tuple[tuple[str, str, str], ...] = ()

    @property
    def self_loop(self) -> bool:
        return self.from_key == self.to_key


def is_flow(world: "WorldMap", transition: Transition) -> bool:
    """Is this edge worth a test? Structural signals only.

    QA Wolf's rule is that a flow must describe what a user *accomplishes*; it
    explicitly rejects test cases like "Display Search Dropdown". Before this
    existed the Generator compiled every recorded edge, so a crawl of saucedemo
    produced "activate the link" and "click Open Menu" beside the login flow,
    and six sibling product links each became their own test.

    Three signals, all already on the objects, and no knowledge of what an
    action string means -- this module keeps actions opaque (see the class
    docstring) and `submit[` appearing here would break that:

        mutating        a non-GET fired. The user changed something, which is
                        the definition of accomplishing anything. Kept even as
                        a self-loop: "the app accepted it and did not
                        re-render" is the most valuable edge in the graph.

        self-loop with
        nothing fired   asked, and nothing happened. `textbox:Email stays`.
                        A test here asserts nothing.

        discovered its
        destination     the edge that first reached `to_key`. If a state is
                        already recorded, a second route into it re-tests a
                        screen the suite already covers.

    **This is not the same question as `frontier()`.** An edge can be highly
    informative to the *map* and worthless as a *test* -- the `Transition`
    docstring defends self-loops, and it is right to, because the crawler needs
    them to model the app. Modelling and testing are different jobs and this is
    where they part.

    The vocabulary rule that belongs with the Generator -- a form submission is
    an accomplishment whatever its structure, so an unhappy path that is
    correctly refused stays in the plan -- is in `generator.worth_testing`,
    which may know what an action means. Together they are the whole policy.
    """
    if transition.mutating:
        return True
    if transition.self_loop:
        return False
    return _discovered(world, transition)


def _discovered(world: "WorldMap", transition: Transition) -> bool:
    """Was this the first recorded edge to reach its destination?

    Insertion order is the crawl order, so the first edge into a state is the
    one that found it -- the same tie-break `paths()` makes by exploring in
    that order. Deterministic for a given map, which is what the check needs.
    """
    for (from_key, action), taken in world.transitions.items():
        for recorded in taken:
            if recorded.to_key != transition.to_key:
                continue
            return (from_key, action) == (transition.from_key, transition.action)
    return False


@dataclass(frozen=True)
class Ground:
    """What a citation may resolve against, frozen at a moment in time.

    `behavior.admit` needs exactly two things from a map: the vocabulary, to
    accept an action verbatim, and the state keys, to widen the 8-character id
    a model is shown into the 16 the map is keyed on. Both are derived by
    *iterating* `WorldMap.states`.

    That is safe while the only reader is the code that finished the crawl. It
    stops being safe the moment the behavioural model runs beside the crawl:
    the crawler inserts into `states` on one thread while `vocabulary()`
    iterates it on another, and CPython raises `RuntimeError: dictionary
    changed size during iteration` from inside the reply handler, nowhere near
    the cause.

    So the guard is handed a value instead of a map. Taken on the crawl
    thread, frozen, and safe to carry anywhere.

    **Staleness is safe in the direction we are in.** A `Ground` taken before
    the crawl found more states can only refuse a citation the newer map would
    have allowed, because states are never removed. The guard can be too
    strict; it cannot be too lax.
    """

    states: frozenset[str]
    actions: frozenset[str]


@dataclass
class WorldMap:
    """States, the edges between them, and the observations backing both.

    `evidence` is the observation store, kept deliberately beside the model
    rather than inside it: nodes and edges stay small enough to hand to a model
    or print to a terminal, and the raw snapshots that justify them stay
    addressable by index. Nothing in `states` or `transitions` holds a snapshot.
    """

    states: dict[str, StateNode] = field(default_factory=dict)
    transitions: dict[tuple[str, str], list[Transition]] = field(
        default_factory=dict
    )
    evidence: list[Observation] = field(default_factory=list)
    entry_key: str | None = None

    # Who is recording right now. Mutable state on the map rather than an
    # argument to `record`/`connect`, because those are called from a dozen
    # places across `ant.py` and `crawler.py` and threading an identity through
    # all of them would put the colony's dispatch structure into the crawler,
    # which does not have ants. The orchestrator sets it around each dispatch;
    # everything else leaves it None and gets today's behaviour.
    attribution: str | None = None

    # Actions a state offers that could not be taken, and why. Measured
    # 2026-09-04: two of three public targets produced a shallow map not
    # because they were shallow but because their login form was refused here
    # -- `testingchallenges` has four textboxes with no accessible name,
    # `practicetestautomation` has no `<form>` element at all. Both dropped the
    # single highest-value edge, and neither said so. An unexplored action and a
    # refused one look identical in `frontier()`, so without this the crawl
    # cannot report the difference between "nothing left to try" and "we gave
    # up on the front door".
    #
    # A fact about the application, not a crawler fault -- which is why it
    # belongs on the map beside `gaps()` rather than in a log.
    skipped: dict[tuple[str, str], str] = field(default_factory=dict)

    # Why the crawl ended. `frontier empty` and `everything left refused` are
    # opposite outcomes that both arrive at the same `break`.
    stopped: str = ""

    # What actions a state offers. Injected rather than imported so this module
    # never learns what an action *means* -- to everything here an action is an
    # opaque string, which is what lets the vocabulary grow (plain clicks today,
    # `submit[valid]:...` now, whatever a model proposes later) without the
    # model of the application changing shape underneath it.
    actions_of: Callable[[Observation], tuple[str, ...]] | None = None

    def _actions(self, observation: Observation) -> tuple[str, ...]:
        if self.actions_of is not None:
            return self.actions_of(observation)
        return tuple(element.descriptor for element in observation.interactive)

    # --- writing ---------------------------------------------------------

    def record(self, observation: Observation) -> str:
        """File an observation and return the key of the state it shows.

        Idempotent per state: a revisit appends evidence and leaves the node
        alone. The first sighting wins for `url`, `title` and `actions`, which
        is right for the first two and a simplification for the third -- an app
        that reveals a new control on the second visit to the same state key
        will not have it noticed. That shows up as a state whose real action set
        is larger than its recorded one, and it is bounded by the fact that a
        new control usually changes the key anyway.
        """
        key = state_key(observation.snapshot)
        self.evidence.append(observation)
        index = len(self.evidence) - 1

        existing = self.states.get(key)
        if existing is None:
            self.states[key] = StateNode(
                key=key,
                url=observation.url,
                title=observation.title,
                actions=self._actions(observation),
                evidence=(index,),
                found_by=self.attribution,
            )
            if self.entry_key is None:
                self.entry_key = key
        else:
            self.states[key] = StateNode(
                key=existing.key,
                url=existing.url,
                title=existing.title,
                actions=existing.actions,
                label=existing.label,
                evidence=existing.evidence + (index,),
                screenshot=existing.screenshot,
                # Not `self.attribution`: the finder is the ant that got here
                # first, not the last one to walk past.
                found_by=existing.found_by,
            )

        return key

    def connect(
        self,
        from_key: str,
        action: str,
        observation: Observation,
        typed: tuple[tuple[str, str, str], ...] = (),
    ) -> Transition:
        """Record where an action led. Files the destination observation too.

        `typed` is `forms.Performed.typed` -- what was actually filled in to
        take this action. See `Transition.typed`.
        """
        to_key = self.record(observation)
        transition = Transition(
            from_key=from_key,
            action=action,
            to_key=to_key,
            mutating=bool(observation.mutating_calls),
            evidence=len(self.evidence) - 1,
            found_by=self.attribution,
            typed=tuple(typed),
        )
        self.transitions.setdefault((from_key, action), []).append(transition)
        return transition

    def attach_screenshot(self, key: str, path: str | None) -> None:
        """Give a state its thumbnail. First one wins; `None` is a no-op.

        Both guards matter. The map is written from two places (`crawler.py`
        and `ant.py`), so "first wins" is what keeps a re-entered state from
        paying for a second picture. And the shooter returns `None` when the
        screenshot failed, which must leave the node alone rather than erase a
        picture an earlier visit already took.
        """
        node = self.states.get(key)
        if node is None or path is None or node.screenshot is not None:
            return
        self.states[key] = replace(node, screenshot=path)

    # --- reading ---------------------------------------------------------

    def frontier(self) -> tuple[tuple[str, str], ...]:
        """(state, action) pairs a state offers that nobody has taken yet."""
        return tuple(
            (key, action)
            for key, node in self.states.items()
            for action in node.actions
            if (key, action) not in self.transitions
        )

    def ground(self) -> Ground:
        """What a citation may resolve against right now, frozen.

        Cheap: two set builds over the states already in memory. Called from
        the crawl thread every time a batch is handed to the behavioural
        model, which is once every few states rather than once per action.
        """
        return Ground(
            states=frozenset(self.states),
            actions=frozenset(self.vocabulary()),
        )

    def vocabulary(self) -> tuple[str, ...]:
        """Every action this app is known to offer anywhere, commonest first.

        The alphabet of the state machine. `gaps()` is this minus what each
        state actually offers.
        """
        counts = Counter(
            action for node in self.states.values() for action in node.actions
        )
        return tuple(action for action, _ in counts.most_common())

    def gaps(self) -> dict[str, tuple[str, ...]]:
        """Per state, the actions the app has elsewhere but this state lacks.

        The empty cells of the states x actions table, and the reason to build a
        graph at all: they are enumerable without running anything, which is
        what makes "missing error states" computable rather than a judgement.

        **This is a candidate generator, not a report.** On a real app the
        alphabet is large and most cells are meaningless -- nobody needs a test
        for submitting a checkout form from the settings page. Ranking them is a
        model seam, and `docs/research/README.md` is explicit that the output
        must be a prioritised list of gaps and never a coverage percentage. The
        percentage would be indefensible; the list is defensible because every
        item on it points at a real cell in a real table.
        """
        alphabet = set(self.vocabulary())
        return {
            key: tuple(sorted(alphabet - set(node.actions)))
            for key, node in self.states.items()
        }

    def nondeterministic(self) -> tuple[tuple[str, str], ...]:
        """Edges where the same action from the same state led somewhere else.

        The split signal. Each one means `normalize()` collapsed two states that
        behave differently, and the two source observations name the difference:
        run `explain()` on them and whatever it prints is the variable that
        should have been part of identity.
        """
        return tuple(
            edge
            for edge, taken in self.transitions.items()
            if len({transition.to_key for transition in taken}) > 1
        )

    def paths(self) -> dict[str, tuple[str, ...]]:
        """Shortest action sequence from the entry state to each other state.

        BFS over recorded transitions. This is how the crawler returns to a
        state it wants to leave from, and how the Generator turns a target state
        into a test body.
        """
        if self.entry_key is None:
            return {}

        routes: dict[str, tuple[str, ...]] = {self.entry_key: ()}
        queue = [self.entry_key]

        while queue:
            key = queue.pop(0)
            for (from_key, action), taken in self.transitions.items():
                if from_key != key:
                    continue
                for transition in taken:
                    if transition.to_key not in routes:
                        routes[transition.to_key] = routes[key] + (action,)
                        queue.append(transition.to_key)

        return routes

    def scale(self) -> str:
        """How big this map is, in four numbers. Constant length.

        The head of `summary()`, split out because the two are read by
        different consumers. `summary()` grows with the map -- it prints every
        state and every action -- and that is right for a reviewer looking at
        a finished crawl. It is wrong for anything sent repeatedly while the
        crawl is still running: `behavior.delta_brief` is called once every
        few states, and a running total that carries the whole state table
        would put back exactly the growth the delta exists to remove.

        Four numbers is enough to tell a claim about this application from a
        claim about applications: a model shown four states needs to know
        whether they are the app or a corner of it.
        """
        edges = sum(len(taken) for taken in self.transitions.values())
        gaps = self.gaps()
        lines = [
            f"{len(self.states)} states, {edges} transitions, "
            f"{len(self.evidence)} observations",
            f"{len(self.frontier())} unexplored actions, "
            f"{sum(len(v) for v in gaps.values())} untried cells",
        ]
        if self.stopped:
            lines.append(f"stopped: {self.stopped}")
        return "\n".join(lines)

    def summary(self) -> str:
        """One screen of text. What the demo shows and what a reviewer reads."""
        lines = [self.scale()]

        if self.skipped:
            lines.append(
                f"{len(self.skipped)} action(s) offered but refused -- these are "
                f"NOT unexplored, they were tried and could not be done:"
            )
            for (key, action), why in self.skipped.items():
                lines.append(f"    {key[:8]} --{action}--  {why}")

        loose = self.nondeterministic()
        if loose:
            lines.append(
                f"{len(loose)} nondeterministic edge(s) -- the projection "
                f"collapsed states that behave differently:"
            )
            lines += [f"    {key[:8]} --{action}-->" for key, action in loose]

        for key, node in self.states.items():
            name = node.label or node.title or node.url
            lines.append(f"\n  [{key[:8]}] {name}")
            lines.append(f"    {urlparse(node.url).path or '/'}")
            for action in node.actions:
                taken = self.transitions.get((key, action))
                if not taken:
                    why = self.skipped.get((key, action))
                    # `x` not `.`: refused is a result, unexplored is a to-do.
                    mark, note = ("x", why) if why else (".", "unexplored")
                    lines.append(f"      {mark}  {action}  ({note})")
                    continue
                for transition in taken:
                    mark = "*" if transition.mutating else "-"
                    where = (
                        "stays"
                        if transition.self_loop
                        else f"-> {transition.to_key[:8]}"
                    )
                    lines.append(f"      {mark}  {action}  {where}")

        return "\n".join(lines)
