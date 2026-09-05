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
from agents import ant, critic, invariants, orchestrator, runner, suite
from agents.explorer import crawler, forms, store
from agents.explorer.synth import Synthesizer
from agents.context import Context, credentials_for, parse as parse_context
from agents.claims import attribute, claimed_by, gaps_for, steer, with_claimed
from agents.generator import scenarios
from agents.llm import load
from agents.shots import shooter
from agents.tracing import start as start_tracing
from ..byok import Choice, byok
from ..config import settings
from ..db import engine, get_session
from ..models import AppState, Event, Run, TestCase, TestSession

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
    unmatched: int = 0,
    halted: bool = False,
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
    if halted:
        # The colony stopped on a provider error -- an exhausted key, a dead
        # route -- and the map is whatever it had walked by then. Green would
        # claim a full exploration happened. `failed` is the other wrong
        # answer: it is red, it reads as "your application is broken", and
        # nothing about the application misbehaved. Same argument as the
        # uncovered claim below, same verdict.
        return "degraded"
    if unmatched:
        # A claim the user typed that no scenario exercises. Not `failed`:
        # nothing about the application misbehaved, and saying otherwise libels
        # it. Not `passed` either -- they named a behaviour and the run did not
        # test it, so green answers a question nobody asked while burying the
        # one they did. `degraded` is exactly the "this run claims less than a
        # full one" case it already exists for.
        return "degraded"
    return "passed" if modelled else "degraded"


def summary_for(
    result: orchestrator.Exploration,
    states: int,
    scenarios: int,
    needing_attention: int,
    modelled: bool,
) -> str:
    """The one line the console's status disclosure shows. One place, so it is
    checkable -- the same argument `status_for` makes one function above.

    The order is the point. A run that *stopped* explains itself before a run
    that *finished* does, because `Exploration.summary` is only ever written by
    the colony's own `finish` call and is therefore empty on exactly the paths
    a human most needs a sentence for. Before this existed the expression was
    inline and read `result.summary or f"stopped: {result.stopped}"`, so a 402
    that had named the affordable token count and the URL to fix it reached the
    header as the word "error".
    """
    if result.stopped_because:
        return result.stopped_because
    if result.summary:
        return result.summary
    if not modelled:
        return (
            f"{states} states, {scenarios} scenarios, {needing_attention} "
            f"needing attention -- crawled without a model. Set "
            f"OPENROUTER_API_KEY (cheapest), ANTHROPIC_API_KEY or "
            f"GEMINI_API_KEY for flows and a summary."
        )
    return f"stopped: {result.stopped}"


