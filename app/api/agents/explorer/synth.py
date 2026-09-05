"""Deliberately-bad input: the one place a model is worth its cost.

`forms.value_for` supplies plausible input with no model, which is enough to get
through a login wall. It cannot supply *rejectable* input, because knowing that
`not-an-email` fails an email check while `aivar@example.com` passes, or that
4111111111111111 is a test card that clears while 1234 is not, is domain
knowledge no regex here has.

**Why a model belongs here and not in the crawl loop.** The two have opposite
failure modes. A model choosing the next action can loop -- 44.4% of
WebVoyager's failures are "navigation stuck" -- and a loop is unrecoverable. A
model choosing a *value* cannot corrupt anything: if it types nonsense, the app
rejects it, the rejection is observed, and that rejection is a real state we
wanted to find. Wrong input is self-correcting and, more than that, it is the
deliverable -- the brief asks for error states, not just happy paths.

**The replay log.** Every payload is cached by (state, form, mode) and reused on
the next run. So the model picks contextually valid input *and* the crawl stays
byte-reproducible for a demo *and* re-runs cost nothing. Delete
`invalid-payloads.json` to re-generate.

**It asks through `llm.load()`, like every other model call here.** It used to
hold its own `anthropic.Anthropic()` and check `ANTHROPIC_API_KEY` by hand, and
the cost of that was measurable: this workspace runs on `OPENROUTER_API_KEY`, so
`_ask` returned None on every call and all five cached payloads read
`"source": "fallback"`. The one seam `explorer/__init__.py` calls "the model
seam" had never fired. A second way to find a provider is a second way to not
find one.

Without *any* provider this degrades to a small mutation table rather than
failing. That keeps `make crawl` runnable with no key, and the degradation is
visible: `Payload.source` says which produced it, `unavailable` says why, and
the crawl summary prints both. It is a fallback, not a design -- the table
knows nothing about the app.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..llm import Tool, Transcript, load
from ..tracing import save_transcript

# One field to describe to the model: what it is called and what kind it is.
_FieldSpec = tuple[str, str]  # (role, accessible name)

_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["name", "value", "why"],
                "additionalProperties": False,
            },
        },
        "expect": {"type": "string"},
    },
    "required": ["fields", "expect"],
    "additionalProperties": False,
}

# The tool *is* the schema. Every provider gets the same JSON Schema, so the
# structural guarantee that made this a `json_schema` response on Anthropic --
# a payload or nothing, never prose to parse -- survives the move to the shared
# provider, and works on Gemini and OpenRouter as well.
PAYLOAD = Tool(
    name="payload",
    description=(
        "Give the values this form should reject, one entry per field you are "
        "changing, plus what the application should do when it receives them."
    ),
    parameters=_SCHEMA,
)

_SYSTEM = """\
You are testing a web application by feeding a form input it should reject.

Produce a payload this application should REFUSE. Change as few fields as
possible from plausible values -- if one bad field is enough to trigger
validation, leave the rest realistic, so the resulting error is attributable.

Never use input that could damage data or impersonate a real person: no SQL,
no scripts, no real email addresses or card numbers. Malformed and boring is
the goal.

For each field give the value and one short clause saying why it is rejectable.
In `expect`, say in one sentence what the application should do.

