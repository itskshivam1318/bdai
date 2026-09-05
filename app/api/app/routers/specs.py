"""The suite as files: what a run wrote, and how to take it away.

The console could always *show* a verdict. What it could not do is hand over the
thing the verdict was about -- `agents/regression.py` writes every scenario to
`artifacts/suites/<target>/vNNN/` as both a `.json` (what a re-run loads) and a
`.spec.ts` (what a human reads and what CI runs with none of this installed),
and until this router existed the second of those was reachable only by opening
the filesystem on the machine that ran the agent.

**A suite belongs to a target, not to a run.** That is the whole point of
keeping one: two runs against the same URL are the before and after, and they
share the directory precisely so the second can replay what the first recorded.
So the run in the path is how the caller *names* a target -- `Run.target_url` --
and `TestCase.suite_version` is how a run says which version it was actually
looking at, which is not always the newest one on disk.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from agents import regression

from ..db import get_session
from ..models import Run, TestCase

router = APIRouter(prefix="/api/runs", tags=["specs"])


class SpecOut(BaseModel):
    """One test file, with enough around it to be worth listing."""

    file: str
    name: str
    #: The state this scenario is *about* -- the map node the console colours.
    node: str | None = None
    #: `map` or `behaviour...` -- which planner proposed it.
    origin: str = ""
    #: How many map states it crosses.
    covers: int = 0
    #: The verdict the run recorded for it, when one exists.
    status: str | None = None
    #: The runnable Playwright source.
    code: str = ""


class VersionOut(BaseModel):
    label: str
    number: int
    parent: str | None = None
    because: str = ""
    source: str = ""
    saved_at: str = ""
    scenarios: int = 0
    #: How many scenarios in this version were repaired into it from its parent.
    heals: int = 0
    #: What replaying the repaired scenarios did before this version was
    #: declared. Empty on a baseline, and on a version emitted with
    #: re-verification turned off -- which are different things, so the panel
    #: says "re-verified" only when this is non-empty.
    reverified: dict[str, int] = {}
    #: Steps recovered by exploring the region that lost the control, rather
    #: than by the resolution ladder. See `agents/rescue.py`.
    rescues: int = 0


class SuiteOut(BaseModel):
    """Everything the Tests panel needs in one round trip."""

    target_url: str
    #: Absolute path on the machine that ran the agent. Shown, not fetched.
    directory: str
    #: Null while a run is still compiling, or when nothing was kept.
    version: VersionOut | None = None
    #: Oldest first. The lineage, so the panel can say "v002 <- v001".
    versions: list[VersionOut] = []
    specs: list[SpecOut] = []


def _version_out(version: regression.Version) -> VersionOut:
    return VersionOut(
        label=version.label,
        number=version.number,
        parent=f"v{version.parent:03d}" if version.parent else None,
        because=version.because,
        source=version.source,
        saved_at=version.saved_at,
        scenarios=len(version.scenarios),
        heals=len(version.heals),
        reverified=dict(version.reverified or {}),
        rescues=len(version.rescues),
    )


def _run_or_404(run_id: int, db: Session) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


def _pick(run_id: int, run: Run, db: Session) -> tuple[Path, tuple[regression.Version, ...], regression.Version | None]:
    """The directory for this run's target, its lineage, and the run's version.

    The run's own version wins over the newest on disk. A console left open on
    run 3 while run 4 heals the suite underneath it would otherwise start
    showing run 4's files against run 3's verdicts, which is a lie about which
    tests produced which result.
    """
    # Scoped to the run's session, and that is what the writer does too: two
    # sessions on one URL keep two suites, so serving the target's suite would
    # hand this run somebody else's tests.
    directory = regression.directory_for(run.target_url, session_id=run.session_id)
    known = regression.versions(directory)
    labelled = db.exec(
        select(TestCase.suite_version)
        .where(TestCase.run_id == run_id)
        .where(TestCase.suite_version.is_not(None))  # type: ignore[union-attr]
    ).first()
    if labelled:
        mine = next((v for v in known if v.label == labelled), None)
        if mine is not None:
            return directory, known, mine
    return directory, known, (known[-1] if known else None)


def _statuses(run_id: int, db: Session) -> dict[str, str]:
    """Scenario name -> the worst verdict this run gave it.

    Keyed by name because that is the only field a `version.json` entry and a
    `TestCase` row share. Names are not unique within a suite -- one saucedemo
    crawl produced two "complete the Submit form and submit it" -- so the worse
    verdict wins rather than the last one read, for the same reason
    `suite.verdicts_by_state` reduces that way: the direction of error that
    matters is a passing row painting over a failing one.
    """
    order = {"escalate": 0, "defect": 1, "healed": 2, "passed": 3}
    worst: dict[str, str] = {}
    for row in db.exec(select(TestCase).where(TestCase.run_id == run_id)).all():
        held = worst.get(row.name)
        if held is None or order.get(row.status, 99) < order.get(held, 99):
            worst[row.name] = row.status
    return worst


def _read(version: regression.Version, stem: str) -> str:
    path = version.root / f"{stem}.spec.ts"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@router.get("/{run_id}/suite", response_model=SuiteOut)
def get_suite(run_id: int, db: Session = Depends(get_session)) -> SuiteOut:
    """The kept suite for this run's target, with every spec's source inline.

    Inline rather than one request per file: a suite is eight scenarios of
    forty lines, and the panel wants to render the list and the source it
    expands to from the same poll it is already making.
    """
    run = _run_or_404(run_id, db)
    directory, known, version = _pick(run_id, run, db)

    out = SuiteOut(
        target_url=run.target_url,
        directory=str(directory),
        versions=[_version_out(v) for v in known],
    )
    if version is None:
        return out

    out.version = _version_out(version)
    verdicts = _statuses(run_id, db)
    for entry in version.scenarios:
        stem = str(entry.get("file", ""))
        name = str(entry.get("name", stem))
        out.specs.append(
            SpecOut(
                file=stem,
                name=name,
                node=entry.get("node") or None,
                origin=str(entry.get("origin", "")),
                covers=len(entry.get("covers", ()) or ()),
                # The version's own recorded outcome is the fallback: a suite
                # downloaded from an old run still knows what it did when it
                # was written, even after the rows were cleared.
                status=verdicts.get(name) or entry.get("verdict") or None,
                code=_read(version, stem),
            )
        )
    return out


@router.get("/{run_id}/suite/download")
def download_suite(
    run_id: int, version: str | None = None, db: Session = Depends(get_session)
) -> Response:
    """The whole suite as a zip a judge can unpack and run.

    What goes in is the `.spec.ts` files, the `version.json` that says what they
    are and why they were emitted, and a README with the one command. The
    `.json` scenarios are deliberately left out: they are this system's own
    replay format, they carry state keys that mean nothing outside a run, and
    including them makes the archive look like it needs AIVAR to be useful.
    """
    run = _run_or_404(run_id, db)
    directory, known, chosen = _pick(run_id, run, db)
    if version:
        chosen = next((v for v in known if v.label == version), None)
    if chosen is None:
        raise HTTPException(404, "no suite has been kept for this target yet")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        specs = sorted(chosen.root.glob("*.spec.ts"))
        for path in specs:
            archive.writestr(f"tests/{path.name}", path.read_text(encoding="utf-8"))
        manifest = chosen.root / regression.VERSION
        if manifest.exists():
            archive.writestr("version.json", manifest.read_text(encoding="utf-8"))
        archive.writestr("README.md", _readme(chosen, len(specs)))
        archive.writestr("playwright.config.ts", _CONFIG)

    slug = regression.directory_for(
        run.target_url, session_id=run.session_id
    ).name
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="aivar-{slug}-{chosen.label}.zip"'
            )
        },
    )


@router.get("/{run_id}/suite/spec/{stem}", response_class=Response)
def download_spec(
    run_id: int,
    stem: str,
    version: str | None = None,
    db: Session = Depends(get_session),
) -> Response:
    """One `.spec.ts`, as a file.

    `stem` is matched against the version's own manifest rather than joined
    onto a path: the manifest is a closed set of names this server wrote, so a
    caller cannot ask for a file by describing where it is.
    """
    run = _run_or_404(run_id, db)
    _, known, chosen = _pick(run_id, run, db)
    if version:
        chosen = next((v for v in known if v.label == version), None)
    if chosen is None:
        raise HTTPException(404, "no suite has been kept for this target yet")
    if stem not in {str(e.get("file", "")) for e in chosen.scenarios}:
        raise HTTPException(404, "no such spec in this version")

    body = _read(chosen, stem)
    if not body:
        raise HTTPException(404, "the spec file is missing from disk")
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}.spec.ts"'
        },
    )


def _readme(version: regression.Version, count: int) -> str:
    return f"""# {version.label} -- {count} Playwright test(s)

Generated by AIVAR from an observed crawl of `{version.target_url}`.
Every assertion is something the application actually did when the explorer
walked this path, not a guess about what it ought to do.

## Run it

```bash
npm i -D @playwright/test && npx playwright install chromium
npx playwright test
```

Nothing from AIVAR is needed -- no Python, no map, no agent.

## What this version is

- recorded: {version.saved_at or "unrecorded"}
- planner: {version.source or "unrecorded"}
- because: {version.because or "-"}
- parent: {f"v{version.parent:03d}" if version.parent else "none, this is the baseline"}
- repairs written into it: {len(version.heals)}

## Credentials

These specs `fill()` a literal username and password, because a test that
cannot log in is not runnable standalone. They are whatever `AIVAR_USERNAME` /
`AIVAR_PASSWORD` were set to when the suite was recorded. Treat this archive as
carrying a credential: it does.
"""


_CONFIG = """import { defineConfig } from '@playwright/test';

// Minimal on purpose. The specs carry their own absolute URLs -- they were
// recorded against a live target -- so there is no baseURL to set here.
export default defineConfig({
  testDir: './tests',
  reporter: 'list',
  use: { trace: 'retain-on-failure' },
});
"""
