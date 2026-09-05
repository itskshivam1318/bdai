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

Healing itself is deliberately dull; see `ladder()`. It is a sequence of
observable rungs, and the rung that fired is recorded, because "healed by
structural match", "healed by name similarity" and "healed because a model
picked it out of four" are different amounts of trust and a report that hides
which one happened is not evidence.

Only the last rung asks a model, and only where nothing observable is left to
ask. It can name a control but never invent one, and whatever it names is
replayed and classified like any other repair -- so the invariant above holds
over it too: healing cannot override a failed verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from playwright.sync_api import Page

from .explorer import forms
from .explorer.forms import Credentials
from .explorer.observer import Observation, Observer
from .explorer.statekey import explain, state_key
from .generator import Scenario, Step
from .llm import Exchange, Tool, Transcript
from .tracing import save_transcript

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
    rung: str  # 'exact' | 'structural' | 'similarity' | 'ranked' | 'unresolved'
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


CHOOSE = Tool(
    name="choose",
    description=(
        "Name which of the offered controls now plays the part the recorded "
        "step played. Answer with a candidate's `id` exactly as given. If none "
        "of them plays that part, or two of them equally could, do not call "
        "this tool at all -- a refusal is a real answer here and a wrong repair "
        "is a green run over a broken application."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "the candidate's id, exactly as given",
            },
            "why": {
                "type": "string",
                "description": (
                    "what makes this the same control, in one concrete sentence "
                    "about its role in the flow -- not 'it looks similar'"
                ),
            },
        },
        "required": ["id", "why"],
    },
)


def _brief(step: Step, candidates: tuple[tuple[str, str], ...]) -> str:
    """The candidate list as the model sees it. Ids are the only writable handle."""
    _, role, name = _parts(step.action)
    lines = [
        "A recorded test step can no longer find the control it was written "
        "against, and more than one control on the page could be it.",
        "",
        f"The step means: {step.intent}",
        f"It was recorded against: {role} named {name!r}",
        "When it ran, the application: "
        + ("moved to another state" if step.expect.moved else "stayed where it was")
        + (" and sent a write request" if step.expect.mutating else ""),
    ]
    if step.expect.added:
        lines += ["", "and these appeared:"]
        lines += [f"    {line.strip()}" for line in step.expect.added[:6]]
    lines += ["", "Controls of the right kind on the page as it is now:", ""]
    for index, (_, candidate) in enumerate(candidates):
        lines.append(f"  [{index}] {role} {candidate!r}")
    return "\n".join(lines)


