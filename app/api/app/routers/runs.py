"""QA runs, their per-check results, evidence, and agent timeline."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models import Artifact, Event, Run, TestCase

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[Run])
def list_runs(
    session_id: int | None = None, session: Session = Depends(get_session)
):
    query = select(Run)
    if session_id is not None:
        query = query.where(Run.session_id == session_id)
    return session.exec(query.order_by(Run.id.desc())).all()


@router.post("", response_model=Run, status_code=201)
def create_run(run: Run, session: Session = Depends(get_session)):
    run.id = None
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.patch("/{run_id}", response_model=Run)
def update_run(run_id: int, patch: dict, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    for key, value in patch.items():
        if key in {"status", "summary", "target_url"}:
            setattr(run, key, value)
    if patch.get("status") in {"passed", "failed", "error"}:
        run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("/{run_id}/tests", response_model=list[TestCase])
def list_tests(run_id: int, session: Session = Depends(get_session)):
    return session.exec(select(TestCase).where(TestCase.run_id == run_id)).all()


@router.post("/{run_id}/tests", response_model=TestCase, status_code=201)
def add_test(
    run_id: int, test: TestCase, session: Session = Depends(get_session)
):
    test.id, test.run_id = None, run_id
    session.add(test)
    session.commit()
    session.refresh(test)
    return test


@router.get("/{run_id}/events", response_model=list[Event])
def list_events(run_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(Event).where(Event.run_id == run_id).order_by(Event.id)
    ).all()


@router.post("/{run_id}/events", response_model=Event, status_code=201)
def add_event(run_id: int, event: Event, session: Session = Depends(get_session)):
    event.id, event.run_id = None, run_id
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@router.get("/{run_id}/artifacts", response_model=list[Artifact])
def list_artifacts(run_id: int, session: Session = Depends(get_session)):
    return session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
