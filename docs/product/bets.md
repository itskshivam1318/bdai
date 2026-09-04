# Bets

An evidence gate, not an approval gate. Before spending another hour, what
would tell us we're wrong? Append only — never delete a lost bet, it is the
cheapest thing in the repo.

Ordered by how much they'd cost us if we discover them late.

---

## BET-001 — Can we log in at all?

**Hypothesis:** the target app has an ordinary HTML login that Playwright can
drive with the given username and password.

**Why it matters:** if it is SSO, MFA, a captcha, or a WebAuthn flow, *nothing
downstream runs* and the entire architecture changes. This is the only bet that
can zero the project.

**Fastest test:** the moment we have the URL, point Playwright at it and log in.
Ten minutes, before any design work.

**Success looks like:** authenticated session, a cookie, a page behind the wall.

**Timebox:** 15 minutes. If it fails, stop everything and re-plan.

**Decision:** PENDING

---

## BET-002 — Is intent→selector compilation actually more robust than a stored selector?

**Hypothesis:** re-resolving a semantic descriptor (role + accessible name +
page context) against a changed DOM beats a stored CSS selector, and beats
asking an LLM to guess a replacement.

**Why it matters:** this is the whole thesis. If it is not clearly better, we
are building a normal test generator and should say so and simplify.

**Fastest test:** three-way comparison on `web/app/sut/?v=1 → v=2 → v=3`, which
already exists and already drifts. Stored selector vs. LLM-guess vs.
descriptor re-resolution, across the ~6 elements on that page.

**Success looks like:** descriptor re-resolution recovers strictly more
elements than stored selectors, and is no worse than the LLM guess while being
faster and explainable.

**Timebox:** 30 minutes. Runs against the existing SUT — needs no target app,
so it can start immediately and in parallel with everything else.

**Decision:** PENDING

---

## BET-003 — Can exploration find enough of the app to write non-trivial tests?

**Hypothesis:** an autonomous crawl from one logged-in entry point discovers
enough flows that the generated tests look like a QA engineer's work, not a
smoke test that clicks every link.

**Why it matters:** if the crawl only yields "page loads, title matches", the
demo is boring regardless of how good the healing is. Beats 2–4 collapse.

**Fastest test:** crawl any comparable public app behind a login and count
distinct multi-step flows found in 5 minutes of crawling.

**Success looks like:** at least 3 flows with 3+ steps that a human would call
a real user journey.

**Timebox:** 45 minutes.

**Decision:** PENDING

---

## BET-004 — Does the live-DOM-mutation demo read as impressive or as cheating?

**Hypothesis:** mutating the live DOM to simulate a release makes beat 5
land harder than using our own SUT.

**Why it matters:** it is the difference between the best 15 seconds of the
demo and a credibility problem in Q&A.

**Fastest test:** show it to someone outside the team and watch their face.
Ask them to say what they think just happened, unprompted.

**Success looks like:** they describe it as "the app changed and the tests kept
working", not "you changed your own test".

**Timebox:** 10 minutes, once beat 5 works at all.

**Decision:** PENDING

---

## BET-005 — Do we have a target rich enough for the crawler to say anything?

**Hypothesis:** a public demo app with real multi-step flows (log in → list →
create → detail → error) yields a World Map whose transitions read as a
meaningful test plan, and whose `gaps()` name error states a QA engineer would
actually write.

**Why it matters:** measured 2026-09-04, the crawler maps our own SUT in full —
**3 states, 18 transitions, frontier empty** — and the result is correct and
useless. The SUT is one page served three ways; it was built for the *healing*
demo (locators drift across `?v=1|2|3`) and healing and exploration want
opposite fixtures. Healing needs one page that changes; exploration needs many
pages that connect. So the SUT cannot answer BET-003, and every mechanism above
the crawler — gap ranking, flow naming, test generation — currently has nothing
to bite on. This is now the binding constraint on the demo, not the code.

**Fastest test:** point `python -m agents.explorer.crawler <url>` at each
candidate for 3 minutes and read `world.summary()`.

Candidates, in order:
1. **Conduit / RealWorld** (`demo.realworld.io`) — Medium clone. Register, log
   in, publish, comment, favourite, follow. Self-hostable if the demo is flaky.
2. **saucedemo.com** — Swag Labs. Small, but `locked_out_user` is a real error
   state and checkout has real validation, which is what the brief's "not just
   happy paths" needs.
3. **OWASP Juice Shop** — richest and self-hostable, but gamified in ways that
   will waste time.

**Success looks like:** ≥8 states, ≥3 flows of 3+ steps that a human would call
a user journey, and at least one `nondeterministic()` edge — because that would
mean the app is rich enough to expose a projection error, which is itself the
evidence that the refinement loop is worth building.

**Keep the SUT regardless.** It is the healing demo and it works. Two fixtures,
two jobs.

**Measured 2026-09-04 19:40, against two public targets, via the MCP server's
`crawl` and `verify`.** Neither target clears the bar, and the reason is not the
targets.

| Target | States | Transitions | Flows | Longest flow |
|---|---|---|---|---|
| `practicetestautomation.com/practice-test-login/` | 10 | 11 | 8 | 2 steps |
| `testingchallenges.thetestingmap.org` | 6 | 15 | 5 | 2 steps |