def ranked(
    step: Step,
    candidates: tuple[tuple[str, str], ...],
    provider=None,
    *,
    on_event=None,
    run_id: int | None = None,
) -> Resolution:
    """The ladder's bottom rung: ask a model to break a tie it may not invent.

    Reached only from `resolve`, and only after `structural` and `similarity`
    have both declined -- which is the position `resolve`'s docstring reserved
    for a model call and the reason this is not one rung higher. Every rung
    above produces an observable fact; this one produces a judgement, so it goes
    last, where the alternative is not a better answer but no answer at all.

    Three things keep it honest, and each is a way it could otherwise start
    manufacturing green.

    **It answers by index.** The model is handed a list `forms.available_actions`
    computed and may only point into it. An id that names no candidate is
    dropped and the escalation stands, exactly as `critic.prioritise` and
    `claims.attribute` drop an invented citation. A control that is not on the
    page cannot be named, whatever the model says.

    **It refuses below two candidates.** One candidate is the structural rung's
    answer and none is `rescue`'s; in both cases a model asked here would be
    overruling a deterministic result with an opinion. The guard lives in this
    function rather than in its caller so the property holds for anyone who
    calls it.

    **It is still replayed.** A repair from this rung goes back through the same
    `_met` check as any other, so a step ranked onto the wrong control fails
    verification and reports DEFECT or ESCALATE. Healing cannot override a
    failed verification -- that invariant is what makes a guess admissible here
    at all.
    """
    def emit(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    if provider is None or len(candidates) < 2:
        return Resolution(None, "unresolved", "no ranking was attempted")

    system = (
        "You decide which control on a web page now plays the part a recorded "
        "test step was written against. You are given only controls that exist "
        "on the page and are already of the right kind; your whole job is to "
        "choose among them or to decline. Declining is correct whenever two of "
        "them could equally be the answer -- a human resolves that in seconds, "
        "and a wrong repair hides a real defect behind a passing test."
    )
    transcript = Transcript(prompt=_brief(step, candidates))
    try:
        turn = provider.turn(system, transcript, [CHOOSE])
    except Exception as exc:
        # Losing the model costs the tie-break and nothing else. The step was
        # already unresolvable when we got here, so the honest outcome is the
        # escalation that was standing -- never a repair, and never a crash that
        # would take the rest of the scenario's steps with it.
        detail = f"could not rank ({type(exc).__name__}: {exc})"
        emit("warn", f"healer {detail}")
        return Resolution(None, "unresolved", detail)

    transcript.exchanges.append(
        Exchange(text=turn.text, calls=turn.calls, opaque=turn.opaque)
    )
    try:
        save_transcript(
            transcript, run_id=run_id, role="healer", system=system, label="rank"
        )
    except Exception:
        # Same rule as `critic.prioritise` and `ant.explore`: losing the
        # write-up must never lose the answer.
        pass

    call = next((c for c in turn.calls if c.name == "choose"), None)
    if call is None:
        return Resolution(
            None, "unresolved",
            f"{len(candidates)} candidates and the healer declined to choose: "
            f"{[name for _, name in candidates[:4]]}",
        )

    index = call.arguments.get("id")
    if not isinstance(index, int) or not 0 <= index < len(candidates):
        return Resolution(
            None, "unresolved",
            f"the healer named candidate {index!r}, which is not one of the "
            f"{len(candidates)} offered",
        )

    action, name = candidates[index]
    why = str(call.arguments.get("why", "")).strip()
    _, _, want_name = _parts(step.action)
    return Resolution(
        action, "ranked",
        f"{want_name!r} -> {name!r}, chosen from {len(candidates)} candidates "
        f"none of which matched by name: {why}",
    )


def resolve(
    page: Page,
    step: Step,
    here: Observation,
    provider=None,
    *,
    on_event=None,
    run_id: int | None = None,
) -> Resolution:
    """Find the control this step means, on the page as it is now.

    Reads the page, then hands the ladder its action vocabulary. The split is
    so that `ladder` -- which is where every policy decision lives -- can be
    checked without a browser, a key or a live app.
    """
    return ladder(
        step,
        forms.available_actions(page, here),
        forms.fields_of(here),
        provider,
        on_event=on_event,
        run_id=run_id,
    )


def ladder(
    step: Step,
    available: tuple[str, ...],
    fields_now: tuple[tuple[str, str], ...],
    provider=None,
    *,
    on_event=None,
    run_id: int | None = None,
) -> Resolution:
    """Which control this step means, given what the page now offers.

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

    **ranked** -- several structural candidates and none of them reads like the
    recorded name. This is the documented model seam (`research/README.md`:
    judges rank reliably and score unreliably), and it took the position this
    docstring reserved for it: above `escalate`, below `structural`. It is last
    because every rung over it produces an observable fact and this one produces
    a judgement, so a model reached earlier would be overruling evidence. It is
    *present* because the alternative here is not a better answer -- it is no
    answer, and the whole scenario ends on this step. See `ranked` for the three
    properties that stop it manufacturing green.
    """
    if step.action in available:
        return Resolution(step.action, "exact", "the recorded control is still here")

    want_mode, want_role, want_name = _parts(step.action)

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

    # Every observable rung has now declined, and the alternative to a
    # judgement here is not a better answer but no answer at all -- the whole
    # scenario ends on this step. `ranked` may only point into `candidates`, and
    # whatever it points at is still replayed and still classified.
    judged = ranked(
        step, tuple(candidates), provider, on_event=on_event, run_id=run_id
    )
    if judged.action is not None:
        return judged

    return Resolution(
        None,
        "unresolved",
        f"{len(candidates)} candidates and none clearly {want_name!r}: "
        f"{[name for _, _, name in scored[:4]]}"
        + (f" ({judged.detail})" if provider is not None else ""),
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
    provider=None,
    run_id: int | None = None,
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
    page.goto(url)
    here = observer.observe()
    emit(f"replaying {scenario.name!r} against {url}")

    for step in scenario.steps:
        resolution = resolve(
            page, step, here, provider, on_event=on_event, run_id=run_id
        )

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
