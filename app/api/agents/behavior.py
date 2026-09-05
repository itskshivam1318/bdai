"""The semantic layer: what the map *means*, in claims the map can check.

    cd app/api && uv run python -m agents.behavior http://localhost:3000/sut

`explorer/` answers "what can I observe, and what transitions can I reproduce?"
That is the factual substrate and it is deliberately incapable of interpretation
-- `worldmap.py` treats an action as an opaque string precisely so that nothing
in it ever learns what a login *is*.

This file is the other half. It takes the finished map and asks a model to say
what the application does: which sequences are flows a user accomplishes, what
ought to hold, what looks like a mutation, what it could not tell. That is the
one question in the system with no deterministic answer, because it is not a
question about the observations -- it is a question about what they are *for*.

**Why the citation guard is the whole file.** The colony has written a summary
and named flows since it existed (`orchestrator.Exploration.summary`, `.flows`),
and both had the same fatal property: nothing checked a word of them against the
map, because nothing downstream read them. A model asked to describe an app it
has only seen through a state table will name a checkout page it never saw --
not from malice but because applications like this one usually have one. A
hypothesis that cites a state the crawler never recorded is a claim about
web applications in general, and compiling a test from it produces a test for a
page that does not exist.

So `admit()` is the seam: every citation must resolve to a state key in
`world.states` or an action in `world.vocabulary()`, and a hypothesis whose
citations do not resolve is dropped and **counted**. This is `critic.prioritise`
applied one layer up -- the Rulers extractive-quote requirement (arXiv:2601.08654)
enforced mechanically rather than requested politely. The difference is that the
critic could hand the model indices into a list; a behavioural claim is prose,
so the handle has to be the map's own vocabulary.

**A hypothesis is unexamined until something examines it.** `status` starts at
`unexamined` and only an observation moves it. Nothing here decides that a claim
is true -- `runner.py` and `invariants.py` do that from evidence, and this file
has none. A model that could mark its own hypothesis `supported` would be the
84.4%-false-positive verifier `critic.py`'s docstring exists to avoid.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from .explorer.worldmap import WorldMap
from .llm import Exchange, Tool, Transcript
from .tracing import save_transcript

# The four kinds, and each is a different amount of trust:
#
#   flow          an ordered sequence a user accomplishes. The generator can
#                 compile one, so this is the kind that becomes a test.
#   invariant     something that should hold of every run. `invariants.py`
#                 checks four hardcoded ones; a proposed invariant is the same
#                 shape with the model choosing the rule instead of us.
#   mutation      an action that appears to change application state. Already
#                 observable per-edge (`Transition.mutating`); at this level it
#                 is a claim about *what* was changed.
#   uncertainty   the model could not tell. Named so the orchestrator can send
#                 an ant at it -- an unknown that says where to look is worth
#                 more than a confident guess.
KINDS = ("flow", "invariant", "mutation", "uncertainty")

UNEXAMINED, SUPPORTED, CONTRADICTED, INCONCLUSIVE = (
    "unexamined",
    "supported",
    "contradicted",
    "inconclusive",
)

# The rule vocabulary an `invariant` hypothesis may bind to. Every one of these
# is decidable from `WorldMap` alone -- a recorded transition either did the
# thing or did not -- which is the property that lets the model choose the claim
# while code returns the verdict.
#
# Kept deliberately small. A richer language would let the model express more,
# and every addition is a new way for a claim to be *unfalsifiable from the map*
# -- at which point the checker has to guess, and guessing is the thing this
# design exists to remove. A claim that cannot be phrased as one of these is
# still worth recording; it is recorded as an `uncertainty` for an ant, not as
# an invariant nothing can rule on.
RULES = {
    "must-move": (
        "taking this action must land in a different state than it started in"
    ),
    "must-mutate": (
        "taking this action must send a non-GET request -- it is supposed to "
        "change something on the server"
    ),
    "must-not-mutate": (
        "taking this action must NOT send a non-GET request -- cancelling, "
        "going back, or merely viewing must not write"
    ),
    "must-reach": (
        "taking this action from the first cited state must land in the second "
        "cited state"
    ),
}

MODEL = Tool(
    name="model",
    description=(
        "Record the behavioural model you have built from the world map. "
        "Every hypothesis must cite the state ids or action strings it is "
        "about, copied exactly from the map you were shown. A hypothesis "
        "citing anything else will be discarded."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "What this application is and what a user does with it. "
                    "Two or three sentences, as you would brief a QA engineer."
                ),
            },
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {
                            "type": "string",
                            "description": (
                                "One sentence, testable in principle. "
                                "'Logging out returns the user to an "
                                "unauthenticated state' -- not 'the app has "
                                "authentication'."
                            ),
                        },
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "cites": {
                            "type": "array",
                            "minItems": 1,
                            "description": (
                                "State ids (8 characters, as shown) and/or "
                                "action strings, copied verbatim from the map."
                            ),
                            "items": {"type": "string"},
                        },
                        "rule": {
                            "type": "string",
                            "enum": list(RULES),
                            "description": (
                                "For an `invariant` only: which checkable rule "
                                "this claim is, bound to the states and actions "
                                "in `cites`. "
                                + " | ".join(
                                    f"{name}: {why}" for name, why in RULES.items()
                                )
                                + ". Omit for any other kind. An invariant with "
                                "no rule cannot be checked and will be reported "
                                "as inconclusive."
                            ),
                        },
                        "why": {
                            "type": "string",
                            "description": "Why this matters to a user. One sentence.",
                        },
                    },
                    "required": ["claim", "kind", "cites", "why"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "hypotheses"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True)
class Hypothesis:
    """One claim about the application, and the evidence it is anchored to.

    `cites` holds full 16-character state keys and/or action strings, never the
    8-character abbreviations the model is shown -- `admit` widens them, so
    every consumer downstream can index `world.states` directly.
    """

    claim: str
    kind: str
    cites: tuple[str, ...]
    why: str = ""
    # For an `invariant`: which entry of `RULES` this claim is. Empty for every
    # other kind, and empty on an invariant the model phrased in prose it could
    # not reduce to a rule -- which `examine` reports as inconclusive rather
    # than assuming either way.
    rule: str = ""
    status: str = UNEXAMINED
    # What moved it off `unexamined`, in one line. Empty while unexamined.
    because: str = ""

    @property
    def states(self) -> tuple[str, ...]:
        """The cited state keys, in the order given. The generator's path.

        `state_key` is 16 hex characters (`statekey.py`), and an action is a
        `role:name` descriptor, so the two are told apart by shape rather than
        by asking the map. Matching on hex specifically, not just on length: a
        16-character action with no colon is unlikely and not impossible, and
        misreading one as a state would silently change what an invariant is
        bound to.
        """
        return tuple(
            c for c in self.cites
            if len(c) == 16 and all(ch in "0123456789abcdef" for ch in c)
        )

    @property
    def actions(self) -> tuple[str, ...]:
        states = self.states
        return tuple(c for c in self.cites if c not in states)

    def render(self) -> str:
        mark = {
            UNEXAMINED: "?",
            SUPPORTED: "+",
            CONTRADICTED: "!",
            INCONCLUSIVE: "~",
        }.get(self.status, "?")
        line = f"  {mark} [{self.kind}] {self.claim}"
        if self.because:
            line += f"\n        {self.status}: {self.because}"
        return line


@dataclass
class BehaviorModel:
    """The semantic memory. Sits beside the map, never inside it.

    Kept separate from `WorldMap` on purpose: the map is the factual record and
    must be identical on every crawl of an unchanged app, while this is an
    interpretation that will differ between models and between runs. Merging
    them would make the map's reproducibility depend on a model's mood.
    """

    summary: str = ""
    hypotheses: tuple[Hypothesis, ...] = ()
    # Hypotheses the model produced that cited nothing in the map. Counted
    # rather than discarded silently, for the same reason `critic.prioritise`
    # counts invented gaps: a guard nobody can see is a guard nobody trusts.
    dropped: int = 0

    def of_kind(self, kind: str) -> tuple[Hypothesis, ...]:
        return tuple(h for h in self.hypotheses if h.kind == kind)

    def as_dict(self) -> dict:
        """The whole model, losslessly, for a version.json to carry.

        `cites` and `status` are the two fields that would be tempting to drop
        and cannot be. Without `cites`, `refresh` cannot tell a claim about the
        region that moved from a claim about the rest of the app, and the merge
        it does becomes a guess. Without `status`, a hypothesis the map had
        already ruled on comes back `unexamined` and the evidence that settled
        it is thrown away on reload.
        """
        return {
            "summary": self.summary,
            "dropped": self.dropped,
            "hypotheses": [
                {
                    "claim": h.claim, "kind": h.kind, "cites": list(h.cites),
                    "why": h.why, "rule": h.rule, "status": h.status,
                    "because": h.because,
                }
                for h in self.hypotheses
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> BehaviorModel:
        """Read one back. An absent or malformed block is an empty model."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            summary=raw.get("summary", "") or "",
            dropped=int(raw.get("dropped", 0) or 0),
            hypotheses=tuple(
                Hypothesis(
                    claim=h.get("claim", ""),
                    kind=h.get("kind", ""),
                    cites=tuple(h.get("cites") or ()),
                    why=h.get("why", ""),
                    rule=h.get("rule", ""),
                    status=h.get("status", UNEXAMINED),
                    because=h.get("because", ""),
                )
                for h in (raw.get("hypotheses") or ())
                if isinstance(h, dict)
            ),
        )

    @property
    def open(self) -> tuple[Hypothesis, ...]:
        """Everything nothing has examined yet. What the orchestrator acts on."""
        return tuple(h for h in self.hypotheses if h.status == UNEXAMINED)

    def render(self) -> str:
        if not self.hypotheses and not self.summary:
            return "no behavioural model (no provider configured)"
        lines = []
        if self.summary:
            lines += ["WHAT THIS APPLICATION IS", f"  {self.summary}", ""]
        if self.hypotheses:
            lines.append(
                f"BEHAVIOURAL MODEL  {len(self.hypotheses)} hypotheses "
                f"({len(self.open)} unexamined)"
            )
            lines += [h.render() for h in self.hypotheses]
        if self.dropped:
            lines += [
                "",
                f"  {self.dropped} hypothesis(es) discarded: they cited states "
                "or actions this crawl never observed",
            ]
        return "\n".join(lines)


