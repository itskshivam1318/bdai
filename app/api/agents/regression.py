"""A versioned Playwright suite that survives the change it exists to catch.

Everything needed for this was already here and none of it was joined up.
`generator.write` could put a `.spec.ts` on disk; `runner.run` could tell a
moved locator from moved behaviour; nothing kept the file between the two. So
the only suite that existed was the one `make specs` re-crawled from scratch
each time, which cannot regress because it has no past.

Four things live here, and the split is the point:

  **record**  plan (`planner.plan`, from the map or from the map plus the
              behavioural model), compile, and emit **v001**.
  **emit**    a new, immutable version directory. Nothing already written is
              ever edited or deleted.
  **verify**  replay the current version. What healed is repaired and emitted
              as the *next* version; what reported a defect is left untouched.
  **drifted** whether the landing state moved since. Evidence for the report,
              deliberately **not** a gate -- see `should_replay`.

**A version is immutable, and that is what makes healing legible.** The first
draft of this module rewrote the suite in place, which meant the only record of
a heal was a line in a log claiming it. Emitting v002 beside v001 makes the
claim checkable with `diff`: the healer's whole output is a directory you can
read. It is also the only way to answer "did the behavioural model produce
better tests?" -- two versions recorded from two planners, compared on what
each caught.

**Running the suite is how a change is detected.** Not a fingerprint. A
fingerprint sees markup and is blind to behaviour, so gating the replay on one
means the run that matters -- the app still looks identical and now returns the
form on a valid submit -- is the run that never happens.

**Why healing writes back here and not in `runner.py`.** The Runner's docstring
says a `.spec.ts` on disk is a bad place to put a healed locator *mid-run*, and
that is right: a scenario is a sequence, and rewriting step 2 while step 5 is
still to come mutates the thing being measured. So the rewrite happens once,
after the verdict for the whole scenario is in, from the outside. The Runner
still knows nothing about files.

**Why a defect never rewrites the file.** This is the whole discipline. Healing
a locator is saying *the test was talking about this control and the control was
renamed*. Rewriting a test because it failed is saying *the test was wrong to
expect that*, which no evidence here supports and which turns a suite green by
deleting the reason it was red. `Resolution.healed` is the only gate, and it is
false unless the Runner resolved the control on a rung it recorded.

**The healer repairs the map too, and does not rewrite history.** A rename that
broke a locator also invalidates the transition the map recorded, so a repair
that stopped at the `.spec.ts` would leave the next planning run compiling from
a map describing an application nobody serves. So every applied repair is also
written down as a `map_update` on the new version, and `apply_to_map` folds
those into a `WorldMap` for the next plan. What it does **not** do is edit the
crawl the map came from: `app/CLAUDE.md` keeps maps per run precisely so two
runs can be compared, and a healer that edited the old rows would destroy the
before/after that proves the app changed at all.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page

from . import runner
from .behavior import BehaviorModel
from .behavior import refresh as behavior_refresh
from .explorer import statekey
from .explorer.forms import Credentials
from .explorer.observer import Observer
from .generator import Scenario, _slug, from_json, spec, to_json

SUITES = Path(__file__).resolve().parent.parent / "artifacts" / "suites"

# The lineage, at the suite root: which versions exist and which is current.
LINEAGE = "suite.json"
# One per version directory: what this version is, and why it was emitted.
VERSION = "version.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def directory_for(
    target_url: str, root: Path | None = None, session_uid: str | None = None
) -> Path:
    """One suite per target -- and per session, wherever there is one.

    **A session is the unit of a suite's history, not a URL.** Keyed on the
    target alone, a second session pointed at the same app opened on the first
    session's tests: `keep` asks the filesystem whether a suite exists, finds
    one, and replays it instead of recording. Everything downstream then reads
    as drift -- the new session's console shows scenarios it never compiled,
    healed against a baseline someone else recorded, sometimes with a context
    box that says something different. Two people testing the same staging URL
    is the normal case, not an edge one.

    Within a session the old behaviour is exactly what is wanted: run twice and
    the second run replays the first one's suite, heals it, and emits v002. That
    is the drift story, and it is why this scopes rather than disables.

    `session_uid=None` keeps the target-only path, which is what every CLI entry
    point (`make suite`, `make pipeline`, `rescue.py`) has and should have --
    there is no session at a command line, and one suite per target is the right
    answer there.

    **The session's `uid`, not its row number.** `TestSession.id` is 1 on a
    fresh database and 1 again after `make reset`, which does not clear
    `artifacts/` -- so a suite directory named after the row number is handed to
    whichever session is first in the *next* database. That is the same bug one
    level down: tests belonging to a session that no longer exists, presented as
    this one's history.
    """
    slug = _slug(target_url.split("://", 1)[-1])
    if session_uid:
        slug = f"{slug}-{_slug(session_uid)}"
    return (root or SUITES) / slug


# ---------------------------------------------------------------- fingerprint


def fingerprint(page: Page, target_url: str) -> str:
    """What the target looks like right now, as one comparable string.

    `state_key` and not a page hash: it is the same normalisation the whole map
    is keyed on, so it already ignores the things that are not changes --
    session ids, timestamps, row ordering, digits inside otherwise stable text.
    A fingerprint that moved is a fingerprint whose *structure* moved.

    **What this cannot see, and why it is not the gate.** A fingerprint compares
    markup, so it can only ever answer *did the markup move*. It is structurally
    blind to a behavioural regression, which is the failure class this whole
    system exists to catch: measured on the SUT, `?bug=1` -- a completed form
    that returns the form instead of a confirmation -- leaves the landing state
    key byte-identical, because the two knobs are orthogonal on purpose. Gating
    the replay on this would have skipped the run and reported calm.

    So an unchanged fingerprint is *not* evidence that nothing changed, and
    `main` runs the suite regardless by default. What the fingerprint buys is
    the cheap opposite: a positive answer, for free, without a replay -- useful
    as a label on a run, and as an opt-in gate (`IF_DRIFTED=1`) for a caller who
    is watching a target whose behaviour they are not changing.
    """
    observer = Observer(page)
    observer.start_window()
    page.goto(target_url)
    return statekey.state_key(observer.observe().snapshot)


# ---------------------------------------------------------------------- files


@dataclass(frozen=True)
class Version:
    """One emitted suite. Written once, never edited.

    `parent` and `because` are what turn a directory of files into a history: a
    reader who diffs v002 against v001 sees *what* changed, and this says *why*
    it was allowed to. A version with a parent and no repairs in `heals` should
    not exist -- the only reason to emit one is that something moved.
    """

    root: Path
    number: int
    parent: int | None = None
    because: str = ""
    # Which planner produced it: `map` or `behaviour`. Carried so two versions
    # recorded from two planners stay comparable after the fact.
    source: str = ""
    target_url: str = ""
    fingerprint: str = ""
    saved_at: str = ""
    # {file, name, node, covers, origin} per scenario. `node` and `covers` are
    # derived from the steps at write time so a reader can answer "what covers
    # this state?" without loading and re-deriving every scenario.
    scenarios: tuple[dict, ...] = ()
    verdicts: dict = field(default_factory=dict)
    heals: tuple[dict, ...] = ()
    map_updates: tuple[dict, ...] = ()
    # What replaying *these* scenarios did, before this version was declared.
    # Empty on a baseline: the plan that became v001 was executed minutes
    # earlier and `verdicts` already holds that. On a healed version it is the
    # difference between "the repairs were computed" and "the repairs work".
    reverified: dict = field(default_factory=dict)
    # Steps recovered by exploring the region that lost them, rather than by
    # the resolution ladder. See `agents/rescue.py`.
    rescues: tuple[dict, ...] = ()
    #: What the run believed about the app when this version was written. The
    #: suite and the understanding that produced it travel together, so run
    #: N+1 starts from what run N learned instead of recomputing from zero --
    #: and two versions' models are a diff rather than two separate opinions.
    behaviour: BehaviorModel = field(default_factory=BehaviorModel)

    @property
    def label(self) -> str:
        return f"v{self.number:03d}"

    @property
    def nodes(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for entry in self.scenarios:
            for key in entry.get("covers", ()):
                seen.setdefault(key, None)
        return tuple(seen)

    @property
    def from_behaviour(self) -> int:
        return sum(
            1 for s in self.scenarios if str(s.get("origin", "")).startswith("behaviour")
        )

    def as_dict(self) -> dict:
        return {
            "version": self.number,
            "parent": self.parent,
            "because": self.because,
            "source": self.source,
            "target_url": self.target_url,
            "fingerprint": self.fingerprint,
            "saved_at": self.saved_at,
            "scenarios": list(self.scenarios),
            "verdicts": dict(self.verdicts),
            "heals": list(self.heals),
            "map_updates": list(self.map_updates),
            "reverified": dict(self.reverified),
            "rescues": list(self.rescues),
            "behaviour": self.behaviour.as_dict(),
        }

    def render(self) -> str:
        counts = ", ".join(f"{n} {v}" for v, n in sorted(self.verdicts.items()))
        return (
            f"{self.label}  {len(self.scenarios)} scenario(s), "
            f"{len(self.nodes)} node(s), {self.from_behaviour} from behaviour"
            + (f"  [{self.source}]" if self.source else "")
            + (f"  <- v{self.parent:03d}" if self.parent else "")
            + (f"  {counts}" if counts else "")
            + (
                "  [re-verified: "
                + ", ".join(f"{n} {v}" for v, n in sorted(self.reverified.items()))
                + "]"
                if self.reverified
                else ""
            )
            + (f"  +{len(self.rescues)} rescued" if self.rescues else "")
            + (f"\n            {self.because}" if self.because else "")
        )


def _version_dir(root: Path, number: int) -> Path:
    return Path(root) / f"v{number:03d}"


def versions(directory: str | Path) -> tuple[Version, ...]:
    """Every emitted version, oldest first. Read from the directories.

    The lineage file is a convenience, not the record: a version exists because
    its directory does. Deriving the list from the filesystem means a lineage
    that was never written -- a crash between the two writes -- costs a name,
    not a suite.
    """
    root = Path(directory)
    if not root.exists():
        return ()
    found = []
    for path in sorted(root.glob("v[0-9][0-9][0-9]")):
        manifest = path / VERSION
        if not manifest.exists():
            continue
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        found.append(
            Version(
                root=path,
                number=raw.get("version", int(path.name[1:])),
                parent=raw.get("parent"),
                because=raw.get("because", ""),
                source=raw.get("source", ""),
                target_url=raw.get("target_url", ""),
                fingerprint=raw.get("fingerprint", ""),
                saved_at=raw.get("saved_at", ""),
                scenarios=tuple(raw.get("scenarios", ())),
                verdicts=raw.get("verdicts", {}),
                heals=tuple(raw.get("heals", ())),
                map_updates=tuple(raw.get("map_updates", ())),
                reverified=raw.get("reverified", {}),
                rescues=tuple(raw.get("rescues", ())),
                behaviour=BehaviorModel.from_dict(raw.get("behaviour")),
            )
        )
    return tuple(found)


def current(directory: str | Path) -> Version | None:
    """The newest version, which is the one a replay runs."""
    known = versions(directory)
    return known[-1] if known else None


#: The verdicts a scenario may carry and still become a baseline.
#:
#: `healed` is here and it is the only debatable entry. It means the locator
#: resolved on a lower rung than `exact` -- the control was renamed -- and then
#: the application did precisely what it was recorded doing. The behaviour under
#: test is intact, so the scenario is sound once the healed locator is written
#: in as the recorded one, which is what `baseline` does. Refusing it would
#: discard a working test because its button changed name between the crawl and
#: the replay minutes later, which is the ordinary case on a live app.
KEEPABLE = (runner.PASSED, runner.HEALED)


@dataclass(frozen=True)
class Baseline:
    """The scenarios that may be recorded, and the ones this run refused."""

    scenarios: tuple[Scenario, ...]
    outcomes: tuple[str, ...]
    #: (name, verdict) for each scenario kept out. Reported, never silent.
    refused: tuple[tuple[str, str], ...] = ()


def baseline(
    plan: tuple[Scenario, ...], results: tuple[runner.Result, ...]
) -> Baseline:
    """Which of this run's scenarios may become the recorded baseline. Pure."""
    kept: list[Scenario] = []
    outcomes: list[str] = []
    refused: list[tuple[str, str]] = []

    for scenario, result in zip(plan, results):
        if result.verdict not in KEEPABLE:
            refused.append((scenario.name, result.verdict))
            continue
        recorded, _ = repaired(scenario, result)
        kept.append(recorded)
        outcomes.append(result.verdict)

    # `zip` already stops at the shorter of the two, so an unexecuted scenario
    # is out of the suite either way. What it is not, without this, is
    # *reported* -- and a baseline that is quietly two tests shorter than the
    # plan the report printed is the failure this whole gate is about.
    for scenario in plan[len(results):]:
        refused.append((scenario.name, "not run"))

    return Baseline(
        scenarios=tuple(kept), outcomes=tuple(outcomes), refused=tuple(refused)
    )