The bar was "≥3 flows of 3+ steps that a human would call a user journey".
**Nothing reached 3 steps.** Both crawls spent their whole budget on site chrome
— Home, Courses, Blog, Contact, Privacy Policy — because `frontier()` orders
untried actions without preferring the ones that carry state. On a marketing
site wrapped around an app, that is most of the budget.

Two distinct causes, worth separating:

1. **Breadth beats depth on real sites.** A journey is a *path*, and BFS
   collects fan-out. `testingchallenges` registered all three form actions on
   the entry state (`submit[empty|valid|invalid]`) and took only `empty` before
   the budget ran out; the other two are sitting in `gaps()`. More budget would
   have found them. This is a **frontier-ordering** problem, and it is the
   strongest argument yet for the agent colony over the deterministic crawler —
   deciding what is worth trying next is exactly what an ant is for.

2. **`forms.form_of` requires a `<form>` element, and real pages often have
   none.** `practicetestautomation.com/practice-test-login/` has
   `document.querySelectorAll('form').length === 0` — the login is two bare
   inputs and a button. The observer sees `textbox:Username`,
   `textbox:Password`, `button:Submit` and the crawler clicks Submit with
   nothing filled in, forever. The `<form>` ancestry test is there for a
   documented reason (`forms.py:124` — without it, `Sign in with Google` gets
   filled and leaves the origin), so this is a **real trade-off, not an
   oversight**: the current rule has no false positives and this class of false
   negative. Unresolved; see the note in `app/CLAUDE.md`.

**What did work**, and is worth keeping in view: `verify` replayed four
navigation flows against `practicetestautomation.com` and returned 4×`passed`
with 19–183 recorded effects per state — no false positives on pages a naive DOM
comparison would flag wholesale. And on `testingchallenges` it returned a
`defect` for the Hall of fame link, correctly: that page is currently serving
`Failed to connect to database: php_network_getaddresses`. The locator resolved
exactly, so the classifier refused to heal it and called it a defect. That is
the product claim working on an unrehearsed third-party app.

**RESOLVED 2026-09-04 19:50 against `saucedemo.com`. BET-005 passes, and the
frontier-ordering diagnosis above was wrong.**

| | States | Transitions | Flows ≥3 steps | Nondeterministic |
|---|---|---|---|---|
| Bar | ≥8 | — | ≥3 | ≥1 |
| saucedemo | **19** | **24** | **3** | 0 |

It crossed the login wall on the first try (`submit[valid]:button:Login` →
`/inventory.html`) and went on to item pages, the burger menu and add-to-cart.
The longest journey is four steps: *log in → open an item → open the menu →
follow All Items*. Only the nondeterministic-edge criterion is unmet, and that
one was always a bonus.

**Frontier ordering was never the problem.** `crawler.py:_priority` already
ranks `submit[valid]` ahead of every link at equal depth, with a comment saying
exactly why ("a login wall is the highest-value edge in any app"). The rule is
right and it fired correctly on saucedemo. On the earlier two targets it never
got the chance, because the edge was **disqualified before ordering applied**:

    forms.perform -> fill_form typed nothing -> returns False
                  -> crawler adds (state, action) to `skipped`, forever

Measured, per target, with `forms.form_of` and `forms.fill_form` directly:

| Target | `form_of` | Fields the observer named | `fill_form` |
|---|---|---|---|
| `saucedemo.com` | found | `textbox:Username`, `textbox:Password` | **2 typed** |
| `testingchallenges` | found | four `textbox:` with **empty names** | **0 typed** |
| `practicetestautomation` | **None** — page has no `<form>` | `textbox:Username`, `textbox:Password` | n/a |

So there are two distinct failure modes, and neither is about budget or
ordering:

1. **No `<form>` element** — `form_of` returns None, no form action is ever
   synthesised. (practicetestautomation)
2. **Fields with no accessible name** — the form is found, but `fill_form`
   cannot decide what to type into an anonymous textbox, types nothing, and the
   action is disqualified. (testingchallenges)

Both end the same way: the single highest-value edge is dropped, the budget then
drains into nav links, and the run *looks* like a shallow-target problem. It is
not. **A crawl that cannot type is a crawl that cannot leave the front page.**

**The missing end statement is not a stopping rule — it is a reason.** The loop
ends at `if not pending: break`, which is reached both when the app is genuinely
exhausted and when everything left is in `skipped`. Those are opposite
outcomes reported identically, and `skipped` is an opaque `set` that records no
reason. Nothing downstream — `summary()`, the console, the MCP `map` tool — can
tell "this app has no more to offer" from "we gave up on its login form".

Cheapest fix, and the one to do first: make `skipped` a
`dict[(state, action), reason]`, and report it. It turns the failure that cost
two of three targets from invisible into a line of output. A retry policy for
the transient reasons (replay failed) versus the permanent ones (off-origin) is
a second, separable step — and unnecessary until the reasons are visible.

**Timebox:** 30 minutes. Unblocked — needs no API key and no target from the
organiser.

**Decision:** PENDING
