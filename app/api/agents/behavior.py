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
import queue
import threading
from dataclasses import dataclass, field, replace

from .explorer.worldmap import Ground, WorldMap
from .llm import Exchange, Tool, ToolResult, Transcript
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


def admit(ground: Ground, raw: dict) -> Hypothesis | None:
    """A raw hypothesis, or None if the map cannot back it.

    The rule is total: at least one citation must resolve, and every citation
    that does not resolve is thrown away rather than carried along as prose.
    A claim reduced to zero surviving citations is a claim about web
    applications in general, not about this one, so it is refused.

    Takes a `Ground` rather than a `WorldMap` because the only two things it
    ever needed are the vocabulary and the state keys, and reading them off a
    live map means iterating a dict the crawler may be inserting into. See
    `worldmap.Ground` -- the rule below is unchanged, only what it reads is.
    """
    claim = str(raw.get("claim", "")).strip()
    kind = str(raw.get("kind", "")).strip()
    if not claim or kind not in KINDS:
        return None

    resolved: list[str] = []

    for cite in raw.get("cites") or []:
        cite = str(cite).strip()
        if not cite:
            continue
        if cite in ground.actions:
            resolved.append(cite)
            continue
        # The model is shown 8-character ids; the map is keyed on 16. Widen,
        # and refuse an ambiguous prefix rather than picking one -- the same
        # rule `orchestrator.run` applies to a dispatch assignment.
        matches = [key for key in ground.states if key.startswith(cite)]
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


def _render_states(world: WorldMap, states) -> list[str]:
    """One line per state, then its actions and where each one led.

    Shared by `brief` and `delta_brief` so a state is described the same way
    whether it is in the opening survey or in a later turn's delta -- the
    citation guard matches on these exact strings, and two renderers would be
    two chances for one of them to drift out of the vocabulary.
    """
    lines: list[str] = []
    for key in states:
        node = world.states[key]
        title = node.label or node.title or node.url or ""
        lines.append(f"  [{key[:8]}] {title}")
        for action in node.actions[:12]:
            taken = world.transitions.get((key, action))
            if taken:
                where = f" -> [{taken[0].to_key[:8]}]" + (
                    "  (changed server state)" if taken[0].mutating else ""
                )
            else:
                where = "  (never tried)"
            lines.append(f"       {action}{where}")
        if len(node.actions) > 12:
            lines.append(f"       ... and {len(node.actions) - 12} more")
    return lines


def delta_brief(world: WorldMap, since: frozenset[str]) -> str | None:
    """What arrived since the last turn, or None if nothing did.

    The incremental counterpart to `brief`. `synthesise` asked one question
    about a finished map, and the reply it wanted back scaled with the map --
    which is how a `tool_calls` payload came to be severed at the ceiling. Fed
    a few states per turn the reply stays the same size whatever the app.

    **`world.summary()` is repeated on every turn and the state list is not.**
    Those are different kinds of fact. The summary is a running total that
    changes as the crawl proceeds, and a turn shown four states with no idea
    whether they are the whole app or a corner of it cannot tell a claim about
    this application from a claim about applications. The states themselves do
    not change once recorded -- `WorldMap.record` is first-sighting-wins -- so
    re-sending them buys nothing and costs the growth this exists to avoid.

    Returns None rather than an empty delta so the caller can skip the turn.
    A model call about states already claimed is paid for and can only
    duplicate what the transcript already holds.
    """
    fresh = [key for key in world.states if key not in since]
    if not fresh:
        return None

    lines = [
        f"Since the last update the crawler reached {len(fresh)} more "
        f"state(s). The application now stands at:",
        "",
        world.scale(),
        "",
        "The new states, with the actions each offers:",
        "",
    ]
    lines += _render_states(world, fresh)

    # Only the refusals that belong to the new states. A refusal already sent
    # is already in the transcript, and this is the one part of the map that
    # can grow for a state the model has seen before.
    refused = [
        (key, action, why)
        for (key, action), why in world.skipped.items()
        if key in fresh
    ]
    if refused:
        lines += ["", "Actions these states offered but refused -- tried and impossible:"]
        for key, action, why in refused[:8]:
            lines.append(f"  [{key[:8]}] {action}  --  {why}")

    return "\n".join(lines)


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
    lines += _render_states(world, world.states)
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
        for flow in prior.get("flows") or ():
            lines.append(f"  flow: {flow.get('name', '')} -- {flow.get('why', '')}")
        for gap in prior.get("gaps") or ():
            lines.append(f"  did not reach: {gap}")

    return "\n".join(lines)


