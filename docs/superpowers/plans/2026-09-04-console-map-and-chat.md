# Console Map and Stage Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the session screen into a state map that the pipeline paints — nodes are screens the crawler found, colours are verdicts the runner produced, and the brief's five stages fill in beside it.

**Architecture:** The backend already models everything (`AppState`, `StateTransition`, `TestCase`) and already runs the pipeline's pieces (`generator.scenarios`, `runner.run`) — but only from CLIs. This plan adds screenshots on first sighting, extends the API's background job past exploration into generate-and-run, exposes one read endpoint for the graph, and replaces the free-placement canvas with a split pane: React Flow map left, stage cards right.

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / Playwright (sync API) · Next.js 15 / React 19 / TypeScript / `@xyflow/react` / `@dagrejs/dagre`

**Spec:** `docs/superpowers/specs/2026-09-04-console-map-and-chat-design.md`

## Global Constraints

- **There is no pytest in this repo.** Checks are `check(label, condition)` lines inside probe modules, run by `make probe`. Never add a `tests/` directory or a pytest dependency. Existing harnesses: `agents/probe.py` (agent layer, scripted provider + real browser), `agents/explorer/probe.py` (browser-driven explorer checks).
- `make probe` needs `make dev` running for the SUT at `http://localhost:3000/sut`.
- `make check` (`npx tsc --noEmit && npm run lint`) must pass before any frontend task is committed.
- Python 3.12, dependencies via `uv` (`cd app/api && uv add <pkg>`). Frontend deps via `npm install` in `app/web`.
- **No migrations.** Adding a column means `make reset` and re-running. `make reset` already clears `api/app.db` and `api/artifacts/run-*`.
- Screenshot files live at `api/artifacts/run-<run_id>/<state_key>.png`, served at `/artifacts/run-<run_id>/<state_key>.png`. The `run-` prefix is what `make reset` already deletes.
- `state_key` is a 16-character hex digest (`statekey.py:266`) — safe as a filename with no escaping.
- Severity order for verdicts, everywhere: `escalate > defect > healed > passed`. This matches `runner.Result.verdict` (`runner.py:104`).
- Preserve working behaviour (`CLAUDE.md` principle 7): `make smoke`, `make crawl`, `make generate`, `make loop` and `make specs` must still work after every task.

## Deviation from the spec

The spec lists `GET /api/runs/{run_id}/suite`. It is **not** built. `GET /api/runs/{run_id}/tests` already exists (`routers/runs.py:56`) and returns `TestCase` rows; Task 5 adds `path` and puts the step detail in `detail`, which carries everything the spec's `/suite` listed. One endpoint fewer, same data.

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `app/api/agents/shots.py` | Build the `Shot` callable: take one screenshot, return its stored path, swallow its own failures |
| `app/api/agents/suite.py` | Persist `runner.Result`s as `TestCase` rows, and read a run's verdict-per-state back |
| `app/api/app/routers/worldmap.py` | `GET /api/runs/{run_id}/map` — states, transitions, derived verdicts |
| `app/api/app/probe.py` | API-layer checks against a `TestClient`. Third line of `make probe` |
| `app/web/lib/map.ts` | Map/suite TypeScript types and the dagre layout function |
| `app/web/components/MapPane.tsx` | React Flow graph of one run |
| `app/web/components/StateCard.tsx` | One node: thumbnail, label, inputs, verdict badge; chip below zoom 0.6 |
| `app/web/lib/stages.ts` | The five stages and the surfaces each listens for |
| `app/web/components/StageRail.tsx` | The five pipeline cards, fed by surfaces |

**Modified:**

| File | Change |
|---|---|
| `agents/explorer/worldmap.py` | `StateNode.screenshot`; `WorldMap.attach_screenshot`; carry `screenshot` through `record()`'s revisit branch |
| `agents/explorer/snapshot.py` | Serialise and reload `screenshot` |
| `agents/explorer/store.py` | Save/load `AppState.screenshot` |
| `agents/explorer/crawler.py` | Accept and call `shot` |
| `agents/ant.py` | Accept and call `shot` |
| `agents/orchestrator.py` | Accept `shot`, pass to ants, call it for the entry state |
| `agents/probe.py` | New checks for Tasks 1, 2, 3, 5 |
| `app/models.py` | `AppState.screenshot`, `AppState.fields`; `TestCase.path` |
| `app/main.py` | Register the `worldmap` router |
| `app/routers/explore.py` | Extend the job: generate → run → persist → emit surfaces |
| `app/web/lib/api.ts` | `getMap`, `listTests`, and their types |
| `app/web/components/SessionView.tsx` | Split layout, run picker |
| `app/Makefile` | `probe` gains the API probe line |

---

### Task 1: A state remembers its thumbnail

**Files:**
- Modify: `app/api/agents/explorer/worldmap.py:53-68` (StateNode), `:126-165` (record)
- Modify: `app/api/agents/explorer/snapshot.py:67-76` (save), `:111-118` (load)
- Test: `app/api/agents/probe.py` (new checks in `main()`, before the existing section 4)

**Interfaces:**
- Consumes: nothing.
- Produces: `StateNode.screenshot: str | None`; `WorldMap.attach_screenshot(key: str, path: str | None) -> None`.

- [ ] **Step 1: Write the failing checks**

In `app/api/agents/probe.py`, inside `main()`, immediately after the existing `with tempfile.TemporaryDirectory() as tmp:` block ends (the snapshot round-trip section, around line 280), add:

```python
        # 3c. A thumbnail is attached once and must survive both a revisit and
        #     a round trip through a file. Losing it on revisit is silent: the
        #     node still renders, just without its picture, and only on the
        #     states the crawler visited more than once.
        from .explorer.worldmap import WorldMap as _WorldMap

        shots = _WorldMap()
        first = world.evidence[0]
        shot_key = shots.record(first)
        shots.attach_screenshot(shot_key, "run-1/abc.png")
        shots.record(first)  # a revisit
        ok &= check(
            "a revisit does not lose the thumbnail",
            shots.states[shot_key].screenshot == "run-1/abc.png",
        )
        shots.attach_screenshot(shot_key, "run-1/second.png")
        ok &= check(
            "the first thumbnail wins",
            shots.states[shot_key].screenshot == "run-1/abc.png",
        )
        ok &= check(
            "attaching None is a no-op, not a wipe",
            (
                shots.attach_screenshot(shot_key, None),
                shots.states[shot_key].screenshot == "run-1/abc.png",
            )[1],
        )
        with tempfile.TemporaryDirectory() as tmp:
            from .explorer.snapshot import load as _load
            from .explorer.snapshot import save as _save

            reloaded = _load(_save(shots, f"{tmp}/shots.json", target=SUT))
            ok &= check(
                "a saved map keeps its thumbnails",
                reloaded.states[shot_key].screenshot == "run-1/abc.png",
            )
```

- [ ] **Step 2: Run the checks and watch them fail**

Run: `cd app/api && uv run python -m agents.probe`
Expected: `AttributeError: 'WorldMap' object has no attribute 'attach_screenshot'`

- [ ] **Step 3: Add the field and the method**

In `worldmap.py`, extend the import at the top of the file:

```python
from dataclasses import dataclass, field, replace
```

Add the field to `StateNode`, after `evidence`:

```python
    evidence: tuple[int, ...] = ()  # indices into WorldMap.evidence
    # Path to one screenshot, relative to the artifacts dir. Taken the first
    # time we stood in this state and never retaken -- see `attach_screenshot`.
    screenshot: str | None = None
```

In `record()`, the revisit branch rebuilds the node field by field, so a new field is silently dropped unless it is carried. Add it:

```python
            self.states[key] = StateNode(
                key=existing.key,
                url=existing.url,
                title=existing.title,
                actions=existing.actions,
                label=existing.label,
                evidence=existing.evidence + (index,),
                screenshot=existing.screenshot,
            )
```

Add the method to `WorldMap`, in the `--- writing ---` section after `connect`:

```python
    def attach_screenshot(self, key: str, path: str | None) -> None:
        """Give a state its thumbnail. First one wins; `None` is a no-op.

        Both guards matter. The map is written from two places (`crawler.py`
        and `ant.py`), so "first wins" is what keeps a re-entered state from
        paying for a second picture. And the shooter returns `None` when the
        screenshot failed, which must leave the node alone rather than erase a
        picture an earlier visit already took.
        """
        node = self.states.get(key)
        if node is None or path is None or node.screenshot is not None:
            return
        self.states[key] = replace(node, screenshot=path)
```

- [ ] **Step 4: Carry it through the file format**

In `snapshot.py::save`, add to the state dict:

```python
                        "label": node.label,
                        "screenshot": node.screenshot,
```

In `snapshot.py::load`, add to the `StateNode(...)` construction:

```python
            label=s.get("label"),
            screenshot=s.get("screenshot"),
```

`.get` rather than `[...]`: maps saved before this change must still load.

- [ ] **Step 5: Run the checks and watch them pass**

Run: `cd app/api && uv run python -m agents.probe`
Expected: the four new checks print PASS, and every pre-existing check still prints PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/agents/explorer/worldmap.py app/api/agents/explorer/snapshot.py app/api/agents/probe.py
git commit -m "WorldMap: a state remembers one thumbnail, and keeps it"
```

---

### Task 2: Take the picture once, at the two places that file observations

**Files:**
- Create: `app/api/agents/shots.py`
- Modify: `app/api/agents/explorer/crawler.py:133-141` (signature), `:164`, `:267`
- Modify: `app/api/agents/ant.py:124-135` (signature), `:263`
- Modify: `app/api/agents/orchestrator.py:99-110` (signature), `:127`, `:195-204`
- Test: `app/api/agents/probe.py`

**Interfaces:**
- Consumes: `WorldMap.attach_screenshot` (Task 1).
- Produces: `agents.shots.Shot = Callable[[str], str | None]`; `agents.shots.shooter(page: Page, run_id: int, root: Path) -> Shot`. `crawl(..., shot: Shot | None = None)`, `ant.explore(..., shot: Shot | None = None)`, `orchestrator.run(..., shot: Shot | None = None)`.

- [ ] **Step 1: Write the failing check**

In `app/api/agents/probe.py`, inside the `with sync_playwright() as pw:` block in `main()`, after the existing crawl in section 4 (search for `from .explorer.crawler import crawl`), add a second crawl that shoots:

```python
        # 4b. One picture per state, and not one more. A revisit that shoots
        #     again is invisible in the UI and quadratic in a real crawl.
        from pathlib import Path as _Path

        from .shots import shooter

        with tempfile.TemporaryDirectory() as tmp:
            shot_page = browser.new_page()
            shot_world = crawl(
                shot_page,
                SUT,
                CrawlBudget(max_actions=12, max_seconds=90),
                credentials=CREDENTIALS,
                shot=shooter(shot_page, run_id=1, root=_Path(tmp)),
            )
            shot_page.close()
            files = list((_Path(tmp) / "run-1").glob("*.png"))
            ok &= check(
                "one screenshot per state, never two",
                len(files) == len(shot_world.states),
                f"{len(files)} files for {len(shot_world.states)} states",
            )
            ok &= check(
                "every state carries a thumbnail path",
                all(n.screenshot for n in shot_world.states.values()),
            )
            ok &= check(
                "the recorded path is what the API serves",
                all(
                    n.screenshot.startswith("run-1/") and n.screenshot.endswith(".png")
                    for n in shot_world.states.values()
                ),
            )
