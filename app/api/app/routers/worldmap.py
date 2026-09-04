"""The graph one run discovered, as the console draws it.

Reads `AppState` and `StateTransition` directly rather than calling
`agents.explorer.store.load`, which rebuilds every `Observation` and re-parses
every aria snapshot to do it. The console needs neither: it draws nodes, edges
and a colour.

`verdict` is **derived, not stored**. A state has no verdict of its own -- what
it has is the scenarios that crossed it, and the worst of their outcomes. See
`agents/suite.py`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.suite import verdicts_by_state

from ..db import get_session
from ..models import AppState, Run, StateTransition

router = APIRouter(prefix="/api/runs", tags=["map"])


class StateOut(BaseModel):
    key: str
    url: str
    title: str
    label: str | None
    is_entry: bool
    actions: list[str]
    screenshot: str | None
    verdict: str | None


class TransitionOut(BaseModel):
    from_key: str
    action: str
    to_key: str
    mutating: bool
    observation_id: int | None


class MapOut(BaseModel):
    run_id: int
    entry_key: str | None
    states: list[StateOut]
    transitions: list[TransitionOut]


@router.get("/{run_id}/map", response_model=MapOut)
def get_map(run_id: int, session: Session = Depends(get_session)) -> MapOut:
    if session.get(Run, run_id) is None:
        raise HTTPException(404, "run not found")

    rows = session.exec(select(AppState).where(AppState.run_id == run_id)).all()
    verdicts = verdicts_by_state(run_id, session)

    states = []
    entry_key = None
    for row in rows:
        if row.is_entry:
            entry_key = row.key
        try:
            actions = json.loads(row.actions or "[]")
        except json.JSONDecodeError:
            # A corrupt blob is one grey node, not a broken console.
            actions = []
        states.append(
            StateOut(
                key=row.key,
                url=row.url,
                title=row.title,
                label=row.label,
                is_entry=row.is_entry,
                actions=actions,
                screenshot=row.screenshot,
                verdict=verdicts.get(row.key),
            )
        )

    edges = session.exec(
        select(StateTransition).where(StateTransition.run_id == run_id)
    ).all()

    return MapOut(
        run_id=run_id,
        entry_key=entry_key,
        states=states,
        transitions=[
            TransitionOut(
                from_key=edge.from_key,
                action=edge.action,
                to_key=edge.to_key,
                mutating=edge.mutating,
                observation_id=edge.observation_id,
            )
            for edge in edges
        ],
    )