class BehaviourSession:
    """One conversation with the behavioural model, fed a few states at a time.

    `synthesise` is this with exactly one turn. The difference matters in two
    places. The reply stays small, because each turn is asked about the states
    that just arrived rather than the whole map -- and a reply that scales with
    the map is what `finish_reason: length` looked like on 2026-09-05. And the
    turns share a transcript, so turn 3 reads what turn 1 concluded and can
    say something turn 1 could not, which is the point of interleaving at all.

    **Turns add. No turn withdraws.** A model permitted to delete its earlier
    hypothesis would be grading its own claim -- the thing this module exists
    to prevent -- in its least visible form: a claim removed before `examine`
    runs leaves no count, no verdict and no trace. So the accumulation is
    append-only, `examine` rules from the map at the end, and a claim that
    later states contradict is reported `contradicted`. That is a finding.
    Silence is not.

    Every turn is guarded by `admit` against the `Ground` it was given, so a
    citation invented on turn 5 is dropped exactly as one invented on turn 1.
    """

    def __init__(self, provider, *, system: str | None = None):
        self.provider = provider
        if system is None:
            from .ant import instructions as load_instructions

            try:
                system = load_instructions("behaviour")
            except FileNotFoundError:  # pragma: no cover - ships with module
                system = _FALLBACK_PROMPT
        self.system = system
        self.transcript: Transcript | None = None
        self.admitted: list[Hypothesis] = []
        self.dropped = 0
        self.summary = ""
        self.turns = 0
        #: `(level, message)` for the caller to emit. Never emitted from here:
        #: this runs on a worker thread and the console's `emit` closes over a
        #: SQLModel session that is not thread-safe. See `behaviour_worker`.
        self.notes: list[tuple[str, str]] = []

    def feed(self, text: str, ground: Ground) -> int:
        """One turn about `text`. Returns how many hypotheses were admitted.

        Never raises. A provider that fails takes this turn and nothing else --
        the crawl beside it keeps walking, the turns already admitted stay
        admitted, and the run continues on whatever the model managed to say.
        """
        if self.transcript is None:
            self.transcript = Transcript(prompt=text)
        else:
            # The opening prompt is turn 1's; every turn after it arrives as
            # the user's reply to what the model last said, which is what
            # keeps one conversation rather than a series of them.
            self.transcript.exchanges[-1] = replace(
                self.transcript.exchanges[-1], follow_up=text
            )

        try:
            turn = self.provider.turn(self.system, self.transcript, [MODEL])
        except Exception as exc:
            self.notes.append(
                ("error", f"behaviour turn failed: {type(exc).__name__}: {exc}")
            )
            # The follow-up is left on the transcript. The next turn overwrites
            # it, so a failed turn costs its states rather than stranding them.
            return 0

        # **Every tool call is answered.** `synthesise` did not have to: it
        # sends one turn and never sends the transcript back, so a dangling
        # call was never serialised. A conversation does send it back, and a
        # provider that validates the message list rejects the whole turn --
        # measured live against `minimax/minimax-m3:free`, which took turns 1
        # and 2 and then 400'd every turn after with "invalid params, tool
        # call result does not follow tool call".
        #
        # There is nothing to report back: the tool *is* the reply, and the
        # map has already ruled on it. So the result says what was kept, which
        # also tells the model its invented citations were dropped without
        # inviting it to re-argue them.
        self.transcript.exchanges.append(
            Exchange(
                text=turn.text,
                calls=turn.calls,
                opaque=turn.opaque,
                results=tuple(
                    ToolResult(
                        call_id=c.id, name=c.name,
                        content="recorded against the map",
                    )
                    for c in turn.calls
                ),
            )
        )
        self.turns += 1

        call = next((c for c in turn.calls if c.name == "model"), None)
        if call is None or (turn.truncated and not call.arguments):
            self.notes.append(("warn", _no_model(turn, self.provider)))
            return 0

        admitted = 0
        for raw in call.arguments.get("hypotheses") or []:
            if not isinstance(raw, dict):
                self.dropped += 1
                continue
            hypothesis = admit(ground, raw)
            if hypothesis is None:
                self.dropped += 1
                continue
            self.admitted.append(hypothesis)
            admitted += 1

        # First one wins. A later turn is shown a few new states and would
        # narrow the account to them; the turn that saw the most of the app is
        # the one whose summary describes the application.
        if not self.summary:
            self.summary = str(call.arguments.get("summary", "")).strip()
        return admitted

    def model(self) -> BehaviorModel:
        return BehaviorModel(
            summary=self.summary,
            hypotheses=tuple(self.admitted),
            dropped=self.dropped,
        )


