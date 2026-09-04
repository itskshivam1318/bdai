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
from sqlmodel import Session, select

# Absolute, not relative: `agents/` is a sibling of `app/` under `api/`, so a
# relative import would climb above this package and fail at import time.
from agents import orchestrator, runner
from agents.explorer import crawler, store
from agents.explorer.forms import Credentials
from agents.explorer.synth import Synthesizer
from agents.generator import scenarios
from agents.llm import load
from agents.tracing import start as start_tracing
from ..config import settings
from ..db import engine, get_session
from ..models import Event, Run, TestCase

router = APIRouter(prefix="/api/runs", tags=["explore"])


class ExploreRequest(BaseModel):
    """Optional steering. The brief requires the URL to be the only *required*
    input, so everything here has a default."""

    intent: str | None = None
    max_waves: int = 3
    max_ants: int = 4
    ant_actions: int = 4


def _crawl_only(page, target_url: str, emit, checkpoint) -> orchestrator.Exploration:
    """The no-model path: the same WorldMap, built breadth-first with no model.

    Returned as an `Exploration` so the caller's save-and-report block does not
    have to branch. `flows` and `summary` stay empty because naming a user
    journey is the one thing on that path that genuinely needs a model -- the
    graph itself never did, which is the whole argument in `explorer/__init__`.

    `gaps` is rendered here rather than in the caller: the orchestrator hands
    back prose a model wrote, `WorldMap.gaps()` hands back a dict, and the
    timeline only knows how to print strings.
    """
    world = crawler.crawl(
        page,
        target_url,
        credentials=Credentials.from_env(),
        # Same cache the CLI uses, so a payload the model chose on an earlier
        # run is reused now that there is no model to ask.
        synthesizer=Synthesizer(cache_path=settings.artifacts_dir / "invalid-payloads.json"),
        checkpoint=checkpoint,
    )
    gaps = tuple(
        f"[{key[:8]}] {', '.join(actions[:6])}"
        for key, actions in sorted(world.gaps().items(), key=lambda kv: -len(kv[1]))
        if actions
    )
    return orchestrator.Exploration(world=world, stopped="no model", gaps=gaps[:3])


