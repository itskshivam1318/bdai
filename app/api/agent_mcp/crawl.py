"""Map an app with the deterministic crawler -- no model, no API key.

`explore` drives the agent colony, which is better at deciding *what is worth
trying* and needs a key and a quota to do it. This is the other route to the
same artifact, and `routers/explore.py` already names it as such when it finds
no key configured.

Keeping both exposed matters more here than in the console. An agent connecting
over MCP is on someone else's machine with someone else's credentials, and a
server whose every tool depends on an API key the user has not set is a server
that appears broken. This one always works.

The run is created and narrated over HTTP exactly as `verify` does, so a crawl
started from a terminal is watchable in the console while it happens --
`store.save` is incremental, so the map fills in rather than appearing at the
end.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from agents.explorer import store
from agents.explorer.crawler import Budget, crawl as crawl_world
from agents.explorer.forms import Credentials
from agents.explorer.synth import Synthesizer
from app.db import engine

from .client import Console
from .read import map_of


def crawl(
    console: Console,
    url: str,
    name: str | None = None,
    max_states: int = 30,
    max_seconds: float = 90.0,
) -> dict:
    from playwright.sync_api import sync_playwright

    # Without a synthesizer `forms.perform` refuses every `submit[invalid]`
    # rather than submitting valid input under an invalid label -- correct, but
    # it silently costs the map its error states, which are a deliverable. This
    # was missing here while `crawler.main` had it, so `make crawl` and the MCP
    # tool disagreed about what the same app offers.
    synthesizer = Synthesizer(cache_path=Path("artifacts/invalid-payloads.json"))

    session = console.session_for(url, name)
    run = console.create_run(session["id"], url)
    run_id = run["id"]
    console.patch_run(run_id, status="running")

    def emit(level: str, message: str, surface: str | None = None) -> None:
        console.emit(run_id, level, message, surface)

    emit("info", f"crawling {url} (deterministic, no model)", surface="timeline")

    # Report progress off the checkpoint the crawler already calls after every
    # edge, rather than adding a second callback: one place decides what "a step
    # happened" means, and it is the same place that decides what gets saved.
    seen = {"states": 0}

    def checkpoint(world) -> None:
        with Session(engine) as db:
            store.save(world, run_id, db)
        if len(world.states) != seen["states"]:
            seen["states"] = len(world.states)
            emit("info", f"{seen['states']} states so far")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            world = crawl_world(
                page,
                url,
                budget=Budget(max_states=max_states, max_seconds=max_seconds),
                credentials=Credentials.from_env(),
                synthesizer=synthesizer,
                checkpoint=checkpoint,
            )
            browser.close()
    except Exception as exc:
        emit("error", f"{type(exc).__name__}: {exc}")
        console.patch_run(
            run_id, status="error", summary=f"{type(exc).__name__}: {exc}"[:500]
        )
        raise

    with Session(engine) as db:
        store.save(world, run_id, db)

    emit("decision", world.summary())
    console.patch_run(run_id, status="passed", summary=world.summary())

    return {
        "session_id": session["id"],
        "run_id": run_id,
        "target_url": url,
        "console_url": console.console_url(session["id"]),
        "status": "passed",
        "map": map_of(run_id),
    }