```

- [ ] **Step 2: Run the check and watch it fail**

Run: `cd app/api && uv run python -m agents.probe`
Expected: `ModuleNotFoundError: No module named 'agents.shots'`

- [ ] **Step 3: Write the shooter**

Create `app/api/agents/shots.py`:

```python
"""Take one picture of a state, and never take it twice.

Injected rather than imported, in the style `explorer/` already uses for
`actions_of`, `guard`, `synthesizer` and `checkpoint`. Two places file
observations against a live page -- `explorer/crawler.py` (the deterministic
path) and `ant.py` (the colony path the UI runs) -- and duplicating capture in
both is how the two copies drift apart.

So both take a `Shot` and neither decides where files go. That also means a
crawl with `shot=None` takes no pictures at all, which is what `make crawl` and
the probes want: a screenshot per state roughly doubles a small crawl.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

# state key -> path relative to the artifacts dir, or None if it failed.
Shot = Callable[[str], str | None]


def shooter(page: Page, run_id: int, root: Path) -> Shot:
    """A `Shot` that writes `<root>/run-<id>/<key>.png`.

    Returns the path **relative to `root`**, because `root` is the artifacts
    directory the API serves at `/artifacts/` -- so the string a node stores is
    already the URL suffix the browser needs, with no second place that knows
    how to build it.

    Viewport, not `full_page`: the card is a thumbnail, and a full-page shot of
    a long catalogue is mostly bytes nobody looks at.
    """
    directory = root / f"run-{run_id}"

    def shoot(key: str) -> str | None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=directory / f"{key}.png", full_page=False)
        except Exception:
            # A crawl must not die because a picture failed. The node renders
            # without a thumbnail, which is a smaller loss than a lost map.
            return None
        return f"run-{run_id}/{key}.png"

    return shoot
```

- [ ] **Step 4: Call it from the crawler**

In `crawler.py`, add the import:

```python
from ..shots import Shot
```

Extend `crawl`'s signature — add after `checkpoint`:

```python
    shot: Shot | None = None,
```

Inside `crawl`, right after `world = WorldMap(actions_of=...)`, add the helper:

```python
    def capture(key: str) -> None:
        """First sighting only. `attach_screenshot` enforces that; this avoids
        paying for the screenshot at all on a state we already have."""
        if shot is None:
            return
        node = world.states.get(key)
        if node is not None and node.screenshot is None:
            world.attach_screenshot(key, shot(key))
```

Call it at the entry (after `here_key: str | None = world.record(here)`):

```python
    here_key: str | None = world.record(here)
    capture(here_key)
```

And after the edge is recorded (after `here_key = world.connect(from_key, action, after).to_key`):

```python
        here_key = world.connect(from_key, action, after).to_key
        capture(here_key)
        here = after
```

- [ ] **Step 5: Call it from the ant**

In `ant.py`, add the import:

```python
from .shots import Shot
```

Extend `explore`'s signature — add after `run_id`:

```python
    shot: Shot | None = None,
```

After `to_key = world.connect(from_key, action, after).to_key` (line ~263):

```python
            to_key = world.connect(from_key, action, after).to_key
            if shot is not None and world.states[to_key].screenshot is None:
                world.attach_screenshot(to_key, shot(to_key))
```

- [ ] **Step 6: Thread it through the orchestrator**

In `orchestrator.py`, add the import:

```python
from .shots import Shot
```

Extend `run`'s signature — add after `run_id`:

```python
    shot: Shot | None = None,
```

After the entry state is recorded (`entry_key = world.record(observer.observe())`):

```python
    entry_key = world.record(observer.observe())
    if shot is not None:
        world.attach_screenshot(entry_key, shot(entry_key))
```

And in the ant dispatch call, add the argument after `run_id=run_id`:

```python
                    run_id=run_id,
                    shot=shot,
```

- [ ] **Step 7: Run the checks and watch them pass**

Run: `cd app/api && uv run python -m agents.probe`
Expected: the three new checks print PASS. Also confirm the default path is untouched:

Run: `cd app/api && uv run python -m agents.explorer.crawler http://localhost:3000/sut`
Expected: crawls as before, and `ls app/api/artifacts` shows no new `run-*` directory (because `make crawl` passes no `shot`).

- [ ] **Step 8: Commit**

```bash
git add app/api/agents/shots.py app/api/agents/explorer/crawler.py app/api/agents/ant.py app/api/agents/orchestrator.py app/api/agents/probe.py
git commit -m "Screenshots: one per state, taken where observations are filed"
```

---

### Task 3: The thumbnail survives the database

**Files:**
- Modify: `app/api/app/models.py:100-120` (AppState)
- Modify: `app/api/agents/explorer/store.py:82-110` (save), `:170-182` (load)
- Test: `app/api/agents/probe.py`

**Interfaces:**
- Consumes: `StateNode.screenshot` (Task 1).
- Produces: `AppState.screenshot: str | None`, round-tripped by `store.save` / `store.load`.

- [ ] **Step 1: Write the failing check**

In `app/api/agents/probe.py`, after the section 3c checks from Task 1, add:

```python
        # 3d. store.save is incremental and upserts states. A thumbnail that
        #     arrives after a state's first save must reach the database on the
        #     next checkpoint, or the UI shows a picture-less node forever.
        from sqlmodel import Session as _Session
        from sqlmodel import SQLModel as _SQLModel
        from sqlmodel import create_engine as _create_engine

        from .explorer import store as _store

        with tempfile.TemporaryDirectory() as tmp:
            engine = _create_engine(f"sqlite:///{tmp}/probe.db")
            _SQLModel.metadata.create_all(engine)
            with _Session(engine) as db:
                bare = _WorldMap()
                bare_key = bare.record(world.evidence[0])
                _store.save(bare, run_id=7, session=db)
                bare.attach_screenshot(bare_key, "run-7/pic.png")
                _store.save(bare, run_id=7, session=db)
                back = _store.load(7, db)
                ok &= check(
                    "a thumbnail attached after the first save still persists",
                    back.states[bare_key].screenshot == "run-7/pic.png",
                )
```

`tempfile` is already imported by the existing section 3b, and `_WorldMap` by Task 1's checks directly above — both are function-local names bound earlier in the same `main()`, so neither is re-imported here.

- [ ] **Step 2: Run the check and watch it fail**

Run: `cd app/api && uv run python -m agents.probe`
Expected: FAIL — `back.states[...].screenshot` is `None`.

- [ ] **Step 3: Add the column**

In `app/models.py`, add to `AppState` after `label`:

```python
    label: Optional[str] = None
    # Path to one screenshot, relative to the artifacts dir, served at
    # /artifacts/<path>. Null when capture was off or the shot failed.
    screenshot: Optional[str] = None
```

- [ ] **Step 4: Round-trip it**

In `store.py::save`, the insert branch gains the field:

```python
                AppState(
                    run_id=run_id,
                    key=key,
                    url=node.url,
                    title=node.title,
                    actions=actions,
                    label=node.label,
                    screenshot=node.screenshot,
                    is_entry=(key == world.entry_key),
                )
```

And the update branch must notice it — a thumbnail usually arrives on a later checkpoint than the state itself, so `screenshot` belongs in both the condition and the write:

```python
        elif (
            row.actions != actions
            or row.label != node.label
            or row.screenshot != node.screenshot
        ):
            # A state's action set can grow as later visits reveal controls,
            # `label` arrives from a model seam long after the crawl, and the
            # screenshot is taken on first sighting -- which may be after this
            # row was first written by an earlier checkpoint.
            row.actions, row.label = actions, node.label
            row.screenshot = node.screenshot
            session.add(row)
            written += 1
```

In `store.py::load`, add to the `StateNode(...)` construction:

```python
            label=row.label,
            screenshot=row.screenshot,
            evidence=tuple(evidence_of.get(row.key, ())),
```

- [ ] **Step 5: Run the check and watch it pass**

Run: `cd app/api && uv run python -m agents.probe`
Expected: PASS, and every earlier check still passes.

- [ ] **Step 6: Reset the database, since the schema changed**

Run: `cd app && make reset`
Expected: `database and artifacts cleared`

- [ ] **Step 7: Commit**

```bash
git add app/api/app/models.py app/api/agents/explorer/store.py app/api/agents/probe.py
git commit -m "store: persist a state's thumbnail, including when it arrives late"
```

---

### Task 4: A run's verdicts, stored where the map can read them

**Files:**
- Create: `app/api/agents/suite.py`
- Modify: `app/api/app/models.py:66-84` (TestCase)
- Test: `app/api/agents/probe.py`

**Interfaces:**
- Consumes: `runner.Result`, `runner.StepResult`, `runner.Resolution`, `generator.Scenario`.
- Produces: `TestCase.path: str` (JSON list of state keys); `suite.save_results(results: list[Result], run_id: int, session: Session) -> int`; `suite.verdicts_by_state(run_id: int, session: Session) -> dict[str, str]`.

- [ ] **Step 1: Write the failing checks**

In `app/api/agents/probe.py`, immediately after the section 3d block from Task 3, add:

```python
        # 3e. A state the pipeline crossed twice takes the worse verdict. A
        #     map that showed the *last* verdict would hide a defect behind a
        #     pass, which is the one direction that must never happen.
        from .generator import scenarios as _scenarios
        from .runner import DEFECT as _DEFECT
        from .runner import PASSED as _PASSED
        from .runner import Resolution as _Resolution
        from .runner import Result as _Result
        from .runner import StepResult as _StepResult
        from . import suite as _suite

        drafted = _scenarios(world)
        if not drafted:
            ok &= check("the probe world yields a scenario to persist", False)
        else:
            one = drafted[0]

            def _result(verdict: str) -> _Result:
                return _Result(
                    scenario=one,
                    target_url=SUT,
                    steps=[
                        _StepResult(
                            step=step,
                            verdict=verdict,
                            resolution=_Resolution(
                                action=step.action, rung="exact", detail=""
                            ),
                            detail="probe",
                        )
                        for step in one.steps
                    ],
                )

            with tempfile.TemporaryDirectory() as tmp:
                engine = _create_engine(f"sqlite:///{tmp}/suite.db")
                _SQLModel.metadata.create_all(engine)
                with _Session(engine) as db:
                    written = _suite.save_results(
                        [_result(_PASSED), _result(_DEFECT)], run_id=9, session=db
                    )
                    ok &= check("both results are stored", written == 2)

                    crossed = _suite.path_of(_result(_PASSED))
                    verdicts = _suite.verdicts_by_state(9, db)
                    ok &= check(
                        "every state on the path gets a verdict",
                        all(key in verdicts for key in crossed),
                    )
                    ok &= check(
                        "the worse verdict wins where scenarios overlap",
                        set(verdicts.values()) == {_DEFECT},
                        f"got {sorted(set(verdicts.values()))}",
                    )
```

- [ ] **Step 2: Run the checks and watch them fail**

Run: `cd app/api && uv run python -m agents.probe`
Expected: `ImportError: cannot import name 'suite' from 'agents'`

- [ ] **Step 3: Add the column**

In `app/models.py`, add to `TestCase` after `healed_selector`:

```python
    healed_selector: Optional[str] = None
    # JSON list of the state keys this scenario crosses, in order. The map
    # colours a node by the worst verdict among the scenarios naming it, so
    # this is the join between a test result and a place on the graph.
    path: str = "[]"
    status: str = "pending"  # pending | passed | failed | healed | defect | escalate
```

- [ ] **Step 4: Write the persistence layer**

Create `app/api/agents/suite.py`:

```python
"""Store what the Runner decided, keyed to where on the map it happened.

`TestCase` already carried the healer's record -- `selector`, `healed_selector`,
`status`, `detail`. What it could not say is *where*: a verdict with no place on
the graph cannot colour a node. `path` closes that, and this module is the only
thing that writes it.

**Worst wins.** Several scenarios cross one state, and the map shows one colour.
Taking the last-written verdict would let a passing scenario paint over a
failing one -- the single direction of error that matters, because it hides a
defect. So `verdicts_by_state` reduces with the same severity order
`runner.Result.verdict` already uses on steps.
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models import TestCase

from .runner import DEFECT, ESCALATE, HEALED, PASSED, Result

# Lower is worse. Any status not listed (a legacy 'failed', a 'pending') sorts
# after everything here, so it can never mask a real verdict.
_SEVERITY = {ESCALATE: 0, DEFECT: 1, HEALED: 2, PASSED: 3}


