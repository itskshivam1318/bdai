"""Sessions -- a target URL plus every run, widget and event under it.

The sidebar needs more than the row itself (how many runs, how it last ended),
so the list endpoint returns a summary rather than the bare table. Everything
else here is ordinary CRUD; the interesting scoping lives in `canvas.py` and
`runs.py`, which now filter by `session_id`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models import Artifact, CanvasNode, Event, Run, TestCase, TestSession

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionSummary(TestSession):
    """A session as the sidebar needs it: the row plus its run rollup."""

    run_count: int = 0
    last_status: Optional[str] = None


@router.get("", response_model=list[SessionSummary])
def list_sessions(session: Session = Depends(get_session)):
    rows = session.exec(
        select(TestSession).order_by(TestSession.id.desc())
    ).all()
    out: list[SessionSummary] = []
    for row in rows:
        runs = session.exec(
            select(Run).where(Run.session_id == row.id).order_by(Run.id.desc())
        ).all()
        out.append(
            SessionSummary(
                **row.model_dump(),
                run_count=len(runs),
                last_status=runs[0].status if runs else None,
            )
        )
    return out


@router.post("", response_model=TestSession, status_code=201)
def create_session(body: TestSession, session: Session = Depends(get_session)):
    body.id = None
    session.add(body)
    session.commit()
    session.refresh(body)
    return body


@router.get("/{session_id}", response_model=TestSession)
def get_one(session_id: int, session: Session = Depends(get_session)):
    row = session.get(TestSession, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return row


@router.patch("/{session_id}", response_model=TestSession)
def update_session(
    session_id: int, patch: dict, session: Session = Depends(get_session)
):
    row = session.get(TestSession, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    for key, value in patch.items():
        if key in {"name", "target_url"}:
            setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int, session: Session = Depends(get_session)):
    """Deletes the session and everything hanging off it.

    SQLite here has no cascade configured, so orphan rows are cleaned up by
    hand. Artifact *files* on disk are left alone -- `make reset` owns those.
    """
    row = session.get(TestSession, session_id)
    if row is None:
        raise HTTPException(404, "session not found")

    run_ids = [
        r.id for r in session.exec(select(Run).where(Run.session_id == session_id))
    ]
    if run_ids:
        for model in (Event, TestCase, Artifact):
            for child in session.exec(select(model).where(model.run_id.in_(run_ids))):
                session.delete(child)
        for run in session.exec(select(Run).where(Run.session_id == session_id)):
            session.delete(run)
    for node in session.exec(
        select(CanvasNode).where(CanvasNode.session_id == session_id)
    ):
        session.delete(node)

    session.delete(row)
    session.commit()


@router.get("/{session_id}/runs", response_model=list[Run])
def list_session_runs(session_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(Run).where(Run.session_id == session_id).order_by(Run.id)
    ).all()


@router.get("/{session_id}/events", response_model=list[Event])
def list_session_events(
    session_id: int, after: int = 0, session: Session = Depends(get_session)
):
    """Every event across the session's runs, oldest first.

    `after` is an event id, not a timestamp: the canvas polls with the highest
    id it has already seen, so it only ever pulls the tail.
    """
    run_ids = [
        r.id for r in session.exec(select(Run).where(Run.session_id == session_id))
    ]
    if not run_ids:
        return []
    return session.exec(
        select(Event)
        .where(Event.run_id.in_(run_ids), Event.id > after)
        .order_by(Event.id)
    ).all()
