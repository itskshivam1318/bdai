"""Walk an app breadth-first and build its WorldMap. No model calls.

    cd app/api && uv run python -m agents.explorer.crawler <url>

The loop is four lines of idea:

    take an unexplored (state, action) from the frontier
    get back to that state by replaying its shortest path from the entry
    do the action, observe what happened
    record the edge

Everything hard is in `statekey.py` (is this the same state?) and `worldmap.py`
(what does the graph mean?). This file is the boring part on purpose: the
research is emphatic that the architecture that wins -- Temac, AutoDroid -- is a
cheap deterministic crawler that builds the graph, with an expensive model
invoked only once coverage plateaus. 44.4% of WebVoyager's failures are
"navigation stuck", and a model in this loop is how you get there.

**Replay rather than back-navigation.** Returning to a state by replaying its
path from the entry costs more page loads than `page.go_back()`, and it is what
Crawljax does, because back-navigation lies: on an SPA it may restore a route
without restoring the state, and on a form it silently drops what was typed. A
replay that fails to land on the expected key is *detected* -- see
`_replay` -- where a bad `go_back()` is not.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from . import forms
# `DESTRUCTIVE`/`is_safe` moved to forms.py so both walkers can honour them;
# re-exported here because this module's callers and probes name them.
from .forms import DESTRUCTIVE, Credentials, is_safe  # noqa: F401
from .observer import Observation, Observer
from .statekey import state_key
from .synth import Synthesizer
from .worldmap import WorldMap
from ..shots import Shot

# Actions whose *name* suggests they destroy something. A stub, and labelled as
# one: real coverage is Magentic-UI's ActionGuard (arXiv:2507.22358), which
# classifies every action always/never/maybe-irreversible and routes "maybe" to
# a judge. This denylist is what stands between an unattended crawl and someone
# else's data in the meantime, which is why it is here rather than deferred:
# `docs/research/exploration-landscape.md` records an agent in ST-WebAgentBench
# creating an unwanted repository while trying to file an issue.
# Every run leaves a file here. A map that exists only in a process is a map you
# lose -- two real explorations were reduced to their printed summaries because
# nothing wrote the object down, and a printed summary can be read but not
# diffed. Resolved from __file__ so it follows `api/` wherever it moves.
RUNS = Path(__file__).resolve().parent.parent.parent / "artifacts" / "runs"


def _slug(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path).replace("/", "-").replace(
        ":", "-"
    ).strip("-") or "run"


def saved_maps(url: str) -> tuple[Path, ...]:
    """Every map `autosave` has written for this target, oldest last.

    Lives here rather than in `snapshot.py` because the filename convention is
    `autosave`'s -- a reader of these files has to agree with their writer
    about what identifies a target, and one function away from the other is how
    that stops being true.

    The caller that wants "the map before this run" must ask **before**
    crawling: `crawl` autosaves its own map on the way out, so by the time a
    run is finished the newest file for this target is its own.
    """
    try:
        return tuple(sorted(RUNS.glob(f"*-{_slug(url)}.json")))
    except Exception:
        return ()


def autosave(world, url: str, **meta):
    """Write this run beside the others. Never raises -- losing the file must
    not lose the run that produced it."""
    from datetime import datetime, timezone

    from .snapshot import save

    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return save(world, RUNS / f"{stamp}-{_slug(url)}.json", target=url, **meta)
    except Exception:
        return None


@dataclass(frozen=True)
class Budget:
    """Hard caps. Every crawler in the literature has these and no crawler
    terminates without them -- a calendar or a faceted search will otherwise
    generate states forever. A documented incident in the same research: 200+
    navigations on a $3 task.
    """

    max_states: int = 30
    max_actions: int = 120
    max_seconds: float = 180.0

    # How far from the entry an action may be and still be worth taking.
    #
    # This is the cap that stops a *linear chain* eating everything, and it is
    # not interchangeable with the other three. Pointed at
    # practicesoftwaretesting.com the crawler found a 17-step tutorial wizard
    # whose "next" button advances one step at a time; each step is honestly a
    # different state, so no amount of better state abstraction collapses it.
    # It walked all 17 and never opened Favorites, Invoices or Profile, because
    # the locality rule in the loop below outranks breadth and a chain never
    # exhausts. Depth is the only thing that makes it stop.
    #
    # Four is a guess, and a shallow one on purpose: an app's important
    # structure is near its entry, and anything past this is reachable on a
    # later pass with a deeper budget once the shallow map is known.
    max_depth: int = 4


def _same_origin(entry: str, url: str) -> bool:
    """Never leave the app under test. QA Wolf's rule -- explore discovered
    addresses only, never invent one -- with the corollary that a link to
    someone else's domain is not our software and not our business.
    """
    return urlparse(entry).netloc == urlparse(url).netloc


def crawl(
    page: Page,
    entry_url: str,
    budget: Budget | None = None,
    guard=is_safe,
    credentials: Credentials | None = None,
    synthesizer: Synthesizer | None = None,
    checkpoint: Callable[[WorldMap], None] | None = None,
    shot: Shot | None = None,
    trace: Callable[[str], None] | None = None,
) -> WorldMap:
    """Explore from `entry_url` until the frontier empties or the budget runs out."""
    budget = budget or Budget()
    credentials = credentials or Credentials.from_env()
    observer = Observer(page)
    # `actions_of` needs the live page (see forms.form_of), and `record` is
    # always called immediately after observing it, so binding it here is safe.
    world = WorldMap(actions_of=lambda obs: forms.available_actions(page, obs))

    def capture(key: str) -> None:
        """First sighting only. `attach_screenshot` enforces that; this avoids
        paying for the screenshot at all on a state we already have."""
        if shot is None:
            return
        node = world.states.get(key)
        if node is not None and node.screenshot is None:
            world.attach_screenshot(key, shot(key))

    deadline = time.monotonic() + budget.max_seconds
    actions_taken = 0
    # Lives on the map, not in this frame: a refused action is a fact about the
    # application that every reader of the map needs, and the frame is gone by
    # the time anyone asks.
    skipped = world.skipped

    def visit(url: str) -> Observation:
        observer.start_window()
        page.goto(url)
        return observer.observe()

    # Where the browser is standing right now, and what it saw there. Tracking
    # this is what makes the crawl affordable: an action available from the
    # current state costs one click, and the same action reached by replaying
    # from the entry costs a page load plus a settle per step of its path.
    # `None` means we do not know where we are and must replay to find out.
    here = visit(entry_url)
    here_key: str | None = world.record(here)
    capture(here_key)

    def _replay(target_key: str) -> Observation | None:
        """Get back to `target_key`. Returns the observation there, or None.

        The check is the point. Replay can fail honestly -- a one-shot flash
        message, a nonce in the page, a state only reachable with data that no
        longer exists -- and a crawler that assumes it worked records edges from
        the wrong node and quietly corrupts the graph.

        It returns the *observation* rather than a bool because the next action
        may be a form submission, and filling a form needs to know which fields
        are on the page it is standing on.
        """
        route = world.paths().get(target_key)
        if route is None:
            return None

        observation = visit(entry_url)
        if world.record(observation) == target_key:
            return observation

        for step in route:
            observer.start_window()
            if not forms.perform(
                page, step, observation, credentials, synthesizer,
                state_key(observation.snapshot),
            ):
                return None
            observation = observer.observe()
            if world.record(observation) == target_key:
                return observation

        return None

    while time.monotonic() < deadline and actions_taken < budget.max_actions:
        routes = world.paths()
        pending = [
            edge
            for edge in world.frontier()
            if edge not in skipped
            and guard(edge[1])
            and len(routes.get(edge[0], ("",) * 99)) < budget.max_depth
        ]
        if not pending:
            # The distinction this whole change exists for. Both arrive here.
            world.stopped = (
                "every remaining action was refused" if skipped
                else "frontier empty -- nothing left to try"
            )
            break

        # Three ordering rules, most significant first.
        #
        # 1. Exhaust where we are standing. Replay is the dominant cost of the
        #    whole crawl -- a page load plus a settle for every step of a path --
        #    and an action offered by the current state costs one click. Ignoring
        #    this measurably wrecked a run: against an Angular SPA, 58
        #    observations bought a single transition, because the loop walked
        #    back to the entry before every action it was already standing next
        #    to.
        # 2. Then breadth-first, so the shallow structure of the app is mapped
        #    before any one branch is chased deep. QA Wolf's two-phase
        #    BFS-then-DFS; rules 1 and 2 together are roughly that shape.
        # 3. At equal depth, submit a form before wandering off -- but take the
        #    form's *rejected* partitions before the one that succeeds.
        #
        #    The second half of that is not a refinement, it is the difference
        #    between having error-state coverage on a real app and having none.
        #    `submit[valid]` on a login form authenticates the browser context,
        #    and a context cannot be un-authenticated by navigating: replaying
        #    to the logged-out login state afterwards lands on the account page
        #    instead. So every other partition of the form we just crossed
        #    becomes permanently unreachable the moment we cross it.
        #
        #    Measured on `practicesoftwaretesting.com/auth/login`, which is the
        #    shape every serious target has: `submit[valid]` was taken, and
        #    `submit[empty]` and `submit[invalid]` on that same button were both
        #    refused with "could not get back to this state to try it", along
        #    with 44 other actions. The crawl reported zero rejectable-input
        #    edges, so `invariants.check` had nothing to evaluate and returned
        #    a clean report for an app it had barely tested.
        #
        #    Ordering fixes it for free. We are already standing in the state
        #    (rule 1), an empty or invalid submit costs one click and leaves us
        #    in an error state the entry URL still reaches, and the wall still
        #    gets crossed -- one action later, with the error states recorded.
        #    Form actions as a group still outrank plain navigation, so the
        #    original argument for this rule is untouched.

        def _form_order(act: str) -> int:
            if act.startswith(("submit[empty]", "submit[invalid]")):
                return 0
            return 1 if act.startswith("submit[valid]") else 2

        def _priority(edge: tuple[str, str]) -> tuple[int, int, int]:
            state, act = edge
            return (
                0 if state == here_key else 1,
                len(routes.get(state, ("",) * 99)),
                _form_order(act),
            )

        from_key, action = min(pending, key=_priority)

        if from_key != here_key or here is None:
            here = _replay(from_key)
            here_key = from_key if here is not None else None
            if here is None:
                skipped[(from_key, action)] = (
                    "could not get back to this state to try it"
                )
                continue

        observer.start_window()
        if not forms.perform(
            page, action, here, credentials, synthesizer, from_key
        ):
            # The descriptor did not resolve, or a form had nothing fillable.
            # Not a crawler bug -- a fact about the app, and one the Generator
            # needs to know before it writes a test that assumes otherwise.
            #
            # A half-filled form is still a changed page, so we no longer know
            # where we are standing and must not assume.
            skipped[(from_key, action)] = (
                # The two failures measured on real targets, named so the map
                # says which one happened. A form action that cannot be filled
                # is the expensive case: it is usually the login wall, and
                # everything behind it stays unreachable.
                "nothing here could be filled -- the fields have no accessible "
                "name, or the button is in no <form>"
                if action.startswith("submit[")
                else "the control did not resolve, or did not respond"
            )
            here, here_key = None, None
            continue

        after = observer.observe()
        actions_taken += 1

        if not _same_origin(entry_url, after.url):
            skipped[(from_key, action)] = f"left the origin, for {after.url}"
            here, here_key = None, None
            continue

        here_key = world.connect(from_key, action, after).to_key
        capture(here_key)
        here = after

        if trace is not None:
            # Per edge, beside `checkpoint`, because the two answer different
            # questions: `checkpoint` persists the map so it can be *watched*,
            # `trace` says what was just done so a run can be *read*. Measured
            # 2026-09-05: a crawl against a remote target printed nothing for
            # 3m20s, and a stall was indistinguishable from slow progress.
            arrow = "->" if here_key != from_key else "stays"
            trace(f"[{actions_taken:>3}] {from_key[:8]} {action} {arrow} {here_key[:8]}")

        if checkpoint is not None:
            # After every edge, not at the end. A map that only appears when
            # the crawl finishes cannot be watched, and watching it is the
            # demo. `store.save` is incremental, so this is cheap.
            checkpoint(world)

        if len(world.states) >= budget.max_states:
            world.stopped = f"budget: reached max_states={budget.max_states}"
            break
    else:
        # The `while` condition went false rather than a `break` firing.
        world.stopped = (
            f"budget: reached max_actions={budget.max_actions}"
            if actions_taken >= budget.max_actions
            else f"budget: reached max_seconds={budget.max_seconds:.0f}"
        )

    if checkpoint is not None:
        checkpoint(world)

    return world


def main(entry_url: str) -> int:
    credentials = Credentials.from_env()
    # Beside the database and artifacts, so a crawl's decisions live with its
    # evidence. Delete it to make the agent choose fresh payloads.
    synthesizer = Synthesizer(cache_path=Path("artifacts/invalid-payloads.json"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Test and staging targets routinely serve self-signed or expired certs;
        # refusing them would make the agent useless on its own target market. The
        # run still reports that transport security was not verified -- see
        # `_tls_warning`.
        page = browser.new_page(ignore_https_errors=True)
        world = crawl(
            page, entry_url, credentials=credentials, synthesizer=synthesizer,
            # Unbuffered, because the reason this exists is watching a remote
            # crawl that has not finished. See `trace` in `crawl`.
            trace=lambda line: print(line, flush=True),
        )
        browser.close()

    saved = autosave(world, entry_url, mode="crawler")
    print(f"CRAWL       {entry_url}")
    if saved:
        print(f"SAVED       {saved}")
    print(
        "CREDENTIALS "
        + (
            f"{credentials.username} (from AIVAR_USERNAME/AIVAR_PASSWORD)"
            if credentials
            else "none set -- forms get generic values, logins will fail. "
            "Export AIVAR_USERNAME and AIVAR_PASSWORD."
        )
    )
    print(
        "PAYLOADS    "
        + (
            ", ".join(
                f"{count} from {source}"
                for source, count in synthesizer.sources().items()
            )
            or "no invalid payloads needed -- no forms found"
        )
        # A run that fell back said so already; this says *why*, which is the
        # half that was missing when every payload came from the table because
        # the synthesizer was looking for a key nobody had set.
        + (f"  ({synthesizer.unavailable})" if synthesizer.unavailable else "")
    )
    print(f"SYNTH       {synthesizer.model}")
    print()
    print(world.summary())
    print()

    for slot, entry in synthesizer.decisions():
        print(f"INVALID     {slot}")
        for name, value in entry["values"].items():
            print(f"  {name!r} = {value!r}  ({entry['why'].get(name, '')})")
        if entry.get("expect"):
            print(f"  expect: {entry['expect']}")
        print()

    gaps = world.gaps()
    ranked = sorted(gaps.items(), key=lambda item: -len(item[1]))[:3]
    if any(actions for _, actions in ranked):
        print("GAPS        actions this app has elsewhere, absent here")
        print("            (candidates for error-state tests; unranked)")
        for key, actions in ranked:
            if actions:
                print(f"  [{key[:8]}] {', '.join(actions[:6])}")

    return 0 if world.states else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"))