def path_of(result: Result) -> list[str]:
    """The state keys a scenario crosses, in order.

    Every step names where it started; the last one also names where it landed.
    That final key is why this is not just a comprehension: a scenario whose
    terminal action opened a confirmation state must colour that state too, or
    the most interesting node on the map stays grey.
    """
    keys = [step.from_key for step in result.scenario.steps]
    terminal = result.scenario.terminal.expect.to_key
    if terminal and terminal != keys[-1]:
        keys.append(terminal)
    return keys


def save_results(results: list[Result], run_id: int, session: Session) -> int:
    """Write one `TestCase` row per result. Returns rows written."""
    written = 0
    for result in results:
        terminal = result.steps[-1] if result.steps else None
        healed = next(
            (s for s in result.steps if s.resolution.healed), None
        )
        session.add(
            TestCase(
                run_id=run_id,
                name=result.scenario.name,
                selector=terminal.step.action if terminal else None,
                healed_selector=healed.resolution.action if healed else None,
                status=result.verdict,
                path=json.dumps(path_of(result)),
                detail=json.dumps(
                    [
                        {
                            "intent": s.step.intent,
                            "action": s.step.action,
                            "from_key": s.step.from_key,
                            "verdict": s.verdict,
                            "rung": s.resolution.rung,
                            "detail": s.detail,
                            "diff": s.diff,
                            "missing": list(s.missing),
                        }
                        for s in result.steps
                    ]
                ),
            )
        )
        written += 1
    session.commit()
    return written


def verdicts_by_state(run_id: int, session: Session) -> dict[str, str]:
    """State key -> the worst verdict any scenario crossing it reported."""
    worst: dict[str, str] = {}
    rows = session.exec(select(TestCase).where(TestCase.run_id == run_id)).all()
    for row in rows:
        try:
            keys = json.loads(row.path or "[]")
        except json.JSONDecodeError:
            continue
        for key in keys:
            current = worst.get(key)
            if current is None or _SEVERITY.get(row.status, 99) < _SEVERITY.get(
                current, 99
            ):
                worst[key] = row.status
    return worst
```

- [ ] **Step 5: Run the checks and watch them pass**

Run: `cd app/api && uv run python -m agents.probe`
Expected: the four new checks print PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/agents/suite.py app/api/app/models.py app/api/agents/probe.py
git commit -m "suite: store verdicts against the states they happened on"
```

---

### Task 5: One endpoint the map reads

**Files:**
- Create: `app/api/app/routers/worldmap.py`
- Create: `app/api/app/probe.py`
- Modify: `app/api/app/main.py` (router registration)
- Modify: `app/Makefile` (the `probe` target)

**Interfaces:**
- Consumes: `AppState`, `StateTransition` (Task 3), `suite.verdicts_by_state` (Task 4).
- Produces: `GET /api/runs/{run_id}/map` returning `{entry_key, states[], transitions[]}` as specified below.

- [ ] **Step 1: Write the failing API checks**

Create `app/api/app/probe.py`:

```python
"""Observable checks for the HTTP surface. Not a test suite -- evidence.

    cd app/api && uv run python -m app.probe

Runs against a `TestClient` and a throwaway SQLite file, so it needs no server,
no browser and no API key. What it is guarding is the shape of the one payload
the console cannot render without: `GET /api/runs/{id}/map`.
"""

from __future__ import annotations

import json
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from .db import get_session
from .main import app
from .models import AppState, Run, StateTransition, TestCase


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def seed(session: Session) -> int:
    """A two-state map with one edge, one passing test and one defect."""
    run = Run(target_url="http://localhost:3000/sut", status="passed")
    session.add(run)
    session.commit()
    session.refresh(run)

    session.add(
        AppState(
            run_id=run.id,
            key="aaaa000000000000",
            url="http://localhost:3000/sut",
            title="Home",
            actions=json.dumps(["button:Sign in"]),
            screenshot=f"run-{run.id}/aaaa000000000000.png",
            is_entry=True,
        )
    )
    session.add(
        AppState(
            run_id=run.id,
            key="bbbb000000000000",
            url="http://localhost:3000/sut",
            title="Signed in",
            actions=json.dumps(["submit[valid]:form:Sign in"]),
            is_entry=False,
        )
    )
    session.add(
        StateTransition(
            run_id=run.id,
            from_key="aaaa000000000000",
            action="button:Sign in",
            to_key="bbbb000000000000",
            mutating=True,
        )
    )
    session.add(
        TestCase(
            run_id=run.id,
            name="sign in",
            status="passed",
            path=json.dumps(["aaaa000000000000", "bbbb000000000000"]),
        )
    )
    session.add(
        TestCase(
            run_id=run.id,
            name="sign in with a bad password",
            status="defect",
            path=json.dumps(["bbbb000000000000"]),
        )
    )
    session.commit()
    return run.id


def main() -> int:
    print("API         TestClient, throwaway database, no browser\n")
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(
            f"sqlite:///{tmp}/probe.db", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(engine)

        def override():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = override
        client = TestClient(app)

        with Session(engine) as session:
            run_id = seed(session)

        body = client.get(f"/api/runs/{run_id}/map").json()

        ok &= check("the map names its entry state", body["entry_key"] == "aaaa000000000000")
        ok &= check("both states are returned", len(body["states"]) == 2)
        ok &= check("the edge is returned", len(body["transitions"]) == 1)
        ok &= check(
            "a mutating edge says so",
            body["transitions"][0]["mutating"] is True,
        )

        states = {s["key"]: s for s in body["states"]}
        ok &= check(
            "actions arrive parsed, not as a JSON string",
            states["aaaa000000000000"]["actions"] == ["button:Sign in"],
        )
        ok &= check(
            "a thumbnail path is the URL suffix the browser needs",
            states["aaaa000000000000"]["screenshot"]
            == f"run-{run_id}/aaaa000000000000.png",
        )
        ok &= check(
            "a state with no screenshot returns null, not an error",
            states["bbbb000000000000"]["screenshot"] is None,
        )
        ok &= check(
            "a state only a passing scenario crosses is green",
            states["aaaa000000000000"]["verdict"] == "passed",
        )
        ok &= check(
            "a state two scenarios cross takes the worse verdict",
            states["bbbb000000000000"]["verdict"] == "defect",
        )
        ok &= check(
            "an unknown run is a 404, not a 500",
            client.get("/api/runs/424242/map").status_code == 404,
        )

        app.dependency_overrides.clear()

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd app/api && uv run python -m app.probe`
Expected: FAIL on every check — `GET /api/runs/1/map` returns 404 because the route does not exist.

- [ ] **Step 3: Write the router**

Create `app/api/app/routers/worldmap.py`:

```python
"""The graph one run discovered, as the console draws it.

Reads `AppState` and `StateTransition` directly rather than calling
`agents.explorer.store.load`, which rebuilds every `Observation` and re-parses
every aria snapshot to do it. The console needs neither: it draws nodes, edges
and a colour.

`verdict` is **derived, not stored**. A state has no verdict of its own -- what
it has is the scenarios that crossed it, and the worst of their outcomes. See
`agents/suite.py`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.suite import verdicts_by_state

from ..db import get_session
from ..models import AppState, Run, StateTransition

router = APIRouter(prefix="/api/runs", tags=["map"])


class StateOut(BaseModel):
    key: str
    url: str
    title: str
    label: str | None
    is_entry: bool
    actions: list[str]
    screenshot: str | None
    verdict: str | None


class TransitionOut(BaseModel):
    from_key: str
    action: str
    to_key: str
    mutating: bool
    observation_id: int | None


class MapOut(BaseModel):
    run_id: int
    entry_key: str | None
    states: list[StateOut]
    transitions: list[TransitionOut]


@router.get("/{run_id}/map", response_model=MapOut)
def get_map(run_id: int, session: Session = Depends(get_session)) -> MapOut:
    if session.get(Run, run_id) is None:
        raise HTTPException(404, "run not found")

    rows = session.exec(select(AppState).where(AppState.run_id == run_id)).all()
    verdicts = verdicts_by_state(run_id, session)

    states = []
    entry_key = None
    for row in rows:
        if row.is_entry:
            entry_key = row.key
        try:
            actions = json.loads(row.actions or "[]")
        except json.JSONDecodeError:
            # A corrupt blob is one grey node, not a broken console.
            actions = []
        states.append(
            StateOut(
                key=row.key,
                url=row.url,
                title=row.title,
                label=row.label,
                is_entry=row.is_entry,
                actions=actions,
                screenshot=row.screenshot,
                verdict=verdicts.get(row.key),
            )
        )

    edges = session.exec(
        select(StateTransition).where(StateTransition.run_id == run_id)
    ).all()

    return MapOut(
        run_id=run_id,
        entry_key=entry_key,
        states=states,
        transitions=[
            TransitionOut(
                from_key=edge.from_key,
                action=edge.action,
                to_key=edge.to_key,
                mutating=edge.mutating,
                observation_id=edge.observation_id,
            )
            for edge in edges
        ],
    )
```

