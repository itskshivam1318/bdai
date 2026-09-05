"""The sentences the user asked for, matched to tests that already exist.

    attribute(claims, scenarios, provider)  ->  {claim: (scenario index, ...)}

**Why this does not generate a test.** The obvious implementation is to hand a
model the claim and the map and let it write a scenario. It cannot, and the
reason is the whole product:

A `Scenario`'s `Expectation` is *measured*. `generator.expectation()` computes
it from the diff between two states the crawl actually observed -- what appeared,
what vanished, whether the app moved. That measurement is the only reason
`runner.py` can tell a renamed button (heal) from a checkout that stopped
working (defect). An expectation a model wrote is one nothing ever observed, so
a step that fails against it cannot be classified at all -- and it would be
unclassifiable on precisely the test the user cared enough about to type out.

So the model does here exactly what it does in `critic.prioritise`: it points at
things it was given, by index. `attribute` validates every index against the
list it supplied and counts what it drops. A claim the model answers with an
invented scenario is *unmatched*, not covered -- because a claim reported as
covered by a test that does not exist is worse than one reported as uncovered.

**An unmatched claim is a result, not a failure.** `gaps_for` turns it into a
`Gap` the report prints, and `pipeline.addressable` treats that gap as one more
exploration could close -- which is true here in a way it is not for most gap
kinds, because the claim itself is the steer for the next wave. If it is still
unmatched after that, the report says so in the user's own words. "You asked for
this and nothing in the map exercises it" is a real answer. A fabricated pass is
not.
"""

from __future__ import annotations

from .critic import Gap
from .llm import Tool, Transcript

ATTRIBUTE = Tool(
    name="attribute",
    description=(
        "Say which of the numbered scenarios exercise each numbered claim. "
        "Cite scenarios by their number only. If no scenario exercises a "
        "claim, give it an empty list -- that is a useful, expected answer, "
        "and far better than a loose match."
    ),
    parameters={
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {
                            "type": "integer",
                            "description": "The claim's number, as listed.",
                        },
                        "scenarios": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "Numbers of the scenarios that actually "
                                "exercise this claim. Empty if none do."
                            ),
                        },
                    },
                    "required": ["claim", "scenarios"],
                },
            }
        },
        "required": ["matches"],
    },
)

SYSTEM = (
    "You decide whether a test suite already covers what a tester asked for. "
    "You are given numbered claims and numbered scenarios, and you call "
    "attribute exactly once.\n\n"
    "A scenario exercises a claim only if running it would actually put the "
    "claim to the test. A scenario that merely visits the same area does not. "
    "Being wrong in the direction of a loose match is the expensive error: it "
    "reports the tester's question as answered by a test that never asked it. "
    "An empty list is the right answer more often than not."
)