def admit(world: WorldMap, raw: dict) -> Hypothesis | None:
    """A raw hypothesis, or None if the map cannot back it.

    The rule is total: at least one citation must resolve, and every citation
    that does not resolve is thrown away rather than carried along as prose.
    A claim reduced to zero surviving citations is a claim about web
    applications in general, not about this one, so it is refused.
    """
    claim = str(raw.get("claim", "")).strip()
    kind = str(raw.get("kind", "")).strip()
    if not claim or kind not in KINDS:
        return None

    vocabulary = set(world.vocabulary())
    resolved: list[str] = []

    for cite in raw.get("cites") or []:
        cite = str(cite).strip()
        if not cite:
            continue
        if cite in vocabulary:
            resolved.append(cite)
            continue
        # The model is shown 8-character ids; the map is keyed on 16. Widen,
        # and refuse an ambiguous prefix rather than picking one -- the same
        # rule `orchestrator.run` applies to a dispatch assignment.
        matches = [key for key in world.states if key.startswith(cite)]
        if len(matches) == 1:
            resolved.append(matches[0])

    if not resolved:
        return None

    # Deduplicated, order preserved: a model repeating a state id is being
    # emphatic, not describing a loop.
    seen: set[str] = set()
    cites = tuple(c for c in resolved if not (c in seen or seen.add(c)))

    # An unrecognised rule is dropped to empty rather than carried: `examine`
    # would report it inconclusive either way, and keeping the string would put
    # a rule name in the report that nothing in `RULES` explains.
    rule = str(raw.get("rule", "")).strip()
    if rule not in RULES:
        rule = ""

    return Hypothesis(
        claim=claim, kind=kind, cites=cites, rule=rule,
        why=str(raw.get("why", "")).strip(),
    )



