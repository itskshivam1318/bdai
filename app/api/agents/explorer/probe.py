"""Observable check for Observer + StateKey. Not a test suite -- evidence.

    cd app/api && uv run python -m agents.explorer.probe [base_url]

Answers four questions, in order of how much the crawler depends on them:

0. **Does the projection keep the right differences?** Seven synthetic pages,
   each isolating one thing `normalize` must ignore or must not. This section
   needs no server and is the one that fails first when someone edits
   `statekey.py`.
1. **Is the key stable?** Load the same page twice, cold. If the two keys
   differ, every component above this one is unbuildable -- the crawler would
   treat each revisit as a new state and never terminate.
2. **Does it separate genuinely different pages?** A key that never changes is
   just as useless as one that always changes.
3. **What does it do with the SUT's drift variants?** `?v=1|2|3` are the same
   *functional* page with renamed ids, changed button copy, reordered fields and
   an extra wrapper. Whether those collapse is a real design question, not a
   pass/fail -- see the note the run prints.

Sections 1-3 need `make dev`. They are skipped, loudly, if it is not running,
so section 0 stays runnable on its own.
"""

from __future__ import annotations

import sys

import os

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from agents.explorer import Observer, forms, state_key
from agents.explorer.statekey import explain

# See agents/probe.py: honouring WEB_PORT is what makes a probe in a worktree
# test that worktree rather than whatever owns port 3000.
DEFAULT_BASE = f"http://localhost:{os.environ.get('WEB_PORT') or '3000'}"

# Each case is (name, page A, page B, expected verdict). "same" means the
# projection must ignore the difference; "different" means it must not. Both
# directions are failures, and both are cheap to get wrong -- see the module
# docstring of statekey.py for what each error costs.
_FORM = """<main><h1>Create Project</h1><form>
<label>Name <input name=n {n_attrs}></label>
<label>Owner <input name=o></label>
<button {btn_attrs}>Create</button></form>{extra}</main>"""

_DASH = """<main><h1>Dashboard</h1><ul>{items}</ul>
<button>New project</button></main>"""


def _form(*, n_attrs: str = "", btn_attrs: str = "", extra: str = "") -> str:
    return _FORM.format(n_attrs=n_attrs, btn_attrs=btn_attrs, extra=extra)


def _rows(count: int) -> str:
    return _DASH.format(
        items="".join(f"<li>Project {i}</li>" for i in range(count))
    )


def _boxes(*, checked: int, total: int = 6) -> str:
    """A checklist with the first `checked` boxes ticked. Models the modal that
    ate a whole crawl budget on practicesoftwaretesting.com."""
    items = "".join(
        f'<label><input type=checkbox {"checked" if i < checked else ""}> '
        f"Step {i}</label>"
        for i in range(total)
    )
    return f"<main><h1>Testing Guide</h1>{items}</main>"


_SIDEBAR = """<div><nav aria-label="Sessions"><ul>{items}</ul>
<a href="/new">New session</a></nav>
<main><h1>AIVAR</h1>{extra}</main></div>"""


def _sidebar(*names: str, extra: str = "") -> str:
    """A session list: sibling links whose names are *data*, not affordance.

    `_rows` collapses because `canonical_value` rewrites "Project 17" into
    "Project #", leaving byte-identical neighbours for `collapse_runs` to fold.
    These names -- hostnames somebody typed -- canonicalise to nothing alike,
    so there is no run to fold and the count survives into the key.

    Measured: run 10 crawled the console at :3000 and produced 78 states, 5
    transitions, 1082 unexplored actions. 31 of the 78 were the same URL, `/`,
    differing only in how many sessions the crawl had itself created by the
    time it got there.
    """
    items = "".join(
        f'<li><a href="/s/{i}">{name} <span>{i}</span></a></li>'
        for i, name in enumerate(names, start=1)
    )
    return _SIDEBAR.format(items=items, extra=extra)