def attribute(
    claims: tuple[str, ...],
    scenarios: tuple,
    provider=None,
    on_event=None,
    where: dict[str, str] | None = None,
) -> dict[str, tuple[int, ...]]:
    """Match each claim to the scenarios that exercise it. Indices, verbatim.

    Every claim appears in the result whether or not anything matched, so a
    caller can always tell "not covered" from "not considered". With no
    provider, nothing is matched -- there is no deterministic way to decide
    whether "an out-of-stock item can't be added to the cart" is the same
    question as "add an out-of-stock item to the cart", and a string overlap
    that guessed would be wrong in the expensive direction.
    """

    def emit(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    if not claims:
        return {}
    if provider is None or not scenarios:
        return {claim: () for claim in claims}

    turn = provider.turn(
        SYSTEM, Transcript(prompt=brief(claims, scenarios, where)), [ATTRIBUTE]
    )
    call = next((c for c in turn.calls if c.name == ATTRIBUTE.name), None)
    if call is None:
        emit("warn", "nothing was attributed to the claims; treating them as uncovered")
        return {claim: () for claim in claims}

    matched: dict[str, tuple[int, ...]] = {claim: () for claim in claims}
    invented = 0

    for entry in call.arguments.get("matches") or ():
        if not isinstance(entry, dict):
            invented += 1
            continue
        index = entry.get("claim")
        if not isinstance(index, int) or not 0 <= index < len(claims):
            invented += 1
            continue
        cited: list[int] = []
        for cite in entry.get("scenarios") or ():
            # The extractive-quote requirement, enforced rather than requested.
            # A scenario number outside the list it was handed is one the model
            # imagined, and a claim covered by an imagined test reports a pass
            # nobody ran.
            if isinstance(cite, int) and 0 <= cite < len(scenarios) and cite not in cited:
                cited.append(cite)
            else:
                invented += 1
        matched[claims[index]] = tuple(cited)

    if invented:
        emit("warn", f"{invented} citation(s) pointed at nothing; dropped")
    covered = sum(1 for cites in matched.values() if cites)
    emit(
        "decision",
        f"claims: {covered} of {len(claims)} covered by the compiled suite",
    )
    return matched


def brief(
    claims: tuple[str, ...], scenarios: tuple, where: dict[str, str] | None = None
) -> str:
    """Both numbered lists. The numbers are the only thing the model may return.

    `where` maps a state key to something a person would recognise -- the URL
    the crawl saw it at. It is a plain dict rather than the WorldMap it comes
    from so that this module stays about claims; the caller already holds the
    map and reducing it to key -> url is one comprehension.

    **Without it the brief cannot answer the question it asks.** A crawl of
    practicetestautomation.com produced two scenarios both named "complete the
    Submit form and submit it" -- one landing on `/logged-in-successfully/`,
    one on `/contact/`. Asked which of them covers "a valid login should land
    on the logged-in-successfully page", the model was shown two identical
    lines and correctly declined to guess, so the claim came back *uncovered*
    while the suite had in fact tested it and passed. A false "not tested" on
    the one thing the user asked for by name is worse than not testing it.

    Measured against a live model 2026-09-05: same claim, same scenarios, the
    only difference being the line below -- no match, then the right one of the
    two. A name is not an identity when the generator can emit it twice; where
    a scenario *ends* is what tells them apart, and it is measured rather than
    invented, which is what makes it citable at all.
    """
    where = where or {}
    lines = ["The tester asked for these to be checked:", ""]
    lines += [f"  [{i}] {claim}" for i, claim in enumerate(claims)]
    lines += ["", "The suite compiled from the crawl contains these scenarios:", ""]
    for i, scenario in enumerate(scenarios):
        steps = " -> ".join(step.intent for step in scenario.steps)
        lines.append(f"  [{i}] {scenario.name}")
        lines.append(f"      {steps}")
        # Absent for a scenario whose terminal state is not in the map handed
        # in. Omitted rather than rendered as "unknown": a claim is matched on
        # what the crawl saw, and a line saying nothing is not evidence.
        landing = where.get(scenario.terminal.expect.to_key)
        if landing:
            lines.append(f"      ends on {landing}")
    lines += [
        "",
        "Call attribute once. Cite scenarios by number. An empty list for a "
        "claim nothing here exercises is the correct answer, not a failure.",
    ]
    return "\n".join(lines)


def with_claimed(plan: tuple, considered: tuple, matched: dict[str, tuple[int, ...]]) -> tuple:
    """The ranked suite, plus any scenario a claim needs that the cap dropped.

    `generator.scenarios()` caps the suite and interleaves it so one chatty form
    cannot fill it -- the right policy for a suite nobody asked anything
    specific of. A claim is somebody asking something specific, so the scenario
    that answers it must not be the one fairness discarded. Appended rather than
    promoted: the generator's ranking is still the ranking, and a claim adds to
    the suite rather than reordering it.
    """
    names = {scenario.name for scenario in plan}
    extra = []
    for cites in matched.values():
        for index in cites:
            scenario = considered[index]
            if scenario.name not in names:
                names.add(scenario.name)
                extra.append(scenario)
    return tuple(plan) + tuple(extra)


def claimed_by(
    matched: dict[str, tuple[int, ...]], considered: tuple
) -> dict[str, tuple[str, ...]]:
    """Claim -> the names of the scenarios answering it.

    Names rather than indices because the caller reports against `Result`s, and
    a `Result` carries its `Scenario` -- by the time verdicts exist the index
    into the list the model was shown means nothing.
    """
    return {
        claim: tuple(considered[index].name for index in cites)
        for claim, cites in matched.items()
    }


def steer(matched: dict[str, tuple[int, ...]], provider=None) -> tuple[str, ...]:
    """The claims a second, aimed exploration could still cover. Often empty.

    `pipeline.addressable` calls an unmatched claim explorable, and it is -- but
    only where there is something to aim. Without a provider `attribute` matches
    nothing by construction, so every claim reads as uncovered and a retry would
    re-crawl the same app once per sentence the user typed, finding the same map
    and matching it no better. That is precisely the loop `addressable` exists
    to tell apart from a decision.
    """
    if provider is None:
        return ()
    return tuple(claim for claim, cites in matched.items() if not cites)


def gaps_for(matched: dict[str, tuple[int, ...]]) -> tuple[Gap, ...]:
    """The claims nothing covers, as gaps the report already knows how to print.

    `state_key` and `action` are empty on purpose. On every other kind of gap
    they are the citation -- a row a reader can look up in the map. A claim that
    nothing exercises has no such row by definition, and filling those fields
    with the nearest thing would manufacture the evidence the gap exists to
    report the absence of.
    """
    return tuple(
        Gap(
            kind="unmatched-claim",
            state_key="",
            where="",
            action="",
            why=f"nothing in the suite exercises: {claim}",
        )
        for claim, cites in matched.items()
        if not cites
    )
