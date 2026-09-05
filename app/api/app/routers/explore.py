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
from agents import critic, invariants, orchestrator, runner, suite
from agents.explorer import crawler, store
from agents.explorer.forms import Credentials
from agents.explorer.synth import Synthesizer
from agents.generator import scenarios
from agents.llm import load
from agents.shots import shooter
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


def report_invariants(world, emit) -> tuple[invariants.Violation, ...]:
    """Check the finished map against rules no baseline is needed to apply.

    Every other verdict this router reports is *differential*: `generator.py`
    records what the application did and `runner.py` says DEFECT when it later
    does something else. That oracle has one permanent blind spot -- an app
    already broken when the crawler watched it has its brokenness recorded as
    the specification -- and the blind spot swallows the whole console on any
    target we cannot redeploy, because there is no second deploy to differ
    from and every reachable verdict is PASSED.

    `agents/invariants.py` is the other kind of oracle and was already written;
    it was reachable only from `agents/pipeline.py`, the CLI. This is the same
    call on the surface we demo.

    Silence is reported too. "No rule fired" and "nobody looked" are different
    claims, and only one of them is worth a line in the timeline.
    """
    violations = invariants.check(world)
    for violation in violations:
        emit(
            "error",
            f"invariant [{violation.rule}] {violation.action} "
            f"in {violation.state[:8]} -- {violation.because}",
            "defect",
        )
    if violations:
        rules = ", ".join(sorted({violation.rule for violation in violations}))
        emit("error", f"invariants: {len(violations)} broken ({rules})", "defect")
    else:
        # Not a claim of correctness -- only that these properties were checked
        # and found intact. That belongs to the report; tagging it `defect`
        # would open the defect widget to say nothing was wrong.
        emit(
            "decision",
            "invariants: every rule that could be evaluated over this map held",
            "report",
        )
    return violations


# Which invariants are strong enough to colour the badge, and it is not all of
# them. The split is whether the rule needs a *specification* to be true.
#
#   server-error     the server said 5xx in its own words. Needs nothing.
#   empty-accepted   an empty submission reached the same state as a filled
#                    one, so the fields were not required. Read off the map.
#
# The two left out both rest on `submit[invalid]`, and what makes that payload
# invalid is a policy the synthesizer *guessed*. Measured on our own SUT: the
# cached payload for `button:Continue` is `{Password: "short"}`, the app's only
# rule is `complete = Boolean(email && password)`, and it confirms the order --
# correctly. `invalid-accepted` called that a defect. The fallback payloads are
# worse: `{'Project name': ''}` is an empty submission wearing an invalid
# label, and it would fire the rule against an app doing nothing wrong.
#
# They are still reported, on the defect surface, with their evidence. They are
# simply not proof, and a badge that reddens for a suspicion is a badge that
# means nothing when it reddens for a 5xx. Narrowing the rule itself belongs in
# `agents/invariants.py` -- gating on `Payload.source` is the obvious move --
# and that file is another packet's.
_PROVABLE = frozenset({"server-error", "empty-accepted"})


def status_for(
    tally: dict[str, int],
    violations: tuple[invariants.Violation, ...],
    incomplete: bool,
    modelled: bool,
) -> str:
    """The badge, from everything the run learned. One place, so it is checkable.

    A proven violation counts exactly like a DEFECT because it is one -- found
    without a baseline rather than against one, which on a target we cannot
    redeploy is the only way a defect can be found at all.

    `degraded` survives a model-free run: a map and a suite exist, but no flow
    was named and no intent was honoured, so green would claim more than
    happened. It is unknown to STATUS_TONE and renders grey -- see
    SessionView.tsx.
    """
    proven = sum(1 for violation in violations if violation.rule in _PROVABLE)
    if tally[runner.DEFECT] or tally[runner.ESCALATE] or proven or incomplete:
        return "failed"
    return "passed" if modelled else "degraded"