def emit(
    scenarios_: tuple[Scenario, ...],
    directory: str | Path,
    *,
    because: str,
    credentials: Credentials | None = None,
    target_url: str = "",
    mark: str = "",
    source: str = "",
    parent: int | None = None,
    verdicts: dict | None = None,
    heals: tuple[Repair, ...] = (),
    map_updates: tuple[dict, ...] = (),
    outcomes: tuple[str, ...] = (),
    reverified: dict | None = None,
    rescues: tuple[dict, ...] = (),
    behaviour: BehaviorModel | None = None,
) -> Version:
    """Write the next version. Never touches one already written.

    Both forms of every scenario are written, and they are not redundant. The
    `.json` is what a re-run loads and what a repair is applied to -- it carries
    `from_key`, the expected diff and the field list, none of which survive a
    round trip through TypeScript. The `.spec.ts` is the export: what a judge
    can read, what CI can run with no part of this system installed.

    The number is allocated from what is on disk rather than from the lineage
    file, so two processes racing produce two directories and at worst one
    confusing name -- never one overwriting the other's tests.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    credentials = credentials or Credentials.from_env()

    existing = versions(root)
    number = (existing[-1].number + 1) if existing else 1
    if parent is None and existing:
        parent = existing[-1].number

    where = _version_dir(root, number)
    where.mkdir(parents=True, exist_ok=True)

    entries = []
    for index, scenario in enumerate(scenarios_, start=1):
        stem = f"{index:02d}-{_slug(scenario.name)}"
        (where / f"{stem}.json").write_text(to_json(scenario), encoding="utf-8")
        (where / f"{stem}.spec.ts").write_text(
            spec(scenario, credentials), encoding="utf-8"
        )
        entry = {
            "file": stem,
            "name": scenario.name,
            "node": scenario.node,
            "covers": list(scenario.covers),
            "origin": scenario.origin,
        }
        # Aligned by position, never matched by name: two scenarios in one
        # suite may legitimately share a name, and a lookup would give both the
        # first one's verdict.
        if index - 1 < len(outcomes):
            entry["verdict"] = outcomes[index - 1]
        entries.append(entry)

    version = Version(
        root=where,
        number=number,
        parent=parent,
        because=because,
        source=source,
        target_url=target_url or (scenarios_[0].target_url if scenarios_ else ""),
        fingerprint=mark,
        saved_at=_now(),
        scenarios=tuple(entries),
        verdicts=dict(verdicts or {}),
        heals=tuple(r.as_dict() for r in heals),
        map_updates=tuple(map_updates),
        reverified=dict(reverified or {}),
        rescues=tuple(rescues),
        behaviour=behaviour or BehaviorModel(),
    )
    (where / VERSION).write_text(
        json.dumps(version.as_dict(), indent=2), encoding="utf-8"
    )
    _write_lineage(root, version)
    return version


def _write_lineage(root: Path, latest: Version) -> None:
    """The suite's index: what a reader opens first.

    The suite's `target_url` is the one it was *recorded* against and never the
    one a later version was verified against. The two differ every time the
    demo stands in for a redeploy by moving the URL (`?v=2`), and taking the
    later one would quietly re-point the suite at the variant -- so the next
    bare replay would verify the drifted app against itself and find it calm.
    """
    history = versions(root)
    (root / LINEAGE).write_text(
        json.dumps(
            {
                "target_url": (history[0].target_url if history else latest.target_url),
                "verified_against": latest.target_url,
                "current": latest.label,
                "versions": [
                    {
                        "version": v.label,
                        "parent": f"v{v.parent:03d}" if v.parent else None,
                        "saved_at": v.saved_at,
                        "source": v.source,
                        "scenarios": len(v.scenarios),
                        "heals": len(v.heals),
                        "because": v.because,
                    }
                    for v in versions(root)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def lineage(directory: str | Path) -> dict:
    path = Path(directory) / LINEAGE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load(where: str | Path | Version) -> tuple[Scenario, ...]:
    """Every scenario in one version, in the order it was written.

    Accepts a `Version`, a version directory, or a suite root -- a suite root
    resolves to its current version, because "load the suite" and "load the
    version a replay would run" are the same request.
    """
    if isinstance(where, Version):
        root = where.root
    else:
        root = Path(where)
        if not (root / VERSION).exists():
            newest = current(root)
            if newest is None:
                return ()
            root = newest.root
    files = sorted(p for p in root.glob("*.json") if p.name not in (VERSION, LINEAGE))
    return tuple(from_json(p.read_text(encoding="utf-8")) for p in files)


def unseen(
    candidates: tuple[Scenario, ...], directory: str | Path
) -> tuple[Scenario, ...]:
    """The candidates the saved suite does not already exercise.

    **Matched on the action sequence, never on the state key or the name.**
    That is the whole of the redundancy guard, and the reason it is not keyed
    on state is worth stating: `state_key` folds in accessible names, so a
    renamed button re-keys every state it appears on, and a diff of two maps
    then reports *every* edge through those states as added. Keyed on state,
    a cosmetic rename would append a duplicate of the entire suite -- the
    precise failure "only generate tests for what changed" exists to prevent.

    An action sequence survives that, because the Healer has already rewritten
    the saved suite's actions to the new names by the time this runs (see
    `pipeline._keep`, which extends only after `verify`). What is left over is
    a path through the application that the suite genuinely does not walk.
    """
    already = {
        tuple(step.action for step in scenario.steps)
        for scenario in load(directory)
    }
    return tuple(
        candidate
        for candidate in candidates
        if tuple(step.action for step in candidate.steps) not in already
    )


def extend(
    directory: str | Path,
    additions: tuple[Scenario, ...],
    *,
    because: str,
    credentials: Credentials | None = None,
    target_url: str = "",
    mark: str = "",
    source: str = "",
    outcomes: tuple[str, ...] = (),
) -> Version | None:
    """Emit the saved suite plus `additions` as the next version.

    The kept suite grows here and nowhere else. `verify` may only *repair* what
    is already on disk -- `pipeline._keep` says why at length: a suite that
    recompiles itself from the app as it is now agrees with the app by
    construction and can no longer catch a regression. Adding a test for a flow
    that did not exist when the baseline was recorded is the one change to a
    kept suite that does not have that problem, because nothing is being
    replaced.

    Returns None when there is nothing to add, so a caller can treat "no new
    behaviour was found" as the ordinary outcome it is rather than as an error
    or an empty version on disk.
    """
    if not additions:
        return None

    existing = load(directory)
    tally: dict[str, int] = {}
    for verdict in outcomes:
        if verdict:
            tally[verdict] = tally.get(verdict, 0) + 1

    return emit(
        existing + additions,
        directory,
        because=because,
        credentials=credentials,
        target_url=target_url,
        mark=mark,
        source=source,
        # Positional, and only the additions were run just now: the inherited
        # scenarios carry no verdict rather than a stale one copied forward. A
        # verdict on a version says what *this* version's run proved.
        outcomes=("",) * len(existing) + outcomes,
        verdicts=tally,
    )


def path_of(root: Path, scenario: Scenario) -> Path | None:
    """The `.json` a scenario was loaded from, matched by name."""
    for path in sorted(
        p for p in Path(root).glob("*.json") if p.name not in (VERSION, LINEAGE)
    ):
        if from_json(path.read_text(encoding="utf-8")).name == scenario.name:
            return path
    return None


# -------------------------------------------------------------------- trigger


@dataclass(frozen=True)
class Drift:
    changed: bool
    before: str
    after: str

    @property
    def why(self) -> str:
        if not self.before:
            return "no fingerprint was recorded, so the suite has no past to compare to"
        if self.changed:
            return "the landing state key moved since the suite was saved"
        return "the landing state key is unchanged"


def should_replay(drift: Drift, if_drifted: bool = False) -> bool:
    """Whether to spend a replay. Pure, so the policy is checkable on its own.

    The default is yes, and the asymmetry is the point. A fingerprint that moved
    proves the markup changed; a fingerprint that did not move proves nothing,
    because behaviour drift is invisible to it. Reading the second as "all
    clear" is the failure this function exists to name: it was the first
    behaviour of this module, and against the SUT's `?bug=1` it skipped the run
    and reported calm on a suite that had three defects waiting in it.
    """
    return drift.changed or not if_drifted


def drifted(page: Page, directory: str | Path, target_url: str = "") -> Drift:
    """Has the target moved since the current version was emitted?

    A missing fingerprint reports `changed=True`. The alternative -- treating an
    unknown past as "nothing happened" -- makes the first run after adopting the
    suite the one run that never checks anything.
    """
    newest = current(directory)
    before = newest.fingerprint if newest else ""
    after = fingerprint(page, target_url or (newest.target_url if newest else ""))
    return Drift(changed=before != after, before=before, after=after)


# --------------------------------------------------------------------- repair


@dataclass
class Repair:
    """One step's locator, rewritten on disk, with why."""

    scenario: str
    step: int
    intent: str
    was: str
    now: str
    rung: str
    detail: str
    # Where on the map the repaired action is taken, and where it landed this
    # time. Carried so a repair can patch the map without the patcher having to
    # find the scenario again -- see `map_updates_for`.
    node: str = ""
    to_key: str = ""
    at: str = field(default_factory=_now)

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario, "step": self.step, "intent": self.intent,
            "was": self.was, "now": self.now, "rung": self.rung,
            "detail": self.detail, "node": self.node, "to_key": self.to_key,
            "at": self.at,
        }


