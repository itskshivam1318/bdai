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

import json
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

# Absolute, not relative: `agents/` is a sibling of `app/` under `api/`, so a
# relative import would climb above this package and fail at import time.
from agents import ant, orchestrator, runner, suite
from agents.explorer import crawler, forms, store
from agents.explorer.forms import Credentials
from agents.explorer.synth import Synthesizer
from agents.generator import scenarios
from agents.llm import load
from agents.shots import shooter
from agents.tracing import start as start_tracing
from ..config import settings
from ..db import engine, get_session
from ..models import AppState, Event, Run, TestCase

router = APIRouter(prefix="/api/runs", tags=["explore"])


class ExploreRequest(BaseModel):
    """Optional steering. The brief requires the URL to be the only *required*
    input, so everything here has a default."""

    intent: str | None = None
    max_waves: int = 3
    max_ants: int = 4
    ant_actions: int = 4


def _crawl_only(page, target_url: str, emit, checkpoint, shot) -> orchestrator.Exploration:
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
        # The console shows a thumbnail per state whichever path built the map,
        # so a degraded run gets cards with pictures in them rather than boxes.
        shot=shot,
    )
    gaps = tuple(
        f"[{key[:8]}] {', '.join(actions[:6])}"
        for key, actions in sorted(world.gaps().items(), key=lambda kv: -len(kv[1]))
        if actions
    )
    return orchestrator.Exploration(world=world, stopped="no model", gaps=gaps[:3])