def examine(world: WorldMap, model: BehaviorModel) -> BehaviorModel:
    """Rule on every `invariant` hypothesis from the recorded transitions.

    **The model wrote the claim and does not get to grade it.** Each rule in
    `RULES` is decided by looking up the edge the hypothesis cites and reading
    what the crawler observed: did the state change, did a non-GET fire, where
    did it land. Those are the same two orthogonal signals `runner.py` crosses,
    and they are facts about the run rather than opinions about it.

    Three outcomes, and the third is the one that keeps the other two honest:

        supported       the recorded transition does what the claim requires
        contradicted    it does the opposite -- a defect provable from the map
        inconclusive    there is no recorded transition to rule on, the rule is
                        missing, or it is one nothing here can evaluate

    `inconclusive` is never collapsed into `supported`. An invariant about an
    edge the crawler never walked has not been upheld; it has not been tested,
    and reporting the two as one is how a suite starts manufacturing green.

    Non-invariant hypotheses are returned untouched. A `flow` becomes a test and
    is ruled on by `runner.py`; nothing here is entitled to an opinion about it.
    """
    ruled: list[Hypothesis] = []

    for hypothesis in model.hypotheses:
        if hypothesis.kind != "invariant" or hypothesis.status != UNEXAMINED:
            ruled.append(hypothesis)
            continue
        status, because = _rule_on(world, hypothesis)
        ruled.append(
            Hypothesis(
                claim=hypothesis.claim, kind=hypothesis.kind,
                cites=hypothesis.cites, why=hypothesis.why, rule=hypothesis.rule,
                status=status, because=because,
            )
        )

    return BehaviorModel(
        summary=model.summary, hypotheses=tuple(ruled), dropped=model.dropped
    )