def repaired(
    scenario: Scenario, result: runner.Result
) -> tuple[Scenario, tuple[Repair, ...]]:
    """The scenario with every healed locator substituted in. Pure.

    Positional, not by lookup: `result.steps` is aligned with `scenario.steps`
    from the front, and a scenario may legitimately contain the same action
    twice. Matching on value would repair the first occurrence twice and the
    second never.

    `expect.to_key` is refreshed alongside the action, and only because the
    Runner is explicit that it is evidence and never a pass condition. Leaving
    it stale would keep a state key that names markup nobody serves any more,
    in the one field a reader turns to for "what did it look like".
    """
    steps = list(scenario.steps)
    repairs: list[Repair] = []

    for index, outcome in enumerate(result.steps):
        if not outcome.resolution.healed or outcome.resolution.action is None:
            continue
        before = steps[index]
        expect = before.expect
        if outcome.actual_key:
            expect = replace(expect, to_key=outcome.actual_key)
        steps[index] = replace(before, action=outcome.resolution.action, expect=expect)
        repairs.append(Repair(
            scenario=scenario.name,
            step=index + 1,
            intent=before.intent,
            was=before.action,
            now=outcome.resolution.action,
            rung=outcome.resolution.rung,
            detail=outcome.resolution.detail,
            node=before.from_key,
            to_key=outcome.actual_key or expect.to_key,
        ))

    return replace(scenario, steps=tuple(steps)), tuple(repairs)


