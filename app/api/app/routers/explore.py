"""Start an exploration from the UI, and stream what the colony decides.

    POST /api/runs/{run_id}/explore   {"intent": "focus on checkout"}

One endpoint. It returns immediately and the colony runs behind it, writing
`Event` rows as it goes -- which is the whole integration, because the canvas is
already listening. `Canvas.tsx` polls session events and opens a widget for any
event carrying a `surface`, so emitting `surface="timeline"` is how the agent
asks to be watched without knowing that widgets exist.

**Why a separate router.** `runs.py` and `sessions.py` belong to the console
work; this file is the seam between the console and `agents/`. Keeping it apart
means the pipeline can be started, restarted or replaced without touching CRUD
that other screens depend on.

**Why Playwright works here at all.** FastAPI runs a `def` background task in a
worker thread, and the sync Playwright API refuses to start only when an asyncio
loop is already running *in its own thread*. A worker thread has none. Writing
this as `async def` would break it, and the failure message ("It looks like you
are using Playwright Sync API inside the asyncio loop") does not obviously point
back to this decision -- hence the note.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

# Absolute, not relative: `agents/` is a sibling of `app/` under `api/`, so a
# relative import would climb above this package and fail at import time.
from agents import orchestrator, runner, suite
from agents.explorer import store
from agents.explorer.forms import Credentials
from agents.generator import scenarios
from agents.llm import load
from agents.shots import shooter
from agents.tracing import start as start_tracing
from ..config import settings
from ..db import engine, get_session
from ..models import Event, Run

router = APIRouter(prefix="/api/runs", tags=["explore"])


class ExploreRequest(BaseModel):
    """Optional steering. The brief requires the URL to be the only *required*
    input, so everything here has a default."""

    intent: str | None = None
    max_waves: int = 3
    max_ants: int = 4
    ant_actions: int = 4


def _explore(run_id: int, target_url: str, body: ExploreRequest) -> None:
    """The background job. Owns its own DB session and its own browser."""
    from playwright.sync_api import sync_playwright

    with Session(engine) as db:

        def emit(level: str, message: str, surface: str | None = None) -> None:
            db.add(
                Event(
                    run_id=run_id,
                    level=level,
                    message=message[:2000],
                    surface=surface,
                )
            )
            db.commit()

        run = db.get(Run, run_id)

        traces = start_tracing()
        if traces:
            emit("info", f"traces: {traces}")

        try:
            provider = load(notify=emit)
        except RuntimeError as exc:
            # No key configured. A dead end, but a legible one -- and the
            # deterministic crawler is still a route to a map.
            emit("error", str(exc))
            if run:
                run.status = "error"
                run.summary = "no model configured"
                run.finished_at = datetime.now(timezone.utc)
                db.add(run)
                db.commit()
            return

        emit("info", f"exploring {target_url}", surface="timeline")
        emit("info", f"model: {provider.name} / {provider.model}")

        results: list = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                result = orchestrator.run(
                    page,
                    target_url,
                    provider,
                    intent=body.intent,
                    budget=orchestrator.Budget(
                        max_waves=body.max_waves,
                        max_ants=body.max_ants,
                        ant_actions=body.ant_actions,
                    ),
                    credentials=Credentials.from_env(),
                    on_event=emit,
                    run_id=run_id,
                    shot=shooter(page, run_id, settings.artifacts_dir),
                )

                rows = store.save(result.world, run_id, db)
                emit(
                    "decision",
                    f"map saved: {len(result.world.states)} states, "
                    f"{sum(len(t) for t in result.world.transitions.values())} "
                    f"transitions ({rows} rows)",
                )

                # --- plan ------------------------------------------------
                for flow in result.flows:
                    emit("decision", f"flow: {flow.get('name')} -- {flow.get('why', '')}")
                emit(
                    "decision",
                    f"plan: {len(result.flows)} flows across "
                    f"{len(result.world.states)} states",
                    surface="plan",
                )

                # --- coverage, before generation, as the brief requires ---
                for gap in result.gaps:
                    emit("warn", f"gap: {gap}")
                emit(
                    "warn" if result.gaps else "info",
                    f"coverage: {len(result.gaps)} gap(s) before generation",
                    surface="coverage",
                )

                # --- suite -----------------------------------------------
                plan = scenarios(result.world)
                emit(
                    "decision",
                    f"suite: {len(plan)} scenarios compiled from recorded paths",
                    surface="suite",
                )

                # --- run and heal ----------------------------------------
                credentials = Credentials.from_env()
                results = []
                for scenario in plan:
                    try:
                        results.append(
                            runner.run(
                                page,
                                scenario,
                                credentials=credentials,
                                on_event=emit,
                            )
                        )
                    except Exception as exc:
                        # One scenario that cannot even be replayed must not
                        # cost the other ten their verdicts.
                        emit("error", f"{scenario.name}: {type(exc).__name__}: {exc}")

                browser.close()

            written = suite.save_results(results, run_id, db)

            for outcome in results:
                for step in outcome.healed_steps:
                    emit(
                        "decision",
                        f"healed: {step.step.action} -> {step.resolution.action} "
                        f"({step.resolution.rung})",
                        surface="heal",
                    )
                if outcome.verdict in {runner.DEFECT, runner.ESCALATE}:
                    emit(
                        "error",
                        f"{outcome.verdict}: {outcome.scenario.name}",
                        surface="defect",
                    )

            tally = {v: sum(1 for r in results if r.verdict == v) for v in (
                runner.PASSED, runner.HEALED, runner.DEFECT, runner.ESCALATE
            )}
            emit(
                "decision",
                f"report: {tally[runner.PASSED]} passed, {tally[runner.HEALED]} healed, "
                f"{tally[runner.DEFECT]} defect, {tally[runner.ESCALATE]} escalate, "
                f"{len(result.gaps)} gap(s) remaining ({written} rows)",
                surface="report",
            )

            if run:
                run.status = "failed" if tally[runner.DEFECT] else "passed"
                run.summary = result.summary or f"stopped: {result.stopped}"

        except Exception as exc:
            # An exploration that dies half way still discovered something, but
            # the map lives in the function frame we are unwinding past. Record
            # why, plainly, rather than leaving a run stuck on "running".
            emit("error", f"{type(exc).__name__}: {exc}")
            emit("error", traceback.format_exc()[-1500:])
            if run:
                run.status = "error"
                run.summary = f"{type(exc).__name__}: {exc}"[:500]

        if run:
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()


@router.post("/{run_id}/explore", status_code=202)
def start_exploration(
    run_id: int,
    body: ExploreRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Kick off exploration for an existing run. Returns before it finishes."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status == "running":
        raise HTTPException(409, "this run is already exploring")

    run.status = "running"
    session.add(run)
    session.commit()

    background.add_task(_explore, run_id, run.target_url, body)
    return {"run_id": run_id, "status": "running"}
