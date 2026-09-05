"""State equivalence: "have I seen this page before?"

This is the hard problem, and it is worth knowing that upfront. Crawljax has
been the reference crawler for ~20 years and its equivalence function went
through four generations: Levenshtein distance over serialised DOM, then
comparator pipelines that strip volatile aspects, then fragment-level
equivalence (FragGen: +62% precision, +70% recall), then learned Siamese
embeddings (WebEmbed: +56% F1, 6-21% downstream coverage gain). Ground truth
exists -- Yandrapally/Stocco/Mesbah, ICSE 2020, 493K state-pairs.

We are at generation two, on purpose. Everything the explorer does rests on this
one function, so it is written to make its own mistakes *visible* rather than to
be right:

    too aggressive  -> distinct states collapse; the crawler stops early and
                       reports coverage it does not have
    too permissive  -> the same page looks new every visit; the crawler never
                       terminates and the frontier grows without bound

`explain()` exists to tell those apart when it goes wrong, which it will.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from .noise import is_foreign

# Per-snapshot or per-layout noise. None of this is identity:
#   [ref=e9]    renumbered on every snapshot
#   [box=...]   moves with viewport, font loading, scrollbars
#   [cursor=..] a styling detail
#   [active]    which field has focus -- see below
#
# Valueless flags need naming individually, and the split is deliberate rather
# than "strip every flag". The rule that decides which side a flag falls on:
#
#     App-decided state is identity. User-entered state is not --
#     its *consequences* are.
#
#     strip   active, focused        where the caret is
#             checked, pressed       what the user ticked or toggled
#             selected, expanded     which tab or disclosure the user opened
#
#     KEEP    disabled               the application's own decision about
#                                    whether an action is available at all
#
# **Every flag on the strip list explodes the state space if kept**, and this
# was measured the expensive way. An earlier version kept `checked`, reasoning
# that it changes available actions. Pointed at practicesoftwaretesting.com the
# crawler opened a checklist modal and spent its entire budget ticking boxes:
# 24 of 25 discovered states were the same page with different checkbox
# combinations, and it never reached Favorites, Invoices or Profile. N
# independent checkboxes are 2^N states, which is the same explosion
# `field_value` exists to prevent for text -- the hole was simply left open for
# everything that is not text.
#
# Nothing behavioural is lost. A "I accept the terms" box that gates a submit
# surfaces as `[disabled]` on the button, which is kept. A tab that reveals
# different content changes the element set, which is keyed on regardless. The
# consequence is visible; only the user's own bookkeeping is dropped.
_NOISE = re.compile(
    r"\s*\[(?:(?:ref|box|cursor)=[^\]]*"
    r"|(?:active|focused|checked|pressed|selected|expanded)(?:=[^\]]*)?)\]"
)

# A node's trailing value: `- paragraph [...]: "Cart total: 42"` -> `Cart total: 42`
# Matches only after a role token, so `- /url: /checkout` is left alone.
_VALUE = re.compile(
    r'^(?P<head>\s*-\s+(?P<role>[a-zA-Z][\w-]*)(?:\s+"(?P<name>[^"]*)")?)'
    r'\s*:\s*(?P<value>.+)$'
)

# Accessible name on a node line, e.g. `- button "Sign in" [...]`.
_NAME = re.compile(r'^\s*-\s+[a-zA-Z][\w-]*\s+"(?P<name>(?:[^"\\]|\\.)*)"')

# `- /url: <href>` -- the one place a hyperlink's destination appears. Its
# "role" token is `/url`, which does not start with a letter, so `_VALUE`
# never matches it and it reaches `normalize()` untouched by every projection
# above. That is deliberate for the href itself (`decisions.md` 2026-09-04
# 18:20: "a link pointing somewhere new *is* a different page") but not for a
# session identifier a server stamps into it -- see `_strip_session_id`.
_URL_LINE = re.compile(r"^(?P<head>\s*-\s+/url:\s*)(?P<url>.+)$")

# A server-minted session id embedded in a URL, in either convention:
#   path parameter   /admin.htm;jsessionid=1D058CE7A24E8B98B4893151D889D9A2
#   query parameter  ?PHPSESSID=... or &sid=...
_JSESSIONID = re.compile(r";jsessionid=[^;?#]*", re.IGNORECASE)
_SESSION_PARAM_NAMES = frozenset(
    {"jsessionid", "phpsessid", "sessionid", "session_id", "sid"}
)


def _strip_session_id(url: str) -> str:
    """Drop a server-minted session id from a URL. Not identity.

    Measured on ParaBank: the first load of a fresh session stamps every href
    on the page with `;jsessionid=1D058CE7A24E8B98B4893151D889D9A2` -- a Java
    servlet-container convention, a path parameter rather than a query one.
    Once the browser's cookie is set, later loads of the *identical* page
    drop it. `state_key` then never matches its own entry state again: every
    ant sent there calls `navigate()`, reloads, observes a page whose links no
    longer carry the id it was recorded with, and reports `stuck` before
    taking a single action. Not this page's bug -- ordinary Java session
    bookkeeping, the same class of noise as a renumbered `[ref=e9]`.

    Query parameters are rebuilt rather than regexed in place, so removing the
    first one does not leave a dangling `&` in front of the second.

    **Known risk, taken deliberately.** `sid` is common enough as an
    unrelated business parameter (a store id, a section id) that stripping it
    is a real trade-off, not a free one -- named here because `sid` was the
    one in `_SESSION_PARAM_NAMES` most likely to also mean something else.
    Weighed against the alternative: a party who reads `sid` as identity looks
    "stuck" identically to ParaBank, for the same reason, and that failure
    mode is the one this function exists to close.
    """
    url = _JSESSIONID.sub("", url)

    path, sep, query = url.partition("?")
    if not sep:
        return url

    query, has_fragment, fragment = query.partition("#")
    kept = [
        param
        for param in query.split("&")
        if param.split("=", 1)[0].lower() not in _SESSION_PARAM_NAMES
    ]
    rebuilt = "&".join(kept)
    return (
        path
        + (f"?{rebuilt}" if rebuilt else "")
        + (f"#{fragment}" if has_fragment else "")
    )


# Any run of digits in a node's text. See `canonical_value`.
_DIGITS = re.compile(r"[0-9]+")

# Roles whose trailing text is something the *user* typed, not something the
# *app* rendered. Routed to `field_value`; everything else goes to
# `canonical_value`. The two need opposite policies and conflating them is what
# made an empty form and a filled one two states with unbounded siblings.
FIELD_ROLES = frozenset(
    {"textbox", "searchbox", "spinbutton", "combobox", "slider"}
)


def canonical_value(role: str, name: str, value: str) -> str:
    """Reduce a node's text to the part that counts as identity.

    Called once per node carrying text. Whatever comes back is what the state
    key sees, so returning a constant makes text irrelevant and returning
    `value` unchanged makes every character of copy significant.

    **Digits change when the page has not; words change when it has.** So we
    replace runs of digits with `#` and keep the prose:

        "Total: 42"  and "Total: 84"       -> "Total: #"        one state
        "Order confirmed" / "Payment declined"                  two states

    Both directions of getting this wrong are fatal, which is why it is its own
    function rather than a flag:

        erase too much  ->  "Order confirmed" and "Payment declined" collapse.
                            The crawler believes it already visited the error
                            page, and the demo's coverage-gap beat becomes
                            undiscoverable.

        keep too much   ->  every quantity change spawns a state, the frontier
                            grows without bound, and the crawl never terminates.

    An earlier version of this asked "is this value volatile?" and returned a
    bool. That question has no correct answer for "Total: 42" -- part of the
    string is identity and part is noise. Canonicalising rather than
    keeping-or-dropping is what makes it tractable.

    Known gaps, both of which announce themselves as an unstable key in the
    probe, and both fixed the same way as injected chrome (see `noise.py`):
    text that rotates by word rather than by number (a random quote, a shuffled
    banner), and personalised copy ("Welcome, Alice" vs "Welcome, Bob").
    """
    return _DIGITS.sub("#", value.strip().strip('"'))


def field_value(role: str, name: str, value: str) -> str:
    """Reduce what a user typed into a field to the part that counts as identity.

    The sibling of `canonical_value`, for the roles in `FIELD_ROLES`. Split out
    because app-rendered prose and user-entered input need opposite treatment,
    and the current default here is a deliberate, changeable policy call rather
    than an obvious answer.

    **Current policy: presence, not content.** A field is `filled` or it is
    empty. So:

        name=""            vs  name="My Project"        two states
        name="My Project"  vs  name="Other Project"     one state

    Measured before this existed (`scratchpad/formprobe.py`): typing two
    characters into two fields produced four distinct state keys. Every
    keystroke was a state, and the frontier grew without bound -- the exact
    failure `canonical_value` warns about, arriving through a door it did not
    cover.

    **Why this is a policy and not a fact.** The behaviourally interesting
    partition of a form is `empty | invalid | valid`, because those three have
    different outgoing transitions -- submit from `valid` creates a thing,
    submit from `invalid` does not. Presence collapses `invalid` into `valid`,
    which we get away with *only* because a rejected form renders an error node,
    and that node splits the state on its own. Verified: an added
    `role=alert` changes the key.

    That leaves one real hole. A form the app rejects **silently** -- inline
    styling, an aria-invalid attribute, a disabled submit with no message --
    looks identical to a valid one here, so "submit invalid input" becomes
    undiscoverable and the brief's "not just happy paths" requirement loses a
    case. Closing it means reading validity off the node's own attributes
    (`[invalid]`, `[required]` with an empty value) rather than off a sibling
    alert.

    Change this function to change that trade-off. Nothing else needs to move.
    """
    return "filled" if value.strip().strip('"') else ""


def collapse_runs(lines: list[str]) -> list[str]:
    """Replace a run of identical consecutive lines with one line and a bucket.

    **How many of a thing there are is almost never behaviour.** A dashboard
    with 17 projects and the same dashboard with 18 offer the same actions and
    lead to the same places; treating them as two states means every row a user
    adds spawns a node and the crawl never ends. Once `canonical_value` has
    turned "Project 17" into "Project #", sibling rows are byte-identical
    consecutive lines, so their *count* is the only thing left separating the
    two snapshots -- and the count is what has to go.

    Buckets are `{1, many}`; zero falls out for free, because no items means no
    line at all. That keeps the one boundary that *is* behaviour: an empty
    collection usually renders a different affordance ("Create your first
    project") from a populated one. Beyond two, nothing changes.

    Measured before this existed (`scratchpad/formprobe.py`): 17 items and 18
    items produced different state keys, and `explain()` reported the difference
    as an empty diff.

    **Known limit.** Run-length works on single lines, so a flat list collapses
    but an interleaved multi-line structure (`row / cell / cell` repeated) does
    not -- `A B A B A B` has no identical neighbours. That is the remaining path
    to a count-sensitive key. It errs toward too *fine*, which surfaces as a
    frontier full of states with identical action sets; the opposite error would
    be silent.
    """
    collapsed: list[str] = []
    index = 0

    while index < len(lines):
        end = index
        while end < len(lines) and lines[end] == lines[index]:
            end += 1
        collapsed.append(
            lines[index] if end - index == 1 else f"{lines[index]}  xmany"
        )
        index = end

    return collapsed


_LISTITEM = re.compile(r"^\s*- listitem\b")


def _depth(line: str) -> int:
    return len(line) - len(line.lstrip())


def collapse_siblings(lines: list[str]) -> list[str]:
    """Replace a run of sibling `listitem` subtrees with one bucket line.

    `collapse_runs` folds *byte-identical* neighbours, which works because
    `canonical_value` rewrites "Project 17" into "Project #" first and leaves
    the rows literally the same. A list whose labels are user data defeats
    that: session names, hostnames, filenames canonicalise to nothing alike,
    no run forms, and the count survives into the key.

    That is only a nuisance until the crawler can *write* to the list. Then the
    list is downstream of the agent's own actions, every submit re-keys every
    page, and the walk maps its own footprints. Measured: crawling the console
    at :3000 gave 78 states and 5 transitions, 31 of them the same URL,
    separated only by how many sessions the crawl had created by the time it
    arrived. It never reached Settings or the canvas.

    So the labels have to go, and only inside the container. Scoped to
    `listitem` because that is what a list actually emits -- the console and
    thetestingmap both do, saucedemo has no list at all and is untouched --
    and because a role we did not ask for keeps its name. A bare `generic`
    sibling run stays as it is; collapsing those would hide real controls.

    Keeping the first item's name would not have worked: the console prepends,
    so the name in position one changes every time somebody adds a row.

    Buckets are `{1, many}`, the same boundary `collapse_runs` keeps and for
    the same reason -- an empty collection renders a different affordance from
    a populated one, and one item is a real design a list of many is not. A
    single item keeps its subtree, and recursion collapses any list nested
    inside it.
    """
    out: list[str] = []
    index = 0

    while index < len(lines):
        if not _LISTITEM.match(lines[index]):
            out.append(lines[index])
            index += 1
            continue

        # Walk the run of siblings: each `listitem` at this depth, plus
        # everything indented under it, is one item.
        depth = _depth(lines[index])
        start, count, end = index, 0, index
        while end < len(lines) and _LISTITEM.match(lines[end]) and _depth(lines[end]) == depth:
            count += 1
            end += 1
            while end < len(lines) and _depth(lines[end]) > depth:
                end += 1

        if count == 1:
            out.append(lines[start])
            out.extend(collapse_siblings(lines[start + 1 : end]))
        else:
            out.append(f"{' ' * depth}- listitem  xmany")
        index = end

    return out


_ROLE = re.compile(r"^(?P<indent>\s*)-\s+(?P<role>[a-zA-Z][\w-]*)")


def _shape(block: list[str]) -> tuple:
    """A subtree's structure with every name and value discarded."""
    return tuple(
        (_depth(line) - _depth(block[0]), m.group("role") if (m := _ROLE.match(line)) else "")
        for line in block
    )


def _blocks(lines: list[str], index: int) -> list[tuple[int, int]]:
    """Consecutive sibling subtrees starting at `index`, as (start, end) spans."""
    depth = _depth(lines[index])
    spans, cursor = [], index
    while cursor < len(lines) and _depth(lines[cursor]) == depth:
        end = cursor + 1
        while end < len(lines) and _depth(lines[end]) > depth:
            end += 1
        spans.append((cursor, end))
        cursor = end
    return spans


def anonymise_rows(lines: list[str]) -> list[str]:
    """Drop the names inside runs of structurally identical sibling subtrees.

    `collapse_siblings` argues that the labels in a list are the user's data
    and not the application's behaviour, and scopes itself to `listitem`
    because "saucedemo has no list at all and is untouched". Measured
    2026-09-05, that scope is where the next explosion came from: saucedemo
    renders its six products as sibling `generic` divs, and each row's button
    flips between `Add to cart` and `Remove` as the cart changes. One product
    page keyed six different ways; **10 of 21 crawler states and 10 of 15
    colony states were `/inventory.html`** differing only in cart contents.

    Which items I have put in a cart is the purest case of the rule this module
    is built on -- *app-decided state is identity, user-entered state is not,
    its consequences are*. It is `checked` wearing a button's name instead of a
    flag, which is why stripping `checked` did not reach it.

    The consequence survives, which is what makes dropping the cause safe: an
    empty cart renders no badge and a non-empty one renders `generic: #`,
    outside the row group and untouched here. Empty-vs-populated -- the one
    boundary `collapse_runs` and `collapse_siblings` both protect -- is still
    identity. Only *which* rows and *how many* stop being.

    **Two guards, and both are load-bearing.** `collapse_siblings` warns that
    collapsing a bare `generic` run "would hide real controls", and it is
    right, so this is stricter than that function in the way that matters:

        identical shape   siblings are a group only if their subtrees have the
                          same roles at the same depths. A row carrying a
                          control its neighbours lack has a different shape,
                          falls out of the group, and keeps its name -- which
                          is exactly the page-swallowing case the grid pins.

        more than one     a single subtree is not a repetition, and a one-line
        line              sibling is a control, not a row. A toolbar of six
                          differently-named buttons is six one-line blocks and
                          is left alone.
    """
    out: list[str] = []
    index = 0

    while index < len(lines):
        spans = _blocks(lines, index)
        shapes = [_shape(lines[a:b]) for a, b in spans]

        run_end = 1
        while (
            run_end < len(spans)
            and shapes[run_end] == shapes[0]
        ):
            run_end += 1

        first_len = spans[0][1] - spans[0][0]
        if run_end > 1 and first_len > 1:
            for a, b in spans[:run_end]:
                out.append(lines[a])
                out.extend(
                    _NAME.sub(lambda m: m.group(0)[: m.start("name") - m.start() - 1].rstrip(), line)
                    if _NAME.match(line)
                    else line
                    for line in anonymise_rows(lines[a + 1 : b])
                )
            index = spans[run_end - 1][1]
            continue

        a, b = spans[0]
        out.append(lines[a])
        out.extend(anonymise_rows(lines[a + 1 : b]))
        index = b

    return out


def normalize(snapshot: str, *, keep_values: bool = False) -> str:
    """Reduce a snapshot to the part we treat as identity.

    Five projections, applied in this order. Each answers one question, and
    each got its answer from a measurement rather than from taste:

        _NOISE            is focus identity?          no; disabled/checked are
        canonical_value   is rendered text identity?  the prose is, digits are not
        field_value       is typed input identity?    presence is, content is not
        _strip_session_id is a URL's session id identity?  no, only its destination is
        collapse_runs     is *how many* identity?     no, past one-vs-several
        collapse_siblings   ... when the rows are not alike?  still no

    Also gone: injected chrome that is not the application (`noise.py`), and the
    trailing `:` that marks a node as having children.

    What survives is roles, accessible names, tree shape, and text with its
    numbers masked -- so "Total: 42" and "Total: 84" are one state while
    "Order confirmed" and "Payment declined" are two.

    Set `keep_values=True` to skip both text projections, which is the fastest
    way to tell "the key never changes" from "the key changes constantly".
    """
    lines: list[str] = []

    for raw in snapshot.splitlines():
        if not raw.strip():
            continue

        line = _NOISE.sub("", raw).rstrip()

        name = _NAME.match(line)
        if name and is_foreign(name.group("name")):
            continue

        if not keep_values:
            value = _VALUE.match(line)
            if value:
                # The node always survives -- a paragraph that exists is
                # structure. Only its text is projected, and by which of the two
                # projections depends on who wrote the text: the app, or the user.
                role = value.group("role")
                project = field_value if role in FIELD_ROLES else canonical_value
                text = project(role, value.group("name") or "", value.group("value"))
                line = f'{value.group("head")}: {text}'

            url_line = _URL_LINE.match(line)
            if url_line:
                line = url_line.group("head") + _strip_session_id(url_line.group("url"))

        # `- list:` means "children follow" and `- list` means "none do" -- but
        # the following lines already say that, and on a field whose value we
        # just projected to nothing the colon is left-over punctuation. Neither
        # case is identity.
        lines.append(line.rstrip().removesuffix(":").rstrip())

    return "\n".join(collapse_runs(collapse_siblings(anonymise_rows(lines))))


def state_key(snapshot: str, *, keep_values: bool = False) -> str:
    """A stable, short identity for the state this snapshot represents."""
    digest = hashlib.sha256(
        normalize(snapshot, keep_values=keep_values).encode("utf-8")
    )
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class KeyDiff:
    """Why two snapshots did or did not collapse to the same state.

    `reordered` matters as much as the content diff. Two snapshots holding the
    same elements in a different order are a *different* kind of problem from
    two holding different elements: the first usually means the app moved
    something (or rendered non-deterministically), the second means we are
    genuinely somewhere else. Reporting both as "different state" with no lines
    listed -- which a set-difference alone does -- is a useless diagnosis.

    The diff is a **multiset** difference, not a set difference. That is not a
    detail. A list of 17 items and the same list with 18 hold an identical *set*
    of normalised lines and differ only in how many times one line repeats, so a
    set difference reports "different state" and then names nothing -- exactly
    the useless diagnosis this docstring warns about, on the single most likely
    failure of `normalize`. Counting occurrences names it: `+ only in B:
    listitem: Project #`.
    """

    same: bool
    reordered: bool
    only_in_a: tuple[str, ...]
    only_in_b: tuple[str, ...]

    def __str__(self) -> str:
        if self.same:
            return "same state"
        if self.reordered:
            return "different state: same elements, different order"

        out = ["different state"]
        out += [f"  - only in A: {line.strip()}" for line in self.only_in_a[:10]]
        out += [f"  + only in B: {line.strip()}" for line in self.only_in_b[:10]]
        extra = (len(self.only_in_a) + len(self.only_in_b)) - 20
        if extra > 0:
            out.append(f"  ... {extra} more")
        return "\n".join(out)


def _excess(lines: list[str], other: list[str]) -> tuple[str, ...]:
    """Lines of `lines` not covered by `other`, counting repeats, in order.

    `["x", "x", "y"]` against `["x", "y"]` yields `("x",)` -- one unmatched copy
    -- where a set difference yields nothing at all.
    """
    budget = Counter(other)
    surplus: list[str] = []

    for line in lines:
        if budget[line]:
            budget[line] -= 1
        else:
            surplus.append(line)

    return tuple(surplus)


def explain(snapshot_a: str, snapshot_b: str, *, keep_values: bool = False) -> KeyDiff:
    """Show what separates two states. The debugging tool for this module.

    When the crawler loops forever, run this on two snapshots it thinks differ:
    whatever shows up is the volatile thing `normalize` failed to strip.
    """
    a = normalize(snapshot_a, keep_values=keep_values).splitlines()
    b = normalize(snapshot_b, keep_values=keep_values).splitlines()
    same = a == b

    return KeyDiff(
        same=same,
        reordered=not same and sorted(a) == sorted(b),
        only_in_a=_excess(a, b),
        only_in_b=_excess(b, a),
    )
