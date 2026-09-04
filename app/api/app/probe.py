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
