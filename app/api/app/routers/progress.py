"""How far the crawl got, and what the map still cannot answer.

    GET /api/runs/{run_id}/progress

**This is a progress number, not a coverage score, and the distinction is the
whole reason it is allowed to exist.** `docs/product/decisions.md` (2026-09-04
19:00) rules out a coverage percentage, and the reasoning holds: its denominator
would be the states x actions table from `worldmap.gaps()`, whose cells are not
equally meaningful -- the SUT produced 63 of 75 cells that were correct and
useless. Dividing by that count produces a number that looks calibrated and is
not.

`frontier()` is a different denominator. An action a state *offers* has either
been taken or it has not; every cell is equally meaningful, because the app
itself put every one of them there. So "7 of 26 offered actions walked" is a
fact about the crawl, and it is deliberately not a claim about the application's
test coverage. `report()` still carries no percentage and `make probe` still
checks that it does not.

Everything else here is a **count**, never a ratio, for exactly the reason the
19:00 decision gives.

**Computed from the database, not from `store.load`.** Rebuilding the WorldMap
re-parses every aria snapshot -- `worldmap.py` says the console needs none of
that -- and this endpoint is polled while a crawl is running.
"""

from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.suite import verdicts_by_state

from ..db import get_session
from ..models import AppState, Run, SkippedAction, StateTransition

router = APIRouter(prefix="/api/runs", tags=["progress"])


class Refusal(BaseModel):
    """Why one offered action could not be taken. Verbatim from the crawler."""

    reason: str
    count: int


class ProgressOut(BaseModel):
    run_id: int
    status: str

    # --- the one ratio, and its parts ------------------------------------
    offered: int  # (state, action) pairs the app put in front of the crawler
    walked: int  # of those, the ones it took
    refused: int  # of those, the ones it tried and could not take
    remaining: int  # offered - walked - refused

    # --- counts, never ratios --------------------------------------------
    states: int
    transitions: int
    mutating: int  # edges that fired a non-GET; the ones that changed something
    untested_states: int  # no scenario has crossed these
    ambiguous_edges: int  # same action, same origin, two destinations
    reasons: list[Refusal]

    @property
    def walked_fraction(self) -> float:
        return self.walked / self.offered if self.offered else 0.0


@router.get("/{run_id}/progress", response_model=ProgressOut)
def get_progress(run_id: int, db: Session = Depends(get_session)) -> ProgressOut:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")

    states = db.exec(select(AppState).where(AppState.run_id == run_id)).all()

    offered: set[tuple[str, str]] = set()
    for row in states:
        try:
            actions = json.loads(row.actions or "[]")
        except json.JSONDecodeError:
            # A corrupt blob is one state contributing nothing, not a 500.
            actions = []
        offered.update((row.key, action) for action in actions)

    edges = db.exec(
        select(StateTransition).where(StateTransition.run_id == run_id)
    ).all()

    # Intersected with `offered` throughout. An edge whose action its origin no
    # longer lists is real history, but it is not a cell of the table being
    # measured -- counting it could put `walked` above `offered`.
    walked = {(e.from_key, e.action) for e in edges} & offered

    refusals = db.exec(
        select(SkippedAction).where(SkippedAction.run_id == run_id)
    ).all()
    # Refused but not walked: the crawler can refuse an action on one visit and
    # take it on another, and having taken it is the stronger fact.
    refused = ({(r.state_key, r.action) for r in refusals} & offered) - walked

    # Same action, same origin, more than one destination. `normalize()`
    # collapsed two states that behave differently -- the map is asserting
    # something it cannot support, which is a coverage fact no ratio conveys.
    destinations: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        destinations.setdefault((edge.from_key, edge.action), set()).add(edge.to_key)

    verdicts = verdicts_by_state(run_id, db)
    counted = Counter(
        r.reason for r in refusals if (r.state_key, r.action) in refused
    )

    return ProgressOut(
        run_id=run_id,
        status=run.status,
        offered=len(offered),
        walked=len(walked),
        refused=len(refused),
        remaining=len(offered) - len(walked) - len(refused),
        states=len(states),
        transitions=len(edges),
        mutating=sum(1 for e in edges if e.mutating),
        # Absence of a verdict is not a pass. This is the coverage question the
        # map can answer honestly: which states has nothing exercised at all.
        untested_states=sum(1 for s in states if verdicts.get(s.key) is None),
        ambiguous_edges=sum(1 for keys in destinations.values() if len(keys) > 1),
        reasons=[
            Refusal(reason=reason, count=count)
            for reason, count in counted.most_common()
        ],
    )
