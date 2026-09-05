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

        app.dependency_overrides.clear()

    ok &= _invariant_reporting()
    ok &= _status_policy()

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
