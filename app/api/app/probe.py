"""Observable checks for the HTTP surface. Not a test suite -- evidence.

    cd app/api && uv run python -m app.probe

Runs against a `TestClient` and a throwaway SQLite file, so it needs no server,
no browser and no API key. What it is guarding is the shape of the one payload
the console cannot render without: `GET /api/runs/{id}/map`.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from .db import _add_missing_columns as add_missing_columns
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
            fields=json.dumps([["textbox", "Email"], ["textbox", "Password"]]),
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



def _invariant_reporting() -> bool:
    """The console path must report invariants, not only the CLI pipeline.

    `agents/invariants.py` is the only oracle that can fire on the first crawl
    of an application we cannot redeploy -- every replay verdict against an
    unchanged third-party target is PASSED by construction. It was wired into
    `agents/pipeline.py` and not into this router, which meant the surface we
    demo was the one surface that could not report a defect.

    The map fixture comes from `agents.probe`, which already builds the exact
    shapes each rule fires and stays silent on. A second copy here would drift
    from the rules it is meant to exercise.
    """
    from agents.probe import _map_of
    from .routers.explore import report_invariants

    ok = True
    valid, invalid = (
        "submit[valid]:button:Sign in",
        "submit[invalid]:button:Sign in",
    )

    # Valid input moved the app forward and input chosen to be rejected landed
    # in the same place: the form took what it should have refused.
    events: list[tuple[str, str, str | None]] = []
    broken = report_invariants(
        _map_of([("a", valid, "b", True), ("a", invalid, "b", True)]),
        lambda level, message, surface=None: events.append((level, message, surface)),
    )

    ok &= check(
        "a broken invariant is returned to the caller",
        [v.rule for v in broken] == ["invalid-accepted"],
        f"rules={[v.rule for v in broken]}",
    )
    ok &= check(
        "a broken invariant reaches the defect surface, not just the timeline",
        any(level == "error" and surface == "defect" for level, _, surface in events),
        f"events={events}",
    )
    ok &= check(
        "the report names the rule that broke",
        any("invalid-accepted" in message for _, message, _ in events),
        f"events={events}",
    )

    # A form that rejects bad input is correct, and silence about it is not the
    # same as never having looked -- the run must still say the check ran.
    clean: list[tuple[str, str, str | None]] = []
    held = report_invariants(
        _map_of([("a", valid, "b", True), ("a", invalid, "c", False)]),
        lambda level, message, surface=None: clean.append((level, message, surface)),
    )
    ok &= check(
        "a form that rejects bad input yields no violation",
        held == (),
        f"rules={[v.rule for v in held]}",
    )
    ok &= check(
        "an intact run still records that the invariants were checked",
        bool(clean) and not any(level == "error" for level, _, _ in clean),
        f"events={clean}",
    )
    ok &= check(
        "silence belongs to the report, not to the defect surface",
        all(surface != "defect" for _, _, surface in clean),
        f"events={clean}",
    )
    return ok


def _status_policy() -> bool:
    """A violation is a defect, so it must colour the badge like one.

    Without this the wiring above is decorative: the console would print
    `invalid-accepted` into the timeline and still stamp the run green, which
    is the `green badge over an empty suite` failure this router already
    refuses one paragraph further down.
    """
    from agents.invariants import Violation

    from .routers.explore import status_for

    ok = True
    quiet = {"passed": 3, "healed": 0, "defect": 0, "escalate": 0}

    def broke(rule: str) -> Violation:
        return Violation(rule=rule, state="a", action="x", because="", evidence=0)

    ok &= check(
        "a clean run with a model passes",
        status_for(quiet, (), incomplete=False, modelled=True) == "passed",
    )
    # An uncovered claim is the third kind of outcome and neither of the other
    # two. Nothing about the application misbehaved, so `failed` would libel it;
    # the user named a behaviour and the run did not test it, so `passed`
    # answers a question nobody asked while burying the one they did.
    ok &= check(
        "a claim the suite never exercised leaves the run degraded, not green",
        status_for(quiet, (), incomplete=False, modelled=True, unmatched=1)
        == "degraded",
    )
    ok &= check(
        "a real defect still outranks an uncovered claim",
        status_for(
            {"passed": 1, "healed": 0, "defect": 1, "escalate": 0},
            (),
            incomplete=False,
            modelled=True,
            unmatched=1,
        )
        == "failed",
    )
    ok &= check(
        "a 5xx during the crawl fails the run even when every replay passed",
        status_for(quiet, (broke("server-error"),), incomplete=False, modelled=True)
        == "failed",
    )
    ok &= check(
        "a form that accepted an empty submission fails the run",
        status_for(quiet, (broke("empty-accepted"),), incomplete=False, modelled=True)
        == "failed",
    )
    # The rule reads "we called this input invalid and the app took it", and
    # what made it invalid was a policy the synthesizer guessed. Verified on our
    # own SUT: the cached payload for `button:Continue` is `{Password: "short"}`
    # and the app's only rule is `complete = Boolean(email && password)`, so it
    # confirms the order and the rule calls that a defect. Worth reporting,
    # never worth a red badge -- a suspicion that colours the run the same as a
    # 5xx makes the badge mean nothing.
    ok &= check(
        "input the synthesizer merely believed was invalid does not redden the badge",
        status_for(quiet, (broke("invalid-accepted"),), incomplete=False, modelled=True)
        == "passed",
    )
    ok &= check(
        "a form that cannot be shown to validate does not redden the badge",
        status_for(quiet, (broke("no-validation"),), incomplete=False, modelled=True)
        == "passed",
    )
    ok &= check(
        "one proven violation among suspicions still fails the run",
        status_for(
            quiet,
            (broke("invalid-accepted"), broke("server-error")),
            incomplete=False,
            modelled=True,
        )
        == "failed",
    )
    ok &= check(
        "a clean model-free run is degraded, not green",
        status_for(quiet, (), incomplete=False, modelled=False) == "degraded",
    )
    ok &= check(
        "a proven violation outranks degraded",
        status_for(quiet, (broke("server-error"),), incomplete=False, modelled=False)
        == "failed",
    )
    return ok


def _suite_download() -> bool:
    """The suite endpoints, against a real version directory written here.

    Written rather than pointed at `artifacts/suites/`: a probe that passes only
    on the machine that has already recorded a suite is a probe that tells you
    nothing on a fresh checkout, and this one has to fail when the router stops
    finding files.
    """
    import io
    import zipfile

    from agents import regression
    from agents.generator import Scenario, Step
    from agents.generator import Expectation

    print("\nSUITE       kept versions, served as files")
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "suites" / "example-com"
        scenario = Scenario(
            name="follow the Home link",
            target_url="https://example.com",
            origin="map",
            steps=(
                Step(
                    intent="follow the Home link",
                    action="link:Home",
                    from_key="aaaa000000000000",
                    fields=(),
                    expect=Expectation(
                        moved=True,
                        mutating=False,
                        added=("heading Welcome",),
                        removed=(),
                        to_key="bbbb000000000000",
                    ),
                ),
            ),
        )
        version = regression.emit(
            (scenario,),
            root,
            because="recorded by the probe",
            target_url="https://example.com",
            source="map",
            outcomes=("passed",),
        )

        engine = create_engine(
            f"sqlite:///{tmp}/suite.db", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(engine)

        def override():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = override
        client = TestClient(app)
        with Session(engine) as session:
            run = Run(target_url="https://example.com", status="passed")
            session.add(run)
            session.commit()
            session.refresh(run)
            session.add(
                TestCase(
                    run_id=run.id,
                    name="follow the Home link",
                    status="defect",
                    suite_version=version.label,
                )
            )
            session.commit()
            run_id = run.id

        # The router resolves the directory from the target URL, so the probe
        # has to put its version where `directory_for` will look.
        original = regression.SUITES
        regression.SUITES = pathlib.Path(tmp) / "suites"
        try:
            body = client.get(f"/api/runs/{run_id}/suite").json()
            ok &= check(
                "a run names the version kept for its target",
                body["version"] and body["version"]["label"] == version.label,
                f"got {body.get('version')}",
            )
            ok &= check(
                "every kept scenario is listed",
                len(body["specs"]) == 1,
                f"got {len(body['specs'])}",
            )
            spec = body["specs"][0] if body["specs"] else {}
            ok &= check(
                "a baseline claims no re-verification, because there was none",
                body["version"]["reverified"] == {} and body["version"]["rescues"] == 0,
                "a version that never replayed anything must not wear the badge",
            )
            ok &= check(
                "a spec arrives with its runnable source, not just a filename",
                "@playwright/test" in spec.get("code", ""),
                f"got {len(spec.get('code', ''))} bytes",
            )
            # The whole point of the join: the panel must show what *this* run
            # did, not the verdict frozen into the manifest when it was written.
            ok &= check(
                "this run's verdict beats the one recorded at emit time",
                spec.get("status") == "defect",
                f"manifest said passed, endpoint said {spec.get('status')!r}",
            )

            archive = client.get(f"/api/runs/{run_id}/suite/download")
            ok &= check(
                "the suite downloads as a zip",
                archive.status_code == 200
                and archive.headers.get("content-type") == "application/zip",
                f"{archive.status_code} {archive.headers.get('content-type')}",
            )
            names = []
            if archive.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(archive.content)) as opened:
                    names = opened.namelist()
            ok &= check(
                "the archive holds the .spec.ts, a config and a README",
                any(n.endswith(".spec.ts") for n in names)
                and "playwright.config.ts" in names
                and "README.md" in names,
                f"got {names}",
            )
            # The `.json` is this system's replay format and carries state keys
            # that mean nothing outside a run. Shipping it makes the archive
            # look like it needs AIVAR to be useful, which is the opposite of
            # what the export is for.
            ok &= check(
                "and not the .json replay format",
                not any(n.endswith(".json") and n != "version.json" for n in names),
                f"got {names}",
            )

            ok &= check(
                "one spec can be downloaded on its own",
                client.get(
                    f"/api/runs/{run_id}/suite/spec/{spec.get('file')}"
                ).status_code
                == 200,
            )
            # `stem` is matched against the manifest, never joined onto a path.
            ok &= check(
                "a spec name the manifest does not hold is a 404",
                client.get(
                    f"/api/runs/{run_id}/suite/spec/..%2F..%2Fsuite"
                ).status_code
                == 404,
            )
            ok &= check(
                "an unknown run is a 404, not a 500",
                client.get("/api/runs/424242/suite").status_code == 404,
            )

            # A target nothing has been recorded against is the state every
            # console sits in before its first run. It must be an empty suite
            # rather than an error, or the panel cannot say why it is empty.
            with Session(engine) as session:
                fresh = Run(target_url="https://nothing.example", status="passed")
                session.add(fresh)
                session.commit()
                session.refresh(fresh)
                fresh_id = fresh.id
            empty = client.get(f"/api/runs/{fresh_id}/suite").json()
            ok &= check(
                "a target with no suite yet reports none rather than failing",
                empty["version"] is None and empty["specs"] == [],
            )
            ok &= check(
                "and downloading it is a 404, not an empty zip",
                client.get(f"/api/runs/{fresh_id}/suite/download").status_code == 404,
            )
        finally:
            regression.SUITES = original
            app.dependency_overrides.clear()

    return ok


def _suites_are_per_session() -> bool:
    """A second session on the same URL must not open the first one's tests.

    Reported from the console: enter a URL, add a context box, press Start, and
    the panel fills with scenarios from a session recorded hours earlier. The
    cause was that `regression.keep` asks the *filesystem* whether a suite
    exists for the target -- which is the right question for `make suite` at a
    command line and the wrong one for a console where two people can be
    pointed at the same staging URL.

    Both halves are checked, because fixing only the writer leaves the reader
    serving the shared directory and the bug is still on screen.
    """
    from agents import regression
    from agents.generator import Expectation, Scenario, Step

    from .models import TestSession

    print("\nSESSIONS    a suite belongs to a session, not to a URL")
    ok = True

    url = "https://shared.example"
    mine = regression.directory_for(url, session_uid="aaaa1111bbbb")
    yours = regression.directory_for(url, session_uid="cccc2222dddd")
    cli = regression.directory_for(url)

    ok &= check(
        "two sessions on one URL are two suites",
        mine != yours,
        f"both resolved to {mine}",
    )
    ok &= check(
        "and neither is the command line's",
        cli != mine and cli != yours and cli.name == "shared-example",
        f"cli={cli.name} session={mine.name}",
    )
    # The whole reason it is the uid: `make reset` reissues row numbers and
    # leaves `artifacts/` alone, so a directory named after `TestSession.id`
    # is handed to the next database's first session.
    ok &= check(
        "a session's suite is named after something a reset cannot reissue",
        "aaaa1111bbbb" in mine.name and "-s1" not in mine.name,
        f"named {mine.name}",
    )

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
            # Two sessions, the same URL, no context in common -- the report.
            first, second = TestSession(target_url=url), TestSession(target_url=url)
            session.add(first)
            session.add(second)
            session.commit()
            session.refresh(first)
            session.refresh(second)
            recorded = Run(target_url=url, status="passed", session_id=first.id)
            fresh = Run(target_url=url, status="running", session_id=second.id)
            session.add(recorded)
            session.add(fresh)
            session.commit()
            session.refresh(recorded)
            session.refresh(fresh)
            first_id, second_id = recorded.id, fresh.id
            owner = first.uid
            ok &= check(
                "each session is issued its own id, unrelated to its row number",
                bool(first.uid) and first.uid != second.uid
                and first.uid != str(first.id),
                f"{first.uid!r} / {second.uid!r}",
            )

        scenario = Scenario(
            name="sign in",
            target_url=url,
            origin="map",
            steps=(
                Step(
                    intent="sign in",
                    action="button:Sign in",
                    from_key="aaaa000000000000",
                    fields=(),
                    expect=Expectation(
                        moved=True, mutating=True, added=(), removed=(),
                        to_key="bbbb000000000000",
                    ),
                ),
            ),
        )

        original = regression.SUITES
        regression.SUITES = pathlib.Path(tmp) / "suites"
        try:
            # Only the first session ever recorded anything.
            regression.emit(
                (scenario,),
                regression.directory_for(url, session_uid=owner),
                because="recorded by the probe",
                target_url=url,
                source="map",
                outcomes=("passed",),
            )
            ok &= check(
                "the session that recorded it can download it",
                client.get(f"/api/runs/{first_id}/suite").json()["version"] is not None,
                "a session cannot see the suite it kept itself",
            )
            ok &= check(
                "a new session on the same URL starts with nothing kept",
                client.get(f"/api/runs/{second_id}/suite").json()["version"] is None,
                "the console is serving another session's tests -- the "
                "reported bug",
            )
            ok &= check(
                "and its download is a 404 rather than someone else's zip",
                client.get(f"/api/runs/{second_id}/suite/download").status_code == 404,
                "a new session can download a suite it never compiled",
            )
            # The uid is the server's to hand out. Accepting one from the body
            # would let a caller pin two sessions to the same suite, which is
            # the single thing the column exists to prevent.
            posted = client.post(
                "/api/sessions", json={"target_url": url, "uid": owner}
            ).json()
            ok &= check(
                "a uid supplied by the caller is ignored, not honoured",
                posted.get("uid") not in (None, "", owner),
                f"created with uid {posted.get('uid')!r}",
            )
        finally:
            regression.SUITES = original
            app.dependency_overrides.clear()

    return ok


def _console_plans_from_behaviour() -> bool:
    """The console compiles its suite through the planner, not around it.

    `planner.DEFAULT_SOURCE` is `behaviour` and `pipeline.py` honours it, but
    this router used to call `generator.scenarios(world)` directly. The colony
    still synthesised a behavioural model, still examined it, still emitted
    `believes [...]` for every hypothesis -- and then compiled none of them.
    Every console spec carried `origin="map"`, which made the one comparison
    `Scenario.origin` exists for return 0 by construction from the entry point
    most people use.

    The failure was invisible in exactly the way that matters: nothing errored,
    the suite was the right size, and the only symptom was a badge
    (`SuitePane.tsx`) that never appeared. So the check is on the wiring rather
    than on the planner, which was always correct.

    `_compile` is a plain function over two in-memory objects, so this needs no
    browser, no key and no live app. The map fixture is `agents.probe`'s -- real
    `Observation`s behind every state, because `expectation()` reads their
    snapshots and a fixture without them would let a broken consumer pass.
    """
    import inspect

    from agents.behavior import BehaviorModel, Hypothesis
    from agents.probe import _behaviour_world

    from .routers import explore as explore_router
    from .routers.explore import _compile

    print("\nPLAN        the console compiles through planner.plan")
    ok = True

    # The checks below exercise `_compile`, which would keep passing if the
    # router went back to calling `generator.scenarios` around it. So the
    # regression is guarded where it actually lived: in the call sites.
    source = inspect.getsource(explore_router)
    ok &= check(
        "the router compiles through the planner, not around it",
        "scenarios(result.world" not in source,
        "a call site still bypasses `_compile`",
    )
    ok &= check(
        "and does not import the generator's compiler to do it",
        not hasattr(explore_router, "scenarios"),
        "`generator.scenarios` is back in the router's namespace",
    )

    world = _behaviour_world()
    believed = BehaviorModel(
        summary="a login form and what is behind it",
        hypotheses=(
            Hypothesis(
                claim="signing in reaches the dashboard",
                kind="flow",
                cites=("a" * 16, "b" * 16),
            ),
        ),
    )

    planned = _compile(world, believed)
    origins = [s.origin for s in planned.scenarios]

    # The check that fails without the fix. Before it, this router never handed
    # the behavioural model to anything, so no origin could ever say otherwise.
    ok &= check(
        "a flow the colony believed in reaches the console's suite",
        any(o.startswith("behaviour") for o in origins),
        f"origins were {origins}",
    )
    ok &= check(
        "and the plan says so, so the A/B is a count rather than an opinion",
        planned.from_behaviour >= 1 and planned.source == "behaviour",
        f"from_behaviour={planned.from_behaviour}, source={planned.source!r}",
    )
    ok &= check(
        "the believed flow is named for the claim, not for its last click",
        any(s.name == "signing in reaches the dashboard" for s in planned.scenarios),
        f"names were {[s.name for s in planned.scenarios]}",
    )

    # The wider set attribution runs against has to come from the same place.
    # A claim answered only by a believed flow would otherwise be unmatchable,
    # and would report as uncovered on a suite that covers it.
    widened = _compile(world, believed, limit=40)
    ok &= check(
        "the set claims are matched against carries the believed flows too",
        any(s.origin.startswith("behaviour") for s in widened.scenarios),
        f"origins were {[s.origin for s in widened.scenarios]}",
    )

    # `PLAN_FROM=map` is the A/B's other arm, and it has to reach the console
    # too or the measurement can only be taken from the CLI. Read at call time
    # by `source_from_env`, so setting it here is the same switch a run sees.
    import os

    was = os.environ.get("PLAN_FROM")
    os.environ["PLAN_FROM"] = "map"
    try:
        deterministic = _compile(world, believed)
    finally:
        if was is None:
            os.environ.pop("PLAN_FROM", None)
        else:
            os.environ["PLAN_FROM"] = was

    ok &= check(
        "PLAN_FROM=map removes the semantic layer from the console as well",
        deterministic.source == "map"
        and all(not s.origin.startswith("behaviour")
                for s in deterministic.scenarios),
        f"source={deterministic.source!r}, "
        f"origins={[s.origin for s in deterministic.scenarios]}",
    )

    # No provider is the demo machine's normal state, and it must still produce
    # a suite -- demoted to what actually happened, and saying which.
    bare = _compile(world, BehaviorModel())
    ok &= check(
        "with no behavioural model the plan is the map alone",
        bare.source == "map" and len(bare.scenarios) > 0,
        f"source={bare.source!r}, {len(bare.scenarios)} scenario(s)",
    )
    ok &= check(
        "and it says why it is smaller rather than reporting a fair comparison",
        bool(bare.degraded),
        f"degraded={bare.degraded!r}",
    )

    return ok


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
            "a state reports its input fields",
            states["aaaa000000000000"]["fields"]
            == [["textbox", "Email"], ["textbox", "Password"]],
        )
        ok &= check(
            "a state with no fields returns an empty list, not null",
            states["bbbb000000000000"]["fields"] == [],
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

        # The second input. It belongs to the session rather than the run: it
        # is what you know about the app, and that does not change between two
        # runs against it. Which also means it has to survive a re-run without
        # being retyped, and has to be editable when the password has a typo.
        created = client.post(
            "/api/sessions",
            json={
                "target_url": "http://localhost:3000/sut",
                "context": "log in as demo / hunter2",
            },
        ).json()
        ok &= check(
            "a session remembers the context typed beside its URL",
            created.get("context") == "log in as demo / hunter2",
            f"got {created.get('context')!r}",
        )
        ok &= check(
            "a URL on its own is still a whole session",
            client.post(
                "/api/sessions", json={"target_url": "http://localhost:3000/sut"}
            ).json()["context"]
            is None,
        )
        patched = client.patch(
            f"/api/sessions/{created['id']}",
            json={"context": "log in as demo / hunter3"},
        ).json()
        ok &= check(
            "a mistyped password can be fixed without a new session",
            patched["context"] == "log in as demo / hunter3",
            f"got {patched.get('context')!r}",
        )
        ok &= check(
            "the fix is what a reload sees",
            client.get(f"/api/sessions/{created['id']}").json()["context"]
            == "log in as demo / hunter3",
        )

        app.dependency_overrides.clear()

    # A database that predates the context column must gain it rather than be
    # deleted. Twenty minutes of crawled map is the thing `rm app.db` costs, and
    # it is exactly what a demo is standing on.
    with tempfile.TemporaryDirectory() as tmp:
        old_shape = create_engine(f"sqlite:///{tmp}/old.db")
        with old_shape.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE testsession (id INTEGER PRIMARY KEY, "
                "target_url VARCHAR, name VARCHAR, created_at DATETIME)"
            )
            conn.exec_driver_sql(
                "INSERT INTO testsession (target_url) VALUES ('http://old')"
            )
            conn.commit()

        add_missing_columns(old_shape)

        with old_shape.connect() as conn:
            columns = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(testsession)")
            }
            surviving = list(conn.exec_driver_sql("SELECT target_url FROM testsession"))
        ok &= check(
            "an existing session table gains the context column",
            "context" in columns,
            f"columns were {sorted(columns)}",
        )
        ok &= check(
            "and the sessions already in it are still there",
            surviving == [("http://old",)],
            f"got {surviving}",
        )

    ok &= _invariant_reporting()
    ok &= _status_policy()
    ok &= _suite_download()
    ok &= _suites_are_per_session()
    ok &= _console_plans_from_behaviour()

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
