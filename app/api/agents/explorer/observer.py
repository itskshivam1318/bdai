"""Turn a live page into one Observation.

An Observation is what the explorer records at a single moment: the semantic
shape of the page, the interactive elements it could act on next, and the
network traffic that happened in a window around an action.

**Why the accessibility tree and not the DOM or a screenshot.** WebMall
(arXiv:2508.13024) A/B-tested observation spaces with the same agent over the
same 91 tasks: AX-tree alone 62%, AX-tree + memory 75%, vision-only 25%. Adding
vision to a working a11y agent frequently made it *worse*. The token economics
are not close either -- an a11y snapshot is ~200-400 tokens against ~3,000-5,000
for a screenshot, and raw HTML on real pages runs 40K-500K.

The known weakness is that the a11y tree is blind to broken markup: the WebAIM
Million (Feb 2026) found 30.6% of home pages have empty buttons and 51% have
missing form labels. That argues for vision as a *fallback* on pages where we
find no interactive elements at all -- not as a co-primary. We do not implement
that fallback yet; `Observation.elements` being empty is the signal that will
trigger it.

Playwright API note: in Python `aria_snapshot` lives on **Locator**, not Page --
`page.locator("body").aria_snapshot(mode="ai")`. The JS docs show
`page.ariaSnapshot()`; the binding differs. Verified against playwright 1.62.0.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .noise import is_foreign
from .statekey import state_key

# Roles a user can act on. Everything else is layout or prose. Taken from the
# ARIA widget roles that Playwright actually emits; extend when a real app
# surfaces one we miss rather than guessing upfront.
INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)

# One node of the AI-mode snapshot, e.g.
#   - button "Sign in" [ref=e9] [box=414,66,57,21]
#   - paragraph [ref=e13] [box=...]: "Cart total: 42"
#   - text: Email
_NODE = re.compile(
    r"""^(?P<indent>\s*)-\s+
        (?P<role>[a-zA-Z][\w-]*)
        (?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?
        (?P<attrs>(?:\s*\[[^\]]*\])*)
        \s*(?::\s*(?P<value>.*))?$
    """,
    re.VERBOSE,
)

# Property lines hanging off a node, e.g. `- /url: /checkout`.
_PROP = re.compile(r"^(?P<indent>\s*)-\s+/(?P<key>[\w-]+):\s*(?P<value>.*)$")

_ATTR = re.compile(r"\[(?P<key>[\w-]+)=(?P<value>[^\]]*)\]")


@dataclass(frozen=True)
class Element:
    """One thing on the page the explorer could act on.

    `ref` is Playwright's opaque handle (`e9`). It is valid **only within the
    snapshot that produced it** -- re-snapshot the page and the numbering can
    change. Never persist a ref as an identity; persist role + name, which is
    also what makes a locator survive UI drift.
    """

    ref: str
    role: str
    name: str
    url: Optional[str] = None  # links only, from the `/url:` property line
    pointer: bool = False  # had [cursor=pointer]

    @property
    def descriptor(self) -> str:
        """The durable way to refer to this element. Survives re-snapshotting."""
        return f'{self.role}:{self.name}' if self.name else self.role


@dataclass(frozen=True)
class NetworkEvent:
    """One request/response pair seen during an observation window.

    The method matters more than the URL for our purposes: a GET is a read and a
    POST/PUT/DELETE is a side effect. That distinction is what ActionGuard will
    gate on, and what tells a Healer later that a click "did something" even
    though the DOM did not change.
    """

    method: str
    url: str
    resource_type: str
    status: Optional[int] = None

    @property
    def mutating(self) -> bool:
        return self.method.upper() not in {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Observation:
    """The page at one moment, plus what the network did to get there."""

    url: str
    title: str
    snapshot: str  # raw AI-mode aria snapshot, kept verbatim as evidence
    elements: tuple[Element, ...] = ()
    network: tuple[NetworkEvent, ...] = ()
    captured_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def interactive(self) -> tuple[Element, ...]:
        return tuple(e for e in self.elements if e.role in INTERACTIVE_ROLES)

    @property
    def mutating_calls(self) -> tuple[NetworkEvent, ...]:
        return tuple(n for n in self.network if n.mutating)


def parse_snapshot(snapshot: str) -> tuple[Element, ...]:
    """Parse an AI-mode aria snapshot into elements.

    The format is indentation-structured YAML-ish text. We flatten it: the
    explorer cares *which* controls exist, not how deeply they nest. Nesting is
    preserved in `Observation.snapshot` for anything that needs it later.
    """
    elements: list[Element] = []
    # Property lines (`/url:`) attach to the nearest preceding node at lower
    # indentation, so we track where the last node was opened.
    last_index: Optional[int] = None

    for line in snapshot.splitlines():
        if not line.strip():
            continue

        prop = _PROP.match(line)
        if prop and last_index is not None:
            if prop.group("key") == "url":
                current = elements[last_index]
                elements[last_index] = Element(
                    ref=current.ref,
                    role=current.role,
                    name=current.name,
                    url=prop.group("value").strip(),
                    pointer=current.pointer,
                )
            continue

        node = _NODE.match(line)
        if not node:
            continue

        attrs = dict(_ATTR.findall(node.group("attrs") or ""))
        ref = attrs.get("ref", "")
        name = (node.group("name") or "").replace('\\"', '"')

        if is_foreign(name):
            # Injected chrome (dev overlays, cookie banners). Excluded here so
            # it never enters the frontier -- an explorer that clicks the cookie
            # banner first has spent its budget on someone else's software.
            continue

        if not ref:
            # Nodes without a ref cannot be acted on (plain `text:` runs, and
            # anything Playwright chose not to expose). They still shape the
            # state key via the raw snapshot, so dropping them here is safe.
            continue

        elements.append(
            Element(
                ref=ref,
                role=node.group("role"),
                name=name,
                pointer=attrs.get("cursor") == "pointer",
            )
        )
        last_index = len(elements) - 1

    return tuple(elements)


class Observer:
    """Watches one Playwright page and produces Observations.

    Network capture is *windowed*: call `start_window()` immediately before an
    action and the next `observe()` reports only the traffic that action caused.
    That pairing -- (action, network effect, DOM delta) -- is the whole reason to
    record network at all. It is what later lets the Healer separate "the
    selector broke" (no request fired) from "the app broke" (the same request
    fired, the DOM did not change). It can only be captured while exploring;
    there is no reconstructing it afterwards.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self._buffer: list[NetworkEvent] = []
        self._recording = False
        page.on("response", self._on_response)

    def _on_response(self, response) -> None:
        if not self._recording:
            return
        request = response.request
        self._buffer.append(
            NetworkEvent(
                method=request.method,
                url=request.url,
                resource_type=request.resource_type,
                status=response.status,
            )
        )

    def start_window(self) -> None:
        """Begin attributing network traffic to the action about to be taken."""
        self._buffer.clear()
        self._recording = True

    def observe(self, settle_ms: int = 400, patience_ms: int = 5000) -> Observation:
        """Snapshot the page once it has stopped changing, and close the window.

        Deliberately not `networkidle`: Playwright's own docs discourage it, and
        an SPA that polls never goes idle. The sanctioned replacement is
        auto-retrying assertions -- but an *explorer* has no expected end state
        to assert on, which is what makes this genuinely unsolved rather than
        merely fiddly.

        What an explorer *does* have is a stability criterion. We already
        compute, for our own reasons, a function that says whether two snapshots
        are the same state. So: snapshot, wait, snapshot again, and return once
        two consecutive reads agree. A static page pays one extra `settle_ms`; a
        slow-hydrating one waits as long as it actually needs, up to
        `patience_ms`.

        **This replaces a fixed wait that measurably did not work.** Against an
        Angular app (practicesoftwaretesting.com) a fixed 400ms caught the
        pre-hydration shell about half the time, so the same URL yielded two
        state keys at random. Every replay then landed somewhere other than
        where it aimed, the landing check correctly rejected it, and the crawl
        recorded **zero transitions** out of 52 candidate actions.

        Returning after `patience_ms` without agreement is not a failure and is
        not raised: a genuinely animated page (a carousel, a live activity
        feed) never stabilises, and refusing to observe it would be worse than
        observing it unstably. The instability then shows up where it should --
        as a nondeterministic edge in the map.
        """
        deadline = time.monotonic() + (patience_ms / 1000)

        self.page.wait_for_timeout(settle_ms)
        snapshot = self._snapshot()

        # A document that is still committing has no `body` yet. Wait for one
        # on the same patience budget as instability, since it is the same
        # question -- has this page finished becoming itself?
        while snapshot is None and time.monotonic() < deadline:
            self.page.wait_for_timeout(settle_ms)
            snapshot = self._snapshot()

        if snapshot is None:
            # Still nothing to read. Report the page as empty rather than
            # raising: an explorer meets documents between states as a matter of
            # course -- a click that leaves the site, a redirect mid-commit --
            # and one of them must not end a crawl that has already mapped
            # everything before it. Measured 2026-09-05 on
            # practicetestautomation.com, whose "AI Workshop" link leaves for
            # luma.com: five states mapped, then the run died `error`.
            #
            # Empty is also the honest answer. `elements=()` is what the caller
            # already reads as "nothing to act on here", and the crawler's
            # origin check refuses the URL immediately afterwards.
            self._recording = False
            return self._observation("")

        while time.monotonic() < deadline:
            self.page.wait_for_timeout(settle_ms)
            again = self._snapshot()
            if again is None or state_key(again) == state_key(snapshot):
                break
            snapshot = again

        self._recording = False

        return self._observation(snapshot)

    def _observation(self, snapshot: str) -> Observation:
        """The one place an Observation is built.

        Both return paths in `observe` come through here, and that is the point
        rather than a tidiness preference: an Observation carries the page's
        text, its URL and its network events, and anything that has to be true
        of all three -- redacting typed credentials out of them, say -- is a
        property of *this* function instead of a rule each return site has to
        remember. Two construction sites that can drift is the shape of the
        next bug; there is one, so "no Observation exists without X" is a claim
        about six lines.

        An empty `snapshot` is the bodyless page and parses to no elements,
        which is the honest reading: nothing was there to act on.
        """
        return Observation(
            url=self.page.url,
            title=self._title() if snapshot else "",
            snapshot=snapshot,
            elements=parse_snapshot(snapshot) if snapshot else (),
            network=tuple(self._buffer),
        )

    def _snapshot(self) -> str | None:
        """The page's aria snapshot, or None if there is no document to read.

        `body` is absent while a document commits, and Playwright answers that
        with `Locator.aria_snapshot: Selector "body" does not match any
        element`. That is a state of the world, not a fault, so it is returned
        rather than raised -- see `observe`.
        """
        try:
            return self.page.locator("body").aria_snapshot(mode="ai")
        except PlaywrightError:
            return None

    def _title(self) -> str:
        """The title, or empty. Reading it evaluates script in the page, which
        a document being torn down refuses -- and a missing title must not cost
        us a snapshot we already took."""
        try:
            return self.page.title()
        except PlaywrightError:
            return ""