PROJECTION_CASES = (
    # (label, html_a, html_b, expected, what it would cost to get wrong)
    (
        "focus is not identity",
        _form(n_attrs="autofocus"),
        _form(),
        "same",
        "tabbing between fields would invent a state; forms never finish",
    ),
    (
        "field content is not identity",
        _form(n_attrs='value="My Project"'),
        _form(n_attrs='value="Other Project"'),
        "same",
        "every keystroke would be a state; the frontier grows without bound",
    ),
    (
        "field presence IS identity",
        _form(),
        _form(n_attrs='value="My Project"'),
        "different",
        "empty and valid forms collapse; submitting a blank form is untestable",
    ),
    (
        "row count is not identity",
        _rows(17),
        _rows(18),
        "same",
        "every row a user adds spawns a state; the crawl never terminates",
    ),
    (
        "empty vs populated IS identity",
        _rows(17),
        _rows(0),
        "different",
        "the empty state and its 'create your first' affordance go undiscovered",
    ),
    (
        "an error node IS identity",
        _form(),
        _form(extra='<p role=alert>Name is required</p>'),
        "different",
        "validation failures collapse into success; no unhappy paths at all",
    ),
    (
        "disabled IS identity",
        _form(),
        _form(btn_attrs="disabled"),
        "different",
        "a submittable form and a blocked one collapse; the guard is invisible",
    ),
    (
        "a ticked box is not identity",
        _boxes(checked=0),
        _boxes(checked=3),
        "same",
        "N checkboxes become 2^N states; a checklist eats the whole budget",
    ),
    (
        "a grown sibling list is not identity",
        _sidebar("localhost", "thetestingmap.org", "UI wiring test"),
        _sidebar("localhost", "thetestingmap.org", "UI wiring test", "saucedemo"),
        "same",
        "an app that lists what the crawler creates re-keys itself on every "
        "write; the crawl maps its own footprints and never reaches the app",
    ),
    (
        "an empty sibling list IS identity",
        _sidebar("localhost", "thetestingmap.org", "UI wiring test"),
        _sidebar(),
        "different",
        "the first-run empty state goes undiscovered -- the same boundary "
        "_rows(17) vs _rows(0) protects, and any fix here must keep it",
    ),
    (
        "collapsing a list does not swallow the page",
        _sidebar("localhost", "thetestingmap.org"),
        _sidebar("localhost", "thetestingmap.org", extra="<button>Start run</button>"),
        "different",
        "a fix aggressive enough to hide a new control costs more than the "
        "explosion it cures",
    ),
)


def _observe(page, url: str):
    observer = Observer(page)
    observer.start_window()
    page.goto(url)
    return observer.observe()


def _snapshot_html(page, html: str) -> str:
    """State-key input for a synthetic page. No server, no navigation."""
    observer = Observer(page)
    observer.start_window()
    page.set_content(html)
    return observer.observe(settle_ms=50).snapshot


# One list page, carrying every kind of frontier noise a real app produces.
# Modelled on practicesoftwaretesting.com's /account/invoices, where 272 of 382
# frontier actions were furniture.
_NOISY_PAGE = (
    "<main><h1>Invoices</h1><table>"
    + "".join(
        f'<tr><td>Invoice {i}</td><td><a href="/invoice/{i}">Details</a></td></tr>'
        for i in range(15)
    )
    + "</table>"
    + "".join(f"<button>Page-{i}</button>" for i in range(1, 12))
    + "<button>Next</button>"
    + '<a href="https://github.com/x">GitHub repo</a>'
    + '<a href="https://unsplash.com">Unsplash</a>'
    + "<button>Open chat</button><button>Select language</button>"
    + "<menu><li role=menuitem></li><li role=menuitem></li></menu>"
    + '<a href="/account/profile">My profile</a></main>'
)


def frontier_noise(page) -> bool:
    """Section 0b: does the action vocabulary keep only what is worth doing?

    Every row here cost an ant a wasted action on a real crawl.
    """
    from agents.explorer import forms

    observer = Observer(page)
    observer.start_window()
    page.set_content(_NOISY_PAGE)
    observation = observer.observe(settle_ms=50)
    actions = set(forms.available_actions(page, observation))

    cases = (
        ("15 identical row links collapse to one", "link:Details" in actions),
        ("unnamed elements collapse to one, not indexed", "menuitem" in actions),
        ("pagination collapses to one action", sum(a.startswith("button:Page") for a in actions) <= 1),
        ("Next is pagination", "button:Next" not in actions),
        ("off-origin links never enter the frontier", "link:GitHub repo" not in actions and "link:Unsplash" not in actions),
        ("third-party chrome is filtered", "button:Open chat" not in actions),
        ("locale switchers are filtered", "button:Select language" not in actions),
        ("real navigation survives", "link:My profile" in actions),
        ("the whole page is a handful of actions", len(actions) <= 6),
    )

    print("FRONTIER    what enters the action vocabulary, and what does not")
    passed = True
    for label, ok in cases:
        passed &= ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not passed:
        print(f"        got: {sorted(actions)}")
    print()
    return passed