# ----------------------------------------------------------------- the map


def map_updates_for(repairs: tuple[Repair, ...]) -> tuple[dict, ...]:
    """The repairs, as corrections to the world model. Deduplicated.

    A rename that broke a locator broke the map's record of the same control,
    and two scenarios crossing the same state produce the same correction twice.
    Keyed on `(node, was)` so the map is told once.

    A repair with no `node` -- a suite written before `Repair` carried one --
    is dropped rather than guessed at. Patching an unnamed state means patching
    every state, which is how a healer starts inventing a map.
    """
    seen: dict[tuple[str, str], dict] = {}
    for repair in repairs:
        if not repair.node or not repair.was or not repair.now:
            continue
        seen.setdefault((repair.node, repair.was), {
            "state": repair.node,
            "was": repair.was,
            "now": repair.now,
            "rung": repair.rung,
            "to_key": repair.to_key,
            "at": repair.at,
        })
    return tuple(seen.values())


def apply_to_map(world, updates: tuple[dict, ...]):
    """A copy of the map with every healed action renamed. `world` is untouched.

    A copy and not a mutation, because the caller's map is usually the record of
    a *crawl* -- the evidence that the application looked one way before the
    change. Editing it in place would leave nothing to compare the new
    behaviour against, which is the whole reason `store.py` scopes a map to a
    run.

    Only the action string moves. The state key, the evidence, and `mutating`
    stay exactly as recorded: what the healer observed is that a control is now
    reachable under a different descriptor, and nothing more. Claiming the
    destination changed too would be asserting behaviour from a locator match.
    """
    from dataclasses import replace as _replace

    if not updates:
        return world

    patched = _replace(
        world,
        states=dict(world.states),
        transitions={key: list(value) for key, value in world.transitions.items()},
    )

    for update in updates:
        state, was, now = update.get("state"), update.get("was"), update.get("now")
        if not state or state not in patched.states:
            continue

        node = patched.states[state]
        if was in node.actions:
            patched.states[state] = _replace(
                node,
                actions=tuple(now if a == was else a for a in node.actions),
                found_by=node.found_by or "healer",
            )

        edges = patched.transitions.pop((state, was), None)
        if edges is not None:
            patched.transitions[(state, now)] = [
                _replace(edge, action=now, found_by="healer") for edge in edges
            ]

    return patched


# --------------------------------------------------------------------- verify