def _rule_on(world: WorldMap, hypothesis: Hypothesis) -> tuple[str, str]:
    """One invariant, decided. Returns (status, one line saying why)."""
    if hypothesis.rule not in RULES:
        return (
            INCONCLUSIVE,
            "no checkable rule was bound to this claim, so nothing here can "
            "decide it -- it needs an experiment against the running app",
        )

    states, actions = hypothesis.states, hypothesis.actions
    if not states or not actions:
        return (
            INCONCLUSIVE,
            "the claim cites no state/action pair, so there is no recorded "
            "transition to read",
        )

    from_key, action = states[0], actions[0]
    taken = world.transitions.get((from_key, action))
    if not taken:
        return (
            INCONCLUSIVE,
            f"the crawler never took {action} from [{from_key[:8]}], so this "
            "has not been upheld -- it has not been tested",
        )

    edge = taken[0]
    where = f"{action} from [{from_key[:8]}]"

    if hypothesis.rule == "must-move":
        moved = edge.to_key != edge.from_key
        return (
            (SUPPORTED, f"{where} landed in [{edge.to_key[:8]}]")
            if moved
            else (
                CONTRADICTED,
                f"{where} stayed in the same state. The app was asked to do "
                "something and did not re-render",
            )
        )

    if hypothesis.rule == "must-mutate":
        return (
            (SUPPORTED, f"{where} sent a non-GET request")
            if edge.mutating
            else (
                CONTRADICTED,
                f"{where} sent no non-GET request, so nothing reached the "
                "server -- whatever it appeared to change was not saved",
            )
        )

    if hypothesis.rule == "must-not-mutate":
        return (
            (SUPPORTED, f"{where} sent no non-GET request")
            if not edge.mutating
            else (
                CONTRADICTED,
                f"{where} sent a non-GET request. An action that should only "
                "read wrote something",
            )
        )

    # must-reach
    if len(states) < 2:
        return (
            INCONCLUSIVE,
            "must-reach needs two cited states -- where the action is taken "
            "and where it should land -- and only one was given",
        )
    wanted = states[1]
    return (
        (SUPPORTED, f"{where} landed in [{wanted[:8]}] as claimed")
        if edge.to_key == wanted
        else (
            CONTRADICTED,
            f"{where} landed in [{edge.to_key[:8]}], not [{wanted[:8]}]",
        )
    )


