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

import re
from pathlib import Path
from urllib.parse import quote

from sqlmodel import Session, select

from app.models import AppState, SkippedAction, StateObservation, StateTransition

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

    # --- refused actions, append-only ------------------------------------
    # Reconciled on (state, action) rather than appended blindly: `save` runs
    # after every edge, and a refusal stays on `world.skipped` for the rest of
    # the crawl, so an unconditional insert would write the same row once per
    # remaining checkpoint.
    refused = set(
        session.exec(
            select(SkippedAction.state_key, SkippedAction.action).where(
                SkippedAction.run_id == run_id
            )
        ).all()
    )

    for (state_key, action), reason in world.skipped.items():
        if (state_key, action) in refused:
            continue
        refused.add((state_key, action))
        session.add(
            SkippedAction(
                run_id=run_id,
                state_key=state_key,
                action=action,
                reason=reason,
            )
        )
        written += 1

    session.commit()
    return written


def admissible(
    rows: list[StateTransition], was: str, rung: str
) -> list[StateTransition]:
    """Which of this run's edges may carry the annotation `was -> rung`.

    `rows` is every edge in this run's map leaving the healed step's state
    under the action the Healer resolved *to*. It can hold none, one, or
    several -- `StateTransition` is deliberately not unique on
    `(run_id, from_key, action)`, because one action landing in two places is
    how a `normalize()` collision stays visible instead of being overwritten.

    Return the rows to write. An empty list is a refusal and is counted, not
    an error.

    The policy, case by case:

      * no rows          this run's crawl never walked the action the Healer
                         resolved onto. Nothing here to annotate.
      * one row          the ordinary case: write it.
      * several rows     the same descriptor landing in two states. The claim
                         is about the descriptor at this state -- "a step the
                         suite recorded under `was` resolved onto this action"
                         -- and every one of those edges is that action, so
                         every one carries it. Choosing a single row would
                         hide the very collision the duplicate exists to show.
      * row already annotated, from a *different* `was`  -- two saved steps
                         healed onto one edge and disagree about its old name.
                         Refused: the first claim stands and is not rewritten.
                         The same `was` again is the idempotent re-run and is
                         written as before.

    `action`, `to_key` and `observation_id` never move: this function only
    decides *where the second claim is written*, never edits the first one.
    """
    return [
        row
        for row in rows
        if row.healed_from is None or row.healed_from == was
    ]


def annotate_heals(
    updates: tuple[dict, ...], run_id: int, session: Session
) -> int:
    """Record, on this run's map, what a kept suite calls each healed edge.

    Returns rows annotated.

    **This is not `regression.apply_to_map`, and the difference is direction.**
    That function patches a map that still holds the *old* name -- one loaded
    back from the run the suite was recorded against. Within a single run the
    crawl and the replay visit the same URL minutes apart, so the map here
    already holds the *new* name and the old one survives only in the suite on
    disk. Measured 2026-09-05: applying a correction to a freshly crawled map
    changes nothing, because there is nothing there under the old name.

    So the match is on `now`, and what gets written is `healed_from`. The claim
    is "a test recorded against an earlier crawl was resolved onto this edge",
    which is the one a person reading this run's map can act on.

    `action` is never touched. It is what this run observed, and overwriting it
    would leave nothing to compare run N against run N+1 on -- the property
    `regression.apply_to_map` returns a copy to protect.
    """
    written = 0

    for update in updates:
        state, was = update.get("state"), update.get("was")
        now, rung = update.get("now"), update.get("rung") or ""
        if not state or not was or not now:
            continue

        rows = list(
            session.exec(
                select(StateTransition)
                .where(StateTransition.run_id == run_id)
                .where(StateTransition.from_key == state)
                .where(StateTransition.action == now)
            ).all()
        )

        for row in admissible(rows, was, rung):
            row.healed_from = was
            row.healed_rung = rung
            session.add(row)
            written += 1

    if written:
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

    # Refusals come back too, so `save` and `load` round-trip. Without this a
    # resumed crawl re-attempts every action it already established it cannot
    # take, and `summary()` silently loses the lines naming why.
    for row in session.exec(
        select(SkippedAction).where(SkippedAction.run_id == run_id)
    ).all():
        world.skipped[(row.state_key, row.action)] = row.reason

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


# --- scrubbing what was recorded before redaction existed ---------------------