- [ ] **Step 4: Register it**

In `app/main.py`, find the block where the other routers are included and add `worldmap` alongside them, matching the existing import and `include_router` style exactly. For example, if the file reads `from .routers import canvas, explore, runs, sessions`, it becomes `from .routers import canvas, explore, runs, sessions, worldmap`, and a matching `app.include_router(worldmap.router)` goes with the others.

- [ ] **Step 5: Run the checks and watch them pass**

Run: `cd app/api && uv run python -m app.probe`
Expected: all eleven checks PASS.

- [ ] **Step 6: Add it to `make probe`**

In `app/Makefile`, extend the `probe` target with a third line:

```make
probe: ## Observable checks for the explorer and the agents. No API key needed.
	cd api && uv run python -m agents.explorer.probe
	cd api && uv run python -m agents.probe
	cd api && uv run python -m app.probe
```

Run: `cd app && make probe`
Expected: three sections print, all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api/app/routers/worldmap.py app/api/app/probe.py app/api/app/main.py app/Makefile
git commit -m "API: one endpoint for the graph a run discovered"
```

---

### Task 6: The pipeline runs past exploration

**Files:**
- Modify: `app/api/app/routers/explore.py:47-140` (the `_explore` background job)

**Interfaces:**
- Consumes: `shots.shooter` (Task 2), `suite.save_results` (Task 4), `generator.scenarios`, `runner.run`.
- Produces: `TestCase` rows for every run started from the UI, and `Event` rows carrying the surfaces `plan`, `coverage`, `suite`, `heal`, `defect`, `report`.

- [ ] **Step 1: Add the imports**

> **Base changed after this plan was written.** `ac99a8b` gave `_explore` a
> no-model fallback: when `llm.load()` raises, `provider` stays `None` and
> `_crawl_only` builds the map with `explorer.crawler` instead. Nothing below
> may delete that path. It is only reachable with no API key configured, so a
> regression here is invisible until the one run that most needs to work.

Add to the imports — do not replace the block, `crawler` and `Synthesizer`
are load-bearing for the fallback:

```python
from agents import orchestrator, runner, suite
from agents.explorer import crawler, store
from agents.explorer.forms import Credentials
from agents.explorer.synth import Synthesizer
from agents.generator import scenarios
from agents.llm import load
from agents.shots import shooter
from agents.tracing import start as start_tracing
from ..config import settings
from ..db import engine, get_session
from ..models import Event, Run
```

- [ ] **Step 2: Take pictures during the crawl**

In `_explore`, the `orchestrator.run(...)` call gains one argument. Add it after `run_id=run_id`:

```python
                    run_id=run_id,
                    shot=shooter(page, run_id, settings.artifacts_dir),
```

- [ ] **Step 3: Move the save inside the browser block, and keep going**

The current body closes the browser, then saves. Generation and replay both
need a live page, so the browser must stay open.

Replace the run of lines from `browser.close()` through the end of the
`for gap in result.gaps:` loop — **and** the `if run:` block after it that sets
`run.status` and `run.summary` — with the block below. That block is included
because the replacement sets both itself, from the tally; leaving the original
in place would report a run green that found a defect.

Since `ac99a8b` that trailing `if run:` is no longer two lines: it is a
conditional that yields `degraded` when `provider is None`. The replacement
below preserves it. `scenarios()` is pure over the `WorldMap`, so the no-model
path reaches every stage of this pipeline — generation and replay do not care
where the map came from.

```python
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
                # A defect is a defect whoever found it. Absent one, `degraded`
                # survives a model-free run: a map and a suite exist, but no
                # flow was named and no intent was honoured, so green would
                # claim more than happened. See the fallback in `_explore`.
                if tally[runner.DEFECT]:
                    run.status = "failed"
                else:
                    run.status = "passed" if provider else "degraded"
                run.summary = result.summary or (
                    f"{len(result.world.states)} states, {len(plan)} scenarios "
                    f"-- crawled without a model. Set ANTHROPIC_API_KEY for "
                    f"flows and a summary."
                    if provider is None
                    else f"stopped: {result.stopped}"
                )
```

Note `results = []` is assigned inside the `with sync_playwright()` block but read after it. Move its initialisation above the `try:` so the `except` path still has a list to reference:

```python
        results: list = []
        try:
            with sync_playwright() as pw:
```

- [ ] **Step 4: Run it end to end**

With `make dev` running in another terminal and an API key configured:

Run: `cd app && make reset` then start a session against `http://localhost:3000/sut` from the UI and press Start run.

Expected, on the run's timeline (visible in the Agent Timeline widget, which still works): events carrying `surface` values `plan`, `coverage`, `suite`, and `report`, in that order, and a final `report:` line tallying verdicts.

Verify the rows landed:

Run: `cd app/api && uv run python -c "from sqlmodel import Session, select; from app.db import engine; from app.models import TestCase; s=Session(engine); print([(t.name, t.status, t.path) for t in s.exec(select(TestCase)).all()])"`
Expected: one row per scenario, each with a non-empty `path` and a status in `passed | healed | defect | escalate`.

Run: `ls app/api/artifacts/run-*/`
Expected: one `.png` per discovered state.

- [ ] **Step 5: Confirm nothing else broke**

Run: `cd app/api && uv run python -c "from app.routers import explore; print(explore.crawler, explore.Synthesizer, explore._crawl_only)"`
Expected: all three resolve. An `AttributeError` here means Step 1 replaced the
import block rather than adding to it, and the no-key path is broken — which no
other check in this plan will notice, because every one of them runs with a key.

Run: `cd app && make probe`
Expected: all three sections PASS.

Run: `cd app && make loop URL=http://localhost:3000/sut`
Expected: the CLI pipeline still runs and prints its classification table.

- [ ] **Step 6: Commit**

```bash
git add app/api/app/routers/explore.py
git commit -m "explore: the job runs the whole pipeline, not just the map"
```

---

### Task 7: A state knows its inputs

The card shows a screen's input fields, but `AppState.actions` holds *action*
descriptors (`button:Sign in`, `submit[valid]:form:Sign in`) — never field
names. Fields come from `forms.fields_of(observation)`, which is pure over an
`Observation`, and `store.save` already holds both the node and its evidence.

`worldmap.py` deliberately does not know what an action means (it takes
`actions_of` injected), so the derivation belongs in `store.py` — the file whose
docstring already calls itself the boundary — and not on `StateNode`.

**Files:**
- Modify: `app/api/app/models.py` (AppState)
- Modify: `app/api/agents/explorer/store.py` (save)
- Modify: `app/api/app/routers/worldmap.py` (StateOut)
- Modify: `app/api/app/probe.py` (seed + one check)
- Test: `app/api/app/probe.py`

**Interfaces:**
- Consumes: `forms.fields_of(observation) -> tuple[tuple[str, str], ...]`.
- Produces: `AppState.fields: str` (JSON list of `[role, name]` pairs); `StateOut.fields: list[list[str]]` on `GET /api/runs/{id}/map`.