@dataclass
class Report:
    directory: Path
    target_url: str
    results: list[runner.Result] = field(default_factory=list)
    # Every repair the Runner's resolution *would* support, and the subset that
    # reached a file. They differ whenever a scenario healed one step and
    # reported a defect on another: the locator really was renamed, and the file
    # is still not rewritten, because the scenario as a whole is now describing
    # an app that is misbehaving. Reporting the first as though it were the
    # second is how a log comes to claim a change that never happened.
    repairs: list[Repair] = field(default_factory=list)
    applied: list[Repair] = field(default_factory=list)
    rewritten: list[Path] = field(default_factory=list)
    # What replaying the repaired scenarios did, before the version was
    # written. A repair is a hypothesis about a control until this runs.
    reverified: list[runner.Result] = field(default_factory=list)
    # Repairs the re-verification withdrew: computed, provisionally applied,
    # and then shown not to work. They are the reason `applied` is not simply
    # `repairs` minus the defects.
    rejected: list[Repair] = field(default_factory=list)
    # Attempts to recover a step nothing on the page could play, by exploring
    # the region it was taken from. See `agents/rescue.py`. Recorded whether or
    # not they succeeded -- "the region was looked at and had no answer" is a
    # finding about the app, not a silence.
    rescues: list = field(default_factory=list)
    # The version that was replayed, and the one the repairs were emitted as.
    # `emitted` stays None on a clean run, on a dry run, and on a run whose
    # only failures were defects -- three different reasons for the same
    # correct outcome: the suite on disk is left as it was.
    replayed: Version | None = None
    emitted: Version | None = None

    @property
    def withheld(self) -> tuple[Repair, ...]:
        """Repairs computed but not written, because their scenario failed."""
        return tuple(r for r in self.repairs if r not in self.applied)

    @property
    def defects(self) -> tuple[runner.Result, ...]:
        return tuple(r for r in self.results if r.verdict == runner.DEFECT)

    @property
    def escalations(self) -> tuple[runner.Result, ...]:
        return tuple(r for r in self.results if r.verdict == runner.ESCALATE)

    @property
    def verdict(self) -> str:
        """The worst verdict in the suite, on the Runner's own severity order."""
        for level in (runner.ESCALATE, runner.DEFECT, runner.HEALED, runner.PASSED):
            if any(r.verdict == level for r in self.results):
                return level
        return runner.PASSED

    @property
    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for result in self.results:
            tally[result.verdict] = tally.get(result.verdict, 0) + 1
        return tally

    @property
    def reverify_counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for result in self.reverified:
            tally[result.verdict] = tally.get(result.verdict, 0) + 1
        return tally

    @property
    def recovered(self) -> tuple:
        """The rescues that found a replacement for a lost control."""
        return tuple(r for r in self.rescues if r.recovered)

    def summary(self) -> str:
        parts = ", ".join(f"{n} {v}" for v, n in sorted(self.counts.items()))
        held = f", {len(self.withheld)} repair(s) withheld" if self.withheld else ""
        where = (
            f"emitted {self.emitted.label}"
            if self.emitted
            else "no new version (nothing to repair)"
        )
        saved = f", {len(self.recovered)} step(s) rescued" if self.recovered else ""
        thrown = (
            f", {len(self.rejected)} repair(s) withdrawn on re-verification"
            if self.rejected
            else ""
        )
        return f"{parts or 'nothing to run'}; {where}{held}{saved}{thrown}"


