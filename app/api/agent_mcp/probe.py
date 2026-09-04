"""Observable checks for the MCP layer. Not a test suite -- evidence.

    cd app/api && uv run python -m agent_mcp.probe

**Needs the API running** (`make dev`), because the thing under test is that an
MCP call lands in the console. A check that stubbed the HTTP client would pass
while the sidebar stayed empty, which is the one failure this layer has.

No model is called and no browser is launched by the checks that run by default.
`--verify` adds the Playwright leg against the SUT, which is slow and needs a
map to already exist.

Each check is a mistake that is easy to make here:

  session_reuse   two `explore` calls at one URL must share a session, or the
                  before/after comparison the healer story needs is split
                  across two sidebar rows that never sit next to each other.
  visible         a run created by MCP must appear in `list_sessions`' rollup,
                  which computes `run_count` by querying -- so a row written
                  around the API would be invisible even though it exists.
  timeline        events posted by MCP must come back on the *session* feed,
                  not just the run feed: that is what `Canvas.tsx` polls.
  tools           every tool the server advertises must have a description, or
                  a client has nothing to route on and will never call it.
"""

from __future__ import annotations

import asyncio
import sys

from . import read, server as server_module
from .client import Console, ConsoleDown

SUT = "http://localhost:3000/sut"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def main() -> int:
    print("MCP         real API, no model, no browser\n")
    ok = True
    console = Console()

    try:
        health = console.health()
    except ConsoleDown as exc:
        print(f"  SKIP  every check -- {exc}")
        return 1

    print(f"1. console reachable ({health.get('worktree')} @ {console.base_url})")
    ok &= check("health answers", health.get("status") == "ok")

    print("\n2. sessions and runs are created through the API")
    url = f"{SUT}?probe=mcp"
    first = console.session_for(url, name="mcp probe")
    second = console.session_for(url)
    ok &= check(
        "session_reuse: one URL, one session",
        first["id"] == second["id"],
        f"got {first['id']} then {second['id']} -- the sidebar would show two rows",
    )

    run = console.create_run(first["id"], url)
    summaries = {row["id"]: row for row in console.sessions()}
    ok &= check(
        "visible: the run reaches the sidebar rollup",
        summaries.get(first["id"], {}).get("run_count", 0) >= 1,
        "list_sessions computes run_count by query -- a direct DB write misses it",
    )

    print("\n3. the timeline reaches the canvas feed")
    console.emit(run["id"], "info", "probe: hello from MCP", surface="timeline")
    events = console.session_events(first["id"])
    mine = [e for e in events if e["message"].startswith("probe: hello")]
    ok &= check("timeline: event on the session feed", bool(mine))
    ok &= check(
        "timeline: surface preserved",
        bool(mine) and mine[-1].get("surface") == "timeline",
        "Canvas.tsx opens a widget only for events carrying a surface",
    )

    print("\n4. the tool surface is routable")
    tools = asyncio.run(server_module.server.list_tools())
    names = {tool.name for tool in tools}
    ok &= check(
        "tools: all six registered",
        names == {"sessions", "explore", "crawl", "map", "impact", "verify"},
        f"got {sorted(names)}",
    )
    ok &= check(
        "tools: every tool describes itself",
        all((tool.description or "").strip() for tool in tools),
    )

    print("\n5. impact matches names the way a diff supplies them")
    ok &= check(
        "matches: exact",
        read._matches("Place Order", ["Place Order"]),
    )
    ok &= check(
        "matches: the map is more specific than the diff",
        read._matches("Email address", ["Email"]),
    )
    ok &= check(
        "matches: the diff is more specific than the map",
        read._matches("Sign in", ["Sign in button"]),
    )
    ok &= check(
        "matches: unrelated names do not match",
        not read._matches("Place Order", ["Newsletter"]),
    )
    ok &= check(
        "matches: an empty needle matches nothing",
        not read._matches("Place Order", ["", "   "]),
        "a diff with no renamed strings must return no flows, not every flow",
    )

    print("\n5b. impact separates acting on a control from merely seeing it")
    latest = read.latest_run(console)
    if latest is None:
        print("  SKIP  no mapped run -- `make mcp-probe` after a crawl to cover this")
    else:
        imp = read.impact_of(latest["id"], ["Sign in"])
        total = len(imp["affected"]) + len(imp["observing"]) + len(
            imp["unaffected_flows"]
        )
        ok &= check(
            "tiers: acting flows are a strict subset of matching ones",
            len(imp["affected"]) < total,
            "renaming one button once matched 8 of 8 flows -- an answer that "
            "ranks nothing ranks everything",
        )
        ok &= check(
            "tiers: every affected flow has an `acts` reason",
            all(hit["acts"] for hit in imp["affected"]),
        )
        ok &= check(
            "tiers: no observing flow acts",
            all(not hit["acts"] for hit in imp["observing"]),
        )

    print("\n6. cleanup")
    console._call("DELETE", f"/api/sessions/{first['id']}")
    ok &= check(
        "the probe leaves no session behind",
        first["id"] not in {row["id"] for row in console.sessions()},
    )

    console.close()
    print("\n" + ("STABLE" if ok else "BROKEN"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
