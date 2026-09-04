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

**Timebox:** 30 minutes. Unblocked — needs no API key and no target from the
organiser.

**Decision:** PENDING
