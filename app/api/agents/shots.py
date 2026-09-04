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