def projection_grid(page) -> bool:
    """Section 0: does `normalize` keep the right differences? Needs no server.

    Every row here is a bug that was live in this file's history, or the
    opposite error that fixing it could have introduced. The grid exists so
    that editing `statekey.py` says which of the two you just caused.
    """
    print("PROJECTION  what normalize() must ignore, and must not")
    passed = True

    for label, html_a, html_b, expected, cost in PROJECTION_CASES:
        key_a = state_key(_snapshot_html(page, html_a))
        key_b = state_key(_snapshot_html(page, html_b))
        verdict = "same" if key_a == key_b else "different"
        ok = verdict == expected
        passed &= ok

        print(f"  {'PASS' if ok else 'FAIL'}  {label:<32} {verdict}")
        if not ok:
            print(f"        expected {expected}; if wrong, {cost}")
            print(f"        {explain(_snapshot_html(page, html_a), _snapshot_html(page, html_b))}")

    print()
    return passed


# Which fields a button owns, on pages that did and did not say so. Each row is
# (label, html, button text, expected -- "scoped" or "none").
#
# The two failures these guard are opposite and both were live: refusing a real
# login because it wore no `<form>` (practicetestautomation.com, whose page has
# zero form elements), and manufacturing `submit[valid]:button:Sign in with
# Google` out of unrelated page chrome (practicesoftwaretesting.com, where ten
# of eleven buttons had nothing to do with the login).
SCOPE_CASES = (
    (
        "a real <form> is still authoritative",
        "<main><form><label>Email <input name=e></label>"
        "<button>Sign in</button></form></main>",
        "Sign in",
        "scoped",
    ),
    (
        "a form-less login is scoped by its region",
        "<main><div id=form><label>Username <input name=u></label>"
        "<label>Password <input type=password name=p></label>"
        "<button>Submit</button></div></main>",
        "Submit",
        "scoped",
        # practicetestautomation.com/practice-test-login/ exactly: two bare
        # inputs and a button in a plain div. Refusing this cost the whole app
        # behind the wall -- the crawl clicked Submit empty until it timed out.
    ),
    (
        "a button sharing a region with another button owns nothing",
        "<main><div><label>Email <input name=e></label>"
        "<button>Sign in</button><button>Sign in with Google</button>"
        "</div></main>",
        "Sign in with Google",
        "none",
    ),
    (
        "page chrome does not reach the login fields",
        "<body><nav><button>Open chat</button><button>EN</button></nav>"
        "<main><div><input name=u><button>Submit</button></div></main></body>",
        "Open chat",
        "none",
    ),
    (
        "a button with no fields anywhere near it owns nothing",
        "<main><div><h1>Docs</h1><button>Print</button></div>"
        "<aside><input name=q></aside></main>",
        "Print",
        "none",
    ),
)


def form_scope(page) -> bool:
    """Does a button know which fields it submits?

    `form_of` answers this, and it is the gate on every `submit[...]` action:
    return None and the crawler can only ever click the button with the form
    empty, which on a login page means never getting in.
    """
    print("FORM SCOPE  which fields a button submits")
    passed = True

    for label, html, button, expected in SCOPE_CASES:
        page.set_content(html)
        scope = forms.form_of(page, f"button:{button}")
        verdict = "none" if scope is None else "scoped"
        ok = verdict == expected
        passed &= ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} {verdict}")
        if not ok:
            print(f"        expected {expected} for button {button!r}")

    print()
    return passed