def _crawl_only(
    page, target_url: str, emit, checkpoint, shot, synthesizer
) -> orchestrator.Exploration:
    """The no-model path: the same WorldMap, built breadth-first with no model.

    Returned as an `Exploration` so the caller's save-and-report block does not
    have to branch. `flows` and `summary` stay empty because naming a user
    journey is the one thing on that path that genuinely needs a model -- the
    graph itself never did, which is the whole argument in `explorer/__init__`.

    `gaps` is left empty on purpose. It used to hold the top three rows of
    `WorldMap.gaps()`, formatted here because the timeline only prints strings
    -- but that is one of four gap kinds, truncated to three, and it made this
    path's coverage claim differ from every other path's. `critic.candidates`
    computes all four from the same map and the caller runs it on both paths.
    """
    world = crawler.crawl(
        page,
        target_url,
        credentials=Credentials.from_env(),
        # Same cache the CLI uses, so a payload the model chose on an earlier
        # run is reused now that there is no model to ask.
        synthesizer=synthesizer,
        checkpoint=checkpoint,
        # The console shows a thumbnail per state whichever path built the map,
        # so a degraded run gets cards with pictures in them rather than boxes.
        shot=shot,
    )
    return orchestrator.Exploration(world=world, stopped="no model")


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
                emit("info", f"  crawled {seen} state(s)")

        # One synthesizer for the whole run, shared by the crawl and the
        # replay. Two reasons it cannot be constructed at either call site:
        # `forms.perform` refuses `submit[invalid]` outright when handed None,
        # so a replay without one turns every invalid-input scenario into an
        # ESCALATE that says "the action would not execute" -- a false
        # unattributable verdict on a test the same function had just written.
        # And the cache is the replay log: the scenario must be re-submitted
        # with the payload the crawl actually recorded, or it is not a replay.
        synthesizer = Synthesizer(
            cache_path=settings.artifacts_dir / "invalid-payloads.json",
            run_id=run_id,
        )

        # Assigned inside the `with` block below but read after it, and the
        # `except` path must still find a list rather than a NameError.
        results: list = []
        # Assigned inside the `with` below and read after it, like `results`.
        ranked: tuple = ()

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
                        synthesizer,
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
                    emit("decision", f"flow: {flow.get('name')} -- {flow.get('why', '')}")
                emit(
                    "decision",
                    f"plan: {len(result.flows)} flows across "
                    f"{len(result.world.states)} states",
                    surface="plan",
                )

                # --- coverage, before generation, as the brief requires
                #
                # `critic.candidates` computes every gap from the map and the
                # model may only reorder them; anything it cites that was not a
                # candidate is dropped and counted. This console used to print
                # `result.gaps` instead -- free text the orchestrator wrote into
                # its `finish` call, uncited and unverifiable, which is the exact
                # class of output `critic.py` exists to make impossible. The two
                # are kept apart rather than merged: one is evidence, the other
                # is an observation, and only one of them can be looked up.
                ranked = critic.prioritise(
                    result.world,
                    provider,
                    intent=body.intent,
                    run_id=run_id,
                    on_event=lambda level, message: emit(
                        level, message, surface="coverage"
                    ),
                )
                for gap in ranked[:12]:
                    emit(
                        "warn",
                        f"gap [{gap.kind}] {gap.action} "
                        f"in {gap.where} -- {gap.risk or gap.why}",
                    )
                # The colony's own account of what it could not reach. Real, and
                # often naming something no computed candidate can -- "we never
                # got past the login wall" is not a cell of the state table --
                # but it is a claim, not a citation, so it does not say "gap".
                for note in result.gaps:
                    emit("info", f"noted: {note}")
                emit(
                    "warn" if ranked else "info",
                    f"coverage: {len(ranked)} gap(s) before generation"
                    + (f", {len(result.gaps)} note(s) from the colony"
                       if result.gaps else ""),
                    surface="coverage",
                )

                # --- suite -------------------------------------------
                plan = scenarios(result.world)
                emit(
                    "warn" if not plan else "decision",
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
                                synthesizer=synthesizer,
                                on_event=emit,
                            )
                        )
                    except Exception as exc:
                        # One scenario that cannot even be replayed must not
                        # cost the others their verdicts.
                        emit("error", f"{scenario.name}: {type(exc).__name__}: {exc}")

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

            # --- invariants --------------------------------------
            # Checked over the finished map rather than during the crawl, and
            # after replay rather than before it, so the timeline reads in the
            # order the evidence arrived. On a target we cannot redeploy this
            # is the only stage that can reach a verdict other than PASSED.
            violations = report_invariants(result.world, emit)

            tally = {
                v: sum(1 for r in results if r.verdict == v)
                for v in (runner.PASSED, runner.HEALED, runner.DEFECT, runner.ESCALATE)
            }
            emit(
                "decision",
                f"report: {tally[runner.PASSED]} passed, {tally[runner.HEALED]} healed, "
                f"{tally[runner.DEFECT]} defect, {tally[runner.ESCALATE]} escalate, "
                f"{len(violations)} invariant(s) broken, "
                f"{len(ranked)} gap(s) remaining ({written} rows)",
                surface="report",
            )

            if run:
                # A run that tested nothing is not a pass either: zero
                # scenarios, or scenarios that all raised before returning a
                # verdict, is not success. For a product whose claim is "a URL
                # in, a meaningful suite out", a green badge over an empty
                # suite is the worst thing to report. The rest of the policy is
                # `status_for`, which `app/probe.py` can check without a
                # browser.
                incomplete = not plan or len(results) != len(plan)
                run.status = status_for(
                    tally, violations, incomplete, provider is not None
                )
                run.summary = result.summary or (
                    f"{len(result.world.states)} states, {len(plan)} scenarios, "
                    f"{tally[runner.DEFECT] + tally[runner.ESCALATE]} needing "
                    f"attention -- crawled without a model. Set "
                    f"OPENROUTER_API_KEY (cheapest), ANTHROPIC_API_KEY or "
                    f"GEMINI_API_KEY for flows and a summary."
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