def _tls_warning(target_url: str) -> str | None:
    """Why the browser would have refused this target, or None if it is fine.

    The browser is launched with `ignore_https_errors=True` -- a QA agent whose
    market is staging environments cannot refuse self-signed certs -- but
    silently ignoring them would let a report imply a security posture the run
    never checked. Playwright does not say whether it had to overlook anything,
    so the handshake is re-attempted here with verification on: the only thing
    this needs to know is whether a *verifying* client would have failed.

    Never raises. A target that is unreachable is the crawl's problem to report,
    not this function's -- it returns None and lets navigation produce the real
    error.
    """
    import socket
    import ssl
    from urllib.parse import urlparse

    parsed = urlparse(target_url)
    if parsed.scheme != "https":
        return None

    port = parsed.port or 443
    context = ssl.create_default_context()
    try:
        with socket.create_connection((parsed.hostname, port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=parsed.hostname):
                return None
    except ssl.SSLCertVerificationError as exc:
        return exc.verify_message or str(exc)
    except (OSError, ValueError):
        return None


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

        # Said once, up front, and recorded on the run: everything below this
        # line talked to a host whose identity was never established.
        untrusted = _tls_warning(target_url)
        if untrusted:
            emit(
                "warn",
                f"TLS not verified for {target_url} ({untrusted}) -- "
                "exploring anyway; treat any security claim in this report as "
                "unproven",
                surface="explore",
            )

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
                emit("info", f"crawled {seen} state(s)", surface="explore")

        # Assigned inside the `with` block below but read after it, and the
        # `except` path must still find a list rather than a NameError.
        results: list = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                # Test and staging targets routinely serve self-signed or expired certs;
                # refusing them would make the agent useless on its own target market. The
                # run still reports that transport security was not verified -- see
                # `_tls_warning`.
                page = browser.new_page(ignore_https_errors=True)
                if provider is None:
                    result = _crawl_only(
                        page,
                        target_url,
                        emit,
                        checkpoint,
                        shooter(page, run_id, settings.artifacts_dir),
                    )
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
                        # Tagged, so the colony's own reasoning opens a widget
                        # rather than only scrolling past in the timeline.
                        on_event=lambda level, message: emit(
                            level, message, surface="explore"
                        ),
                        run_id=run_id,
                        # Without this no state gets a picture and every card on
                        # the map is an empty box.
                        shot=shooter(page, run_id, settings.artifacts_dir),
                        # The same callback the no-model path already gets. The
                        # console polls `/runs/{id}/map`, which reads the
                        # database, so without this the colony's map is invisible
                        # until the run ends -- an empty canvas for the whole
                        # exploration and a finished graph in one jump.
                        checkpoint=checkpoint,
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
                    emit(
                        "decision",
                        f"flow: {flow.get('name')} -- {flow.get('why', '')}",
                        surface="plan",
                    )
                emit(
                    "decision",
                    f"plan: {len(result.flows)} flows across "
                    f"{len(result.world.states)} states",
                    surface="plan",
                )

                # --- coverage, before generation, as the brief requires
                for gap in result.gaps:
                    emit("warn", f"gap: {gap}", surface="coverage")
                emit(
                    "warn" if result.gaps else "info",
                    f"coverage: {len(result.gaps)} gap(s) before generation",
                    surface="coverage",
                )

                # --- suite -------------------------------------------
                plan = scenarios(result.world)
                for scenario in plan:
                    emit(
                        "info",
                        f"{scenario.name} ({len(scenario.steps)} steps)",
                        surface="suite",
                    )
                emit(
                    "warn" if not plan else "decision",
                    f"suite: {len(plan)} scenarios compiled from recorded paths",
                    surface="suite",
                )

                # --- run and heal ------------------------------------
                credentials = Credentials.from_env()
                for index, scenario in enumerate(plan, start=1):
                    # Emitted *before* the replay, not after: a scenario takes
                    # seconds and this is the only line that says which one is
                    # on the page right now.
                    emit(
                        "info",
                        f"{index}/{len(plan)} replaying {scenario.name}",
                        surface="run",
                    )
                    try:
                        outcome = runner.run(
                            page,
                            scenario,
                            credentials=credentials,
                            on_event=emit,
                        )
                        results.append(outcome)
                        emit(
                            "error" if outcome.verdict in {runner.DEFECT, runner.ESCALATE}
                            else "decision",
                            f"{scenario.name}: {outcome.verdict}"
                            + (
                                f" ({len(outcome.healed_steps)} healed)"
                                if outcome.healed_steps
                                else ""
                            ),
                            surface="run",
                        )
                    except Exception as exc:
                        # One scenario that cannot even be replayed must not
                        # cost the others their verdicts.
                        emit(
                            "error",
                            f"{scenario.name}: {type(exc).__name__}: {exc}",
                            surface="run",
                        )

                browser.close()

            # Clearing stale rows is the caller's policy, not the store's: a
            # re-run of the same `run_id` is a second opinion about the same
            # app, not eight more tests, and `store.save` takes the same view of
            # states. Uncommitted, so the delete and the write land together.
            for stale in db.exec(select(TestCase).where(TestCase.run_id == run_id)).all():
                db.delete(stale)

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
                #
                # Neither is a run that tested nothing a pass. Zero scenarios,
                # or scenarios that all raised before returning a verdict, is
                # not success: for a product whose claim is "a URL in, a
                # meaningful suite out", a green badge over an empty suite is
                # the worst thing to report.
                incomplete = not plan or len(results) != len(plan)
                if tally[runner.DEFECT] or tally[runner.ESCALATE] or incomplete:
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


class AntRequest(BaseModel):
    """One ant, aimed by hand at one state.

    The colony decides where its own ants go; this is the override. `action` is
    optional because the two useful questions are different ones: "take *this*
    branch and tell me what is behind it" (an action, copied verbatim off the
    map) and "look harder here" (no action, just a place).
    """

    state_key: str
    action: str | None = None
    instruction: str | None = None
    # Smaller than a colony ant's five. A hand-dispatched ant is answering one
    # question, and the person who asked it is watching the rail.
    budget: int = 3


def _manual_tag(world) -> str:
    """`u1`, `u2`, ... -- the same shape as `w2a1`, and never colliding with it.

    Attribution is what colours a node and an edge on the map, so an ant sent by
    hand has to be tellable from the wave that found the state it started on.
    """
    # Edges as well as states: an ant that walked a branch the map already had
    # discovers no new state, so counting states alone hands the next ant the
    # same tag and two dispatches become one colour on the map.
    used = {
        node.found_by
        for node in world.states.values()
        if node.found_by and node.found_by.startswith("u")
    } | {
        edge.found_by
        for edges in world.transitions.values()
        for edge in edges
        if edge.found_by and edge.found_by.startswith("u")
    }
    return f"u{len(used) + 1}"


def _brief(action: str | None, instruction: str | None) -> str | None:
    """What the ant is told. None when the caller said nothing at all.

    The action is quoted verbatim and named as the *first* thing to do rather
    than the only thing: `tools.ANT_TOOLS` validates every action against the
    state's own list, so a paraphrase is rejected, and an ant that took the
    branch and then stopped looking would waste the two actions it has left.
    """
    parts = []
    if action:
        parts.append(
            f"Start by taking exactly this action, copied verbatim: {action}\n"
            "Then report what changed -- whether it opened a state the map did "
            "not have, and whether anything about it looks wrong."
        )
    if instruction:
        parts.append(instruction)
    return "\n\n".join(parts) or None


def _dispatch_ant(run_id: int, target_url: str, body: AntRequest) -> None:
    """The background job for one hand-aimed ant. Mirrors `_explore`'s shape.

    Writes into the same run as the crawl that produced the map: a state this
    ant discovers is a state of *this* application, and putting it in a run of
    its own would mean a second graph that shares no nodes with the one the
    person is looking at.
    """
    from playwright.sync_api import sync_playwright

    with Session(engine) as db:

        def emit(level: str, message: str, surface: str | None = "explore") -> None:
            db.add(
                Event(run_id=run_id, level=level, message=message[:2000], surface=surface)
            )
            db.commit()

        try:
            provider = load(notify=emit)
        except RuntimeError as exc:
            # Unlike the crawl, there is no deterministic fallback here: an ant
            # *is* the model. Say so and stop rather than pretending.
            emit("error", f"no model, so no ant: {exc}")
            return

        world = store.load(run_id, db)
        node = world.states.get(body.state_key)
        if node is None:
            emit("error", f"no state {body.state_key[:8]} in this run")
            return

        tag = _manual_tag(world)
        emit(
            "decision",
            f"ant {tag} -> {body.state_key[:8]}: "
            + (f"take {body.action}" if body.action else "sent by hand"),
        )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(ignore_https_errors=True)
                # `store.load` leaves this None on purpose -- it needs a live
                # page. Without it `world.record` cannot compute a state's
                # actions and every state the ant reaches looks empty.
                world.actions_of = lambda obs: forms.available_actions(page, obs)
                world.attribution = tag

                report = ant.explore(
                    page,
                    world,
                    provider,
                    entry_url=target_url,
                    start_key=body.state_key,
                    instruction=_brief(body.action, body.instruction),
                    credentials=Credentials.from_env(),
                    budget=max(1, min(body.budget, 8)),
                    run_id=run_id,
                    shot=shooter(page, run_id, settings.artifacts_dir),
                )
                browser.close()

            rows = store.save(world, run_id, db)
            for step in report.trail:
                emit("info", f"  {tag} {step}")
            emit(
                "decision",
                f"  ant {tag} <- {report.actions_taken} action(s), "
                f"{report.states_discovered} new state(s), ended: {report.ended}"
                + (f" ({rows} rows)" if rows else ""),
            )
            if report.summary:
                emit("info", f"  {tag}: {report.summary}")
            if report.uncertain:
                emit("warn", f"  {tag} uncertain: {report.uncertain}")
            for branch in report.branches:
                emit(
                    "info",
                    f"  {tag} branch [{branch.get('priority', '?')}] "
                    f"{branch.get('action', '?')} -- {branch.get('why', '')}",
                )
        except Exception as exc:
            emit("error", f"ant {tag}: {type(exc).__name__}: {exc}")
            emit("error", traceback.format_exc()[-1500:])


@router.post("/{run_id}/ant", status_code=202)
def dispatch_ant(
    run_id: int,
    body: AntRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Send one ant to a state on this run's map. Returns before it lands."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status == "running":
        # Two browsers writing observations into one run would interleave the
        # append-only evidence the transitions are indexed against.
        raise HTTPException(409, "this run is still exploring; wait for it to finish")

    state = session.exec(
        select(AppState).where(
            AppState.run_id == run_id, AppState.key == body.state_key
        )
    ).first()
    if state is None:
        raise HTTPException(404, "no such state on this run's map")

    # Checked here rather than left to the ant: the ant would spend a model call
    # discovering it, and the caller is a UI that has the list already.
    if body.action:
        try:
            available = json.loads(state.actions or "[]")
        except json.JSONDecodeError:
            available = []
        if body.action not in available:
            raise HTTPException(400, "that action is not available on this state")

    background.add_task(_dispatch_ant, run_id, run.target_url, body)
    return {"run_id": run_id, "state_key": body.state_key, "status": "dispatched"}


@router.get("/{run_id}/transcripts")
def list_transcripts(run_id: int, session: Session = Depends(get_session)):
    """Every agent conversation this run wrote, newest last.

    Metadata comes from the filename rather than the file: `tracing.py` names
    them `<stamp>-<role>[-<label>].json`, and a listing that opened all of them
    would read every prompt and every tool result to render a list of names.

    The content is not returned here -- the files are already served by the
    static mount, so the viewer fetches the one it is showing and no more.
    """
    if session.get(Run, run_id) is None:
        raise HTTPException(404, "run not found")

    directory = settings.artifacts_dir / "transcripts" / f"run-{run_id}"
    if not directory.is_dir():
        return []

    out = []
    for path in sorted(directory.glob("*.json")):
        stem = path.stem
        # <YYYYmmdd-HHMMSS-ffffff>-<role>[-<label>]
        parts = stem.split("-")
        role = parts[3] if len(parts) > 3 else "agent"
        label = "-".join(parts[4:]) if len(parts) > 4 else None
        out.append(
            {
                "name": path.name,
                "role": role,
                "label": label,
                "bytes": path.stat().st_size,
                "written_at": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                # Relative to the artifacts mount, which is how every other
                # artifact in this API is addressed (see `shots.py`).
                "url": f"transcripts/run-{run_id}/{path.name}",
            }
        )
    return out