# What `fill_form` types, and where. Each row is (label, html, expected values
# left in the form's fields, in document order). `None` means "left alone".
#
# A form is only reachable if something can be typed into it: `forms.perform`
# returns False when nothing filled, and the crawler then refuses
# `submit[valid]` permanently. Every row here is a shape that produced zero.
FILL_CASES = (
    (
        "a named field is filled by its name",
        "<form><label>Email <input name=e></label>"
        "<button>Go</button></form>",
        ("aivar-explorer@example.com",),
    ),
    (
        "two unnamed fields are two fields, not one twice",
        "<form><input><input><button>Go</button></form>",
        ("AIVAR test input", "AIVAR test input"),
    ),
    (
        "a read-only field is not an input the form takes",
        "<form><input readonly value='Norway'><input>"
        "<button>Go</button></form>",
        ("Norway", "AIVAR test input"),
        # testingchallenges.thetestingmap.org exactly: three read-only
        # textboxes and one real field, all unnamed. `.first` was read-only, so
        # every fill spent its whole timeout and the form was declared
        # unfillable -- the crawler could only ever submit it empty.
    ),
    (
        "a disabled field is skipped, and the next one still fills",
        "<form><input disabled><input><button>Go</button></form>",
        (None, "AIVAR test input"),
    ),
)


def form_fill(page) -> bool:
    """Can the crawler type into this form at all?

    The count matters as much as the values: `forms.perform` gates
    `submit[valid]` on `fill_form` returning non-zero, so a form that fills
    nothing is a form the crawler can only ever submit empty.
    """
    print("FORM FILL   what gets typed, and into which field")
    passed = True
    credentials = forms.Credentials("aivar-explorer@example.com", "Test-Password-1")

    for label, html, expected in FILL_CASES:
        page.set_content(html)
        observer = Observer(page)
        observer.start_window()
        observation = observer.observe(settle_ms=50)
        forms.fill_form(page, observation, credentials,
                        forms.form_of(page, "button:Go"))

        actual = tuple(
            page.locator("form input").nth(i).input_value()
            for i in range(page.locator("form input").count())
        )
        want = tuple(v if v is not None else "" for v in expected)
        ok = actual == want
        passed &= ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} "
              f"{len([a for a in actual if a])}/{len(actual)} non-empty")
        if not ok:
            print(f"        expected {want}")
            print(f"        got      {actual}")

    print()
    return passed


def main(base_url: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        # --- 0. the projection, in isolation ----------------------------------
        projection_ok = projection_grid(page)
        projection_ok &= frontier_noise(page)
        projection_ok &= form_scope(page)
        projection_ok &= form_fill(page)

        try:
            page.goto(base_url, timeout=3000)
        except PlaywrightError:
            print(f"SKIPPED     sections 1-3 need `make dev` at {base_url}")
            browser.close()
            return 0 if projection_ok else 1

        # --- 1. what the observer actually sees -------------------------------
        first = _observe(page, f"{base_url}/sut?v=1")
        print(f"target      {first.url}")
        print(f"title       {first.title}")
        print(f"elements    {len(first.elements)} total, "
              f"{len(first.interactive)} interactive")
        for element in first.interactive:
            suffix = f"  -> {element.url}" if element.url else ""
            print(f"              [{element.ref}] {element.descriptor}{suffix}")
        print(f"network     {len(first.network)} responses, "
              f"{len(first.mutating_calls)} mutating")
        print()

        # --- 2. stability: the property everything else rests on ---------------
        again = _observe(page, f"{base_url}/sut?v=1")
        key_1, key_1b = state_key(first.snapshot), state_key(again.snapshot)
        stable = key_1 == key_1b
        print(f"STABILITY   v1 twice -> {key_1} / {key_1b}  "
              f"{'STABLE' if stable else 'UNSTABLE'}")
        if not stable:
            print(explain(first.snapshot, again.snapshot))
        print()

        # --- 3. discrimination across the drift variants -----------------------
        keys = {"v1": key_1}
        snapshots = {"v1": first.snapshot}
        for variant in ("2", "3"):
            observation = _observe(page, f"{base_url}/sut?v={variant}")
            keys[f"v{variant}"] = state_key(observation.snapshot)
            snapshots[f"v{variant}"] = observation.snapshot

        print("DRIFT       " + "  ".join(f"{k}={v}" for k, v in keys.items()))
        distinct = len(set(keys.values()))
        print(f"            {distinct} distinct state(s) across 3 markup variants")
        print()
        print("v1 vs v2:")
        print(explain(snapshots["v1"], snapshots["v2"]))

        browser.close()
        return 0 if (stable and projection_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE))
