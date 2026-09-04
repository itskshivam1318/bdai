"""Write a WorldMap to a file, read it back, and compare two of them.

    cd app/api && uv run python -m agents.explorer.snapshot <before.json> <after.json>

Two jobs that turn out to be one.

**Saving.** A map that exists only in a process is a map you lose. Two real
explorations of practicesoftwaretesting.com were reduced to their printed
summaries because nothing wrote the object down, and a printed summary cannot be
diffed -- only read. Every run now leaves a file.

**Comparing.** `store.py` already keeps maps per *run* rather than per session,
specifically so that re-crawling produces a second map beside the first. This is
the other half of that: the function that says what changed.

What a comparison is used for, in order of how soon it matters:

    1. Did a change to the explorer help?  Two runs, same app, different code.
       States should be identical and the frontier smaller. Anything else is a
       regression hiding as an improvement.
    2. Did the application change?  Two runs, same code, different builds. This
       is the drift the Healer exists to absorb, and the reason `Transition`
       carries evidence.
    3. Is the crawl deterministic?  Two runs, same everything. Any difference is
       ours -- a flapping state key, a race, a projection that is not a function.

The one thing this deliberately does **not** do is decide that two *differently
keyed* states are "really the same". That is behavioural equivalence across
builds -- the `align()` problem -- and it needs a similarity judgement rather
than a set difference. Keeping them separate means a comparison never quietly
asserts an equivalence it cannot prove.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .observer import Observation
from .worldmap import StateNode, Transition, WorldMap


def save(world: WorldMap, path: str | Path, **meta) -> Path:
    """Write a map plus whatever context makes it comparable later.

    `meta` is deliberately open: the useful fields differ per run (target url,
    model, budget, git revision) and a fixed schema would be wrong within a day.
    What matters is that *something* records which code and which app produced
    this, because a diff between two maps with no provenance is unreadable.

    Evidence is stored as url/title/key only, not raw snapshots. A snapshot is
    ~30KB and there are dozens per run; the diff never reads them, and
    `store.py` keeps the full ones in the database when a run is persisted.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "meta": {k: str(v) for k, v in meta.items()},
                "entry_key": world.entry_key,
                "states": [
                    {
                        "key": node.key,
                        "url": node.url,
                        "title": node.title,
                        "actions": list(node.actions),
                        "label": node.label,
                        "screenshot": node.screenshot,
                    }
                    for node in world.states.values()
                ],
                "transitions": [
                    {
                        "from": t.from_key,
                        "action": t.action,
                        "to": t.to_key,
                        "mutating": t.mutating,
                    }
                    for taken in world.transitions.values()
                    for t in taken
                ],
                "observations": [
                    {"url": o.url, "title": o.title} for o in world.evidence
                ],
            },
            indent=2,
        )
    )
    return path


def load(path: str | Path) -> WorldMap:
    """Rebuild a map from a file. Evidence comes back as stubs.

    The stubs keep `WorldMap.evidence` indices valid so counts and references
    still line up; they carry no snapshot, so `explain()` cannot run against a
    loaded map. That is the honest trade for a file small enough to diff.
    """
    data = json.loads(Path(path).read_text())
    world = WorldMap(entry_key=data.get("entry_key"))

    world.evidence = [
        Observation(url=o["url"], title=o["title"], snapshot="")
        for o in data.get("observations", [])
    ]
    for s in data["states"]:
        world.states[s["key"]] = StateNode(
            key=s["key"],
            url=s["url"],
            title=s["title"],
            actions=tuple(s["actions"]),
            label=s.get("label"),
            screenshot=s.get("screenshot"),
        )
    for t in data["transitions"]:
        world.transitions.setdefault((t["from"], t["action"]), []).append(
            Transition(
                from_key=t["from"],
                action=t["action"],
                to_key=t["to"],
                mutating=t.get("mutating", False),
                evidence=-1,
            )
        )
    return world