- [ ] **Step 1: Write the failing check**

In `app/api/app/probe.py`, add `fields` to the first seeded state:

```python
            actions=json.dumps(["button:Sign in"]),
            fields=json.dumps([["textbox", "Email"], ["textbox", "Password"]]),
            screenshot=f"run-{run.id}/aaaa000000000000.png",
```

And add two checks after the `actions arrive parsed` check:

```python
        ok &= check(
            "a state reports its input fields",
            states["aaaa000000000000"]["fields"]
            == [["textbox", "Email"], ["textbox", "Password"]],
        )
        ok &= check(
            "a state with no fields returns an empty list, not null",
            states["bbbb000000000000"]["fields"] == [],
        )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd app/api && uv run python -m app.probe`
Expected: `TypeError: 'fields' is an invalid keyword argument for AppState`

- [ ] **Step 3: Add the column**

In `app/models.py`, add to `AppState` after `actions`:

```python
    actions: str = "[]"  # JSON list of action descriptors
    # JSON list of [role, name] pairs -- the fillable fields this screen
    # offers. Derived from the state's first observation at save time, not
    # stored on StateNode: `worldmap.py` takes `actions_of` injected precisely
    # so that it never learns what a control means.
    fields: str = "[]"
```

- [ ] **Step 4: Derive it at save time**

In `store.py`, add the helper beside `_key_of` at the bottom of the file:

```python
def _fields_of(world: WorldMap, node: StateNode) -> str:
    """The fillable fields of a state, from the first time we saw it.

    First sighting rather than latest: `record()` already takes `url`, `title`
    and `actions` from the first observation, and a card whose fields came from
    a different visit than its screenshot would be describing two screens.
    """
    from .forms import fields_of

    if not node.evidence:
        return "[]"
    index = node.evidence[0]
    if index >= len(world.evidence):
        return "[]"
    return json.dumps([list(pair) for pair in fields_of(world.evidence[index])])
```

And pass it in the insert branch of `save`:

```python
                AppState(
                    run_id=run_id,
                    key=key,
                    url=node.url,
                    title=node.title,
                    actions=actions,
                    fields=_fields_of(world, node),
                    label=node.label,
                    screenshot=node.screenshot,
                    is_entry=(key == world.entry_key),
                )
```

Only the insert branch. A state's first evidence never changes, so recomputing
it on every checkpoint would parse a snapshot to produce the same string.

- [ ] **Step 5: Return it from the endpoint**

In `routers/worldmap.py`, add to `StateOut`:

```python
    actions: list[str]
    fields: list[list[str]]
```

And in `get_map`, beside the `actions` parse:

```python
        try:
            actions = json.loads(row.actions or "[]")
        except json.JSONDecodeError:
            # A corrupt blob is one grey node, not a broken console.
            actions = []
        try:
            fields = json.loads(row.fields or "[]")
        except json.JSONDecodeError:
            fields = []
```

and pass `fields=fields` into `StateOut(...)`.

- [ ] **Step 6: Run the checks and watch them pass**

Run: `cd app/api && uv run python -m app.probe`
Expected: all thirteen checks PASS.

- [ ] **Step 7: Verify against a real crawl**

Run: `cd app && make reset`, then start a run from the UI against `http://localhost:3000/sut`, then:

Run: `cd app/api && uv run python -c "from sqlmodel import Session, select; from app.db import engine; from app.models import AppState; s=Session(engine); print([(a.title, a.fields) for a in s.exec(select(AppState)).all()])"`
Expected: the sign-in state lists its textbox fields; states with no form show `[]`.

- [ ] **Step 8: Commit**

```bash
git add app/api/app/models.py app/api/agents/explorer/store.py app/api/app/routers/worldmap.py app/api/app/probe.py
git commit -m "AppState: a screen knows which inputs it offers"
```

---

### Task 8: The client half of the contract

**Files:**
- Modify: `app/web/lib/api.ts`
- Create: `app/web/lib/map.ts`
- Create: `app/web/lib/stages.ts`

**Interfaces:**
- Consumes: `GET /api/runs/{id}/map` (Tasks 5, 7), `GET /api/runs/{id}/tests` (existing).
- Produces: types `MapState`, `MapTransition`, `WorldMapPayload`, `TestCaseRow`, `Verdict`; `api.getMap`, `api.listTests`; `artifactUrl(path)`; `layout(payload)`; `STAGES`.

- [ ] **Step 1: Install dagre**

Run: `cd app/web && npm install @dagrejs/dagre`
Expected: it appears in `package.json` dependencies.

- [ ] **Step 2: Add the types and calls**

In `app/web/lib/api.ts`, after the `AgentEvent` type, add:

```ts
/** The four things the Runner can conclude. See api/agents/runner.py. */
export type Verdict = "passed" | "healed" | "defect" | "escalate";

export type MapState = {
  key: string;
  url: string;
  title: string;
  label: string | null;
  is_entry: boolean;
  actions: string[];
  /** [role, name] pairs — the fillable fields of this screen. */
  fields: [string, string][];
  /** Path under /artifacts, or null when capture was off or the shot failed. */
  screenshot: string | null;
  /** Worst verdict among scenarios crossing this state; null if untested. */
  verdict: Verdict | null;
};

export type MapTransition = {
  from_key: string;
  action: string;
  to_key: string;
  /** A non-GET fired during this action. The signal the Runner classifies on. */
  mutating: boolean;
  observation_id: number | null;
};

export type WorldMapPayload = {
  run_id: number;
  entry_key: string | null;
  states: MapState[];
  transitions: MapTransition[];
};

export type TestCaseRow = {
  id: number;
  run_id: number | null;
  name: string;
  selector: string | null;
  healed_selector: string | null;
  status: string;
  detail: string | null;
  /** JSON list of the state keys this scenario crosses. */
  path: string;
  created_at: string;
};

/** Artifacts are served by the API, not by Next. */
export const artifactUrl = (path: string) => `${API_BASE}/artifacts/${path}`;
```

And inside the `api` object, beside `listEvents`:

```ts
  getMap: (runId: number) => request<WorldMapPayload>(`/api/runs/${runId}/map`),
  listTests: (runId: number) => request<TestCaseRow[]>(`/api/runs/${runId}/tests`),
```

- [ ] **Step 3: Write the layout**

Create `app/web/lib/map.ts`:

```ts
import dagre from "@dagrejs/dagre";
import type { MapState, MapTransition } from "@/lib/api";

/** Card footprint. Must match the fixed size StateCard renders at. */
export const NODE_W = 220;
export const NODE_H = 176;

/**
 * Where each state sits.
 *
 * Left-to-right rather than top-down: a user flow reads as a sequence, and the
 * action labels on the edges have somewhere to go. Recomputed whenever the node
 * set changes — positions are never persisted, because the graph is rebuilt per
 * run and a saved position for a state that no longer exists is worse than
 * none.
 */
export function layout(
  states: MapState[],
  transitions: MapTransition[],
): Record<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 48, ranksep: 96 });

  for (const state of states) {
    graph.setNode(state.key, { width: NODE_W, height: NODE_H });
  }
  for (const edge of transitions) {
    // A self-loop is the most informative edge in the graph (the app was asked
    // to do something and stayed put) but dagre cannot rank one, and feeding it
    // one shifts every other node. React Flow draws it from the node's own
    // handles instead.
    if (edge.from_key === edge.to_key) continue;
    if (!graph.hasNode(edge.from_key) || !graph.hasNode(edge.to_key)) continue;
    graph.setEdge(edge.from_key, edge.to_key);
  }

  dagre.layout(graph);

  const positions: Record<string, { x: number; y: number }> = {};
  for (const state of states) {
    const node = graph.node(state.key);
    // dagre centres nodes; React Flow positions by top-left corner.
    positions[state.key] = node
      ? { x: node.x - NODE_W / 2, y: node.y - NODE_H / 2 }
      : { x: 0, y: 0 };
  }
  return positions;
}
```

