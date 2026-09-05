"""What this server can be pointed at, and what it already has a key for.

    GET /api/providers

Serves `agents/llm/catalog.py` verbatim, plus one computed field per provider:
`configured`, meaning the server's own `.env` already holds that key. The
console shows it as "server key set", which turns the Advanced panel from a
form you must fill into one you may -- and answers the question a demo machine
actually asks, which is *"do I need to paste anything?"*

**No key is ever returned**, only whether one is present. `key_env` is the
variable's *name*, which is documentation.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

from agents.llm.catalog import as_json

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
def list_providers() -> dict:
    specs = as_json()
    for spec in specs:
        spec["configured"] = bool(os.environ.get(spec["key_env"]))
    return {"providers": specs}