def verify(
    page: Page,
    directory: str | Path,
    target_url: str | None = None,
    credentials: Credentials | None = None,
    apply: bool = True,
    on_event=None,
    reverify: bool = True,
    rescue: bool = True,
    provider=None,
    run_id: int | None = None,
) -> Report:
    """Replay the saved suite, and write back what healed.

    `apply=False` is the dry run: the same replay, the same repairs computed,
    nothing emitted. It exists so that a caller who wants a *report* about
    drift -- a CI check that should fail rather than self-repair -- can have one
    without a second code path deciding what counts as a heal.

    **Running the suite is the change detector.** There is no drift gate here on
    purpose: `should_replay` explains why a fingerprint cannot see the failure
    that matters. The caller may check `drifted()` for the report; it must not
    use it to decide whether to run.

    **`rescue` is what happens when a step cannot be attempted at all.** The
    resolution ladder works on the page as it is; when nothing on that page
    plays the recorded part there is no rung left, and until this existed the
    scenario was simply abandoned to a human. `agents/rescue.py` explores the
    region the step was taken from and asks the *fresh* map what replaced the
    control -- which is the one question re-reading the old map cannot answer.
    It fires only on that specific escalation and never on a defect.

    **`reverify` is what makes an emitted version a claim rather than a hope.**
    Every repair -- from the ladder or from a rescue -- is a hypothesis until
    the repaired scenario has been replayed and seen to work. Only the changed
    scenarios are replayed, because the unchanged ones were just run. A repair
    the replay contradicts is *withdrawn*, the original is kept, and the
    withdrawal is reported: a suite that quietly wrote a repair it could not
    stand behind would be a worse liar than one that never repaired at all.
    """
    root = Path(directory)
    version = current(root)
    if version is None:
        return Report(directory=root, target_url=target_url or "")

    url = target_url or version.target_url
    credentials = credentials or Credentials.from_env()
    report = Report(directory=root, target_url=url, replayed=version)

    # The suite as it will be written if anything is emitted: every scenario,
    # repaired where the evidence allowed it. Built even on a dry run, because
    # the difference between the two must be whether it is *written*, not
    # whether it is computed -- a second code path deciding what counts as a
    # heal is exactly what makes a dry run stop predicting the real one.
    from . import rescue as rescue_agent

    originals = list(load(version))
    next_suite: list[Scenario] = []
    # The repairs proposed for each *position*, so re-verification can withdraw
    # exactly the ones a failed confirmation invalidates. Matching by scenario
    # name would withdraw both of two scenarios that share one, which this suite
    # format explicitly allows.
    proposed: list[tuple[Repair, ...]] = []

    for scenario in originals:
        result = runner.run(
            page, scenario, target_url=url, credentials=credentials,
            on_event=on_event, provider=provider, run_id=run_id,
        )
        report.results.append(result)

        candidate, repairs = repaired(scenario, result)

        # The step nothing on the page can play. The ladder has no rung left,
        # so the only thing that can answer is a fresh look at the region --
        # which is a different act from re-reading the map the suite was
        # recorded against, and the reason this is not just another rung.
        rescued = None
        if rescue and result.verdict == runner.ESCALATE:
            try:
                rescued = rescue_agent.attempt(
                    page, scenario, result,
                    target_url=url, credentials=credentials,
                    provider=provider, on_event=on_event,
                    # So the wave it may send files its transcripts under this
                    # run rather than under `adhoc/`. A console run is the only
                    # caller that has an id; the CLI passes None and keeps the
                    # old destination.
                    run_id=run_id,
                )
            except Exception as exc:
                # An exploration that fell over must not cost the rest of the
                # suite its verdicts. The scenario stays escalated, which is
                # exactly where it was before this ran.
                if on_event:
                    on_event("warn", f"rescue failed: {type(exc).__name__}: {exc}")
            if rescued is not None:
                report.rescues.append(rescued)
                if rescued.recovered:
                    candidate, recovery = rescue_agent.apply(candidate, rescued)
                    if recovery is not None:
                        repairs = repairs + (recovery,)

        if not repairs:
            next_suite.append(scenario)
            proposed.append(())
            continue
        report.repairs.extend(repairs)

        # A defect anywhere in the scenario stops the repair for the whole
        # scenario, not just the offending step. A file half-repaired against an
        # app that is behaving wrongly is a file describing an app that never
        # existed, and the next run would compare against it as if it had.
        #
        # An escalation used to be treated the same way and no longer is, but
        # only when a rescue answered it: "no control here plays this part" is
        # an absence, and an absence that a fresh crawl can fill is a rename.
        # An escalation nobody could answer is still left exactly as recorded.
        blocked = result.verdict == runner.DEFECT or (
            result.verdict == runner.ESCALATE
            and not (rescued is not None and rescued.recovered)
        )
        if blocked:
            next_suite.append(scenario)
            proposed.append(())
            continue

        next_suite.append(candidate)
        proposed.append(repairs)
        report.applied.extend(repairs)

    if not (apply and report.applied):
        return report

    # --- re-verify --------------------------------------------------------
    #
    # Every repair above is a hypothesis: the ladder says "this control is the
    # one that was renamed" and a rescue says "this is what replaced it", and
    # neither has yet been asked to *work*. Replaying the changed scenarios is
    # the only thing that can tell the difference, and it costs one pass over
    # the scenarios that changed rather than over the suite.
    if reverify:
        confirmed: list[Scenario] = []
        survived: list[Repair] = []
        for index, repairs in enumerate(proposed):
            if not repairs:
                confirmed.append(next_suite[index])
                continue
            outcome = runner.run(
                page, next_suite[index], target_url=url,
                credentials=credentials, on_event=on_event,
            )
            report.reverified.append(outcome)
            if outcome.verdict in (runner.PASSED, runner.HEALED):
                confirmed.append(next_suite[index])
                survived.extend(repairs)
                continue
            # The repair did not survive contact with the app. Keep the
            # original: a test that escalates is a question, and a test
            # rewritten to a control that does not work is a wrong answer.
            confirmed.append(originals[index])
            report.rejected.extend(repairs)
            if on_event:
                on_event(
                    "warn",
                    f"withdrawn: {originals[index].name!r} -- the repair replayed "
                    f"as {outcome.verdict}, so it is not one",
                )
        next_suite = confirmed
        report.applied = survived
        if not report.applied:
            return report

    applied = tuple(report.applied)
    updates = map_updates_for(applied)
    # --- what this run now believes ---------------------------------------
    #
    # The suite is about to become a new version, and the understanding that
    # produced it has to travel with it or the loop does not close: run N+1
    # would recompute from zero, and the repair this run just made would teach
    # the system nothing.
    #
    # A failed replay is the signal that something moved, so `moved` gates
    # whether this happens at all -- a suite that replayed clean teaches
    # nothing new and pays for no crawl. `rescue.look` then re-crawls a REGION
    # budget from the entry (with one aimed colony wave when a provider
    # exists) and `behavior.refresh` re-reads it, carrying every claim about
    # states the region did not reach through untouched.
    #
    # **The region is around the entry, not around the failing node.** `look`
    # maps outward from a url and the failing node's url is not reliably known
    # once the replay loop has finished -- the page is wherever the last
    # scenario left it. So this refreshes the neighbourhood the crawl can reach
    # in eight states rather than the precise screen that moved, and a failure
    # deep in the app may re-read ground that did not change. Narrowing it
    # needs the runner to report where each scenario was standing, which is a
    # change to `Result` and not to this.
    #
    # With no provider there is nothing that can interpret anything, and the
    # honest answer is what we already believed rather than an empty model.
    believed = version.behaviour
    moved = tuple({
        outcome.step.from_key
        for result in report.results
        if result.verdict != runner.PASSED
        for outcome in result.steps
        if outcome.verdict != runner.PASSED and outcome.step.from_key
    })
    if provider is not None and moved:
        try:
            region, how = rescue_agent.look(
                page, url,
                intent=(
                    "A saved test failed on this application. Describe what "
                    "this part of it does now, so a reading of it taken before "
                    "the change can be replaced rather than trusted."
                ),
                credentials=credentials, provider=provider, on_event=on_event,
                run_id=run_id,
            )
            believed = behavior_refresh(believed, region, provider,
                                        on_event=on_event)
            if on_event:
                on_event(
                    "decision",
                    f"behaviour refreshed by {how} after "
                    f"{len(moved)} state(s) failed to replay: "
                    f"{len(believed.hypotheses)} claim(s) now held",
                )
        except Exception as exc:
            # A refresh that fell over must not cost the suite its repairs.
            # The model stays exactly as the last version left it.
            if on_event:
                on_event("warn", f"behaviour refresh failed: "
                                 f"{type(exc).__name__}: {exc}")

    report.emitted = emit(
        tuple(next_suite),
        root,
        because=(
            f"healed {len(applied)} locator(s) across "
            f"{len({r.scenario for r in applied})} scenario(s)"
            + (
                f", {sum(1 for r in applied if r.rung == 'rescue')} of them "
                "recovered by exploring the region that lost the control"
                if any(r.rung == "rescue" for r in applied)
                else ""
            )
            + (
                f"; re-verified before emitting ({len(report.reverified)} replayed)"
                if report.reverified
                else ""
            )
        ),
        credentials=credentials,
        target_url=url,
        # The suite now describes the app as it is, so the fingerprint it is
        # compared against moves with it. Left at the parent's value, every
        # later run would report drift against a version already repaired.
        mark=fingerprint(page, url),
        source=version.source,
        parent=version.number,
        verdicts=report.counts,
        heals=applied,
        map_updates=updates,
        reverified=report.reverify_counts,
        rescues=tuple(r.as_dict() for r in report.recovered),
        behaviour=believed,
    )
    report.rewritten = sorted(report.emitted.root.glob("*.spec.ts"))
    return report


# ------------------------------------------------------------------- keeping


@dataclass(frozen=True)
class Kept:
    """What one run did to the suite on disk, in the terms a reader needs.

    `version` is always the one to download -- the baseline this run recorded,
    the repaired version it emitted, or the untouched version it replayed and
    found intact. A caller that had to work that out from a `Report` and a
    `Version | None` would get it wrong on the third case, which is the common
    one.
    """

    directory: Path
    version: Version | None
    #: True when this run authored the baseline, rather than replaying one.
    recorded: bool = False
    #: The replay, when there was a suite to replay. None on the first run.
    report: "Report | None" = None

    @property
    def healed(self) -> bool:
        return bool(self.report and self.report.emitted)


