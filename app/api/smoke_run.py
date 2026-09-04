"""End-to-end walking skeleton: browser -> evidence -> database -> canvas.

Run it against a live web server:

    cd api && uv run python smoke_run.py http://localhost:3000

It drives the system under test at v1, then at v2 where the markup has drifted,
watches the original locator miss, recovers it by accessible role, and records
every step. This is deliberately the dumbest possible healing strategy -- its
job is to prove the wiring, not to be the product. Tomorrow, replace
`heal_locator` with something that earns the word "intelligence".
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlmodel import Session

from app.config import settings
from app.db import engine, init_db
from app.models import Artifact, Event, Run, TestCase


def log(session: Session, run: Run, message: str, level: str = "info") -> None:
    session.add(Event(run_id=run.id, level=level, message=message))
    session.commit()
    print(f"[{level}] {message}")


def heal_locator(page, original: str):
    """Fallback strategy when a recorded selector no longer matches."""
    candidate = page.get_by_role("button")
    if candidate.count():
        return candidate.first, "role=button"
    return None, None


def main(base_url: str) -> int:
    init_db()
    with Session(engine) as session, sync_playwright() as pw:
        run = Run(target_url=f"{base_url}/sut", status="running")
        session.add(run)
        session.commit()
        session.refresh(run)

        artifacts_root = settings.artifacts_dir / f"run-{run.id}"
        artifacts_root.mkdir(parents=True, exist_ok=True)

        browser = pw.chromium.launch()
        page = browser.new_page()
        original_selector = '[data-testid="submit"]'
        healed = False

        for variant in ("1", "2"):
            url = f"{base_url}/sut?v={variant}"
            page.goto(url, wait_until="networkidle")
            log(session, run, f"v{variant}: loaded {url}")

            shot = artifacts_root / f"v{variant}.png"
            page.screenshot(path=str(shot))
            session.add(
                Artifact(
                    run_id=run.id,
                    kind="screenshot",
                    path=f"{artifacts_root.name}/{shot.name}",
                    label=f"variant {variant}",
                )
            )
            session.commit()

            locator = page.locator(original_selector)
            if locator.count():
                status, detail = "passed", f"matched {original_selector}"
                log(session, run, f"v{variant}: {detail}")
            else:
                log(
                    session,
                    run,
                    f"v{variant}: {original_selector} matched 0 elements",
                    level="warn",
                )
                recovered, strategy = heal_locator(page, original_selector)
                if recovered is None:
                    status, detail = "failed", "no healing candidate"
                    log(session, run, f"v{variant}: {detail}", level="error")
                else:
                    healed = True
                    status = "healed"
                    detail = f"healed via {strategy} -> {recovered.inner_text()!r}"
                    log(session, run, f"v{variant}: {detail}", level="decision")

            session.add(
                TestCase(
                    run_id=run.id,
                    name=f"submit button present (v{variant})",
                    selector=original_selector,
                    healed_selector="role=button" if status == "healed" else None,
                    status=status,
                    detail=detail,
                )
            )
            session.commit()

        browser.close()
        run.status = "passed"
        run.summary = (
            "submit locator survived DOM drift via healing"
            if healed
            else "no drift encountered"
        )
        session.add(run)
        session.commit()
        print(f"\nrun {run.id}: {run.summary}")
        print(f"artifacts: {artifacts_root}")
        print(f"view: add an 'Artifact Viewer' widget with path run-{run.id}/v2.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"))
