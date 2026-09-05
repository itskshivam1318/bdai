"""Execute a generated scenario, and let the failure classify itself.

    cd app/api && uv run python -m agents.runner http://localhost:3000/sut

The brief's hardest requirement is "distinguish a broken test script from a
genuine application defect", and the research says why it is hard: Google
measured **84% of Pass->Fail transitions as flaky**, and only 1.23% of tests
ever caught a real breakage. Against that base rate, an agent that answers
"script problem" every time scores well and knows nothing. So the answer here is
not a classifier that is asked to be right -- it is two *independent* observable
signals whose combination leaves nothing to guess:

    did the control resolve?        a question about the page's markup
    did the expected thing happen?  a question about the app's behaviour

Those are orthogonal, and crossing them gives the whole taxonomy:

                    | expectation met      | expectation missed
    ----------------+----------------------+---------------------------
    resolved as-is  | PASSED               | DEFECT
    resolved healed | HEALED               | ESCALATE
    did not resolve | --                   | ESCALATE

**DEFECT is the cell that earns the design.** The locator resolved, so nothing
about the test is stale; the click landed, so nothing about the environment is
broken; and the application did something other than what it did when the
explorer watched it. There is no repair to make and a healer that "fixes" this
by finding a different button is manufacturing a green run over a real bug --
which is the failure mode Functionize names as the healing invariant: *healing
cannot override a failed verification.*

**ESCALATE is the cell nobody ships.** `docs/research/README.md` surveyed ~25
products and found policy-level escalation in none of them. When we healed the
locator *and* the outcome changed, both variables moved at once and the run is
genuinely unattributable -- so it says so, with both diffs attached, instead of
picking the answer that makes the dashboard greener.

Healing itself is deliberately dull; see `resolve()`. It is a ladder of
observable rungs, and the rung that fires is recorded, because "healed by
structural match" and "healed by name similarity" are different amounts of
trust and a report that hides which one happened is not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from playwright.sync_api import Page

from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observation, Observer, goto
from .explorer.statekey import explain, state_key
from .generator import Scenario, Step

_FORM_ACTION = re.compile(r"^submit\[(?P<mode>\w+)\]:(?P<descriptor>.+)$")

PASSED, HEALED, DEFECT, ESCALATE = "passed", "healed", "defect", "escalate"

# How close two accessible names must be before similarity alone is allowed to
# justify a repair, and how far clear of the runner-up. Both are conservative:
# the cost of a wrong heal is a green run over a real bug, and the cost of
# refusing is an escalation that a human resolves in ten seconds.
_SIMILAR_ENOUGH = 0.55
_CLEAR_MARGIN = 0.15


@dataclass(frozen=True)
class Resolution:
    """Which control this step will act on, and how we decided that."""

    action: str | None
    rung: str  # 'exact' | 'structural' | 'similarity' | 'unresolved'
    detail: str

    @property
    def healed(self) -> bool:
        return self.action is not None and self.rung != "exact"


@dataclass(frozen=True)
class StepResult:
    step: Step
    verdict: str
    resolution: Resolution
    detail: str
    diff: str = ""  # the KeyDiff between what was expected and what happened
    actual_key: str = ""
    moved: bool = False
    missing: tuple[str, ...] = ()  # recorded effects that did not reappear


@dataclass
class Result:
    scenario: Scenario
    target_url: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """The worst thing that happened. Severity order, not last-write-wins."""
        order = [ESCALATE, DEFECT, HEALED, PASSED]
        for verdict in order:
            if any(step.verdict == verdict for step in self.steps):
                return verdict
        return PASSED

    @property
    def healed_steps(self) -> tuple[StepResult, ...]:
        return tuple(s for s in self.steps if s.resolution.healed)


def _parts(action: str) -> tuple[str | None, str, str]:
    """(form mode | None, role, name) for an action string."""
    form = _FORM_ACTION.match(action)
    descriptor = form.group("descriptor") if form else action
    role, _, name = descriptor.partition(":")
    return (form.group("mode") if form else None), role, name


def resolve(page: Page, step: Step, here: Observation) -> Resolution:
    """Find the control this step means, on the page as it is now.

    A ladder, most trustworthy rung first. Each rung is an observable fact about
    the live page, not an opinion about it, and the rung that fired is reported
    so a reader can weigh the repair rather than take it on faith.

    **exact** -- the recorded action is still offered. No healing happened and
    the step is eligible to report a defect.

    **structural** -- one candidate has the same kind (plain click vs form
    submit), the same role, and the form it submits still has the same fields.
    On the SUT that is the whole of the drift story: `button:Sign in` becomes
    `button:Log in` and it is still the only button in a form of Email +
    Password. This rung needs no model and no similarity metric, and it is the
    one that should fire on a real rename.

    **similarity** -- several structural candidates, one of which reads like the
    recorded name. This is the guess, and it is gated twice: a floor on how
    alike the names are, and a margin over the runner-up, so a page with two
    equally plausible buttons escalates instead of coin-flipping.

    Deliberately not here: a model call. Ranking candidates is the documented
    model seam (`research/README.md`: judges rank reliably and score
    unreliably), but every rung above already produces evidence, and a model
    added at the bottom would only ever speak when the deterministic rungs have
    already failed -- which is precisely when its answer would be least
    checkable. It belongs above `escalate`, not above `structural`.
    """
    available = forms.available_actions(page, here)

    if step.action in available:
        return Resolution(step.action, "exact", "the recorded control is still here")

    want_mode, want_role, want_name = _parts(step.action)
    fields_now = forms.fields_of(here)

    candidates = []
    for action in available:
        mode, role, name = _parts(action)
        if mode != want_mode or role != want_role:
            continue
        if want_mode is not None and sorted(fields_now) != sorted(step.fields):
            # A form submit whose form no longer has the same fields is not the
            # same step. Healing onto it would silently retarget the test.
            #
            # Compared as a *set*, because field order is markup. The SUT's v2
            # renders Password before Email for exactly this reason, and an
            # ordered comparison here refused to heal a form that had not
            # changed in any way a user could perceive.
            continue
        candidates.append((action, name))

    if not candidates:
        return Resolution(
            None,
            "unresolved",
            f"nothing on this page plays the part of {step.action!r}: "
            f"offered {list(available)[:6]}",
        )

    if len(candidates) == 1:
        action, name = candidates[0]
        return Resolution(
            action,
            "structural",
            f"{want_role} {want_name!r} -> {name!r}: the only {want_role} "
            f"of its kind here, and the form still has {len(fields_now)} matching fields",
        )

    scored = sorted(
        ((SequenceMatcher(None, want_name.lower(), name.lower()).ratio(), action, name)
         for action, name in candidates),
        reverse=True,
    )
    best, runner_up = scored[0], scored[1]

    if best[0] >= _SIMILAR_ENOUGH and (best[0] - runner_up[0]) >= _CLEAR_MARGIN:
        return Resolution(
            best[1],
            "similarity",
            f"{want_name!r} -> {best[2]!r} at {best[0]:.2f}, "
            f"clear of {runner_up[2]!r} at {runner_up[0]:.2f}",
        )

    return Resolution(
        None,
        "unresolved",
        f"{len(candidates)} candidates and none clearly {want_name!r}: "
        f"{[name for _, _, name in scored[:4]]}",
    )


def _met(step: Step, moved: bool, added: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    """Did the app do what it did last time? Returns (met, what is missing).

    Two checks, both relative to this run's own before/after pair so that markup
    drift cancels out:

    **moved** -- whether the state changed at all. Structural, and the signal
    that catches the SUT's injected defect: a completed form that used to reach
    a confirmation now returns the form, and no amount of locator repair will
    change that.

    **added** -- every line the action introduced last time introduced again. A
    subset check, not equality: an app is allowed to render *more* than it did
    (a new banner, a flash message) without that being a regression, but the
    effect we recorded has to still be there.
    """
    if moved != step.expect.moved:
        return False, ()
    missing = tuple(line for line in step.expect.added if line not in added)
    return not missing, missing


def run(
    page: Page,
    scenario: Scenario,
    target_url: str | None = None,
    credentials: Credentials | None = None,
    synthesizer=None,
    on_event=None,
) -> Result:
    """Execute one scenario and classify every step.

    `target_url` overrides where the scenario starts, and that override *is* the
    drift experiment: the same recorded scenario replayed against
    `/sut?v=2` (markup moved, behaviour identical) must heal, and against
    `/sut?bug=1` (markup identical, behaviour moved) must report a defect. One
    scenario, two knobs, opposite verdicts -- which is the only way to show the
    classification is doing work rather than always answering the same thing.

    Execution stops at the first step that cannot proceed. A scenario is a
    sequence, and continuing past a step that did not happen would run the rest
    from a state the test never meant to be in and attribute the resulting mess
    to the wrong step.
    """
    credentials = credentials or Credentials.from_env()
    observer = Observer(page)
    url = target_url or scenario.target_url
    result = Result(scenario=scenario, target_url=url)

    def emit(message: str, level: str = "info") -> None:
        if on_event:
            on_event(level, message)

    observer.start_window()
    # `goto` carries its own retry budget -- see `explorer.observer.goto`.
    if not goto(page, url):
        detail = (
            f"{url} could not be reached at all after retrying, so no step "
            "was attempted -- this is a fact about reaching the target, not "
            "about whether the application behaved as recorded. A human has "
            "to say what this run now means."
        )
        resolution = Resolution(action=None, rung="unresolved", detail=detail)
        result.steps.append(StepResult(scenario.steps[0], ESCALATE, resolution, detail))
        emit(f"could not replay {scenario.name!r}: {detail}", level="error")
        return result
    here = observer.observe()
    emit(f"replaying {scenario.name!r} against {url}")

    for step in scenario.steps:
        resolution = resolve(page, step, here)

        if resolution.action is None:
            detail = (
                "the step cannot be attempted at all: no control here plays the "
                "recorded part, so there is nothing to observe and nothing to "
                "classify. A human has to say what this step now means."
            )
            result.steps.append(StepResult(step, ESCALATE, resolution, detail))
            emit(f"escalate: {step.intent} -- {resolution.detail}", "error")
            break

        if resolution.healed:
            emit(
                f"healed [{resolution.rung}]: {step.intent} -- {resolution.detail}",
                "decision",
            )

        before = here
        observer.start_window()
        performed = forms.perform(
            page, resolution.action, before, credentials, synthesizer,
            state_key(before.snapshot),
        )
        if not performed:
            detail = (
                f"resolved {resolution.action!r} but the action would not "
                "execute -- the control is present and inert"
            )
            result.steps.append(StepResult(step, ESCALATE, resolution, detail))
            emit(f"escalate: {step.intent} -- {detail}", "error")
            break

        here = observer.observe()
        diff = explain(before.snapshot, here.snapshot)
        added = tuple(
            line for line in diff.only_in_b if not line.lstrip().startswith("- /")
        )
        moved = not diff.same
        met, missing = _met(step, moved, added)
        actual_key = state_key(here.snapshot)

        if met:
            verdict = HEALED if resolution.healed else PASSED
            detail = (
                f"the app did what it did when this was recorded "
                f"({'moved' if moved else 'stayed put'}, "
                f"{len(step.expect.added)} recorded effects present)"
            )
        elif resolution.healed:
            verdict = ESCALATE
            detail = (
                "the locator was repaired AND the outcome changed, so the two "
                "cannot be told apart from one run: either the repair retargeted "
                "the test or the app regressed. Both diffs are attached."
            )
        else:
            verdict = DEFECT
            detail = (
                f"the control resolved exactly and the click landed, but the app "
                f"{'moved somewhere else' if moved else 'stayed put'} where it "
                f"previously {'moved' if step.expect.moved else 'stayed put'}. "
                "Nothing here is repairable -- there is no broken locator."
            )

        result.steps.append(
            StepResult(
                step, verdict, resolution, detail,
                diff=str(diff), actual_key=actual_key, moved=moved, missing=missing,
            )
        )
        emit(f"{verdict}: {step.intent} -- {detail}", "decision" if met else "warn")

        if verdict in {DEFECT, ESCALATE}:
            break

    emit(f"{scenario.name!r}: {result.verdict}", "decision")
    return result


def render(result: Result) -> str:
    """The run as evidence a human can read. What the final report is built from."""
    lines = [
        f"{result.verdict.upper():<9} {result.scenario.name}",
        f"          against {result.target_url}",
    ]
    for step in result.steps:
        lines.append(f"  [{step.verdict}] {step.step.intent}")
        lines.append(f"      resolved: {step.resolution.rung} -- {step.resolution.detail}")
        lines.append(f"      {step.detail}")
        if step.missing:
            lines.append("      recorded effects that did not reappear:")
            lines += [f"        - {line.strip()}" for line in step.missing[:5]]
        if step.verdict in {DEFECT, ESCALATE} and step.diff:
            lines.append("      what actually happened:")
            lines += [f"        {line}" for line in step.diff.splitlines()[:8]]
    return "\n".join(lines)


def main(entry_url: str) -> int:
    """The acceptance experiment: one crawl, one scenario, every verdict it has.

    Needs `make dev`. Spends no quota -- nothing in this path calls a model.

    The targets come from `pipeline.fixture_variants` rather than from string
    concatenation here, and that is a fix, not a tidy-up. This function used to
    append `?v=2` and `?bug=1` to whatever URL it was given; pointed at a real
    app those are query parameters it ignores, so two of the three runs
    re-tested a byte-identical page and the output labelled them "markup drift"
    and "injected defect". Nothing failed, so nothing looked wrong. Against a
    third-party URL there are now no extra runs and the baseline stands alone,
    which is the honest answer.
    """
    from playwright.sync_api import sync_playwright

    from .explorer.crawler import Budget, crawl
    from .generator import scenarios

    # Local: `pipeline` imports this module at module level, so importing it
    # back at the top would be a cycle. This is a CLI entry point and the file
    # already imports its other collaborators here for the same reason.
    from .pipeline import fixture_variants

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Test and staging targets routinely serve self-signed or expired certs;
        # refusing them would make the agent useless on its own target market. The
        # run still reports that transport security was not verified -- see
        # `_tls_warning`.
        page = browser.new_page(ignore_https_errors=True)

        world = crawl(page, entry_url, Budget(max_actions=40, max_seconds=180))
        plan = scenarios(world)
        happy = next(
            (s for s in plan if s.terminal.action.startswith("submit[valid]")),
            plan[0] if plan else None,
        )
        if happy is None:
            print("no scenario generated -- nothing to run")
            return 1

        print(f"scenario: {happy.name}\n")

        # Labels are positional against `fixture_variants`, which returns the
        # SUT's knobs in a fixed order and an empty tuple for anything else.
        # `zip` is what makes a third-party target degrade to the baseline
        # alone rather than to three mislabelled runs.
        expected = (
            "expect HEALED   -- markup moved, behaviour did not",
            "expect DEFECT   -- markup untouched, behaviour changed",
            "expect ESCALATE -- both moved; neither observation explains the other",
        )
        targets = [("1. baseline", entry_url, "expect PASSED")]
        targets += [
            (f"{i}. {url.split('?', 1)[1]}", url, note)
            for i, (url, note) in enumerate(
                zip(fixture_variants(entry_url), expected), start=2
            )
        ]

        for label, url, note in targets:
            print(f"--- {label:<22} {note} ---")
            result = run(page, happy, target_url=url)
            print(render(result))
            print()

        browser.close()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/sut"))
