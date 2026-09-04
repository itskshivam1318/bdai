"""Things in the accessibility tree that are not the application.

Discovered by the probe's stability check failing on its first run: Next.js
injects "Open Next.js Dev Tools" into the page *after* load, so two visits to an
identical page produced two different state keys.

The dev overlay is our own fixture's problem. The category is not -- real targets
inject cookie banners, support-chat bubbles, analytics toolbars and A/B widgets,
all of which appear asynchronously and none of which are part of the app under
test.

Two consumers need this, which is why it is its own module:

    statekey.py   foreign chrome must not change a state's identity
    observer.py   foreign chrome must not enter the frontier, or the crawler
                  spends its budget clicking a cookie banner

This is a **denylist**, so it only removes noise we have already met. An unknown
widget on a new target shows up as an unstable state key and `statekey.explain()`
names it. That is the failure mode we want: loud, and it points at itself.
"""

from __future__ import annotations

import re

# Matched against an element's accessible name.
#
# Extended after a crawl of practicesoftwaretesting.com, where 272 of 382
# frontier actions were furniture: image credits, a support-chat bubble, a
# language picker, links to the project's own GitHub. An explorer that treats
# those as application surface spends its whole budget on other people's
# software -- and worse, they poison `gaps()`, because a states x actions table
# whose alphabet is mostly chrome reports gaps nobody would ever test.
#
# Names only. Everything structural -- off-origin links, duplicates, pagination
# -- is handled in `forms.available_actions`, because those are *provable* and
# this list is only ever a denylist of things we have already met.
FOREIGN_NAMES = (
    # Dev overlays injected into our own fixture.
    re.compile(r"Next\.js Dev Tools"),
    # Consent and cookie banners. Appear asynchronously on most real targets.
    re.compile(r"\bcookies?\b.*\b(accept|reject|allow|manage|settings)\b", re.I),
    re.compile(r"\b(accept|reject|allow|manage)\b.*\bcookies?\b", re.I),
    re.compile(r"^(accept|reject) all$", re.I),
    re.compile(r"\bconsent\b", re.I),
    # Support-chat bubbles.
    re.compile(r"\b(open|start|close)\s+chat\b", re.I),
    re.compile(r"\blive chat\b|\bchat with us\b|\bhelp ?scout\b|\bintercom\b", re.I),
    # Photo credits and attribution links. Never the application.
    re.compile(r"^(unsplash|pexels|barn images|freepik|shutterstock)$", re.I),
    # The project's own meta-links. `Documentation` is a judgement call and it is
    # made deliberately: docs describe the app, they are not the app.
    re.compile(r"^(github( repo)?|source code|documentation|docs)$", re.I),
    # Locale switchers. Re-render the whole app in another language, which
    # produces a state per locale and teaches nothing about behaviour.
    re.compile(r"^(select |choose )?language$", re.I),
)


def is_foreign(name: str) -> bool:
    """True if an accessible name belongs to injected chrome, not the app."""
    return any(pattern.search(name) for pattern in FOREIGN_NAMES)
