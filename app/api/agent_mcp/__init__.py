"""MCP server: the console's pipeline, exposed to an external coding agent.

    make mcp        # stdio server, for Claude Code / Cursor / any MCP client

**Why this exists.** `docs/problem/statement.md` complains that a human supplies
the same application context over and over. The console answers that for a human
watching a canvas. This package answers it for the other party that has the same
problem and cannot open a browser: the coding agent that just changed the app.

Claude Code holds the diff and knows nothing about behaviour. The world map
knows behaviour and nothing about files. Neither half can do impact analysis
alone; together they can, and the join is an accessible name -- which is what a
diff contains and what `statekey.normalize()` keys on.

**Why every write goes over HTTP.** `client.py` drives the same endpoints the
browser console drives, rather than writing rows itself. The sidebar's rollup
(`routers/sessions.py:list_sessions`) and the canvas's event tail
(`sessions/{id}/events`) are then fed from one place, so an MCP-started run is
indistinguishable from a browser-started one and there is no second code path to
keep in sync. It costs a request per event and buys the requirement outright.

**Why it owns no existing file.** The console track (`work/map`) has claimed
`routers/worldmap.py`, `routers/explore.py`, `agents/suite.py` and `app/models.py`.
Everything here is new, additive, and reads through interfaces those files
already publish -- so the two tracks merge without a conflict.
"""
