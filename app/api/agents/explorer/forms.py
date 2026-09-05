"""What an explorer can *do* to a page, and what to type when it does.

Split from `crawler.py` on purpose. The crawler is the loop that decides which
action to take next; this module is the hands that take it. One explorer node --
one "ant" -- needs the hands and not the loop, because the thing choosing where
ants go comes later and may not be a loop at all.

**Filling a form is one action, not one action per field.** This is the whole
design and it is not obvious. `statekey.field_value` makes empty-vs-filled part
of state identity, deliberately: a blank form and a completed one have different
outgoing transitions, and collapsing them makes "submit an empty form"
undiscoverable. But that same choice means a three-field form has 2^3 = 8
distinct states reachable by typing alone, and a breadth-first crawler will
visit every one of them. Four fields, sixteen. It never gets to the submit.

So filling is atomic. The intermediate half-filled states are never observed and
never enter the map. What the map records is the *outcome* of a form, which is
the only part with behavioural meaning:

    submit[empty]     nothing typed, submit anyway   -> the validation error state
    submit[valid]     everything typed, submit       -> whatever success looks like
    submit[invalid]   deliberately bad input         -> NOT BUILT. See below.

Two of the three need no model, and they are the two that matter most: `empty`
is an unhappy path for free, and `valid` is how the crawler gets past a login
wall into the application at all.

`submit[invalid]` is the model seam. Knowing that "not-an-email" is a rejectable
email, or that 4111111111111111 is a test card that will pass while 1234 will
not, is domain knowledge no heuristic here supplies. It is also the seam that
closes the hole `statekey.field_value` documents: a form the app rejects
*silently* is indistinguishable from a valid one until something deliberately
feeds it something bad.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from .observer import Observation
from .statekey import FIELD_ROLES

# Compound action grammar. `WorldMap` treats actions as opaque strings, so this
# vocabulary can grow without touching the model.
_FORM_ACTION = re.compile(r"^submit\[(?P<mode>\w+)\]:(?P<descriptor>.+)$")

# Pagination controls, matched against the whole accessible name. Anchored so
# that a real control is never caught by accident: "Next" is pagination,
# "Next step" is a checkout flow and must survive.
_PAGINATION = re.compile(
    r"^(page[\s-]?\d+|\d+|next|previous|prev|first|last|\u00bb|\u00ab|\u203a|\u2039|>|<)$",
    re.IGNORECASE,
)

# Field intent, matched against the accessible name. Order matters: "confirm
# password" must reach the password rule, and an "email" rule that ran first
# would still be wrong for "confirmation email address" -- which is why the
# password test is a substring and comes first.
_PASSWORD = re.compile(r"pass(word|phrase)?\b|\bpwd\b", re.IGNORECASE)
_EMAIL = re.compile(r"e-?mail", re.IGNORECASE)
_USERNAME = re.compile(r"user\s*(name|id)?|login|account|handle", re.IGNORECASE)
_SEARCH = re.compile(r"search|filter|query|find", re.IGNORECASE)


@dataclass(frozen=True)
class Credentials:
    """The one piece of configuration the brief allows besides the URL.

    Authentication is pre-crawl configuration in every system in
    `docs/research/exploration-landscape.md`, and emergent in none of them. An
    agent cannot deduce a password, and one that tries is one signup form away
    from creating accounts on someone else's service.
    """

    username: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls) -> Credentials:
        return cls(
            username=os.environ.get("AIVAR_USERNAME"),
            password=os.environ.get("AIVAR_PASSWORD"),
        )

    def __bool__(self) -> bool:
        return bool(self.username or self.password)


def value_for(role: str, name: str, credentials: Credentials) -> str:
    """What to type into one field. Deterministic, and deliberately dull.

    This is the half of input synthesis that needs no model: recognising that a
    field called "Password" wants the configured password. It gets a crawler
    through a login wall, which is the single highest-value thing it can do,
    and it does not pretend to know what a valid VAT number looks like.

    Generic values are chosen to be *plausible and inert*: they should pass
    format validation so the crawler reaches the success path, and mean nothing
    if they end up in someone's database. `docs/research/exploration-landscape.md`
    records an agent creating an unwanted repository while trying to file an
    issue; the text an explorer types is the part that ends up persisted.
    """
    if _PASSWORD.search(name):
        return credentials.password or "Test-Password-1"
    if _EMAIL.search(name):
        return credentials.username or "aivar-explorer@example.com"
    if _USERNAME.search(name):
        return credentials.username or "aivar-explorer"
    if _SEARCH.search(name):
        return "test"

    return {
        "spinbutton": "1",
        "slider": "1",
        "searchbox": "test",
    }.get(role, "AIVAR test input")


def form_of(page: Page, descriptor: str):
    """The fields a button submits: its `<form>`, or the region standing in for
    one on a page that never wrote the tag. None if the button owns no fields.

    **A page having fields does not make every button on it a submit.** An
    earlier version assumed it did, and on a real login page
    (practicesoftwaretesting.com) that manufactured actions like
    `submit[valid]:button:Open chat`, `submit[valid]:button:Select language` and
    -- worst -- `submit[valid]:button:Sign in with Google`, which fills the
    login form and then leaves the origin entirely. Ten of the eleven buttons on
    that page were nothing to do with the login form.

    The accessibility tree cannot answer this: a `<form>` carries no implicit
    ARIA role, so it appears as an anonymous `generic` node indistinguishable
    from a styling wrapper. So this is the one place we ask the DOM directly.
    It is a narrow question -- ancestry, not content -- and the alternative was
    a heuristic over sibling nesting that would be wrong silently.

    **A real `<form>` still wins.** It is a declaration by the page's author of
    which fields belong to which submit, and nothing inferred beats being told.
    `_implicit_scope` runs only when there is no such declaration -- see its
    docstring for why that fallback does not undo the paragraph above.
    """
    element = locate(page, descriptor)
    form = element.locator("xpath=ancestor::form[1]")
    try:
        if form.count() and form.locator("input, textarea, select").count():
            return form
    except Exception:
        return None
    return _implicit_scope(element)


# How far to climb looking for a form that was never marked up as one. Six is
# past every real case measured (practicetestautomation's login is one hop) and
# short enough that a button in a page-level container runs out of rope before
# it reaches unrelated fields.
_MAX_HOPS = 6

_FILLABLE = (
    "input:not([type=hidden]):not([type=submit]):not([type=button]),"
    "textarea,select"
)
_CLICKABLE = "button,input[type=submit],input[type=button]"

# A form region is not a page region. If the smallest area holding the button
# and some fields is itself a landmark, or straddles one, then the two are in
# different parts of the page and the "region" is really just the page.
#
# This is the rule that stops a lone Print button adopting the search box in
# the sidebar -- caught by `form_scope` in explorer/probe.py, which is exactly
# the `submit[valid]:button:Print` shape the <form> test was protecting against.
_LANDMARK = "main,nav,aside,header,footer,body,html,section[role],form"

# Climb from the button; stop at the first ancestor holding a fillable field,
# and give up the moment another button comes into view.
#
# The second half is the whole safety argument. A `<form>` is a *declaration* of
# which fields belong to which submit, and without one the honest proxy is "the
# smallest region containing this button and some fields and no other button" --
# because a region with two buttons in it has not told us which one owns the
# fields.
_SCOPE_JS = """(el) => {
  const fillable = %s;
  const clickable = %s;
  const landmark = %s;
  let node = el, hop = 0;
  while (node.parentElement && hop < %d) {
    node = node.parentElement; hop++;
    if (node.matches(landmark)) return 0;
    if ([...node.querySelectorAll(clickable)].some(x => x !== el)) return 0;
    if (node.querySelector(landmark)) return 0;
    if (node.querySelectorAll(fillable).length) return hop;
  }
  return 0;
}""" % (repr(_FILLABLE), repr(_CLICKABLE), repr(_LANDMARK), _MAX_HOPS)


def _implicit_scope(element):
    """The fields a button owns on a page that never wrote a `<form>` tag.

    **Measured, not guessed.** `practicetestautomation.com/practice-test-login/`
    has `document.querySelectorAll('form').length === 0`: its login is two bare
    inputs and a button in a `<div id="form">`. The observer saw
    `textbox:Username` and `textbox:Password`, `form_of` returned None, no
    `submit[...]` action was ever synthesised, and the crawler clicked Submit
    with nothing filled in until its budget ran out. A crawl that cannot type
    cannot get past a login wall, and everything behind the wall stayed
    unmapped.

    **Why this does not resurrect the bug the `<form>` test was added for.**
    Simulated against `practicesoftwaretesting.com/auth/login`, the page that
    motivated that test -- all ten of its clickables, under this rule:

        Sign in with Google     rejected at hop2 (2 other buttons)
        Open chat               rejected at hop3 (9 other buttons)
        EN (language)           rejected at hop2 (1 other button)
        Toggle navigation       rejected at hop1 (2 other buttons)
        Testing Guide           rejected at hop1 (1 other button)
        Bug Hunting             rejected at hop1 (1 other button)
        Categories              rejected at hop3 (1 other button)
        Login, and one unnamed  already inside a <form> -- fast path, unchanged

    Zero new actions on that page. The dangerous buttons are dangerous
    *because* they sit among other buttons in shared page chrome, which is
    exactly what the stop rule keys on.

    Returns None rather than a page-wide scope when it finds nothing, so the
    caller's existing "no form here" behaviour is unchanged.
    """
    try:
        hop = element.evaluate(_SCOPE_JS)
    except Exception:
        return None
    if not hop:
        return None
    # The ancestor axis is reverse-ordered, so [1] is the parent and [hop] is
    # the element the rule stopped on.
    return element.locator(f"xpath=ancestor::*[{hop}]")


def leaves_origin(here: str, href: str | None) -> bool:
    """Does this link point off the application? A relative href never does.

    Also catches `mailto:`, `tel:` and `javascript:` -- schemes an explorer
    should not follow and that have no origin to compare.
    """
    if not href:
        return False
    target = urlparse(urljoin(here, href))
    if target.scheme and target.scheme not in ("http", "https"):
        return True
    return bool(target.netloc) and target.netloc != urlparse(here).netloc


DESTRUCTIVE = re.compile(
    r"\b(delete|remove|destroy|deactivate|cancel|unsubscribe|revoke|archive|"
    r"reset|clear|purge|close account|log ?out|sign ?out)\b",
    re.IGNORECASE,
)


def is_safe(descriptor: str) -> bool:
    """Default guard. Sign-out is excluded for a boring reason as well as a
    safe one: it ends the session and every subsequent replay lands on a login
    page, so one click poisons the rest of the crawl.

    Lives here rather than in `crawler.py`, where it was written, because it is
    a fact about *what an explorer may do to a page* and this module is the
    hands. Keeping it in the crawler made it the crawler's private property:
    `ant.py` never had it, so the colony -- the engine the console runs
    whenever an API key is present -- walked unguarded. A guard that only one
    of two walkers honours is not a guard.

    A stub, and labelled as one: real coverage is Magentic-UI's ActionGuard
    (arXiv:2507.22358), which classifies every action always/never/maybe
    irreversible and routes "maybe" to a judge. This denylist is what stands
    between an unattended run and someone else's data in the meantime --
    `docs/research/exploration-landscape.md` records an agent in
    ST-WebAgentBench creating an unwanted repository while trying to file an
    issue.
    """
    return not DESTRUCTIVE.search(descriptor)


def available_actions(page: Page, observation: Observation) -> tuple[str, ...]:
    """The action vocabulary of one state.

    Two rules beyond "every interactive element is clickable":

    **Fields are not clickable.** Clicking a textbox only moves focus, and focus
    is deliberately not part of state identity (`statekey._NOISE`), so the click
    provably cannot change the state. It was a guaranteed-wasted crawl step and
    a permanent `textbox:Email -> stays` edge in every map. Fields are reached
    through form actions instead.

    **A button inside a form with inputs submits that form.** Everything else
    stays a plain click. The plain click is dropped for those buttons rather
    than kept alongside, because clicking submit on an untouched form *is*
    `submit[empty]` -- keeping both would explore one thing twice under two
    names.

    Then three rules that exist because a real page is mostly not application.
    Measured on practicesoftwaretesting.com: **272 of 382 frontier actions were
    furniture**, and a frontier that is 71% chrome does not merely waste ants --
    it poisons `gaps()`, whose whole value is that its alphabet is the app's.

    **A link that leaves the origin is not ours.** The crawler already refused
    to follow one, but only *after* spending an action to find out. Checking the
    href first is free and provable, needing no denylist.

    **Duplicate descriptors collapse to one.** The largest single reduction, and
    it follows from what a descriptor means. A list with fifteen `link:Details`
    has fifteen elements and *one* action: they are indistinguishable to
    `locate()`, which resolves `.first` for every one. Exploring the same action
    fifteen times is not coverage.

    That also settles unnamed elements. Four bare `menuitem` entries produced
    four identical descriptors and four duplicate edges to the same state in a
    real map. The fix is deliberately *not* a positional descriptor
    (`menuitem#2`): position is the brittle locator this design exists to avoid,
    and a test written against one would not survive the drift the Healer is for.
    An element we cannot name is one we cannot write a stable test for, so it is
    explored once and honestly.

    **Pagination collapses to a single action.** `Page-1 … Page-11`, `Next`,
    `Previous` reach the same state template with different rows -- which
    `statekey.collapse_runs` already folds into one state. One is enough to
    record that the app paginates; the rest are the linear chain that
    `Budget.max_depth` exists to escape.
    """
    here = observation.url
    actions: list[str] = []
    seen: set[str] = set()
    paginators = 0

    for element in observation.interactive:
        if element.role in FIELD_ROLES:
            continue
        if leaves_origin(here, element.url):
            continue
        if _PAGINATION.match(element.name.strip()):
            paginators += 1
            if paginators > 1:
                continue

        if element.role == "button" and form_of(page, element.descriptor):
            candidates = [
                f"submit[empty]:{element.descriptor}",
                f"submit[valid]:{element.descriptor}",
                f"submit[invalid]:{element.descriptor}",
            ]
        else:
            candidates = [element.descriptor]

        for action in candidates:
            if action not in seen:
                seen.add(action)
                actions.append(action)

    return tuple(actions)


def locate(page: Page, descriptor: str):
    """Resolve `role:name` against the live page.

    The one resolution path in the system. The crawler uses it to explore, and
    the Generator and Healer will use it to run and repair -- so a descriptor
    that fails to resolve here would have failed in a generated test too, and
    learning that during the crawl costs nothing.
    """
    role, _, name = descriptor.partition(":")
    if name:
        return page.get_by_role(role, name=name, exact=True).first
    return page.get_by_role(role).first


def fields_of(observation: Observation) -> tuple[tuple[str, str], ...]:
    """(role, name) for every fillable field in a state. What `synth` describes."""
    return tuple(
        (element.role, element.name)
        for element in observation.interactive
        if element.role in FIELD_ROLES
    )


def _next_unnamed(root, role: str, cursor: dict[str, int]):
    """The next fillable field of `role` that carries no accessible name.

    **Two bugs, one function.** `fill_form` used to resolve every unnamed field
    to `get_by_role(role).first`, which is the same element every time: N
    unnamed fields meant one field typed into N times, and the others never
    touched. And `.first` is frequently not fillable, because a read-only field
    is still a textbox to the accessibility tree.

    Measured on `testingchallenges.thetestingmap.org`, whose form has four
    textboxes with no accessible name, three of them
    `<input readonly value="Norway">` and one real `#firstname`. `.first` was a
    read-only one, so every attempt spent the full `fill` timeout and returned
    False, `submit[valid]` was refused, and the crawler could only ever click
    Submit on an empty form. The map called it a shallow app.

    **Why position, when this codebase refuses positional locators.**
    `available_actions` argues at length that `menuitem#2` is the brittle
    locator the whole design avoids -- and that is right, for an *action*,
    because an action becomes a test that has to survive drift. This is not an
    action. It is one step inside performing one, re-derived against the live
    page every single time it runs, and never written into a spec. Nothing here
    is recorded and replayed, so there is nothing for drift to break.

    An unnamed field also has, by definition, nothing else to match on. The
    honest options were position or nothing, and nothing is what we had.
    """
    candidates = root.get_by_role(role)
    index = cursor.get(role, 0)
    try:
        total = candidates.count()
    except Exception:
        return None

    while index < total:
        field = candidates.nth(index)
        index += 1
        try:
            # A read-only or disabled field is not an input the form takes.
            # Checking costs milliseconds; discovering it through `fill` costs
            # the whole timeout, per field, and there may be many.
            if field.is_editable(timeout=500):
                cursor[role] = index
                return field
        except Exception:
            continue

    cursor[role] = index
    return None


def fill_form(
    page: Page,
    observation: Observation,
    credentials: Credentials,
    scope=None,
    overrides: dict[str, str] | None = None,
) -> int:
    """Type into the fields of one form. Returns how many were filled.

    `scope` is the form to stay inside, from `form_of`. Without it every field
    on the page is fair game, which is wrong on any page carrying two forms --
    a login form beside a newsletter signup, say -- because filling both means
    the submit under test never sees the input it was given.

    `overrides` replaces the deterministic value for named fields, and is how
    `submit[invalid]` gets its rejectable input from `synth`. A field absent
    from the overrides still gets its plausible default, so a payload that
    makes one field bad leaves the rest realistic and the resulting error stays
    attributable to that field.

    Best-effort per field: a field that will not accept input is skipped rather
    than aborting the whole form, because a partially filled form still reaches
    the submit and still tells us something. A form where *nothing* filled is
    reported by the count, and the crawler treats that as a failed action rather
    than pretending it submitted a completed form.
    """
    filled = 0
    root = scope if scope is not None else page
    # Where the next unnamed field of each role will be looked for. Unnamed
    # fields are the one case with nothing to match on, so they are consumed in
    # document order -- see `_next_unnamed`.
    cursor: dict[str, int] = {}

    for element in observation.interactive:
        if element.role not in FIELD_ROLES:
            continue
        try:
            if element.name:
                field = root.get_by_role(
                    element.role, name=element.name, exact=True
                ).first
            else:
                field = _next_unnamed(root, element.role, cursor)
                if field is None:
                    continue
            value = (overrides or {}).get(
                element.name, value_for(element.role, element.name, credentials)
            )
            field.fill(value, timeout=3000)
            filled += 1
        except Exception:
            # Not in this form, read-only, hidden behind an overlay, or a
            # combobox wanting a selection rather than text. None is fatal.
            continue

    return filled


def perform(
    page: Page,
    action: str,
    observation: Observation,
    credentials: Credentials,
    synthesizer=None,
    state_key: str = "",
) -> bool:
    """Do one action. False if it could not be done at all.

    `observation` is the state as it was seen *before* this call, and it is what
    names the fields to fill -- so the caller must have already arrived at that
    state. Returning False rather than raising is deliberate: an action that
    cannot be performed is a fact about the application, and the caller records
    it and moves on rather than treating it as a crawler fault.

    `synthesizer` is consulted only for `submit[invalid]`. Passing None makes
    that mode unavailable rather than silently submitting valid input, because
    a `submit[invalid]` edge that actually carried a valid payload would be a
    lie in the map -- and the map is what the Planner reads.
    """
    form = _FORM_ACTION.match(action)

    if form is None:
        try:
            locate(page, action).click(timeout=3000)
            return True
        except Exception:
            return False

    mode = form.group("mode")
    descriptor = form.group("descriptor")
    scope = form_of(page, descriptor)
    overrides: dict[str, str] | None = None

    if mode == "invalid":
        if synthesizer is None:
            return False
        overrides = synthesizer.invalid_payload(
            state_key, descriptor, observation.title, fields_of(observation)
        ).values

    if mode in {"valid", "invalid"} and not fill_form(
        page, observation, credentials, scope, overrides
    ):
        # Nothing could be typed, so this is not the filled-input case at all.
        # Submitting anyway would record a path that never happened.
        return False

    try:
        locate(page, descriptor).click(timeout=3000)
        return True
    except Exception:
        return False
