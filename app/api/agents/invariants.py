"""Defects the map can prove without ever having seen the app before.

    cd app/api && uv run python -m agents.invariants http://localhost:3000/sut

**Why this file exists.** Every other verdict in this system is *differential*:
`generator.py` records what the application did, and `runner.py` reports a
DEFECT when it later does something else. That is a strong oracle and it has one
permanent blind spot -- an application that was already broken when the crawler
watched it has its brokenness recorded as the specification, and passes forever.

The blind spot is not theoretical for us. `pipeline.fixture_variants` returns
`()` for any URL that is not our own SUT, because `?v=2` and `?bug=1` are knobs
on `web/app/sut/` and query-string noise everywhere else. So against a
third-party target -- which is what the brief calls the primary validation
surface -- the pipeline crawls, generates, replays against the *same unchanged
app*, and every verdict it can reach is PASSED. Three of the runner's four
words are unreachable on any application we cannot redeploy.

An invariant is the other kind of oracle: a claim that is true of a correct web
application regardless of what this one did. It needs no baseline, so it can
fire on the first crawl of an app we have never seen.

**The vocabulary is what makes them cheap.** `forms.available_actions` already
partitions a form's submit into `submit[valid]`, `submit[invalid]` and
`submit[empty]` -- ISTQB equivalence partitioning, computed, sitting on the map
as three edges out of one state. Once those three edges exist, the relations
*between* them are metamorphic properties of the form, and checking them is
dictionary lookups over a graph we already built. No model, no browser, no
network. That is the whole trick: the oracle was already implied by the action
names; nothing had gone looking for it.

**What is deliberately not here.** Anything needing a judgement call about the
*content* of a page -- whether a total is right, whether a recommendation is
sensible. Those need a specification we do not have. Every rule below is a
structural relation between edges that already exist, and each one names which
of the two states involved is the evidence, so a report can cite it.

**Ambiguity is refused, not guessed** -- the same policy `runner.ESCALATE`
follows. `invalid-accepted` fires only when the valid path demonstrably
progressed, because that is the only configuration in which "the app took input
it should have rejected" is the single available reading. When valid and
invalid are identical *and neither moves*, the form is broken in some way we
cannot name from one crawl, and `no-validation` says exactly that instead of
picking the more impressive-sounding half.

**One rule was written, fired, and removed**, and it is worth the paragraph
because the next person will think of it too. `empty-mutates` reported a form
that fired a POST when submitted with nothing filled in. It found four
violations on `testingchallenges.thetestingmap.org` on the first crawl of an
app we had never seen -- and every one of them was unprovable. That form's only
editable field carries no `required` attribute, Playwright's aria snapshot
carries no requiredness flag for us to read (checked: the word "required" on
that page is prose in a `<p>`), and a search box or an optional filter would
trip the same rule. A POST from an empty form is *evidence*, not *proof*, and
the honest home for it is a coverage gap.

Nothing was lost by removing it: the case where an empty submission is provably
wrong is the one where it reaches the same state as valid input, and
`empty-accepted` already reports that from the map alone. Making the rule
precise instead would mean adding requiredness to `Element`, to the snapshot
parser and to the observer -- the hot path -- to salvage a signal we can
already get precisely. If someone adds requiredness for another reason, this
rule becomes worth having again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .explorer.worldmap import Transition, WorldMap

# Same shape as `runner._FORM_ACTION`, redefined rather than imported: `runner`
# sits two layers up (it imports `generator`, which imports this layer), and a
# module that must not grow an import cycle is worth one duplicated regex.
_FORM_ACTION = re.compile(r"^submit\[(?P<mode>\w+)\]:(?P<descriptor>.+)$")

# A response at or above this is the server saying it failed, in its own words.
# 4xx is excluded on purpose: a 401 on a login wall or a 404 on a speculative
# prefetch is the application working, and a rule that cries defect on those
# would be the `assert status == 200` of invariants -- lots of activity, no
# discrimination.
_SERVER_ERROR = 500


@dataclass(frozen=True)
class Violation:
    """One invariant, broken, with the evidence that proves it.

    `evidence` indexes `WorldMap.evidence`, so a report can print the actual
    observation rather than a claim about it -- the same contract
    `Transition.evidence` already carries.
    """

    rule: str
    state: str
    action: str
    because: str
    evidence: int

    def render(self) -> str:
        return (
            f"  [{self.rule}] {self.action}\n"
            f"     in {self.state[:8]}\n"
            f"     {self.because}"
        )


def _submits(world: WorldMap, state: str) -> dict[tuple[str, str], Transition]:
    """The form submissions taken from `state`, keyed by (descriptor, mode).

    Only edges that were actually walked appear here. An action a state offers
    and nobody took is a *coverage gap* -- `critic.candidates()` owns that
    question -- and inventing a violation from an edge we never traversed would
    be exactly the fabrication `critic.prioritise()` exists to discard.
    """
    found: dict[tuple[str, str], Transition] = {}
    for (from_key, action), edges in world.transitions.items():
        if from_key != state or not edges:
            continue
        form = _FORM_ACTION.match(action)
        if form:
            found[(form.group("descriptor"), form.group("mode"))] = edges[0]
    return found


def _moved(edge: Transition) -> bool:
    return edge.to_key != edge.from_key


def payloads_from(synthesizer) -> dict[str, dict]:
    """What each form's `submit[invalid]` actually carried, keyed by descriptor.

    Built by the caller and handed in, rather than read off disk by `check`.
    The difference matters: a rule that opens `invalid-payloads.json` itself
    knows a file format, changes behaviour when the cache moves, and reports a
    clean run for a reason nobody can see. Passing the synthesizer's own
    accounting keeps `check` a pure function of its arguments, and a caller
    with no synthesizer simply passes nothing and gets today's behaviour.

    The synthesizer keys its cache by form *shape* -- `descriptor|role:name,...`
    -- because one form appears as many states. We key by descriptor alone,
    which is what a `Transition.action` carries. Two different forms sharing a
    button label would collide; the consequence is a rationale quoted against
    the wrong form, not a wrong verdict, since every rule below still decides
    from the map.
    """
    return {
        slot.split("|", 1)[0]: entry for slot, entry in synthesizer.decisions()
    }


def _rejectable(payload: dict | None) -> tuple[bool, str]:
    """Whether this payload is input a form could meaningfully reject, and why not.

    **An all-empty payload is an empty submission wearing an `invalid` label.**
    `_fallback` writes `""` for any field its mutation table has no rule for --
    `{'Project name': ''}` and `{'': ''}` were both live in this workspace's
    cache -- and submitting nothing is already `submit[empty]`'s job. Letting
    `invalid-accepted` fire on one reports the same finding twice under the
    name that sounds more serious.

    Unknown payloads are admitted. A caller that passed no synthesizer has not
    told us the input was bad, and refusing to evaluate on that basis would
    silently disable the rule for every caller that has not been updated --
    the opposite failure to the one this guards.
    """
    if payload is None:
        return True, ""
    values = payload.get("values") or {}
    if values and not any(str(v).strip() for v in values.values()):
        return False, "every value it carried was empty"
    return True, ""


def _rationale(payload: dict | None) -> str:
    """The synthesizer's own words for why its input was rejectable.

    Carried into the violation because this is the assumption the finding rests
    on, and the reader is better placed to judge it than we are. `{'Password':
    'short'}` against an application that declares no minimum length is a
    defect only if the policy the synthesizer assumed exists -- so the report
    prints the assumption beside the verdict instead of laundering it into one.
    """
    if not payload:
        return ""
    why = payload.get("why") or {}
    reasons = "; ".join(
        f"{field}: {reason}" for field, reason in why.items() if reason
    ) if isinstance(why, dict) else str(why)
    source = payload.get("source", "unknown")
    return f" [input chosen by {source}{': ' + reasons if reasons else ''}]"


def check(
    world: WorldMap, payloads: dict[str, dict] | None = None
) -> tuple[Violation, ...]:
    """Every invariant broken by what this crawl observed.

    Pure: a `WorldMap` in, violations out. No page, no provider, no database --
    which is what lets `probe.py` check the rules against a hand-built map with
    no server running, and what lets the pipeline run this over a map loaded
    back from `store.load` without re-crawling.

    `payloads` is `payloads_from(synthesizer)` when there was one. It never
    creates a violation; it only suppresses and annotates, so a caller that
    omits it gets a superset of the findings rather than a different set.
    """
    violations: list[Violation] = []
    payloads = payloads or {}

    for state in sorted(world.states):
        submits = _submits(world, state)
        descriptors = sorted({descriptor for descriptor, _ in submits})

        for descriptor in descriptors:
            valid = submits.get((descriptor, "valid"))
            invalid = submits.get((descriptor, "invalid"))
            empty = submits.get((descriptor, "empty"))

            # 1. The form accepted what it was told to reject.
            #
            # Guarded on `_moved(valid)`: the reading "invalid was accepted"
            # requires that being accepted looks like something here. If the
            # valid path never leaves the form either, the two edges agreeing
            # says nothing about validation -- rule 3 takes that case.
            payload = payloads.get(descriptor)
            usable, unusable_because = _rejectable(payload)

            if (
                valid
                and invalid
                and usable
                and _moved(valid)
                and invalid.to_key == valid.to_key
            ):
                violations.append(
                    Violation(
                        rule="invalid-accepted",
                        state=state,
                        action=f"submit[invalid]:{descriptor}",
                        because=(
                            "input the form was given to be rejected reached the "
                            f"same state as valid input ({valid.to_key[:8]}), so "
                            "this form does not reject it" + _rationale(payload)
                        ),
                        evidence=invalid.evidence,
                    )
                )
            elif valid and invalid and not usable and invalid.to_key == valid.to_key:
                # The relation held, but the input that produced it was not
                # rejectable input at all. Silence would be wrong -- something
                # did happen -- and `invalid-accepted` would be wrong too, so
                # this says which of the two it is and stops.
                violations.append(
                    Violation(
                        rule="invalid-not-rejectable",
                        state=state,
                        action=f"submit[invalid]:{descriptor}",
                        because=(
                            "this form treated its invalid-input case the same as "
                            f"valid input, but {unusable_because}, so the case "
                            "never tested rejection and no defect follows from it"
                            + _rationale(payload)
                        ),
                        evidence=invalid.evidence,
                    )
                )

            # 2. Same relation, empty input. Kept separate from rule 1 because
            # the fixes differ -- an empty submission is usually a missing
            # `required`, and invalid input usually a missing format check --
            # and a report that merges them tells a developer less.
            if valid and empty and _moved(valid) and empty.to_key == valid.to_key:
                violations.append(
                    Violation(
                        rule="empty-accepted",
                        state=state,
                        action=f"submit[empty]:{descriptor}",
                        because=(
                            "submitting the form with nothing filled in reached "
                            f"the same state as valid input ({valid.to_key[:8]}), "
                            "so these fields are not required"
                        ),
                        evidence=empty.evidence,
                    )
                )

            # 3. Nothing distinguishes valid from invalid.
            #
            # Weaker than rule 1 and reported as its own rule rather than
            # folded in: we can prove the form does not discriminate, and we
            # cannot prove from one crawl whether that is because it accepts
            # everything or rejects everything. Naming it honestly is the same
            # policy as ESCALATE -- both diffs attached, no coin flipped.
            if (
                valid
                and invalid
                and not _moved(valid)
                and not valid.mutating
                and invalid.to_key == valid.to_key
            ):
                violations.append(
                    Violation(
                        rule="no-validation",
                        state=state,
                        action=f"submit[valid]:{descriptor}",
                        because=(
                            "valid and invalid input both stayed on this state "
                            "and neither fired a request, so the form either "
                            "accepts everything or accepts nothing -- one crawl "
                            "cannot tell which"
                        ),
                        evidence=valid.evidence,
                    )
                )

    violations.extend(_server_errors(world))
    return tuple(violations)


def _server_errors(world: WorldMap) -> list[Violation]:
    """Any transition whose evidence contains the server admitting a failure.

    Reported per edge rather than per response, because the action is what a
    human can reproduce and a bare status line is not.
    """
    found: list[Violation] = []
    for (from_key, _), edges in sorted(world.transitions.items()):
        for edge in edges:
            if not 0 <= edge.evidence < len(world.evidence):
                continue
            failures = [
                call
                for call in world.evidence[edge.evidence].network
                if call.status is not None and call.status >= _SERVER_ERROR
            ]
            if failures:
                call = failures[0]
                found.append(
                    Violation(
                        rule="server-error",
                        state=from_key,
                        action=edge.action,
                        because=(
                            f"{call.method} {call.url} answered {call.status}"
                            + (
                                f" (and {len(failures) - 1} more)"
                                if len(failures) > 1
                                else ""
                            )
                        ),
                        evidence=edge.evidence,
                    )
                )
    return found


def render(violations: tuple[Violation, ...]) -> str:
    """The section a report prints. Empty is a sentence, not a blank."""
    if not violations:
        return (
            "DEFECTS PROVEN BY INVARIANT\n"
            "  none -- no invariant was broken by what this crawl observed.\n"
            "  This is not a claim that the application is correct: an\n"
            "  invariant only sees the properties it was written to see."
        )

    # `invalid-not-rejectable` is not a defect and must never be counted as
    # one. It is the rule reporting that it could not decide, which is a
    # different section of the same report -- printing it under a "DEFECTS"
    # heading is how a suite ends up with an impressive number and no meaning.
    proven = [v for v in violations if v.rule != "invalid-not-rejectable"]
    undecided = [v for v in violations if v.rule == "invalid-not-rejectable"]

    counts: dict[str, int] = {}
    for violation in proven:
        counts[violation.rule] = counts.get(violation.rule, 0) + 1
    tally = "  ".join(f"{rule}: {n}" for rule, n in sorted(counts.items()))

    lines = ["DEFECTS PROVEN BY INVARIANT"]
    if proven:
        lines += [
            f"{len(proven)} violation(s) -- {tally}",
            "  Each of these is a defect on first sight: it needs no recording of",
            "  previous behaviour, so it holds on an application we have never",
            "  crawled before.",
            "",
        ]
        lines += [violation.render() for violation in proven]
    else:
        lines += [
            "  none -- no invariant was broken by what this crawl observed.",
            "  This is not a claim that the application is correct: an",
            "  invariant only sees the properties it was written to see.",
        ]

    if undecided:
        lines += [
            "",
            "CASES THAT COULD NOT BE DECIDED",
            f"{len(undecided)} form(s) where the invalid-input case did not carry",
            "  input a form could reject, so nothing was tested and nothing is",
            "  claimed. These are gaps, not defects.",
            "",
        ]
        lines += [violation.render() for violation in undecided]

    return "\n".join(lines)


def main(entry_url: str) -> int:
    """Crawl `entry_url` and check every invariant over the map it produced.

    **The synthesizer is not optional here, and that is the whole reason this
    function is more than three lines.** `forms.fill_and_submit` refuses
    `submit[invalid]` when no synthesizer is configured, rather than submitting
    valid input under an invalid label -- so without one the map has no invalid
    edges, and `invalid-accepted`, the sharpest rule in this file, cannot fire
    on any application at all. Written the obvious way, calling `crawl()` with
    its defaults, this module ran green against every target it was pointed at
    for exactly that reason.

    Nothing fails when no model is configured. The synthesizer degrades to its
    static mutation table, `Payload.source` records that it did, and the line
    below says so -- because a run that quietly checked fewer rules must not
    look like a run that found nothing wrong.
    """
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from .explorer.crawler import Budget, crawl
    from .explorer.forms import Credentials
    from .explorer.synth import Synthesizer

    synthesizer = Synthesizer(cache_path=Path("artifacts/invalid-payloads.json"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Test and staging targets routinely serve self-signed or expired
        # certificates; refusing them would make this useless on the exact
        # market it is for. `crawler.main` takes the same position.
        page = browser.new_page(ignore_https_errors=True)
        try:
            world = crawl(
                page,
                entry_url,
                budget=Budget(),
                credentials=Credentials.from_env(),
                synthesizer=synthesizer,
            )
        finally:
            browser.close()

    violations = check(world, payloads_from(synthesizer))
    walked = sum(len(edges) for edges in world.transitions.values())
    invalid_edges = sum(
        len(edges)
        for (_, action), edges in world.transitions.items()
        if action.startswith("submit[invalid]")
    )

    # `synthesizer.model` alone reads "none" for a run served entirely from
    # cache, which is indistinguishable from a run that found no provider --
    # and those are opposite facts about how much to trust the invalid edges.
    sources = ", ".join(f"{n} {src}" for src, n in sorted(synthesizer.sources().items()))
    print(f"TARGET      {entry_url}")
    print(f"MAP         {len(world.states)} states, {walked} transitions")
    print(
        f"SYNTH       {synthesizer.model} -- payloads: {sources or 'none asked for'}"
        + (f"  ({synthesizer.unavailable})" if synthesizer.unavailable else "")
    )
    # The denominator for the form rules. Zero here means those rules were not
    # evaluated rather than satisfied, and the two must never read the same.
    print(f"INVALID     {invalid_edges} rejectable-input edge(s) walked\n")
    print(render(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