- [ ] **Step 4: Write the stage table**

Create `app/web/lib/stages.ts`:

```ts
/**
 * The brief's must-haves, in order, and the surface each one listens for.
 *
 * `widgets/surfaces.ts` maps a surface to a *floating widget* and stays as it
 * is — the widget board still works. This table maps the same surfaces to
 * *fixed slots*, which is what a rail is. Two tables because they answer
 * different questions, both fed by the one `Event.surface` the agent emits.
 */
export type Stage = {
  /** Event.surface values that feed this card. */
  surfaces: string[];
  title: string;
  ordinal: string;
  /** True when every matching event should be listed, not just the latest. */
  accumulate?: boolean;
};

export const STAGES: Stage[] = [
  { surfaces: ["plan"], title: "Plan", ordinal: "1" },
  { surfaces: ["coverage"], title: "Coverage", ordinal: "2" },
  { surfaces: ["suite"], title: "Suite", ordinal: "3" },
  { surfaces: ["heal", "defect"], title: "Run", ordinal: "4", accumulate: true },
  { surfaces: ["report"], title: "Report", ordinal: "5" },
];
```

- [ ] **Step 5: Typecheck**

Run: `cd app && make check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app/web/lib/api.ts app/web/lib/map.ts app/web/lib/stages.ts app/web/package.json app/web/package-lock.json
git commit -m "web: types, dagre layout and the stage table"
```

---

### Task 9: The map pane

**Files:**
- Create: `app/web/components/StateCard.tsx`
- Create: `app/web/components/MapPane.tsx`

**Interfaces:**
- Consumes: `api.getMap`, `artifactUrl`, `layout`, `NODE_W`, `NODE_H` (Task 8).
- Produces: `<MapPane runId={number | null} />`; node data shape `{ state: MapState }` under React Flow node type `"state"`.

**On colour.** `globals.css` says the palette is two accents and that "if a colour
appears anywhere else it is a bug". Four verdicts, two colours — so verdict is
carried by **glyph and border style**, with colour only saying pass or fail:

| Verdict | Colour | Border | Glyph |
|---|---|---|---|
| passed | live | solid | ✓ |
| healed | live | dashed | ↻ |
| defect | fault | solid | ✗ |
| escalate | fault | dashed | ⚠ |
| untested | muted | solid | · |

- [ ] **Step 1: Write the card**

Create `app/web/components/StateCard.tsx`:

```tsx
"use client";
import { Handle, Position, useStore, type NodeProps } from "@xyflow/react";
import { artifactUrl, type MapState, type Verdict } from "@/lib/api";
import { NODE_H, NODE_W } from "@/lib/map";

/** Below this zoom a thumbnail is unreadable, so the card becomes a chip. */
const COMPACT_BELOW = 0.6;

const VERDICT: Record<Verdict | "untested", { tone: string; dashed: boolean; glyph: string }> = {
  passed: { tone: "text-live", dashed: false, glyph: "✓" },
  healed: { tone: "text-live", dashed: true, glyph: "↻" },
  defect: { tone: "text-fault", dashed: false, glyph: "✗" },
  escalate: { tone: "text-fault", dashed: true, glyph: "⚠" },
  untested: { tone: "text-muted", dashed: false, glyph: "·" },
};

export type StateNodeData = { state: MapState };

export default function StateCard({ data }: NodeProps) {
  const { state } = data as unknown as StateNodeData;
  const compact = useStore((s) => s.transform[2] < COMPACT_BELOW);
  const mark = VERDICT[state.verdict ?? "untested"];
  const name = state.label ?? state.title ?? state.url;

  const frame = `rounded-md border bg-paper ${
    mark.dashed ? "border-dashed" : "border-solid"
  } ${state.verdict ? "border-current" : "border-rule"} ${mark.tone}`;

  if (compact) {
    return (
      <div className={`${frame} px-3 py-2`} style={{ width: NODE_W }}>
        <Handle type="target" position={Position.Left} />
        <span className="text-ink text-sm">{name}</span>
        <span className="ml-2">{mark.glyph}</span>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  return (
    <div className={`${frame} overflow-hidden`} style={{ width: NODE_W, height: NODE_H }}>
      <Handle type="target" position={Position.Left} />
      <div className="h-24 bg-hush">
        {state.screenshot ? (
          // eslint-disable-next-line @next/next/no-img-element -- artifacts are
          // served by the API on another origin; next/image would need a loader
          // config for a picture that is already the right size.
          <img
            src={artifactUrl(state.screenshot)}
            alt={`Screenshot of ${name}`}
            className="h-24 w-full object-cover object-top"
          />
        ) : (
          <div className="flex h-24 items-center justify-center text-xs text-muted">
            no capture
          </div>
        )}
      </div>
      <div className="px-2.5 py-2">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm text-ink">{name}</span>
          <span className="ml-auto text-sm">{mark.glyph}</span>
        </div>
        <div className="mt-0.5 text-[11px] text-muted">
          {state.fields.length} input{state.fields.length === 1 ? "" : "s"} ·{" "}
          {state.actions.length} action{state.actions.length === 1 ? "" : "s"}
        </div>
        {state.fields.length > 0 && (
          <div className="mt-1 truncate text-[11px] text-muted">
            {state.fields.map(([, fieldName]) => fieldName).join("  ")}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
```

- [ ] **Step 2: Write the pane**

Create `app/web/components/MapPane.tsx`:

```tsx
"use client";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import StateCard from "@/components/StateCard";
import { api, type WorldMapPayload } from "@/lib/api";
import { layout } from "@/lib/map";

/**
 * The graph one run discovered.
 *
 * Polls rather than streams: `store.save` is incremental and writes after every
 * edge, so re-reading the map every two seconds is how the graph draws itself
 * while the colony is still walking. Positions are recomputed from scratch each
 * time the node set changes and never persisted — see lib/map.ts.
 */
export default function MapPane({ runId }: { runId: number | null }) {
  const [payload, setPayload] = useState<WorldMapPayload | null>(null);

  useEffect(() => {
    if (runId === null) {
      setPayload(null);
      return;
    }
    let cancelled = false;
    const poll = () =>
      api
        .getMap(runId)
        .then((next) => {
          if (!cancelled) setPayload(next);
        })
        .catch(() => {
          // The run may not have written a map yet. Try again next tick.
        });
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId]);

  const { nodes, edges } = useMemo(() => {
    if (!payload) return { nodes: [] as Node[], edges: [] as Edge[] };
    const positions = layout(payload.states, payload.transitions);
    return {
      nodes: payload.states.map<Node>((state) => ({
        id: state.key,
        type: "state",
        position: positions[state.key],
        data: { state },
        draggable: true,
      })),
      edges: payload.transitions.map<Edge>((edge, index) => ({
        id: `${edge.from_key}-${index}`,
        source: edge.from_key,
        target: edge.to_key,
        label: edge.action,
        type: "smoothstep",
        // A non-GET fired: heavier line. This is the distinction runner.py
        // classifies on, so it belongs on the picture.
        style: { strokeWidth: edge.mutating ? 2 : 1 },
        labelStyle: { fontSize: 10 },
      })),
    };
  }, [payload]);

  const nodeTypes = useMemo(() => ({ state: StateCard }), []);

  if (runId === null) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        Start a run to map this application.
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      proOptions={{ hideAttribution: true }}
      fitView
      fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
    >
      <Background />
      <Controls />
      <MiniMap pannable zoomable />
    </ReactFlow>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd app && make check`
Expected: no errors.

- [ ] **Step 4: See it against a real run**

With `make dev` running, open a session, press Start run, and watch. (MapPane is
not mounted until Task 11; to see it now, temporarily render `<MapPane runId={latest?.id ?? null} />`
in place of `<Canvas .../>` in `SessionView.tsx` and revert before committing.)

Expected: nodes appear one at a time as the colony walks, each with a
screenshot; edges carry action labels; mutating edges are visibly heavier;
zooming out past ~0.6 collapses the cards to chips.