def brief(world: WorldMap, prior: dict | None = None) -> str:
    """The map as the synthesiser sees it: states, edges, vocabulary, gaps.

    Fuller than `tools.brief` -- this call happens once, not once per wave, so
    it can afford the action lists that would blow the orchestrator's context.
    """
    lines = [
        "A deterministic crawler walked this application and recorded the "
        "following. Every id and action string below is something it actually "
        "observed.",
        "",
        world.summary(),
        "",
        "States, with the actions each offers:",
        "",
    ]
    for key, node in world.states.items():
        title = node.label or node.title or node.url or ""
        lines.append(f"  [{key[:8]}] {title}")
        for action in node.actions[:12]:
            taken = world.transitions.get((key, action))
            where = ""
            if taken:
                where = f" -> [{taken[0].to_key[:8]}]" + (
                    "  (changed server state)" if taken[0].mutating else ""
                )
            else:
                where = "  (never tried)"
            lines.append(f"       {action}{where}")
        if len(node.actions) > 12:
            lines.append(f"       ... and {len(node.actions) - 12} more")

    if world.skipped:
        lines += ["", "Actions offered but refused -- tried and impossible:"]
        for (key, action), why in list(world.skipped.items())[:8]:
            lines.append(f"  [{key[:8]}] {action}  --  {why}")

    if prior:
        # The colony's own account, when there was one. Offered as a starting
        # point rather than as fact: it was written by a model too, and it has
        # never been through `admit`.
        lines += [
            "",
            "An exploration colony also walked this app and wrote the "
            "following. It is unverified -- treat it as a lead, not evidence:",
            "",
            f"  {prior.get('summary', '')}",
        ]
        # What was believed last time, when this is a *refresh* rather than a
        # first reading. The region below has just been re-crawled because
        # something in it moved, so these are the claims the revision is
        # revising -- a model asked to re-read a screen with no record of what
        # it used to think there will simply describe it afresh, and the
        # difference between "this changed" and "this is what I see" is the
        # whole of what a refresh is for.
        believed = prior.get("hypotheses") or ()
        if believed:
            lines += [
                "",
                "It previously believed the following about this application. "
                "Say which of these the region below has changed, and restate "
                "those; leave the rest alone:",
                "",
            ]
            lines += [
                f"  [{claim.get('kind', '?')}] {claim.get('claim', '')}"
                + (
                    f"  ({claim['status']})"
                    if claim.get("status") and claim["status"] != "unexamined"
                    else ""
                )
                for claim in believed
            ]
        for flow in prior.get("flows") or ():
            lines.append(f"  flow: {flow.get('name', '')} -- {flow.get('why', '')}")
        for gap in prior.get("gaps") or ():
            lines.append(f"  did not reach: {gap}")

    return "\n".join(lines)


def refresh(
    prior: BehaviorModel,
    region: WorldMap,
    provider=None,
    *,
    on_event=None,
    run_id: int | None = None,
) -> BehaviorModel:
    """Re-interpret the part of the app that moved, and keep the rest.

    RECORD builds the behavioural model once. WATCH then replays the suite and
    heals locators against the live page, writing corrections back to the
    *world* model -- and touching this one not at all. So after a structural
    change the system's understanding of the application is the understanding
    it had before the change, and stays that way until somebody runs a whole
    record pass again. That is the loop the architecture draws and the code did
    not have.

    **Everything outside the region is carried, not re-admitted.** `admit()`
    grounds a hypothesis against the map it is handed, and the map here is a
    region -- a handful of states around the one that moved. Re-admitting the
    prior model against it would drop every claim about the rest of the
    application for the single reason that this crawl did not look there,
    turning a local repair into global amnesia. Membership is decided by
    citation: a hypothesis citing any state the region crawl re-observed is the
    model's old reading of ground that has just been re-read, so the fresh
    reading replaces it.

    **With no provider the prior model survives unchanged.** `synthesise`
    returns an *empty* model without one, by design -- there is no deterministic
    way to guess what an application means. Forwarding that here would delete
    the whole behavioural model on a no-key WATCH run, which is the opposite of
    the honest answer: nothing was learned, so nothing changes.
    """
    if provider is None:
        return prior

    fresh = synthesise(
        region, provider, prior=as_prior(prior), on_event=on_event, run_id=run_id
    )
    if not fresh.hypotheses:
        # The provider answered with nothing admissible. The region was looked
        # at and produced no reading, which is not evidence against what we
        # already believed.
        return prior

    inside = set(region.states)
    carried = tuple(
        hypothesis
        for hypothesis in prior.hypotheses
        if not any(citation in inside for citation in hypothesis.cites)
    )
    return BehaviorModel(
        summary=fresh.summary or prior.summary,
        hypotheses=carried + fresh.hypotheses,
        dropped=fresh.dropped,
    )