def keep(
    page: Page,
    plan: tuple[Scenario, ...],
    *,
    target_url: str,
    results: tuple[runner.Result, ...] = (),
    credentials: Credentials | None = None,
    because: str = "",
    source: str = "",
    root: Path | None = None,
    export_too: bool = True,
    provider=None,
    on_event=None,
    run_id: int | None = None,
) -> Kept:
    """Record this run's plan as the baseline, or replay the kept suite and heal.

    Which of the two happens is decided by the filesystem, not by a flag, for
    the reason `main` decides it that way: "is there a suite for this target
    yet" is a fact about the world rather than a preference.

    **A later run never authors new tests into the kept suite.** The caller has
    just compiled a fresh plan against the app as it is now; that plan is this
    run's *report*. Writing it over the saved scenarios would rebuild the suite
    from the current app, which agrees with the current app by construction --
    the exact move that stops a regression suite catching a regression.

    This is the console's route to the same place `pipeline._keep` reaches from
    the CLI. It exists because a run whose tests live only in the process that
    made them has nothing to download, and nothing to fail next week.
    """
    directory = Path(root) if root else directory_for(target_url)
    credentials = credentials or Credentials.from_env()
    existing = current(directory)

    def say(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    if existing is None:
        # The gate. A baseline is the thing every later run is measured
        # against, so what may go into it is decided here and refusals are
        # spoken aloud -- see `baseline`.
        chosen = baseline(tuple(plan), tuple(results))
        if chosen.refused:
            say(
                "warn",
                f"{len(chosen.refused)} scenario(s) not recorded: "
                + ", ".join(f"{name} ({verdict})" for name, verdict in chosen.refused)
                + " -- a suite recorded already failing has nothing to regress from",
            )
        if not chosen.scenarios:
            say(
                "warn",
                "no suite kept: "
                + (
                    "every scenario this run compiled was refused"
                    if chosen.refused
                    else "this run compiled no scenarios"
                ),
            )
            return Kept(directory=directory, version=None)
        counts: dict[str, int] = {}
        for verdict in chosen.outcomes:
            counts[verdict] = counts.get(verdict, 0) + 1
        version = emit(
            chosen.scenarios,
            directory,
            because=because or f"recorded from the {source or 'map'} world model",
            credentials=credentials,
            target_url=target_url,
            mark=fingerprint(page, target_url),
            source=source,
            verdicts=counts,
            outcomes=chosen.outcomes,
        )
        if export_too:
            export(version)
        say(
            "decision",
            f"kept {version.label}: {len(version.scenarios)} scenario(s) written "
            f"to disk as the baseline the next run is measured against"
            + (
                f", {len(chosen.refused)} refused"
                if chosen.refused
                else ""
            )
            ,
        )
        return Kept(directory=directory, version=version, recorded=True)

    report = verify(
        page,
        directory,
        target_url=target_url,
        credentials=credentials,
        # The colony's own provider, so a lost control is looked for by ants
        # rather than only by a breadth-first crawl. None is not a failure --
        # `rescue.look` degrades to the crawl, which answers the common case.
        provider=provider,
        on_event=on_event,
        run_id=run_id,
    )
    version = report.emitted or existing
    if export_too and version is not None:
        export(version)
    for attempt in report.rescues:
        say(
            "decision" if attempt.recovered else "warn",
            f"rescue [{attempt.source}, {attempt.explored} state(s)]: "
            f"{attempt.scenario} step {attempt.step} -- {attempt.why}",
        )
    if report.reverified:
        say(
            "info",
            f"re-verified {len(report.reverified)} repaired scenario(s): "
            + (", ".join(f"{n} {v}" for v, n in sorted(report.reverify_counts.items())))
            + (f"; {len(report.rejected)} repair(s) withdrawn" if report.rejected else ""),
        )
    if report.emitted is not None:
        say(
            "decision",
            f"healed {existing.label} into {report.emitted.label}: "
            f"{len(report.applied)} locator(s) re-resolved against changed markup"
            + (f", {len(report.recovered)} recovered by exploring"
               if report.recovered else "")
            + (f", {len(report.withheld)} withheld from a failing scenario"
               if report.withheld else ""),
        )
    else:
        say(
            "decision",
            f"{existing.label} still describes this app -- "
            + (
                f"{len(report.defects) + len(report.escalations)} failure(s) left "
                "on disk exactly as recorded, because rewriting a test that "
                "failed is how a suite turns green by deleting the reason it "
                "was red"
                if report.defects or report.escalations
                else "every locator still resolved as written"
            ),
        )
    return Kept(directory=directory, version=version, report=report)


# ------------------------------------------------------------------------ cli


def record(
    page: Page,
    entry_url: str,
    directory: str | Path,
    credentials: Credentials | None = None,
    colony: bool = True,
    on_event=None,
    source: str = "",
    limit: int = 8,
) -> Version:
    """Explore, plan, compile, run, and emit v001. Crawl first, then the ants.

    The order is the architecture and `CLAUDE.md` is emphatic about it: the
    deterministic crawler answers *what can I reproduce*, and only then is there
    something for judgement to be about. What the colony adds, for this
    module's purposes, is reach -- the states behind a login wall, the branches
    an ant chose to take -- and every one of those becomes a scenario the crawl
    alone could not have compiled. **The ants run here.** `source="map"` changes
    which world model the *planner* reads; it does not stop the colony
    exploring, because a plan from a map nobody finished exploring is a smaller
    plan, not a deterministic one.

    **The suite is run before it is saved.** A scenario is a recording of
    observed behaviour, so a scenario that cannot reproduce that behaviour
    against the app it was just recorded from is not a baseline -- it is a fact
    about the application, usually nondeterminism. Each verdict is written into
    the version rather than used to drop the scenario: a suite that silently
    deleted what it could not reproduce would report a clean baseline and hide
    the flakiest part of the app.

    `colony=False`, or no API key, records the crawl alone. That is a smaller
    suite, not a broken one: `crawl` needs no key by design, and a suite you can
    record with no account is the difference between this being usable on
    someone else's machine and being a demo.
    """
    from .explorer.crawler import Budget as CrawlBudget
    from .explorer.crawler import crawl
    from .planner import plan as make_plan
    from .planner import source_from_env

    announce = on_event or (lambda level, message: print(f"            {message}"))
    credentials = credentials or Credentials.from_env()
    source = source or source_from_env()

    announce("info", "crawling deterministically first")
    world = crawl(page, entry_url, CrawlBudget(max_actions=40, max_seconds=180),
                  credentials=credentials)
    announce("info", f"crawl: {len(world.states)} states, {len(world.skipped)} refused")

    behaviour = None
    if colony:
        try:
            from .llm import load
            from .orchestrator import Budget as ColonyBudget
            from .orchestrator import run as explore

            provider = load()
            announce("info",
                     f"colony: {provider.name} / {provider.model} over the crawled map")
            exploration = explore(
                page, entry_url, provider, budget=ColonyBudget(),
                credentials=credentials, world=world,
                on_event=lambda level, message: announce(level, message),
            )
            world = exploration.world
            behaviour = getattr(exploration, "behaviour", None)
            announce("info",
                     f"colony: {len(world.states)} states after {exploration.waves} wave(s)")
        except Exception as exc:
            # No key, an exhausted key, a provider that will not construct. The
            # crawl already happened and is already a suite; losing it because
            # the optional half failed would be the worse outcome by far.
            announce("warn",
                     f"colony skipped ({type(exc).__name__}: {exc}) -- recording the crawl")

    planned = make_plan(world, behaviour, source=source, limit=limit)
    announce("info",
             f"planner[{planned.source}]: {len(planned)} scenario(s), "
             f"{planned.from_behaviour} from the behavioural model, "
             f"{len(planned.nodes)} node(s)")
    if planned.degraded:
        announce("warn", f"planner: {planned.degraded}")

    outcomes = []
    for scenario in planned:
        result = runner.run(page, scenario, target_url=entry_url,
                            credentials=credentials)
        outcomes.append(result.verdict)
    counts: dict[str, int] = {}
    for verdict in outcomes:
        counts[verdict] = counts.get(verdict, 0) + 1
    announce("info", "baseline: " + (", ".join(f"{n} {v}" for v, n in sorted(counts.items()))
                                     or "nothing to run"))

    return emit(
        tuple(planned.scenarios),
        directory,
        because=f"recorded from the {planned.source} world model",
        credentials=credentials,
        target_url=entry_url,
        mark=fingerprint(page, entry_url),
        source=planned.source,
        verdicts=counts,
        outcomes=tuple(outcomes),
    )


GENERATED = Path(__file__).resolve().parent.parent.parent / "web" / "generated"


def export(version: Version, destination: Path | None = None) -> tuple[Path, ...]:
    """Copy one version's `.spec.ts` to `web/generated`. Returns what it wrote.

    The export exists because a suite nobody can run without this system
    installed is a claim rather than a deliverable: `npx playwright test
    generated` runs these with no Python, no map and no agent. It is a copy of a
    version and never the source of truth -- the version directory is
    immutable, this directory is disposable, and stale files from a longer
    previous suite are removed so what is here is exactly one version.
    """
    into = destination or GENERATED
    into.mkdir(parents=True, exist_ok=True)
    for stale in into.glob("*.spec.ts"):
        stale.unlink()
    written = []
    for path in sorted(version.root.glob("*.spec.ts")):
        target = into / path.name
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)
    (into / "SUITE.md").write_text(
        f"# {version.label}\n\n"
        f"Exported from `{version.root}` at {_now()}.\n\n"
        f"- target: {version.target_url}\n"
        f"- planner: {version.source or 'unrecorded'}\n"
        f"- scenarios: {len(version.scenarios)} "
        f"({version.from_behaviour} from the behavioural model)\n"
        f"- nodes covered: {len(version.nodes)}\n"
        f"- because: {version.because}\n\n"
        "Regenerated by `make suite`. Edits here are overwritten; the suite of "
        "record is the version directory above.\n",
        encoding="utf-8",
    )
    return tuple(written)