- [ ] **Step 5: Commit**

```bash
git add app/web/components/StateCard.tsx app/web/components/MapPane.tsx
git commit -m "web: the map draws itself while the colony walks"
```

---

### Task 10: The stage rail

**Files:**
- Create: `app/web/components/StageRail.tsx`

**Interfaces:**
- Consumes: `api.listSessionEvents`, `STAGES` (Task 8).
- Produces: `<StageRail sessionId={number} />`.

- [ ] **Step 1: Write it**

Create `app/web/components/StageRail.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { api, type AgentEvent } from "@/lib/api";
import { STAGES } from "@/lib/stages";

const LEVEL_TONE: Record<string, string> = {
  error: "text-fault",
  warn: "text-fault",
  decision: "text-ink",
  info: "text-muted",
};

/**
 * The brief's five must-haves, filling in as the meta-agent advances.
 *
 * Reads the same `Event.surface` seam the widget board reads — the agent names
 * what deserves attention and never learns that a rail exists. A surface with
 * no stage falls through to the timeline, exactly as before.
 */
export default function StageRail({ sessionId }: { sessionId: number }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    let after = 0;
    const poll = () =>
      api
        .listSessionEvents(sessionId, after)
        .then((batch) => {
          if (cancelled || !batch.length) return;
          after = batch[batch.length - 1].id;
          setEvents((current) => [...current, ...batch.filter((e) => e.surface)]);
        })
        .catch(() => {
          // API down mid-session: keep what we have and try again next tick.
        });
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId]);

  return (
    <div className="h-full overflow-y-auto border-l border-rule bg-hush px-3 py-3">
      {STAGES.map((stage) => {
        const mine = events.filter((e) => e.surface && stage.surfaces.includes(e.surface));
        const shown = stage.accumulate ? mine : mine.slice(-1);
        return (
          <section key={stage.title} className="mb-3 rounded-md border border-rule bg-paper p-3">
            <header className="flex items-baseline gap-2">
              <span className="text-[11px] text-muted">{stage.ordinal}</span>
              <h2 className="text-xs font-medium uppercase tracking-wide text-ink">
                {stage.title}
              </h2>
              {shown.length === 0 && (
                <span className="ml-auto text-[11px] text-muted">pending</span>
              )}
            </header>
            {shown.length > 0 && (
              <ul className="mt-2 space-y-1">
                {shown.map((event) => (
                  <li
                    key={event.id}
                    className={`text-xs leading-snug ${LEVEL_TONE[event.level] ?? "text-muted"}`}
                  >
                    {event.message}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd app && make check`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/web/components/StageRail.tsx
git commit -m "web: the five stages, filling in as the agent decides"
```

---

### Task 11: Split the session screen

**Files:**
- Modify: `app/web/components/SessionView.tsx`

**Interfaces:**
- Consumes: `<MapPane runId>` (Task 9), `<StageRail sessionId>` (Task 10).
- Produces: the session screen as specified — map left, rail right, intent box below.

- [ ] **Step 1: Swap the body**

In `SessionView.tsx`, replace the imports of `Canvas` with:

```tsx
import MapPane from "@/components/MapPane";
import StageRail from "@/components/StageRail";
```

Add a selected-run state beside the others:

```tsx
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
```

The default follows the newest run until the user picks one. Add after `const latest = runs[runs.length - 1];`:

```tsx
  // Runs are scoped maps, not versions of one map: re-crawling after the app
  // changes writes a second graph beside the first, which is the drift story.
  // So the picker is not a convenience — it is how you compare two builds.
  const shownRunId = selectedRunId ?? latest?.id ?? null;
```

Add the picker to the header, immediately before the target-url link:

```tsx
        {runs.length > 1 && (
          <select
            value={shownRunId ?? ""}
            onChange={(e) => setSelectedRunId(Number(e.target.value))}
            aria-label="Run to show on the map"
            className="rounded border border-rule bg-paper px-1.5 py-1 text-xs"
          >
            {runs.map((r, i) => (
              <option key={r.id} value={r.id}>
                run {i + 1} · {r.status}
              </option>
            ))}
          </select>
        )}
```

Replace the canvas block:

```tsx
      <div className="min-h-0 flex-1">
        <Canvas sessionId={sessionId} />
      </div>
```

with:

```tsx
      <div className="grid min-h-0 flex-1 grid-cols-[3fr_2fr]">
        <div className="min-w-0">
          <MapPane runId={shownRunId} />
        </div>
        <StageRail sessionId={sessionId} />
      </div>
```

- [ ] **Step 2: Typecheck and lint**

Run: `cd app && make check`
Expected: no errors. If `Canvas` is now unused, remove its import — lint will say so.

- [ ] **Step 3: Watch the whole thing**

Run: `cd app && make reset`, then `make dev`. Create a session against
`http://localhost:3000/sut`, press Start run, and do not touch anything else.

Expected, in order: nodes appear with screenshots as the colony walks; the Plan
and Coverage cards fill; the Suite card names a scenario count; nodes turn
green, and at least one turns red when the run is against `?bug=1`; the Report
card tallies the outcome.

Then press Run again to create a second run, switch the picker to run 1, and
confirm the first map is still there and unchanged.

- [ ] **Step 4: Confirm nothing regressed**

Run: `cd app && make probe && make check && make smoke`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/web/components/SessionView.tsx
git commit -m "console: map left, stages right, the pipeline painting both"
```

---

## Not in this plan

**The chat.** It is an independent subsystem — a new `agents/facts.py`, a new
endpoint, a new component, and a new model seam — and it was agreed as the first
thing to cut if time compresses. It also depends on what the map turns out to
need: the fast-path question shapes should be the ones people actually ask while
watching this screen, and that is not knowable before the screen exists. It gets
its own spec section (already written) and its own plan, after Task 11 lands.

**Two maps overlaid in one view.** `snapshot.compare` answers "what changed" as
text today. A visual diff is a second feature; the run picker in Task 11 is the
cheap version.

**Persisted node positions.** See `lib/map.ts` — positions are recomputed per
run on purpose.

## Self-review

Checked against `docs/superpowers/specs/2026-09-04-console-map-and-chat-design.md`:

| Spec section | Task |
|---|---|
| Layout — split pane, chat below | 11 (chat box already exists in `SessionView`) |
| Map pane — nodes, edges, dagre, no persisted positions | 8, 9 |
| Node content — thumbnail, label, inputs, verdict | 7, 9 |
| Zoom-dependent detail | 9 |
| Painting by pipeline stage, worst verdict wins | 4, 5, 9 |
| Mutating edges heavier | 9 |
| Screenshots, first observation only, non-fatal | 1, 2 |
| `screenshot` through snapshot + store | 1, 3 |
| Stage rail, five cards, surfaces | 6, 8, 10 |
| Cards cross-link into the map | **not implemented** — see below |
| Chat | deferred, see "Not in this plan" |
| `GET /map` reads tables directly | 5 |
| `AppState.screenshot`, `TestCase.path` | 3, 4 |
| Ordering (1–4 the demo, chat the cut) | matches |
| Verification list | 1–11 each end in a check |

**Two deviations from the spec, both deliberate:**

1. **`GET /suite` is not built.** `GET /api/runs/{id}/tests` plus `TestCase.path`
   and `detail` carry the same data. Recorded at the top of this plan.
2. **Verdict colour.** The spec said green / amber / red / purple. `globals.css`
   allows two accents and calls a third a bug, so Task 9 carries verdict in
   glyph and border style with colour saying only pass or fail. Strictly more
   legible in the dark theme, and it obeys the palette.

**One spec item with no task: cards cross-linking into the map.** Clicking a
coverage gap should highlight the state it names. It needs the gap text to carry
a state key, which `Exploration.gaps` (a tuple of plain strings, `orchestrator.py:68`)
does not. Making it structured is a change to the orchestrator's output contract
and to the prompt that produces it — too large to bury inside a frontend task.
It is deliberately left out; the rail and the map both work without it, and it
should be its own packet if there is time.
