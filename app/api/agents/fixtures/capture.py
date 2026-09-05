"""Capture the real before/after snapshot pair for the Testing Guide modal.

Provenance for `fixtures/testing-guide-modal.json`. Run when the
fixture needs refreshing against the live site; the probe reads the file, not
the site, so this is not part of any test run.
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright
from agents.explorer.observer import Observer

URL = "https://practicesoftwaretesting.com"
OUT = Path(__file__).resolve().parent / "testing-guide-modal.json"

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    obs = Observer(page)
    obs.start_window()
    page.goto(URL)
    before = obs.observe()

    button = page.get_by_role("button", name="Testing Guide")
    if button.count() == 0:
        print("FAIL  no 'Testing Guide' button on this visit -- the entry page varies")
        sys.exit(1)
    obs.start_window()
    button.first.click()
    page.wait_for_timeout(1500)
    after = obs.observe()
    browser.close()

OUT.write_text(json.dumps({
    "captured_at": datetime.now(timezone.utc).isoformat(),
    "source_url": URL,
    "action": "button:Testing Guide",
    "why": (
        "The modal whose 469-line delta became a 469-line assertion, so a modal "
        "that opened correctly was reported as a DEFECT three times. Real site "
        "data because the target is nondeterministic -- 11, 5, 6 and 2 state "
        "maps across four runs of the same URL -- so A/B testing a fix against "
        "it is not a usable experiment."
    ),
    "before": before.snapshot,
    "after": after.snapshot,
}, indent=1), encoding="utf-8")
print(f"SAVED {OUT}  before={len(before.snapshot.splitlines())} lines  "
      f"after={len(after.snapshot.splitlines())} lines")