def main(entry_url: str, root: Path | None = None) -> int:
    """Record the suite if there is none; otherwise replay it and heal.

    One command, and which half runs is decided by the filesystem rather than
    by a flag, because "is there a suite yet" is a fact and not a preference.
    """
    import os

    from playwright.sync_api import sync_playwright

    directory = root or directory_for(entry_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(ignore_https_errors=True)

        if current(directory) is None:
            print(f"RECORD      no suite at {directory}")
            version = record(page, entry_url, directory,
                             colony=os.environ.get("COLONY", "1") != "0")
            browser.close()
            print(f"\nSUITE       {version.render()}")
            for path in sorted(version.root.glob("*.spec.ts")):
                print(f"            {path}")
            for path in export(version):
                print(f"  EXPORT    {path}")
            return 0

        # Reported, never obeyed. The suite is the detector: a behavioural
        # regression moves nothing a fingerprint compares, so gating the replay
        # on this would skip exactly the run worth making.
        drift = drifted(page, directory, entry_url)
        print(f"DRIFT       {drift.changed} -- {drift.why}")
        if not should_replay(drift, os.environ.get("IF_DRIFTED") == "1"):
            print("            IF_DRIFTED=1 and the markup has not moved; skipping")
            browser.close()
            return 0
        if not drift.changed:
            print("            markup unchanged -- replaying anyway, since a "
                  "behavioural regression moves nothing a fingerprint compares")

        report = verify(page, directory, target_url=entry_url,
                        on_event=lambda level, message: print(f"            {message}"))
        browser.close()

    replayed = report.replayed
    print(f"\nSUITE       replayed {replayed.label if replayed else '(none)'} -- "
          f"{report.summary()}")
    for repair in report.applied:
        print(f"  HEAL      {repair.scenario} step {repair.step}: "
              f"{repair.was!r} -> {repair.now!r} [{repair.rung}]")
    for repair in report.withheld:
        print(f"  WITHHELD  {repair.scenario} step {repair.step}: "
              f"{repair.was!r} -> {repair.now!r} would have healed, but the "
              f"scenario reported a defect")
    for result in report.defects:
        print(f"  DEFECT    {result.scenario.name} -- left on disk unchanged")
    for result in report.escalations:
        print(f"  ESCALATE  {result.scenario.name} -- a human has to say what it means")

    if report.emitted:
        print(f"\n{report.emitted.render()}")
        for update in report.emitted.map_updates:
            print(f"  MAP       [{str(update['state'])[:8]}] "
                  f"{update['was']!r} -> {update['now']!r}")
        for path in export(report.emitted):
            print(f"  EXPORT    {path}")

    return 0 if report.verdict in (runner.PASSED, runner.HEALED) else 1


if __name__ == "__main__":
    # A second argument names an existing suite to replay, rather than deriving
    # the directory from the URL. Needed because the URL is not always the
    # suite's identity: the SUT's `?v=2` knob stands in for a redeploy, and
    # deriving the path from the query string would record a second suite
    # instead of verifying the first one against the change.
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"
    where = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    sys.exit(main(url, where))
