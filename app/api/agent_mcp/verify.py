"""Replay a mapped run's scenarios and classify what happened.

    verify(console, baseline_run_id, flows=[...], target_url=...)

This is the loop-closer. `explore` answers *what does this app do*; `verify`
answers *did my change break it, and is the break cosmetic or real* -- which is
the only question a coding agent actually has after editing a file.

**Why the job lives here rather than behind an endpoint.** `routers/explore.py`
is owned by the console track, which is folding generate-and-run into its own
background job. Adding a second endpoint to that file would collide; running the
same two modules from this process does not, and costs nothing, because
`generator` and `runner` are pure library code that take a `Page`.

The run is still created, narrated and closed over HTTP, so a verification
started from Claude Code lands in the sidebar next to the exploration that
produced its baseline -- which is the point of scoping runs to a session.
"""

from __future__ import annotations

from sqlmodel import Session

from agents import generator, planner, runner
from agents.explorer import store
from agents.explorer.forms import Credentials
from app.db import engine

from .client import Console

# `TestCase.status` predates the classifier and has no cell for the distinction
# the classifier exists to draw. Both failures are `failed` to the console's
# rollup; which failure it was survives in `detail`, never lost, just not
# encoded in a column another track owns.
STATUS = {
    runner.PASSED: "passed",
    runner.HEALED: "healed",
    runner.DEFECT: "failed",
    runner.ESCALATE: "failed",
}


def scenarios_for(run_id: int, limit: int = 8) -> tuple[generator.Scenario, ...]:
    """Every runnable scenario the baseline run's map supports."""
    with Session(engine) as db:
        world = store.load(run_id, db)
    return generator.scenarios(
        world, limit=limit, per_page=planner.share(world, limit)[1]
    )


def verify(
    console: Console,
    baseline_run_id: int,
    flows: list[str] | None = None,
    target_url: str | None = None,
    limit: int = 8,
) -> dict:
    """Replay scenarios from `baseline_run_id`, reporting a verdict for each.

    `target_url` is the drift knob: replaying a scenario recorded against the
    app as it was, at the app as it is now, is the whole experiment. Left None
    it replays where it was recorded, which is the useful default when the
    change is a redeploy rather than a different URL.
    """
    from playwright.sync_api import sync_playwright

    baseline = console.get_run(baseline_run_id)
    session_id = baseline.get("session_id")
    url = target_url or baseline["target_url"]

    scenarios = scenarios_for(baseline_run_id, limit=limit)
    if flows:
        wanted = {name.strip() for name in flows}
        scenarios = tuple(s for s in scenarios if s.name in wanted)

    run = console.create_run(session_id, url)
    run_id = run["id"]
    console.patch_run(run_id, status="running")

    def emit(level: str, message: str, surface: str | None = None) -> None:
        console.emit(run_id, level, message, surface)

    if not scenarios:
        emit("warn", f"no scenarios to verify from run {baseline_run_id}")
        console.patch_run(run_id, status="failed", summary="no scenarios")
        return _report(console, run_id, session_id, baseline_run_id, url, [])

    emit(
        "info",
        f"verifying {len(scenarios)} flow(s) from run {baseline_run_id} against {url}",
        surface="timeline",
    )

    results: list[runner.Result] = []
    credentials = Credentials.from_env()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            # Test and staging targets routinely serve self-signed or expired certs;
            # refusing them would make the agent useless on its own target market. The
            # run still reports that transport security was not verified -- see
            # `_tls_warning`.
            page = browser.new_page(ignore_https_errors=True)
            for scenario in scenarios:
                result = runner.run(
                    page,
                    scenario,
                    target_url=url,
                    credentials=credentials,
                    on_event=emit,
                )
                results.append(result)
                _persist(console, run_id, result)
                emit("decision", f"{result.verdict.upper()}  {scenario.name}")
            browser.close()
    except Exception as exc:
        # A verification that dies still classified whatever it reached. Record
        # why rather than leaving the run stuck on `running` -- the same failure
        # `explore.py` guards against, for the same reason.
        emit("error", f"{type(exc).__name__}: {exc}")
        console.patch_run(
            run_id, status="error", summary=f"{type(exc).__name__}: {exc}"[:500]
        )
        return _report(console, run_id, session_id, baseline_run_id, url, results)

    worst = _worst(results)
    console.patch_run(
        run_id,
        status="passed" if worst in {runner.PASSED, runner.HEALED} else "failed",
        summary=_summary(results),
    )
    return _report(console, run_id, session_id, baseline_run_id, url, results)


def _persist(console: Console, run_id: int, result: runner.Result) -> None:
    """One `TestCase` row per step -- the self-healing story, as evidence.

    `selector` and `healed_selector` are what those columns were built for: the
    action as recorded, and what the resolution ladder fell back to when the
    recorded one no longer matched.
    """
    for index, step in enumerate(result.steps, start=1):
        console.add_test(
            run_id,
            name=f"{result.scenario.name} / {index}. {step.step.intent}",
            selector=step.step.action,
            healed_selector=(
                step.resolution.action if step.resolution.healed else None
            ),
            status=STATUS.get(step.verdict, "failed"),
            detail=f"[{step.verdict}] {step.detail}"
            + (f"\n{step.diff}" if step.diff else ""),
        )


def _worst(results: list[runner.Result]) -> str:
    for verdict in (runner.ESCALATE, runner.DEFECT, runner.HEALED, runner.PASSED):
        if any(r.verdict == verdict for r in results):
            return verdict
    return runner.PASSED


def _summary(results: list[runner.Result]) -> str:
    tally: dict[str, int] = {}
    for result in results:
        tally[result.verdict] = tally.get(result.verdict, 0) + 1
    return ", ".join(f"{count} {verdict}" for verdict, count in tally.items())


def _report(
    console: Console,
    run_id: int,
    session_id: int | None,
    baseline_run_id: int,
    url: str,
    results: list[runner.Result],
) -> dict:
    """What the MCP client sees. Verdicts first; prose only where it earns it."""
    return {
        "run_id": run_id,
        "session_id": session_id,
        "baseline_run_id": baseline_run_id,
        "target_url": url,
        "verdict": _worst(results) if results else "escalate",
        "console_url": console.console_url(session_id) if session_id else None,
        "flows": [
            {
                "name": result.scenario.name,
                "verdict": result.verdict,
                "steps": [
                    {
                        "intent": step.step.intent,
                        "verdict": step.verdict,
                        "action": step.step.action,
                        "resolved": step.resolution.action,
                        "rung": step.resolution.rung,
                        "detail": step.detail,
                        "diff": step.diff,
                    }
                    for step in result.steps
                ],
            }
            for result in results
        ],
    }