def _no_model(turn, provider) -> str:
    """Why no behavioural model came back, distinguishing the two causes."""
    if turn.truncated:
        return (
            "no behavioural model returned -- the reply was cut off at the "
            "model's token ceiling, so the map stands alone. Raise it for "
            f"{getattr(provider, 'model', 'this model')} in catalog.py, or "
            "set LLM_MAX_TOKENS higher"
        )
    return "no behavioural model returned; the map stands alone"


class BehaviourWorker:
    """A `BehaviourSession` on its own thread, fed by the crawl's checkpoint.

    ```
    crawl thread   --edge--edge--edge--edge-----edge--edge--edge--edge-->
      tick()                | push(delta, ground)      | push(...)
                            | drain() -> emit() -> DB  | drain()
    worker thread           +-- provider.turn -> admit +
    ```

    `tick` is a `crawler.checkpoint` -- `Callable[[WorldMap], None]`, called
    after every edge -- so the trigger already exists and nothing in the
    crawler changes. It counts new states, and every `every`-th one it builds
    the delta and the `Ground` **here, on the crawl thread**, and puts them on
    a queue. Both are immutable by the time they cross.

    **Nothing this class runs on the worker touches the database.** The
    console's `emit` closes over a single SQLModel `Session`; `db.py` sets
    `check_same_thread: False`, so a second thread committing on it does not
    raise, it corrupts the session's unit of work with no error anywhere. So
    the worker only ever appends `(level, message)` to a queue, and `tick`
    drains it and emits -- on the crawl thread, in order.

    Everything degrades to today's behaviour. No provider means no thread at
    all: the crawl runs exactly as it does on the no-key path.
    """

    #: How many new states are worth a model call. Four is the smallest batch
    #: that still says something about *structure* rather than about one page,
    #: and small enough that a 20-state app gets five turns rather than one.
    EVERY = 4

    #: Longest `close()` will wait for a turn already in flight. A model call
    #: is the run's longest legitimate silence; past this the run continues
    #: with what was admitted rather than holding the browser open.
    GRACE = 90.0

    def __init__(self, provider, *, every: int = EVERY, on_event=None,
                 system: str | None = None):
        self.provider = provider
        self.every = every
        self._on_event = on_event
        self.batches = 0
        self._sent: frozenset[str] = frozenset()
        self._session = (
            BehaviourSession(provider, system=system) if provider else None
        )
        self._work: queue.Queue = queue.Queue()
        self._said: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    # --- the crawl thread -------------------------------------------------

    def tick(self, world: WorldMap) -> None:
        """Called after every edge. Cheap unless a batch is due."""
        if self._session is None:
            return
        fresh = [key for key in world.states if key not in self._sent]
        if len(fresh) >= self.every:
            self._push(world)
        self.drain()

    def _push(self, world: WorldMap) -> None:
        """Freeze what the model will read, and hand it over.

        Both values are built here rather than on the worker, and that is the
        whole safety argument: `delta_brief` and `ground()` iterate
        `world.states`, and doing that while the crawler inserts is
        `RuntimeError: dictionary changed size during iteration`.
        """
        since = frozenset(self._sent)
        text = delta_brief(world, since)
        if text is None:
            return
        self._sent = frozenset(world.states) | since
        self.batches += 1
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="behaviour", daemon=True
            )
            self._thread.start()
        self._work.put((text, world.ground()))

    def drain(self) -> None:
        """Forward whatever the worker has said. Crawl thread only."""
        while True:
            try:
                level, message = self._said.get_nowait()
            except queue.Empty:
                return
            if self._on_event:
                self._on_event(level, message)

    def close(self, world: WorldMap | None = None) -> BehaviorModel:
        """Send the tail, stop taking work, wait briefly, and return the model.

        `world` is the finished map. Batches go out every `every` states, so a
        crawl that stops with fewer than that unsent would leave them out --
        and the states a crawl reaches last are its deepest, which is where
        the behaviour worth a claim tends to be. Passing the map here sends
        them as a final short batch.

        Safe to call more than once and safe to call with no map: `_push`
        returns without sending when `delta_brief` finds nothing new, and
        `close` is reached from more than one path.
        """
        if self._session is None:
            return BehaviorModel()
        if world is not None:
            self._push(world)
        if self._thread is not None:
            self._work.put(None)
            self._thread.join(timeout=self.GRACE)
            if self._thread.is_alive():
                self._said.put((
                    "warn",
                    f"the behavioural model was still thinking after "
                    f"{self.GRACE:.0f}s; continuing with "
                    f"{len(self._session.admitted)} hypothesis(es)",
                ))
        self.drain()
        return self._session.model()

    # --- the worker thread ------------------------------------------------

    def _run(self) -> None:
        """Take batches until told to stop. Never raises, never emits."""
        while True:
            item = self._work.get()
            if item is None:
                return
            text, ground = item
            try:
                self._session.feed(text, ground)
            except Exception as exc:  # pragma: no cover - feed swallows its own
                self._said.put((
                    "error",
                    f"behaviour worker died: {type(exc).__name__}: {exc}",
                ))
                return
            # `feed` records rather than emits, for exactly this reason.
            while self._session.notes:
                self._said.put(self._session.notes.pop(0))
            self._said.put((
                "decision",
                f"behavioural model: {len(self._session.admitted)} grounded "
                f"hypothesis(es) after {self._session.turns} turn(s)",
            ))


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
    if call is None or (turn.truncated and not call.arguments):
        # Two different failures wore the same sentence until 2026-09-05, and
        # the difference is the whole diagnosis. A model that read the map and
        # declined to call the tool is a modelling problem. A model whose reply
        # was cut off at the ceiling never got to finish the call -- the
        # truncated `tool_calls` JSON arrives here as `arguments={}`, which is
        # indistinguishable from silence unless the provider says so.
        #
        # The second clause is narrow on purpose: *truncated* and the arguments
        # did not survive. A model that calls `model` with a summary and an
        # empty hypothesis list has said something, and it still flows through
        # to the count below.
        #
        # Measured on `sarvam-105b`, whose ceiling was 8192 while reasoning is
        # on by default and reasoning tokens are charged against the same
        # budget: the ceiling was spent thinking, the call was severed, and
        # this line blamed the model. `Turn.truncated` is what tells them
        # apart. See `catalog.py` for why that number moved.
        emit("warn", _no_model(turn, provider))
        return BehaviorModel()

    admitted: list[Hypothesis] = []
    dropped = 0
    for raw in call.arguments.get("hypotheses") or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        hypothesis = admit(world.ground(), raw)
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