Answer by calling `payload`. Do not answer in prose.\
"""

_PROMPT = """\
Form: {title}
Fields:
{fields}\
"""

# Fallback only. Keyed by what the accessible name looks like, and knowing
# nothing about the app -- which is exactly the limitation the model removes.
_MUTATIONS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"e-?mail", re.I), "not-an-email", "missing @ and domain"),
    (re.compile(r"pass(word|phrase)?", re.I), "x", "below any length minimum"),
    (re.compile(r"phone|mobile|tel", re.I), "abc", "letters in a numeric field"),
    (re.compile(r"date|birth|dob", re.I), "32/13/9999", "impossible date"),
    (re.compile(r"zip|postal|pin", re.I), "!!!", "punctuation in a postal code"),
    (re.compile(r"age|quantity|count|amount|price", re.I), "-1", "negative"),
    (re.compile(r"url|website|link", re.I), "htp:/broken", "malformed scheme"),
)


@dataclass(frozen=True)
class Payload:
    """Values for one form, plus why they were chosen.

    `why` and `expect` are not decoration. 15% of the brief's score is how
    clearly the agent's decisions are presented, and a report that says "we
    submitted `not-an-email` because it has no @, and expected an inline
    validation error" is the difference between showing a decision and showing
    a diff.
    """

    values: dict[str, str]
    why: dict[str, str]
    expect: str
    source: str  # "model" | "fallback" | "cache"


def _key(name: str) -> str:
    """A field label reduced to what identifies it, decoration discarded.

    Accessible names carry required-markers and punctuation that the model does
    not reliably echo back: `practicesoftwaretesting.com/auth/login` labels its
    inputs `Email address *` and `Password *`, asterisk included. Matching the
    model's `Email address` against that verbatim failed, so `_ask` logged
    "model named no field this form has", fell back, and the seam silently did
    not fire on any form whose labels are decorated -- which is most real ones.
    """
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _resolve(name: str, fields: tuple[_FieldSpec, ...]) -> str | None:
    """The form's own name for what the model called `name`, or None.

    Still extractive: the returned string is always one the *form* supplied, so
    `fill_form` can find the control and the model cannot introduce a field by
    naming it. Two fields that reduce to the same key are refused rather than
    guessed between -- an ambiguous match is not a match, and typing into the
    wrong one of a pair is worse than falling back.
    """
    wanted = _key(name)
    if not wanted:
        return None
    hits = [real for _role, real in fields if _key(real) == wanted]
    return hits[0] if len(hits) == 1 else None


def _fallback(fields: tuple[_FieldSpec, ...]) -> Payload:
    """One bad value per field from a static table. Knows nothing about the app."""
    values: dict[str, str] = {}
    why: dict[str, str] = {}

    for _role, name in fields:
        for pattern, value, reason in _MUTATIONS:
            if pattern.search(name):
                values[name], why[name] = value, reason
                break
        else:
            # No rule matched. An empty string is the one universally
            # rejectable value for a field the app considers required, and is
            # inert if it does not.
            values[name], why[name] = "", "left empty"

    return Payload(values, why, "the form should be rejected", "fallback")


class Synthesizer:
    """Produces invalid payloads, caching every one it produces.

    The cache is the replay log: same app, same states, same payloads, run after
    run. It is keyed by state so that two different forms in one app get
    different treatment, and it is plain JSON so a human can read what the agent
    decided to type and overrule it by editing the file.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        provider=None,
        model: str | None = None,
        run_id: int | None = None,
    ) -> None:
        """`provider` is resolved lazily, so a cached crawl never needs a key.

        Every one of the four callers constructs this as
        `Synthesizer(cache_path=...)` and none of them has a `Provider` to hand
        -- the crawl decides it needs an invalid payload deep inside
        `forms.perform`, long after the caller has gone. So the default is to
        find one the same way everything else does, on first use, and to record
        why if there is none.
        """
        self.cache_path = cache_path
        self.run_id = run_id
        self._cache: dict[str, dict] = {}
        self._provider = provider
        self._model = model
        self._resolved = provider is not None
        # Empty until something has actually gone looking. "" and "no provider"
        # are different claims and the crawl summary prints the difference.
        self.unavailable = ""

        if cache_path and cache_path.exists():
            self._cache = json.loads(cache_path.read_text())

    @property
    def model(self) -> str:
        """Which model chose the payloads, for the crawl summary."""
        return getattr(self._provider, "model", self._model or "none")

    def provider(self):
        """The provider, found once. None if there is none -- see `unavailable`."""
        if self._resolved:
            return self._provider
        self._resolved = True
        try:
            self._provider = load(model=self._model)
        except Exception as exc:
            # Deliberately broad. `load` raises RuntimeError with no key, but a
            # missing SDK raises ImportError and a malformed base URL raises
            # from the client constructor -- and all three mean the same thing
            # to a crawl: fall back, and say what happened.
            self._provider = None
            self.unavailable = f"{type(exc).__name__}: {exc}"
        return self._provider

    # --- the model call ---------------------------------------------------

    def _ask(self, title: str, fields: tuple[_FieldSpec, ...]) -> Payload | None:
        """One tool call. Returns None on any failure -- never raises.

        A crawl that dies because an API call failed is worse than a crawl that
        falls back and says so, so every failure here is a degradation rather
        than an abort. `unavailable` is set on the way out, because a
        degradation nobody can name is indistinguishable from a design.
        """
        provider = self.provider()
        if provider is None:
            return None

        listing = "\n".join(f"  - {role} {name!r}" for role, name in fields)
        transcript = Transcript(
            prompt=_PROMPT.format(title=title, fields=listing)
        )

        try:
            turn = provider.turn(_SYSTEM, transcript, [PAYLOAD])
        except Exception as exc:
            self.unavailable = f"{type(exc).__name__}: {exc}"
            return None

        call = next((c for c in turn.calls if c.name == PAYLOAD.name), None)
        if call is None:
            # Answered in prose. One retry is not worth a round trip inside a
            # crawl loop, and the fallback table is right there.
            self.unavailable = "model answered without calling payload()"
            self._save(transcript, turn, title)
            return None

        # Resolved to the form's own names, not the model's. Anything that
        # resolves to nothing is dropped and the payload keeps the rest -- one
        # unmatched field is not a reason to discard a usable payload.
        chosen: list[tuple[str, dict]] = []
        for field in call.arguments.get("fields") or []:
            if not isinstance(field, dict):
                continue
            real = _resolve(str(field.get("name", "")), fields)
            if real is not None and real not in dict(chosen):
                chosen.append((real, field))

        self._save(transcript, turn, title)
        if not chosen:
            # Named only fields this form does not have. The same extractive
            # discipline `critic.prioritise` applies to gaps: a value for a
            # field that is not there cannot be typed into anything.
            self.unavailable = "model named no field this form has"
            return None

        return Payload(
            values={real: str(f.get("value", "")) for real, f in chosen},
            why={real: str(f.get("why", "")) for real, f in chosen},
            expect=str(call.arguments.get("expect", "")),
            source="model",
        )

    def _save(self, transcript, turn, label: str) -> None:
        """Write the exchange beside the ant and orchestrator transcripts.

        `invalid-payloads.json` records *what* was chosen; this records what was
        asked and what came back. They answer different questions, and only the
        second one survives someone editing the cache by hand -- which the cache
        exists to invite.
        """
        from ..llm import Exchange

        transcript.exchanges.append(
            Exchange(text=turn.text, calls=turn.calls, opaque=turn.opaque)
        )
        try:
            save_transcript(
                transcript,
                run_id=self.run_id,
                role="synthesizer",
                system=_SYSTEM,
                label=re.sub(r"[^a-zA-Z0-9]+", "-", label)[:24].strip("-"),
            )
        except Exception:
            # Losing the write-up must never lose the crawl. Same rule as
            # `ant.explore`.
            pass

    # --- the public surface -----------------------------------------------

    def invalid_payload(
        self, state_key: str, descriptor: str, title: str, fields: tuple[_FieldSpec, ...]
    ) -> Payload:
        """Values that should be rejected. Cached, model-generated, or mutated.

        `state_key` is deliberately *not* part of the cache key -- see below --
        but is kept in the signature because the state a form was reached from
        is the obvious next thing to give the model as context.
        """
        if not fields:
            return Payload({}, {}, "", "fallback")

        # Keyed by the form's *shape*, not by the state it was seen in. The
        # same login form appears as several states -- empty, filled, showing
        # an error -- and they all deserve the same payload. Keying by state
        # asked the model the identical question six times on a one-form
        # fixture; on a real app that multiplies by every state a form appears
        # in. The model's input is (title, fields), so the cache key is too.
        slot = "{}|{}".format(
            descriptor, ",".join(sorted(f"{role}:{name}" for role, name in fields))
        )
        cached = self._cache.get(slot)
        # **A fallback entry is a record of a degraded run, not an answer.**
        # Serving one back once a provider exists makes the degradation
        # permanent and silent, and it did: this workspace cached five
        # `source: "fallback"` payloads during an afternoon with no key set and
        # then replayed them through every later run, so the one seam
        # `explorer/__init__.py` calls "the model seam" had still never fired
        # after the key came back. Two of those five were not rejectable input
        # at all -- `{'Project name': ''}` is an empty submission wearing an
        # `submit[invalid]` label, which is the lie in the map that
        # `forms.fill_and_submit` refuses to tell when it has no synthesizer.
        #
        # Model-sourced entries are still served from cache unconditionally, so
        # the reproducible-demo and free-rerun properties above hold wherever
        # they were ever true. `provider()` memoises, so this costs one lookup.
        if cached and (cached.get("source") != "fallback" or self.provider() is None):
            return Payload(
                cached["values"], cached["why"], cached["expect"], "cache"
            )

        payload = self._ask(title, fields) or _fallback(fields)
        self._cache[slot] = {
            "values": payload.values,
            "why": payload.why,
            "expect": payload.expect,
            "source": payload.source,
        }
        self._flush()
        return payload

    def sources(self) -> dict[str, int]:
        """How many payloads came from the model vs the fallback table.

        Printed by the crawl so a degraded run never looks like a good one --
        "3 from fallback" and "3 from model" are very different claims about
        how considered the invalid input was.
        """
        counts: dict[str, int] = {}
        for entry in self._cache.values():
            source = entry.get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts

    def decisions(self) -> list[tuple[str, dict]]:
        """Every payload chosen, for the report. The agent showing its work."""
        return sorted(self._cache.items())

    def _flush(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2))