def scrub(session: Session) -> dict[str, int]:
    """Redact credentials already persisted, in place. Returns rows changed.

    `observer.redact_*` stops new exposure; it cannot reach what is already on
    disk. Measured on this workspace 2026-09-05, before redaction landed: 108
    `StateObservation.snapshot` rows carrying a Password value, 48 rows with a
    non-empty `password=` in a URL across two tables, 39 with one in the
    recorded network traffic. Two of the URL values were real configured
    credentials rather than anything `synth.py` generates.

    **Rewrites rather than deletes.** `make reset` also removes the credentials,
    by removing the runs -- which throws away the evidence a map is *for*, and
    makes remediation and history loss the same button. This keeps every row and
    changes only the secret, so a scrubbed database still answers every question
    it could answer before except what the password was.

    **Raw SQL, not the ORM, and that is the point.** The databases that need
    scrubbing are the old ones, and an old database is precisely the one whose
    schema has drifted -- this workspace's own `app.db` predates
    `AppState.fields`, so `select(AppState)` raises `no such column` and the
    remediation cannot run on the data that needs it most. Reading only the
    columns it touches makes the tool independent of every column it does not.

    **Safe to run twice.** Redaction is idempotent -- a row already reading
    `[redacted]` re-renders identically -- so this can be re-run after any crawl
    that predates the fix without compounding.

    `state_key` is not recomputed and does not need to be: it hashes the
    normalised snapshot, and `field_value` reduces a field's input to presence,
    so a redacted value is still "filled" and still hashes to the row's existing
    key. Verified by `agents.probe`, "redacting does not change the state key".
    """
    from .observer import redact_snapshot, redact_url

    changed = {"snapshot": 0, "url": 0, "network": 0}
    connection = session.connection()

    def columns(table: str) -> set[str]:
        try:
            rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
        except Exception:
            return set()
        return {row[1] for row in rows}

    observation_columns = columns("stateobservation")
    if {"id", "snapshot"} <= observation_columns:
        wanted = [c for c in ("snapshot", "url", "network") if c in observation_columns]
        rows = connection.exec_driver_sql(
            f"SELECT id, {', '.join(wanted)} FROM stateobservation"
        ).all()
        for row in rows:
            row_id, values = row[0], dict(zip(wanted, row[1:]))
            updates = {}

            if values.get("snapshot"):
                cleaned = redact_snapshot(values["snapshot"])
                if cleaned != values["snapshot"]:
                    updates["snapshot"] = cleaned
            if values.get("url"):
                cleaned = redact_url(values["url"])
                if cleaned != values["url"]:
                    updates["url"] = cleaned
            if values.get("network"):
                # A JSON array of events; only `url` can carry a secret, so it
                # is rebuilt field by field rather than pattern-matched over the
                # serialisation.
                try:
                    events = json.loads(values["network"])
                except (TypeError, ValueError):
                    events = None
                if isinstance(events, list):
                    for event in events:
                        if isinstance(event, dict) and event.get("url"):
                            event["url"] = redact_url(event["url"])
                    rebuilt = json.dumps(events)
                    if rebuilt != values["network"]:
                        updates["network"] = rebuilt

            for column, value in updates.items():
                connection.exec_driver_sql(
                    f"UPDATE stateobservation SET {column} = ? WHERE id = ?",
                    (value, row_id),
                )
                changed[column] += 1

    if {"id", "url"} <= columns("appstate"):
        for row_id, url in connection.exec_driver_sql(
            "SELECT id, url FROM appstate"
        ).all():
            if not url:
                continue
            cleaned = redact_url(url)
            if cleaned != url:
                connection.exec_driver_sql(
                    "UPDATE appstate SET url = ? WHERE id = ?", (cleaned, row_id)
                )
                changed["url"] += 1

    session.commit()
    return changed


def scrub_artifacts(root: Path) -> dict[str, int]:
    """Redact credentials in the files beside the database. Returns files changed.

    `scrub` reaches `app.db`, and that is not all of it. An `Observation.url`
    also lands in `artifacts/runs/*.json` via `crawler.autosave`, and a model
    that read a URL off the page repeats it into `artifacts/transcripts/*.json`.
    Measured here after scrubbing the database and believing the job done: 17
    files still carried a non-empty `password=`, two distinct values, across
    `runs/` and `transcripts/`.

    **Text substitution, not URL parsing.** A transcript is prose with URLs
    embedded in it -- a tool result quoting a state, an ant's summary -- so
    there is no field to parse. What is stable is the query-string form itself,
    `password=<value>`, which is how the credential got into all of these in the
    first place.

    Files are rewritten only when they change, so re-running touches nothing and
    the mtimes stay meaningful.
    """
    from .observer import REDACTED, _SECRET_NAME, _configured_secrets

    # `name=value` inside a URL, where the name reads as a secret and the value
    # is neither empty nor already redacted. Stops at the delimiters a query
    # string can end on plus the ones JSON and prose add around it.
    pattern = re.compile(r'(\b[A-Za-z_][\w.\-]*)=([^&"\'\\\s]+)')

    def hide(match: re.Match) -> str:
        name, value = match.group(1), match.group(2)
        if not _SECRET_NAME.search(name) or value in ("", quote(REDACTED)):
            return match.group(0)
        return f"{name}={quote(REDACTED)}"

    changed = {"files": 0}
    if not root.exists():
        return changed

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".json", ".txt", ".md"}:
            continue
        try:
            before = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        after = pattern.sub(hide, before)
        for secret in _configured_secrets():
            after = after.replace(secret, REDACTED).replace(quote(secret), REDACTED)

        if after != before:
            path.write_text(after)
            changed["files"] += 1

    return changed


def main() -> int:
    """Scrub this checkout's database. Prints what it changed and nothing else.

        cd app/api && uv run python -m agents.explorer.store
    """
    from sqlmodel import Session as _Session

    from app.config import settings
    from app.db import engine

    # Deliberately no `init_db()`: the database this is pointed at is usually an
    # old one, and creating missing tables before scrubbing would be a schema
    # change smuggled into a remediation.
    with _Session(engine) as session:
        changed = scrub(session)

    print(f"SCRUBBED    {settings.database_url}")
    for field, count in changed.items():
        print(f"  {field:<9} {count} row(s) redacted")

    # The database was never the whole of it. `autosave` writes the same url
    # into artifacts/runs, and a model that read one off the page repeats it
    # into a transcript -- 17 files here still carried a credential after the
    # database came back clean.
    files = scrub_artifacts(settings.artifacts_dir)
    print(f"            {settings.artifacts_dir}")
    print(f"  files     {files['files']} file(s) redacted")

    if not any(changed.values()) and not files["files"]:
        print("  nothing to do -- no credential survived here")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
