"""The MCP server itself: five tools over the console's pipeline.

    make mcp

Registered with Claude Code as a stdio server (see `README` section in
`app/CLAUDE.md`). Every tool that changes anything does so through
`client.Console`, so the work shows up in the console's sidebar as it happens
and the developer can watch a run they started from a terminal.

**Why every tool is `async def` wrapping a thread.** `runner.run` drives the
*sync* Playwright API, which refuses to start when an asyncio loop is already
running in its own thread -- the trap `routers/explore.py` documents at length.
An MCP server is an asyncio server, so calling the job inline would reproduce
that failure with a message that points nowhere near this file. `to_thread`
gives the job a thread with no loop, which is the same shape FastAPI's
`BackgroundTasks` gives `_explore`.
"""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from . import crawl as crawl_job, read, verify as verify_job
from .client import Console, ConsoleDown

server = MCPServer(
    "aivar",
    instructions=(
        "AIVAR maps a running web application by exploring it, then replays "
        "recorded flows to tell a cosmetic change apart from a real defect.\n\n"
        "Typical use after editing UI code: call `impact` with the user-visible "
        "strings your diff changed to learn which flows are affected, then "
        "`verify` those flows. A `healed` verdict means the markup moved and "
        "behaviour did not -- no action needed. A `defect` verdict means the "
        "locator still resolved but the application behaved differently, which "
        "is a real bug. `escalate` means the step can no longer be attempted "
        "at all and a human has to say what it now means.\n\n"
        "Requires the AIVAR stack to be running (`make dev`)."
    ),
)


def _console() -> Console:
    return Console()


def _resolve_run(console: Console, run_id: int | None, url: str | None = None) -> int:
    """Every read tool takes an optional run id and defaults to the newest.

    An agent that must call `sessions()` before it can ask anything spends
    three round-trips on a question with one obvious answer.
    """
    if run_id is not None:
        return run_id
    run = read.latest_run(console, url)
    if run is None:
        raise ValueError("no runs yet -- call `explore` with a URL first")
    return run["id"]


@server.tool()
async def sessions() -> dict:
    """List every mapped application and its runs.

    Use this to find a `run_id` when you need one, or to check whether a URL
    has already been explored before spending minutes exploring it again.
    """

    def job() -> dict:
        console = _console()
        try:
            rows = console.sessions()
            return {
                "worktree": console.health().get("worktree"),
                "sessions": [
                    {
                        "session_id": row["id"],
                        "name": row.get("name") or "Untitled session",
                        "target_url": row["target_url"],
                        "runs": row.get("run_count", 0),
                        "last_status": row.get("last_status"),
                        "console_url": console.console_url(row["id"]),
                    }
                    for row in rows
                ],
            }
        finally:
            console.close()

    return await asyncio.to_thread(job)


@server.tool()
async def explore(
    url: str,
    intent: str | None = None,
    name: str | None = None,
    wait: bool = True,
    timeout_s: float = 300.0,
) -> dict:
    """Explore a running web app and build a behavioural map of it.

    An agent colony drives a real browser, filling forms and following links,
    and records every state it reaches and every transition between them. This
    is the prerequisite for `impact` and `verify` -- both read the map this
    produces.

    Minutes, not seconds. `wait=False` returns as soon as the run starts, and
    the session appears in the AIVAR console immediately either way, so the
    developer can watch it draw itself.

    Args:
        url: the running application to map, e.g. http://localhost:3000
        intent: optional steering, e.g. "focus on checkout"
        name: optional label for the session in the sidebar
        wait: block until the exploration finishes
        timeout_s: give up waiting after this long (the run keeps going)
    """

    def job() -> dict:
        console = _console()
        try:
            session = console.session_for(url, name)
            run = console.create_run(session["id"], url)
            console.start_explore(run["id"], intent=intent)

            report = {
                "session_id": session["id"],
                "run_id": run["id"],
                "target_url": url,
                "console_url": console.console_url(session["id"]),
                "status": "running",
            }
            if not wait:
                return report

            finished = console.wait(run["id"], timeout_s=timeout_s)
            report["status"] = finished.get("status")
            report["summary"] = finished.get("summary")
            if finished.get("status") == "running":
                report["note"] = (
                    f"still exploring after {timeout_s:.0f}s -- watch it at "
                    f"{report['console_url']}, or call `map` for what it has "
                    "found so far"
                )
                return report
            # `map` on the same call: an agent that explored wants the result,
            # and making it ask again wastes a round-trip on a run it just
            # watched finish.
            report["map"] = read.map_of(run["id"])
            return report
        finally:
            console.close()

    return await asyncio.to_thread(job)


