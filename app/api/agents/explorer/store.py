"""Persist a WorldMap, and read one back.

The one place the explorer meets the database. Everything else in this package
is pure -- `WorldMap` holds dicts, `frontier()` is a comprehension over them,
and the crawl loop calls it every iteration. Putting SQLModel inside those would
make the hot path a query and couple the model of the application to the
schema it happens to be stored in today.

So the boundary lives here, and it goes both ways:

    save(world, run_id, session)   -> the UI can draw it, and it survives the process
    load(run_id, session)          -> a later stage reads it back

**`save` is incremental and idempotent.** It is written to be called *during* a
crawl, not only at the end, because a map that appears only when the crawl
finishes cannot be watched. Calling it every few actions streams the graph into
the UI, and calling it twice with no new observations writes nothing.

That same property is what makes a worker pool possible later: several
processes writing into one run's rows, each reading the frontier from the
database rather than from its own memory. Nothing here needs to change for
that -- what it needs is a claim on an edge so two workers do not take the same
one, and that is a column, not a rewrite.
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models import AppState, StateObservation, StateTransition

from .observer import Element, NetworkEvent, Observation
from .worldmap import StateNode, Transition, WorldMap


def save(world: WorldMap, run_id: int, session: Session) -> int:
    """Write everything new in `world` to this run. Returns rows written.

    Observations are the anchor: they are append-only and ordered, so the count
    already in the database is exactly how many of `world.evidence` have been
    written. Everything else is reconciled against what is already there.
    """
    written = 0

    # --- evidence, append-only ------------------------------------------
    # Only the ids. Selecting whole rows here would re-read every stored
    # snapshot on every checkpoint, and `save` is called after each edge.
    observation_ids = list(
        session.exec(
            select(StateObservation.id)
            .where(StateObservation.run_id == run_id)
            .order_by(StateObservation.id)
        ).all()
    )

    for observation in world.evidence[len(observation_ids) :]:
        row = StateObservation(
            run_id=run_id,
            state_key=_key_of(world, observation),
            url=observation.url,
            title=observation.title,
            snapshot=observation.snapshot,
            network=json.dumps(
                [
                    {
                        "method": event.method,
                        "url": event.url,
                        "status": event.status,
                        "resource_type": event.resource_type,
                    }
                    for event in observation.network
                ]
            ),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        observation_ids.append(row.id)
        written += 1

    # --- states, upserted ------------------------------------------------
    existing_states = {
        row.key: row
        for row in session.exec(
            select(AppState).where(AppState.run_id == run_id)
        ).all()
    }

    for key, node in world.states.items():
        actions = json.dumps(list(node.actions))
        row = existing_states.get(key)
        if row is None:
            session.add(
                AppState(
                    run_id=run_id,
                    key=key,
                    url=node.url,
                    title=node.title,
                    actions=actions,
                    fields=_fields_of(world, node),
                    label=node.label,
                    screenshot=node.screenshot,
                    found_by=node.found_by,
                    is_entry=(key == world.entry_key),
                )
            )
            written += 1
        elif (
            row.actions != actions
            or row.label != node.label
            or row.screenshot != node.screenshot
            or row.found_by != node.found_by
        ):
            # A state's action set can grow as later visits reveal controls,
            # `label` arrives from a model seam long after the crawl, and the
            # screenshot is taken on first sighting -- which may be after this
            # row was first written by an earlier checkpoint.
            row.actions, row.label = actions, node.label
            row.screenshot = node.screenshot
            row.found_by = node.found_by
            session.add(row)
            written += 1

    # --- transitions, append-only ---------------------------------------
    seen = set(
        session.exec(
            select(
                StateTransition.from_key,
                StateTransition.action,
                StateTransition.to_key,
            ).where(StateTransition.run_id == run_id)
        ).all()
    )

    for edges in world.transitions.values():
        for edge in edges:
            signature = (edge.from_key, edge.action, edge.to_key)
            if signature in seen:
                continue
            seen.add(signature)
            session.add(
                StateTransition(
                    run_id=run_id,
                    from_key=edge.from_key,
                    action=edge.action,
                    to_key=edge.to_key,
                    mutating=edge.mutating,
                    found_by=edge.found_by,
                    observation_id=(
                        observation_ids[edge.evidence]
                        if edge.evidence < len(observation_ids)
                        else None
                    ),
                )
            )
            written += 1

    session.commit()
    return written


def load(run_id: int, session: Session) -> WorldMap:
    """Rebuild the WorldMap this run produced.

    `actions_of` is left None: it needs a live page (see `forms.form_of`), and a
    map being read back is being read for its content, not crawled further. A
    caller that wants to resume a crawl sets it before handing this to `crawl`.
    """
    observations = sorted(
        session.exec(
            select(StateObservation).where(StateObservation.run_id == run_id)
        ).all(),
        key=lambda row: row.id or 0,
    )

    world = WorldMap()
    world.evidence = [
        Observation(
            url=row.url,
            title=row.title,
            snapshot=row.snapshot,
            # Elements are re-derived rather than stored: they are a pure
            # function of the snapshot, and storing them twice invites the two
            # copies to disagree.
            elements=_parse(row.snapshot),
            network=tuple(
                NetworkEvent(
                    method=event["method"],
                    url=event["url"],
                    resource_type=event.get("resource_type", ""),
                    status=event.get("status"),
                )
                for event in json.loads(row.network or "[]")
            ),
        )
        for row in observations
    ]

    by_id = {row.id: index for index, row in enumerate(observations)}
    evidence_of: dict[str, list[int]] = {}
    for index, row in enumerate(observations):
        evidence_of.setdefault(row.state_key, []).append(index)

    for row in session.exec(select(AppState).where(AppState.run_id == run_id)).all():
        world.states[row.key] = StateNode(
            key=row.key,
            url=row.url,
            title=row.title,
            actions=tuple(json.loads(row.actions or "[]")),
            label=row.label,
            screenshot=row.screenshot,
            found_by=row.found_by,
            evidence=tuple(evidence_of.get(row.key, ())),
        )
        if row.is_entry:
            world.entry_key = row.key

    for row in session.exec(
        select(StateTransition).where(StateTransition.run_id == run_id)
    ).all():
        world.transitions.setdefault((row.from_key, row.action), []).append(
            Transition(
                from_key=row.from_key,
                action=row.action,
                to_key=row.to_key,
                mutating=row.mutating,
                found_by=row.found_by,
                evidence=by_id.get(row.observation_id, 0),
            )
        )

    return world


def _fields_of(world: WorldMap, node: StateNode) -> str:
    """The fillable fields of a state, from the first time we saw it.

    First sighting rather than latest: `record()` already takes `url`, `title`
    and `actions` from the first observation, and a card whose fields came from
    a different visit than its screenshot would be describing two screens.
    """
    from .forms import fields_of

    if not node.evidence:
        return "[]"
    index = node.evidence[0]
    if index >= len(world.evidence):
        return "[]"
    return json.dumps([list(pair) for pair in fields_of(world.evidence[index])])


def _key_of(world: WorldMap, observation: Observation) -> str:
    """The state an observation shows. Recomputed rather than tracked.

    `state_key` is cheap and pure, and asking it again is safer than threading
    a key alongside every observation and hoping the two never drift apart.
    """
    from .statekey import state_key

    return state_key(observation.snapshot)


def _parse(snapshot: str) -> tuple[Element, ...]:
    from .observer import parse_snapshot

    return parse_snapshot(snapshot)
