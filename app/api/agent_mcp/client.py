"""HTTP client for the console API. Every mutation an MCP tool makes goes here.

The alternative -- opening the SQLite file and inserting rows -- is faster and
wrong: `list_sessions` computes `run_count` and `last_status` by querying runs,
and `Canvas.tsx` polls `sessions/{id}/events` for anything carrying a `surface`.
Both are behaviours of the API, not of the schema. Driving the API means an
MCP-originated session *is* a console session, with no third thing to maintain.

Requires `make dev` (or `make api`). A dead API is reported as such rather than
silently degrading to a local write that the UI would never show.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings


class ConsoleDown(RuntimeError):
    """The API is not answering. Actionable, unlike a bare ConnectError."""


class Console:
    """Thin, synchronous, and deliberately not an abstraction layer.

    Methods map one-to-one onto endpoints so that a reader can check a call
    against `routers/` without holding a translation in their head.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or f"http://127.0.0.1:{settings.api_port}").rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    # -- plumbing ---------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise ConsoleDown(
                f"no API at {self.base_url} -- start it with `make dev`"
            ) from exc
        if response.status_code >= 400:
            raise ConsoleDown(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return None if response.status_code == 204 else response.json()

    def health(self) -> dict:
        """Also names the worktree, so a wrong-stack connection is visible."""
        return self._call("GET", "/health")

    # -- sessions ---------------------------------------------------------

    def sessions(self) -> list[dict]:
        return self._call("GET", "/api/sessions")

    def session_for(self, target_url: str, name: str | None = None) -> dict:
        """Find the session for this URL, or create one.

        Find-or-create rather than always-create: `TestSession` is documented as
        outliving any single run, and an agent that calls `explore` then
        `verify` from the same repo means one application, not two. Always
        creating would fill the sidebar with duplicates of the same target and
        break the side-by-side before/after comparison the healer story needs.
        """
        for row in self.sessions():
            if row.get("target_url") == target_url:
                return row
        return self._call(
            "POST",
            "/api/sessions",
            json={"target_url": target_url, "name": name},
        )

    def session_events(self, session_id: int, after: int = 0) -> list[dict]:
        return self._call(
            "GET", f"/api/sessions/{session_id}/events", params={"after": after}
        )

    # -- runs -------------------------------------------------------------

    def create_run(self, session_id: int, target_url: str) -> dict:
        return self._call(
            "POST",
            "/api/runs",
            json={"session_id": session_id, "target_url": target_url},
        )

    def get_run(self, run_id: int) -> dict:
        return self._call("GET", f"/api/runs/{run_id}")

    def patch_run(self, run_id: int, **fields: Any) -> dict:
        return self._call("PATCH", f"/api/runs/{run_id}", json=fields)

    def events(self, run_id: int) -> list[dict]:
        return self._call("GET", f"/api/runs/{run_id}/events")

    def emit(
        self,
        run_id: int,
        level: str,
        message: str,
        surface: str | None = None,
        ref: str | None = None,
    ) -> dict:
        """One timeline line. Truncated to match what `explore.py` stores."""
        return self._call(
            "POST",
            f"/api/runs/{run_id}/events",
            json={
                "level": level,
                "message": message[:2000],
                "surface": surface,
                "ref": ref,
            },
        )

    def add_test(self, run_id: int, **fields: Any) -> dict:
        return self._call("POST", f"/api/runs/{run_id}/tests", json=fields)

    def tests(self, run_id: int) -> list[dict]:
        return self._call("GET", f"/api/runs/{run_id}/tests")

    # -- the pipeline -----------------------------------------------------

    def start_explore(self, run_id: int, **body: Any) -> dict:
        return self._call("POST", f"/api/runs/{run_id}/explore", json=body)

    def wait(self, run_id: int, timeout_s: float = 300.0, poll_s: float = 2.0) -> dict:
        """Block until the run leaves `running`, or the budget expires.

        Returns the run either way. A timeout is not an error here: exploration
        is genuinely open-ended, the run keeps going in the API's background
        task, and the caller can look at the console or poll again. Raising
        would throw away a partial map that is still being written.
        """
        deadline = time.monotonic() + timeout_s
        run = self.get_run(run_id)
        while run.get("status") == "running" and time.monotonic() < deadline:
            time.sleep(poll_s)
            run = self.get_run(run_id)
        return run

    def console_url(self, session_id: int) -> str:
        return f"{settings.web_origin}/?session={session_id}"

    def close(self) -> None:
        self._http.close()

    def runs(self, session_id: int | None = None) -> list[dict]:
        """Newest first, per `routers/runs.py:list_runs`."""
        params = {"session_id": session_id} if session_id is not None else None
        return self._call("GET", "/api/runs", params=params)
