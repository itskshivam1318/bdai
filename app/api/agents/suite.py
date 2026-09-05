"""Store what the Runner decided, keyed to where on the map it happened.

`TestCase` already carried the healer's record -- `selector`, `healed_selector`,
`status`, `detail`. What it could not say is *where*: a verdict with no place on
the graph cannot colour a node. `path` closes that, and this module is the only
thing that writes it.

**Worst wins.** Several scenarios cross one state, and the map shows one colour.
Taking the last-written verdict would let a passing scenario paint over a
failing one -- the single direction of error that matters, because it hides a
defect. So `verdicts_by_state` reduces with the same severity order
`runner.Result.verdict` already uses on steps.
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models import TestCase

from .runner import DEFECT, ESCALATE, HEALED, PASSED, Result

# Lower is worse. Any status not listed (a legacy 'failed', a 'pending') sorts
# after everything here, so it can never mask a real verdict.
_SEVERITY = {ESCALATE: 0, DEFECT: 1, HEALED: 2, PASSED: 3}


def path_of(result: Result) -> list[str]:
    """The state keys a run actually crossed, in order.

    Derived from `result.steps` -- what executed -- and never from
    `result.scenario.steps` -- what was planned. `runner.run` stops at the first
    ESCALATE or DEFECT, so on a failing multi-step scenario the plan outlives
    the run, and colouring the planned remainder would paint a verdict onto
    states the browser never opened.

    The final key is where the last executed step actually landed, falling back
    to where it expected to. On a defect those differ by definition, and the
    honest answer is where the application actually went.
    """
    if not result.steps:
        return []
    keys = [outcome.step.from_key for outcome in result.steps]
    last = result.steps[-1]
    landed = last.actual_key or last.step.expect.to_key
    if landed and landed != keys[-1]:
        keys.append(landed)
    return keys


def save_results(
    results: list[Result], run_id: int, session: Session, version: str = ""
) -> int:
    """Write one `TestCase` row per result. Returns rows written.

    `version` is the emitted suite label (`v002`) when the results came from
    replaying a kept suite, and empty when they came from a plan compiled in
    memory. Stored rather than inferred, because the two are genuinely
    different claims -- "this is what the saved suite did" and "this is what
    this run's fresh plan did" -- and a reader who cannot tell them apart
    cannot tell a regression from a first sighting.
    """
    written = 0
    for result in results:
        terminal = result.steps[-1] if result.steps else None
        healed = next(
            (s for s in result.steps if s.resolution.healed), None
        )
        session.add(
            TestCase(
                run_id=run_id,
                name=result.scenario.name,
                selector=terminal.step.action if terminal else None,
                healed_selector=healed.resolution.action if healed else None,
                status=result.verdict,
                path=json.dumps(path_of(result)),
                # The planned node, not the last state reached: a defect is
                # interesting exactly where the action was taken, and on a
                # defect those two differ by definition.
                node=result.scenario.node or None,
                suite_version=version or None,
                detail=json.dumps(
                    [
                        {
                            "intent": s.step.intent,
                            "action": s.step.action,
                            "from_key": s.step.from_key,
                            "verdict": s.verdict,
                            "rung": s.resolution.rung,
                            "detail": s.detail,
                            "diff": s.diff,
                            "missing": list(s.missing),
                        }
                        for s in result.steps
                    ]
                ),
            )
        )
        written += 1
    session.commit()
    return written


def verdicts_by_state(run_id: int, session: Session) -> dict[str, str]:
    """State key -> the worst verdict any scenario crossing it reported."""
    worst: dict[str, str] = {}
    rows = session.exec(select(TestCase).where(TestCase.run_id == run_id)).all()
    for row in rows:
        try:
            keys = json.loads(row.path or "[]")
        except json.JSONDecodeError:
            continue
        for key in keys:
            current = worst.get(key)
            if current is None or _SEVERITY.get(row.status, 99) < _SEVERITY.get(
                current, 99
            ):
                worst[key] = row.status
    return worst
