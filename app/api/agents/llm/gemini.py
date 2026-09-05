"""Google Gemini provider. Same interface, different wire shape.

`GEMINI_MODEL` overrides the default, and the ant prints which model it used on
every run so a wrong id announces itself rather than confusing a later failure.
The default was checked against `models.list()` on this account on 2026-09-04.

**Gemini 3.x requires thought signatures to be replayed.** A `functionCall` part
carries a `thought_signature` (bytes), and sending that call back without it is
a hard 400 on the *next* request -- so the failure appears one turn after the
mistake. This provider therefore replays the SDK's own content object verbatim
(`Exchange.opaque`) rather than rebuilding the turn from its parts.

Uses the `google-genai` SDK (`from google import genai`), not the older
`google-generativeai` package -- the two have different import paths and
different function-calling shapes, and the older one is the more commonly
recalled of the two.
"""

from __future__ import annotations

import os
import time
import uuid

from . import Exchange, Tool, ToolCall, Transcript, Turn

# `GEMINI_MODEL` is honoured by `llm.load` against the catalogue, which is the
# same list the console's model select shows -- see `catalog.py`. This constant
# is the fallback for constructing `Gemini` directly.
from .catalog import BY_ID  # noqa: E402

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL") or BY_ID["google"].default_model


class Gemini:
    name = "gemini"

    def __init__(
        self, model: str | None = None, notify=None, api_key: str | None = None
    ) -> None:
        # Called when a request is retried. Waiting silently for half a minute
        # is indistinguishable from hanging, and a UI watching an event stream
        # shows nothing at all -- so the wait has to announce itself.
        self._notify = notify or (lambda level, message: None)
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency guidance
            raise RuntimeError(
                "google-genai is not installed. Add it with: "
                "uv add google-genai"
            ) from exc

        # Passed in by the console's Advanced panel, or the server's own.
        key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        self.model = model or DEFAULT_MODEL
        self._genai = genai
        self._client = genai.Client(api_key=key)

    def _contents(self, transcript: Transcript) -> list[dict]:
        """Neutral transcript -> Gemini's `contents`.

        Gemini differs from Anthropic in two ways that matter here. Roles are
        `user` and `model` rather than `user` and `assistant`; and a tool result
        is a `functionResponse` part in a *user* turn keyed by function **name**
        rather than by call id. The neutral `ToolResult` carries both, so the
        same transcript serialises for either.
        """
        contents: list[dict] = [
            {"role": "user", "parts": [{"text": transcript.prompt}]}
        ]

        for exchange in transcript.exchanges:
            # Replay our own turn exactly as the SDK gave it to us -- this is
            # what carries `thought_signature` through. The rebuild below is for
            # transcripts produced by a different provider.
            if exchange.opaque is not None:
                contents.append(exchange.opaque)
                answering = self._answering(exchange)
                if answering:
                    contents.append({"role": "user", "parts": answering})
                continue

            parts: list[dict] = []
            if exchange.text:
                parts.append({"text": exchange.text})
            parts += [
                {"functionCall": {"name": call.name, "args": call.arguments}}
                for call in exchange.calls
            ]
            if parts:
                contents.append({"role": "model", "parts": parts})

            answering = self._answering(exchange)
            if answering:
                contents.append({"role": "user", "parts": answering})

        return contents

    @staticmethod
    def _answering(exchange: Exchange) -> list[dict]:
        """The user turn that answers a model turn: results, a follow-up, both.

        One turn rather than two. Gemini merges consecutive user contents
        silently rather than erroring, so appending them separately would work
        by accident -- and stop working the day it does not.
        """
        parts: list[dict] = [
            {
                "functionResponse": {
                    "name": result.name,
                    "response": {"result": result.content},
                }
            }
            for result in exchange.results
        ]
        if exchange.follow_up:
            parts.append({"text": exchange.follow_up})
        return parts

    # Gemini accepts a strict subset of JSON Schema and rejects the request --
    # not the field -- when it meets anything else. `additionalProperties` is
    # the one that bites, because Anthropic's strict tool mode *requires* it, so
    # a schema correct for one provider is a 400 on the other. Whitelisting
    # rather than blacklisting means the next unsupported keyword we adopt fails
    # by being dropped here instead of by breaking every call.
    _SCHEMA_KEYS = frozenset(
        {
            "type",
            "format",
            "description",
            "nullable",
            "enum",
            "properties",
            "required",
            "items",
            "minItems",
            "maxItems",
        }
    )

    @classmethod
    def _schema(cls, node):
        """Recursively reduce a JSON Schema to the subset Gemini accepts.

        Not a blind recursion. A JSON Schema mixes two kinds of dict at
        different depths and they must be treated differently: `properties` is a
        map whose **keys are user-chosen property names**, while every other
        object here is keyed by schema keywords. Filtering both against the
        keyword whitelist deletes every property and leaves `required` naming
        fields that no longer exist -- which Gemini reports, accurately, as
        `parameters.required[0]: property is not defined`.
        """
        if not isinstance(node, dict):
            return node

        cleaned: dict = {}
        for key, value in node.items():
            if key not in cls._SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {
                    name: cls._schema(sub) for name, sub in value.items()
                }
            elif key == "items":
                cleaned[key] = cls._schema(value)
            else:
                cleaned[key] = value

        return cleaned

    def _generate(self, system: str, transcript: Transcript, tools: list[Tool]):
        """One request, retried on transient server-side failures.

        A colony makes hundreds of calls, so 503 "experiencing high demand" is
        normal weather rather than an edge case -- it was hit repeatedly on the
        first live run, on two different models. Without this, one overloaded
        moment kills a whole exploration and loses the map with it.

        Only 429 and 5xx are retried. A 400 is our bug and retrying it wastes
        time and money while hiding the mistake.
        """
        from google.genai import errors

        config = {
            "system_instruction": system,
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": self._schema(tool.parameters),
                        }
                        for tool in tools
                    ]
                }
            ],
        }

        delay = 2.0
        for attempt in range(5):
            try:
                return self._client.models.generate_content(
                    model=self.model,
                    contents=self._contents(transcript),
                    config=config,
                )
            except (errors.ServerError, errors.ClientError) as exc:
                status = getattr(exc, "code", None)
                if status not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise

                # A 429 is two different failures wearing one status code. A
                # burst over the per-minute limit clears in seconds and is worth
                # waiting out; a *daily* quota does not clear at all, and
                # backing off five times against it wastes half a minute before
                # failing anyway. Google tells us which -- the free tier's daily
                # cap arrives with a `PerDay` quota id.
                if status == 429 and "PerDay" in str(exc):
                    raise RuntimeError(
                        f"{self.model}: daily quota exhausted on this key. "
                        "Free-tier Gemini keys allow ~20 requests per day per "
                        "model. Use a different GEMINI_MODEL, a billed key, or "
                        "OPENROUTER_API_KEY. The deterministic crawler needs no "
                        "key at all: python -m agents.explorer.crawler <url>"
                    ) from exc

                self._notify(
                    "warn",
                    f"{self.model}: {status} from the provider, retrying in "
                    f"{delay:.0f}s (attempt {attempt + 1}/5)",
                )
                time.sleep(delay)
                delay *= 2

        raise RuntimeError("unreachable")

    def turn(
        self, system: str, transcript: Transcript, tools: list[Tool]
    ) -> Turn:
        response = self._generate(system, transcript, tools)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        raw = None

        for candidate in response.candidates or []:
            raw = candidate.content
            for part in getattr(candidate.content, "parts", None) or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                call = getattr(part, "function_call", None)
                if call is not None:
                    calls.append(
                        ToolCall(
                            # Gemini supplies an id; synthesise one only if a
                            # future model stops doing so, since the neutral
                            # transcript pairs results to calls by it.
                            id=getattr(call, "id", None)
                            or f"call_{uuid.uuid4().hex[:12]}",
                            name=call.name,
                            arguments=dict(call.args or {}),
                        )
                    )

        return Turn(
            text="".join(text_parts).strip(), calls=tuple(calls), opaque=raw
        )


__all__ = ["DEFAULT_MODEL", "Gemini"]