def _save_results(results, run_id: int, db: Session) -> int:
    """Replay outcomes as `TestCase` rows. Idempotent per run.

    Stands in for `suite.save_results` (Task 4), which lives on `work/map`
    along with `agents/suite.py`. Writing it here rather than creating that
    file keeps this change off the map branch's territory -- swap the call when
    the branch lands, and delete this.

    Rows are cleared before writing: a re-run of the same `run_id` is a second
    opinion about the same app, not eight more tests. `store.save` takes the
    same view of states.
    """
    for stale in db.exec(select(TestCase).where(TestCase.run_id == run_id)).all():
        db.delete(stale)

    for outcome in results:
        healed = outcome.healed_steps
        # The terminal step is what the scenario is *about* -- generator.py
        # builds one scenario per distinct terminal action -- so its locator is
        # the one worth recording against the row.
        terminal = outcome.scenario.terminal
        db.add(
            TestCase(
                run_id=run_id,
                name=outcome.scenario.name,
                selector=terminal.action,
                healed_selector=healed[-1].resolution.action if healed else None,
                status=outcome.verdict,
                detail=" | ".join(
                    f"[{s.verdict}] {s.step.intent}" for s in outcome.steps
                )[:2000]
                or None,
            )
        )

    db.commit()
    return len(results)


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

        # A missing key degrades the run; it does not end it. The error message
        # has always named `explorer.crawler` as the way to get a map anyway,
        # and the crawler needs a browser and a URL -- both of which are right
        # here. Returning instead was the message telling the user to go and do
        # by hand the thing this function was already standing in front of.
        provider = None
        try:
            provider = load(notify=emit)
        except RuntimeError as exc:
            emit("error", str(exc))
            emit("warn", "no model: falling back to the deterministic crawler")

        emit("info", f"exploring {target_url}", surface="timeline")
        emit(
            "info",
            f"model: {provider.name} / {provider.model}"
            if provider
            else "model: none -- breadth-first crawl, no flows and no summary",
        )
        if body.intent and not provider:
            # Better than silently ignoring it: the crawler has nowhere to put
            # an intent, and a user who typed one deserves to know that.
            emit("warn", f"intent ignored without a model: {body.intent!r}")

        # Incremental, because a map that only appears when the crawl finishes
        # cannot be watched, and watching it is the demo. `store.save` is
        # idempotent, so re-saving an unchanged map writes nothing.
        seen = 0

        def checkpoint(world) -> None:
            nonlocal seen
            store.save(world, run_id, db)
            if len(world.states) > seen:
                seen = len(world.states)
                emit("info", f"  crawled {seen} state(s)")

        # Assigned inside the `with` block below but read after it, and the
        # `except` path must still find a list rather than a NameError.
        results: list = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                if provider is None:
                    result = _crawl_only(page, target_url, emit, checkpoint)
                else:
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
                    )
                # --- map ---------------------------------------------
                # The browser stays open past this point: generation reads the
                # map, but replay drives a live page, and reopening one would
                # throw away the session the crawl just established.
                rows = store.save(result.world, run_id, db)
                emit(
                    "decision",
                    f"map saved: {len(result.world.states)} states, "
                    f"{sum(len(t) for t in result.world.transitions.values())} "
                    # "new": the crawl checkpoints as it goes, so a healthy run
                    # ends at 0 here. Bare "(0 rows)" reads like a failed write.
                    f"transitions ({rows} new rows)",
                )

                # --- plan --------------------------------------------
                for flow in result.flows:
                    emit("decision", f"flow: {flow.get('name')} -- {flow.get('why', '')}")
                emit(
                    "decision",
                    f"plan: {len(result.flows)} flows across "
                    f"{len(result.world.states)} states",
                    surface="plan",
                )

                # --- coverage, before generation, as the brief requires
                for gap in result.gaps:
                    emit("warn", f"gap: {gap}")
                emit(
                    "warn" if result.gaps else "info",
                    f"coverage: {len(result.gaps)} gap(s) before generation",
                    surface="coverage",
                )

                # --- suite -------------------------------------------
                plan = scenarios(result.world)
                emit(
                    "decision",
                    f"suite: {len(plan)} scenarios compiled from recorded paths",
                    surface="suite",
                )

                # --- run and heal ------------------------------------
                credentials = Credentials.from_env()
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
                        # cost the others their verdicts.
                        emit("error", f"{scenario.name}: {type(exc).__name__}: {exc}")

                browser.close()

            written = _save_results(results, run_id, db)

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

            tally = {
                v: sum(1 for r in results if r.verdict == v)
                for v in (runner.PASSED, runner.HEALED, runner.DEFECT, runner.ESCALATE)
            }
            emit(
                "decision",
                f"report: {tally[runner.PASSED]} passed, {tally[runner.HEALED]} healed, "
                f"{tally[runner.DEFECT]} defect, {tally[runner.ESCALATE]} escalate, "
                f"{len(result.gaps)} gap(s) remaining ({written} rows)",
                surface="report",
            )

            if run:
                # A defect is a defect whoever found it, so it outranks the
                # model question. Absent one, `degraded` survives a model-free
                # run: a map and a suite exist, but no flow was named and no
                # intent was honoured, so green would claim more than happened.
                # `degraded` is unknown to STATUS_TONE and renders grey, which
                # is the point -- see SessionView.tsx.
                if tally[runner.DEFECT] or tally[runner.ESCALATE]:
                    run.status = "failed"
                else:
                    run.status = "passed" if provider else "degraded"
                run.summary = result.summary or (
                    f"{len(result.world.states)} states, {len(plan)} scenarios, "
                    f"{tally[runner.DEFECT] + tally[runner.ESCALATE]} needing "
                    f"attention -- crawled without a model. Set "
                    f"ANTHROPIC_API_KEY for flows and a summary."
                    if provider is None
                    else f"stopped: {result.stopped}"
                )

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