@server.tool()
async def crawl(
    url: str,
    name: str | None = None,
    max_states: int = 30,
    max_seconds: float = 180.0,
) -> dict:
    """Map an app without a model. Faster than `explore`, and needs no API key.

    A breadth-first walk that takes every action it can reach and records where
    each one led. It covers less than `explore` -- nothing decides what is worth
    trying -- but it is deterministic, free, and enough to give `impact` and
    `verify` a baseline. Use it when no model is configured, or when a repeatable
    map matters more than a thorough one.

    Args:
        url: the running application to map
        name: optional label for the session in the sidebar
        max_states: stop after this many distinct states
        max_seconds: stop after this long
    """

    def job() -> dict:
        console = _console()
        try:
            return crawl_job.crawl(console, url, name, max_states, max_seconds)
        finally:
            console.close()

    return await asyncio.to_thread(job)


@server.tool(name="map")
async def app_map(
    run_id: int | None = None, include_snapshots: bool = False
) -> dict:
    """What the application does: states, transitions and runnable flows.

    Also reports `gaps` (actions seen but never taken) and `nondeterministic`
    edges (one action from one state landing in two different places, which
    means two behaviours are being treated as one state).

    Args:
        run_id: which mapped run to read; defaults to the most recent
        include_snapshots: attach each state's raw accessibility tree. Large.
    """

    def job() -> dict:
        console = _console()
        try:
            return read.map_of(_resolve_run(console, run_id), include_snapshots)
        finally:
            console.close()

    return await asyncio.to_thread(job)


@server.tool()
async def impact(names: list[str], run_id: int | None = None) -> dict:
    """Which recorded flows touch these user-visible strings.

    Call this with the accessible names your diff changed -- button copy, link
    text, field labels, headings -- and it reports the flows that act on them,
    naming the step. Pass the strings from *both* sides of a rename; the old
    one is what the map recorded and the new one is what the page says now.

    Answers "what might I have just broken" before you run anything.

    Two tiers. `affected` flows click or fill the control -- verify these.
    `observing` flows only expect it somewhere in a state delta, which nearly
    every flow on the same page does; they matter when your change *removed*
    something, and are weak evidence otherwise.

    Args:
        names: user-visible strings, e.g. ["Place Order", "Complete Purchase"]
        run_id: which mapped run to read; defaults to the most recent
    """

    def job() -> dict:
        console = _console()
        try:
            return read.impact_of(_resolve_run(console, run_id), names)
        finally:
            console.close()

    return await asyncio.to_thread(job)


@server.tool()
async def verify(
    flows: list[str] | None = None,
    target_url: str | None = None,
    run_id: int | None = None,
) -> dict:
    """Replay recorded flows against the app as it is now, and classify each.

    Per step, one of four verdicts:

      passed    the control resolved unchanged and the app behaved as recorded
      healed    the control moved or was renamed; behaviour is unchanged.
                Cosmetic drift. No action needed.
      defect    the control resolved fine, but the application did something
                different. A real bug -- this is the verdict worth acting on.
      escalate  no control here plays the recorded part any more. A human has
                to say what the step now means.

    Creates a run in the AIVAR console with a live timeline, under the same
    session as its baseline, so the before and after sit side by side.

    Args:
        flows: flow names to replay, from `impact` or `map`. Omit for all.
        target_url: replay somewhere other than where it was recorded
        run_id: the mapped run to take flows from; defaults to the most recent
    """

    def job() -> dict:
        console = _console()
        try:
            return verify_job.verify(
                console,
                _resolve_run(console, run_id, target_url),
                flows=flows,
                target_url=target_url,
            )
        finally:
            console.close()

    return await asyncio.to_thread(job)


def main() -> None:
    try:
        Console().health()
    except ConsoleDown as exc:
        # Fail at startup with the fix in the message. An MCP client that
        # connects successfully and then fails on every tool call is far harder
        # to diagnose than one that never connects.
        raise SystemExit(f"aivar-mcp: {exc}")
    server.run("stdio")


if __name__ == "__main__":
    main()