def as_prior(model: BehaviorModel) -> dict:
    """The model as `brief` wants to read it back."""
    return {
        "summary": model.summary,
        "hypotheses": [
            {"claim": h.claim, "kind": h.kind, "status": h.status}
            for h in model.hypotheses
        ],
    }


def synthesise(
    world: WorldMap,
    provider=None,
    *,
    prior: dict | None = None,
    instructions: str | None = None,
    on_event=None,
    run_id: int | None = None,
) -> BehaviorModel:
    """Build the behavioural model from the finished map. One call.

    **With no provider this returns an empty model, and that is not a degraded
    ranking -- it is nothing.** `critic.prioritise` can fall back to a computed
    order because the candidates were computed; there is no deterministic way
    to guess what an application *means*, so the honest no-model answer is
    silence. Everything downstream must work without one, which is why the
    generator still compiles from the map and only *prefers* a flow hypothesis.
    """

    def emit(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    if provider is None or not world.states:
        return BehaviorModel()

    from .ant import instructions as load_instructions

    try:
        system = instructions or load_instructions("behaviour")
    except FileNotFoundError:  # pragma: no cover - prompt ships with the module
        system = _FALLBACK_PROMPT

    transcript = Transcript(prompt=brief(world, prior))
    try:
        turn = provider.turn(system, transcript, [MODEL])
    except Exception as exc:
        # Same rule as the orchestrator's provider guard: losing the semantic
        # layer must not lose the crawl that produced it.
        emit("error", f"behaviour synthesis failed: {type(exc).__name__}: {exc}")
        return BehaviorModel()

    transcript.exchanges.append(
        Exchange(text=turn.text, calls=turn.calls, opaque=turn.opaque)
    )
    try:
        save_transcript(transcript, run_id=run_id, role="behaviour", system=system)
    except Exception:
        pass

    call = next((c for c in turn.calls if c.name == "model"), None)
    if call is None:
        emit("warn", "no behavioural model returned; the map stands alone")
        return BehaviorModel()

    admitted: list[Hypothesis] = []
    dropped = 0
    for raw in call.arguments.get("hypotheses") or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        hypothesis = admit(world, raw)
        if hypothesis is None:
            dropped += 1
            continue
        admitted.append(hypothesis)

    if dropped:
        emit(
            "warn",
            f"{dropped} hypothesis(es) cited states or actions this crawl never "
            "observed; dropped",
        )
    emit(
        "decision",
        f"behavioural model: {len(admitted)} grounded hypothesis(es) over "
        f"{len(world.states)} states",
    )
    return BehaviorModel(
        summary=str(call.arguments.get("summary", "")).strip(),
        hypotheses=tuple(admitted),
        dropped=dropped,
    )


_FALLBACK_PROMPT = (
    "You are given a world map: the states of a web application, the actions "
    "each offers, and which transitions were actually taken. Build a "
    "behavioural model of the application. Call `model` exactly once. Every "
    "hypothesis must cite state ids or action strings copied verbatim from the "
    "map. Do not describe features you cannot point at."
)


def main(entry_url: str) -> int:
    from playwright.sync_api import sync_playwright

    from .explorer.crawler import Budget, crawl
    from .explorer.forms import Credentials
    from .llm import load

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        world = crawl(
            page, entry_url, Budget(), credentials=Credentials.from_env(),
            trace=lambda line: print(f"  {line}"),
        )
        browser.close()

    try:
        provider = load()
    except Exception as exc:
        print(f"no model configured ({exc}); the map stands alone")
        provider = None

    model = synthesise(
        world, provider, on_event=lambda level, msg: print(f"[{level}] {msg}")
    )
    print()
    print(model.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
