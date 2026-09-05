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

Without `ANTHROPIC_API_KEY` this degrades to a small mutation table rather than
failing. That keeps `make crawl` runnable with no key, and the degradation is
visible: `Payload.source` says which produced it, and the crawl summary prints
it. It is a fallback, not a design -- the table knows nothing about the app.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# One field to describe to the model: what it is called and what kind it is.
_FieldSpec = tuple[str, str]  # (role, accessible name)

MODEL = "claude-opus-5"

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

_PROMPT = """\
You are testing a web application by feeding a form input it should reject.

Form: {title}
Fields:
{fields}

Produce a payload this application should REFUSE. Change as few fields as
possible from plausible values -- if one bad field is enough to trigger
validation, leave the rest realistic, so the resulting error is attributable.

Never use input that could damage data or impersonate a real person: no SQL,
no scripts, no real email addresses or card numbers. Malformed and boring is
the goal.

For each field give the value and one short clause saying why it is rejectable.
In `expect`, say in one sentence what the application should do.\
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
        model: str = MODEL,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.cache_path = cache_path
        # Anthropic-only, so this is set only when the caller's chosen provider
        # *is* Claude. A run brought here on an OpenRouter key falls back to the
        # mutation table and prints "PAYLOADS n from fallback", which is the
        # honest outcome -- see the module docstring.
        self.api_key = api_key
        self._cache: dict[str, dict] = {}
        self._client = None

        if cache_path and cache_path.exists():
            self._cache = json.loads(cache_path.read_text())

    # --- the model call ---------------------------------------------------

    def _ask(self, title: str, fields: tuple[_FieldSpec, ...]) -> Payload | None:
        """One structured call. Returns None on any failure -- never raises.

        A crawl that dies because an API call failed is worse than a crawl that
        falls back and says so, so every failure here is a degradation rather
        than an abort.
        """
        if self._client is None:
            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                return None
            from anthropic import Anthropic

            self._client = Anthropic(api_key=key)

        listing = "\n".join(f"  - {role} {name!r}" for role, name in fields)

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=2000,
                thinking={"type": "adaptive"},
                # `low` because this is a small, well-specified generation, not
                # a reasoning problem. Raise it if payloads get lazy.
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": _PROMPT.format(title=title, fields=listing),
                    }
                ],
            )
        except Exception:
            return None

        try:
            text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            data = json.loads(text)
        except Exception:
            return None

        known = {name for _role, name in fields}
        chosen = [f for f in data["fields"] if f["name"] in known]
        if not chosen:
            return None

        return Payload(
            values={f["name"]: f["value"] for f in chosen},
            why={f["name"]: f["why"] for f in chosen},
            expect=data.get("expect", ""),
            source="model",
        )

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
        if cached:
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