@dataclass
class Diff:
    """What changed between two maps. Every field is a set difference."""

    states_added: tuple[str, ...] = ()
    states_removed: tuple[str, ...] = ()
    states_shared: tuple[str, ...] = ()
    actions_added: dict[str, tuple[str, ...]] = field(default_factory=dict)
    actions_removed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    edges_added: tuple[tuple[str, str, str], ...] = ()
    edges_removed: tuple[tuple[str, str, str], ...] = ()
    frontier_before: int = 0
    frontier_after: int = 0
    titles: dict[str, str] = field(default_factory=dict)

    @property
    def identical(self) -> bool:
        return not (
            self.states_added
            or self.states_removed
            or self.actions_added
            or self.actions_removed
            or self.edges_added
            or self.edges_removed
        )

    def render(self) -> str:
        def name(key: str) -> str:
            return f"[{key[:8]}] {self.titles.get(key, '')[:34]}"

        if self.identical:
            return "IDENTICAL   the two maps agree on every state, action and edge"

        lines = [
            f"STATES      {len(self.states_shared)} shared, "
            f"{len(self.states_added)} added, {len(self.states_removed)} removed",
            f"FRONTIER    {self.frontier_before} -> {self.frontier_after} "
            f"({self.frontier_after - self.frontier_before:+d})",
            "",
        ]

        for key in self.states_added:
            lines.append(f"  + state   {name(key)}")
        for key in self.states_removed:
            lines.append(f"  - state   {name(key)}")

        # Action changes on a *shared* state are the interesting case: the app
        # is the same and the state is the same, so the difference is ours.
        for key, actions in self.actions_removed.items():
            lines.append(f"\n  {name(key)}")
            lines += [f"      - {a}" for a in actions[:12]]
            if len(actions) > 12:
                lines.append(f"      ... {len(actions) - 12} more removed")
        for key, actions in self.actions_added.items():
            if key not in self.actions_removed:
                lines.append(f"\n  {name(key)}")
            lines += [f"      + {a}" for a in actions[:12]]

        if self.edges_added or self.edges_removed:
            lines.append("")
            for f, a, t in self.edges_removed[:10]:
                lines.append(f"  - edge    {f[:8]} --{a}--> {t[:8]}")
            for f, a, t in self.edges_added[:10]:
                lines.append(f"  + edge    {f[:8]} --{a}--> {t[:8]}")

        return "\n".join(lines)


def compare(before: WorldMap, after: WorldMap) -> Diff:
    """What changed from `before` to `after`. States match by key.

    Matching on the key rather than the URL is the whole point: identity is what
    `normalize()` kept, so two runs of the same code against the same app must
    produce the same keys. When they do not, either the app moved or the
    projection is not a function -- and both are worth knowing about loudly.
    """
    keys_before, keys_after = set(before.states), set(after.states)

    actions_added: dict[str, tuple[str, ...]] = {}
    actions_removed: dict[str, tuple[str, ...]] = {}
    for key in keys_before & keys_after:
        was = set(before.states[key].actions)
        now = set(after.states[key].actions)
        if now - was:
            actions_added[key] = tuple(sorted(now - was))
        if was - now:
            actions_removed[key] = tuple(sorted(was - now))

    def edges(world: WorldMap) -> set[tuple[str, str, str]]:
        return {
            (t.from_key, t.action, t.to_key)
            for taken in world.transitions.values()
            for t in taken
        }

    edges_before, edges_after = edges(before), edges(after)

    return Diff(
        states_added=tuple(sorted(keys_after - keys_before)),
        states_removed=tuple(sorted(keys_before - keys_after)),
        states_shared=tuple(sorted(keys_before & keys_after)),
        actions_added=actions_added,
        actions_removed=actions_removed,
        edges_added=tuple(sorted(edges_after - edges_before)),
        edges_removed=tuple(sorted(edges_before - edges_after)),
        frontier_before=len(before.frontier()),
        frontier_after=len(after.frontier()),
        titles={
            **{k: v.title for k, v in before.states.items()},
            **{k: v.title for k, v in after.states.items()},
        },
    )


def main(before_path: str, after_path: str) -> int:
    before, after = load(before_path), load(after_path)
    print(f"BEFORE      {before_path}")
    print(f"            {len(before.states)} states, "
          f"{sum(len(t) for t in before.transitions.values())} transitions")
    print(f"AFTER       {after_path}")
    print(f"            {len(after.states)} states, "
          f"{sum(len(t) for t in after.transitions.values())} transitions")
    print()
    print(compare(before, after).render())
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[2].strip())
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
