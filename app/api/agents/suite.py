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
    """The state keys a scenario crosses, in order.

    Every step names where it started; the last one also names where it landed.
    That final key is why this is not just a comprehension: a scenario whose
    terminal action opened a confirmation state must colour that state too, or
    the most interesting node on the map stays grey.
    """
    keys = [step.from_key for step in result.scenario.steps]
    terminal = result.scenario.terminal.expect.to_key
    if terminal and terminal != keys[-1]:
        keys.append(terminal)
    return keys


def save_results(results: list[Result], run_id: int, session: Session) -> int:
    """Write one `TestCase` row per result. Returns rows written."""
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