def _crawl_only(
    page, target_url: str, emit, checkpoint, shot, credentials, synthesizer
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
        credentials=credentials,
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


def _landings(world) -> dict[str, str]:
    """State key -> the URL the crawl saw it at.

    `attribute` matches a claim against scenarios, and a scenario is told apart
    from its namesake by where it ends. The map holds that; `claims.py` should
    not have to know what a WorldMap is to read it, so it is reduced here.
    """
    return {key: node.url for key, node in world.states.items()}


def _session_context(run: Run, db: Session) -> str | None:
    """The box typed beside this run's URL, or None.

    Read in the request rather than in the background job: the job already owns
    a database session, but making it walk `Run -> TestSession` would give the
    agent layer a reason to know what a session is, and it does not.
    """
    if run.session_id is None:
        return None
    row = db.get(TestSession, run.session_id)
    return row.context if row else None


def _explore(
    run_id: int,
    target_url: str,
    body: ExploreRequest,
    keys: Choice,
    context_raw: str | None = None,
) -> None:
    """The background job. Owns its own DB session and its own browser.

    `keys` travels as an argument rather than as process state because
    FastAPI runs this in a worker thread of a shared process -- see
    `app/byok.py`. `context_raw` travels the same way and for the same reason:
    it is read off the session row in the request, so the job never has to know
    that sessions exist.
    """
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
            provider = load(notify=emit, **keys.kwargs())
        except (RuntimeError, ValueError) as exc:
            emit("error", str(exc))
            emit("warn", "no model: falling back to the deterministic crawler")

        emit("info", f"exploring {target_url}", surface="timeline")
        emit(
            "info",
            f"model: {provider.name} / {provider.model} [{keys.redacted}]"
            if provider
            else "model: none -- breadth-first crawl, no flows and no summary",
        )
        if body.intent and not provider:
            # Better than silently ignoring it: the crawler has nowhere to put
            # an intent, and a user who typed one deserves to know that.
            emit("warn", f"intent ignored without a model: {body.intent!r}")

        # The box beside the URL. Parsed here rather than when it was typed,
        # because telling a password from a sentence takes a model and the model
        # is only chosen now -- the caller may have brought their own key.
        #
        # Announced *before* the call, not after. A slow model turned this into
        # minutes of a run sitting on "running" behind two timeline lines, with
        # nothing saying what it was waiting for.
        if context_raw and context_raw.strip():
            emit("info", "reading the context box", surface="timeline")
        context = parse_context(context_raw or "", provider)
        if context:
            if provider is None:
                # Said plainly. A run that quietly ignored the credentials it
                # was given will fail at the login wall and report a map of the
                # login page, which looks like the app being small rather than
                # like the box being dropped.
                emit(
                    "warn",
                    "context ignored without a model: nothing can tell a "
                    "password from a sentence. Falling back to AIVAR_USERNAME "
                    "/ AIVAR_PASSWORD, if set.",
                )
            elif not context.parsed:
                # Not the same as an empty box, and the difference is what the
                # user should do next: nothing, and try again.
                emit(
                    "warn",
                    "could not read the context box -- the model did not answer. "
                    "Exploring without it; the box itself is fine.",
                )
            else:
                emit("info", f"context: {context.redacted}", surface="timeline")
                if not (context.credentials or context.focus or context.claims):
                    emit(
                        "warn",
                        f"nothing usable in the context box: {context.raw!r}",
                    )

        credentials = credentials_for(context)

        # Session context first, this run's intent second. Both are steering and
        # the model reads them as one section; the later one is the one being
        # typed right now, and recency is the only ordering that matches what a
        # person means by changing their mind.
        intent = " ".join(p for p in (context.focus, body.intent) if p) or None

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
            # The caller's own key pays for payload synthesis too. Left None
            # this resolves a provider lazily from the server's environment,
            # which on a bring-your-own-key run is the wrong wallet. Passing the
            # already-loaded provider supersedes the `api_key=` this used to
            # take, which only ever worked for Claude.
            provider=provider,
        )

        # Assigned inside the `with` block below but read after it, and the
        # `except` path must still find a list rather than a NameError.
        results: list = []
        # Same hazard, same fix: a crawl that throws before generation still
        # reaches the report, and a claim that was never attributed is reported
        # as uncovered rather than crashing the run that would have said so.
        matched: dict[str, tuple[int, ...]] = {claim: () for claim in context.claims}
        answering: dict[str, tuple[str, ...]] = {claim: () for claim in context.claims}
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
                        credentials,
                        synthesizer,
                    )
                else:
                    # Crawl first, then colonise. The deterministic walk costs
                    # no quota and produces the map in a couple of minutes; the
                    # colony then spends its waves on judgement instead of on
                    # rediscovering the same structure. Measured on saucedemo at
                    # equal action budgets: crawler 21 states for nothing,
                    # colony 16 for ~$0.09, and in three complete unseeded runs
                    # the orchestrator never reached `finish` -- so the console
                    # showed a map and never the written account underneath it.
                    #
                    # It also fills the canvas immediately: `checkpoint` streams
                    # the crawl, so the graph draws while the model is still
                    # being asked its first question.
                    emit("info", "crawling deterministically first", surface="explore")
                    seed = crawler.crawl(
                        page,
                        target_url,
                        crawler.Budget(),
                        credentials=credentials,
                        synthesizer=synthesizer,
                        checkpoint=checkpoint,
                        # The seed crawl discovers nearly every state, and
                        # only the few an ant later stands in get
                        # re-photographed -- and `attach_screenshot` is
                        # first-wins, so a state the crawler found and no ant
                        # re-entered kept no picture at all. `_crawl_only` was
                        # handed a camera and this call was not, which made the
                        # thumbnails depend on whether a model was configured.
                        # Measured: a run whose colony died on its first call
                        # left 1 of 7 states with a picture and the rest of the
                        # map reading "no capture"; a healthy colony elsewhere
                        # still managed 17.
                        shot=shooter(page, run_id, settings.artifacts_dir),
                    )
                    emit(
                        "decision",
                        f"seed: {len(seed.states)} states, "
                        f"{sum(len(t) for t in seed.transitions.values())} "
                        f"transitions, {len(seed.skipped)} refused -- "
                        "handing to the colony",
                        surface="explore",
                    )
                    result = orchestrator.run(
                        page,
                        target_url,
                        provider,
                        world=seed,
                        synthesizer=synthesizer,
                        intent=intent,
                        budget=orchestrator.Budget(
                            max_waves=body.max_waves,
                            max_ants=body.max_ants,
                            ant_actions=body.ant_actions,
                        ),
                        credentials=credentials,
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
                        # main's ad6f945: these belong to the coverage widget,
                        # not the general timeline.
                        surface="coverage",
                    )
                # The colony's own account of what it could not reach. Real, and
                # often naming something no computed candidate can -- "we never
                # got past the login wall" is not a cell of the state table --
                # but it is a claim, not a citation, so it does not say "gap".
                for note in result.gaps:
                    emit("info", f"noted: {note}", surface="coverage")
                emit(
                    "warn" if ranked else "info",
                    f"coverage: {len(ranked)} gap(s) before generation"
                    + (f", {len(result.gaps)} note(s) from the colony"
                       if result.gaps else ""),
                    surface="coverage",
                )

                # --- suite -------------------------------------------
                plan = scenarios(result.world)

                # The claims the user typed, matched against tests that already
                # exist -- never against tests a model wrote for them. See
                # `agents/claims.py` for why that distinction is the product.
                #
                # Attribution runs against a *wider* plan than the one that will
                # be executed: `scenarios()` caps and interleaves for fairness,
                # which is right for a suite nobody asked anything specific of,
                # and wrong for the one scenario answering a question somebody
                # typed out. Anything a claim needs is added back below.
                considered = (
                    scenarios(result.world, limit=40) if context.claims else plan
                )
                matched = attribute(
                    context.claims,
                    considered,
                    provider,
                    on_event=lambda level, message: emit(
                        level, message, surface="coverage"
                    ),
                    # Where each scenario ends, in URLs a person recognises.
                    # Two scenarios can carry the same name -- this crawl
                    # produced two "complete the Submit form and submit it" --
                    # so the name cannot be what tells them apart, and without
                    # this a claim the suite already covers reads as untested.
                    where=_landings(result.world),
                )
                plan = with_claimed(plan, considered, matched)

                # One more wave, aimed. This is the meta-agent deciding rather
                # than looping: the claim the first pass could not cover is
                # itself the steer for the second, so this is a different
                # exploration and not a longer one. Exactly once -- a claim the
                # app genuinely does not implement would otherwise buy waves
                # forever, and `agents/critic.py` records why one round is the
                # number the research supports.
                uncovered = steer(matched, provider)
                if uncovered:
                    emit(
                        "decision",
                        f"{len(uncovered)} claim(s) uncovered -- one more wave, "
                        f"aimed at: {'; '.join(uncovered)}",
                        surface="coverage",
                    )
                    result = orchestrator.run(
                        page,
                        target_url,
                        provider,
                        world=result.world,
                        synthesizer=synthesizer,
                        intent=(
                            "Reach and exercise these specific behaviours, which "
                            "the exploration so far has not covered:\n"
                            + "\n".join(f"- {claim}" for claim in uncovered)
                        ),
                        budget=orchestrator.Budget(
                            max_waves=1,
                            max_ants=body.max_ants,
                            ant_actions=body.ant_actions,
                        ),
                        credentials=credentials,
                        on_event=lambda level, message: emit(
                            level, message, surface="explore"
                        ),
                        run_id=run_id,
                        shot=shooter(page, run_id, settings.artifacts_dir),
                        checkpoint=checkpoint,
                    )
                    store.save(result.world, run_id, db)
                    # Re-compiled, not amended: the map grew, so the ranking and
                    # the interleave both change, and patching the old plan would
                    # produce a suite `generator.scenarios` would never emit.
                    plan = scenarios(result.world)
                    considered = scenarios(result.world, limit=40)
                    matched = attribute(
                        context.claims,
                        considered,
                        provider,
                        on_event=lambda level, message: emit(
                            level, message, surface="coverage"
                        ),
                        where=_landings(result.world),
                    )
                    plan = with_claimed(plan, considered, matched)

                answering = claimed_by(matched, considered)
                for claim, names in answering.items():
                    emit(
                        "decision" if names else "warn",
                        f"claim {'covered by' if names else 'uncovered'}: {claim}"
                        + (f" -- {', '.join(names)}" if names else ""),
                        surface="coverage",
                    )

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
                        # `synthesizer` is the run's own, so a `submit[invalid]`
                        # scenario is re-submitted with the payload the crawl
                        # recorded. Without it `forms.perform` refuses the
                        # action and the verdict is a false ESCALATE.
                        outcome = runner.run(
                            page,
                            scenario,
                            credentials=credentials,
                            synthesizer=synthesizer,
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

            # The user's own sentence, with the verdict of the test that
            # answered it. This is the line the whole context box exists for:
            # everything else is steering, and this is the reply.
            #
            # A claim nothing covered says so in the user's words. That is a
            # real answer -- "you asked for this and nothing in the map
            # exercises it" -- and the one thing that must never happen here is
            # a claim reported green because a scenario was nearby.
            for claim, names in answering.items():
                verdicts = [r.verdict for r in results if r.scenario.name in names]
                if not verdicts:
                    emit(
                        "warn",
                        f"claim not tested: {claim}"
                        + (" -- the scenario covering it never returned a verdict"
                           if names else " -- nothing in the suite exercises it"),
                        surface="report",
                    )
                    continue
                # The worst verdict wins: a claim covered by two scenarios, one
                # of which found a defect, is a claim that found a defect.
                worst = next(
                    (v for v in (runner.ESCALATE, runner.DEFECT, runner.HEALED)
                     if v in verdicts),
                    runner.PASSED,
                )
                emit(
                    "error" if worst in {runner.DEFECT, runner.ESCALATE} else "decision",
                    f"claim {worst}: {claim} -- {', '.join(names)}",
                    surface="report",
                )

            unmatched = gaps_for(matched)

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
                f"{len(ranked) + len(unmatched)} gap(s) remaining "
                + (f"({len(unmatched)} of them claims nothing covered) "
                   if unmatched else "")
                + f"({written} rows)",
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
                    tally,
                    violations,
                    incomplete,
                    provider is not None,
                    len(unmatched),
                    halted=bool(result.stopped_because),
                )
                run.summary = summary_for(
                    result,
                    states=len(result.world.states),
                    scenarios=len(plan),
                    needing_attention=tally[runner.DEFECT] + tally[runner.ESCALATE],
                    modelled=provider is not None,
                )
                if unmatched:
                    # The header disclosure reads `run.summary`, and it is the
                    # only place a grey badge explains itself. A run that went
                    # degraded because the user's own sentence went untested
                    # must say that first, ahead of whatever the colony wrote
                    # about where it stopped.
                    run.summary = (
                        f"{len(unmatched)} claim(s) untested: "
                        + "; ".join(
                            gap.why.removeprefix("nothing in the suite exercises: ")
                            for gap in unmatched
                        )
                        + f" -- {run.summary}"
                    )[:500]

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
    keys: Choice = Depends(byok),
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

    background.add_task(
        _explore, run_id, run.target_url, body, keys, _session_context(run, session)
    )
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


def _dispatch_ant(
    run_id: int,
    target_url: str,
    body: AntRequest,
    keys: Choice,
    context_raw: str | None = None,
) -> None:
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
            provider = load(notify=emit, **keys.kwargs())
        except (RuntimeError, ValueError) as exc:
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
                    # An ant sent by hand walks the same app behind the same
                    # login wall. It has a model by definition -- there is no
                    # ant without one -- so the box is always parseable here.
                    credentials=credentials_for(
                        parse_context(context_raw or "", provider)
                    ),
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
    keys: Choice = Depends(byok),
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

    background.add_task(
        _dispatch_ant,
        run_id,
        run.target_url,
        body,
        keys,
        _session_context(run, session),
    )
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
