"""One provider for every endpoint that speaks OpenAI chat-completions.

OpenRouter, DeepSeek, Groq, Cerebras, Together, Fireworks and a local Ollama
are not six integrations. They are one wire format behind six base URLs, so
this is a single class parameterised by `base_url` -- swapping models is a
string, and swapping vendors is a string and a key.

**Why `httpx` and not the `openai` SDK.** Chat-completions is one POST. The SDK
would add a dependency whose only job here is to serialise a dict we already
have to build by hand, and its typed response objects are modelled on OpenAI's
own server -- which is not the server we are talking to. The vendors above
disagree at the edges (see `_arguments`), and a raw dict makes those
disagreements visible instead of raising a validation error three layers down.
`httpx` is already a dependency for the API.

Verified against OpenRouter, 2026-09-04.
"""

from __future__ import annotations

import json
import os
import time

from . import Exchange, Tool, ToolCall, Transcript, Turn

# Cheap, fast, and reliable at tool calling -- the three things an ant needs.
# The colony is ~78 model calls per run and almost all of them are mechanical
# click-and-observe, so per-token cost dominates the bill far more than the
# marginal quality of any single decision. Override per run; see `load()`.
#
# Both constants come from the catalogue, so the console's OpenRouter select and
# this class cannot disagree about what "default" means -- see `catalog.py`.
from .catalog import BY_ID, max_output_for  # noqa: E402

DEFAULT_MODEL = BY_ID["openrouter"].default_model

# Ceiling on one reply, resolved per model in `__init__` -- see `catalog.py`,
# which holds the number and the arithmetic behind it. It was a flat 4096 here,
# which is 14% of what the default model will emit and 2% of what the free one
# will, and a long `finish` -- the one call that returns every flow and the
# summary -- was being cut off inside that.
#
# `LLM_MAX_TOKENS` overrides the catalogue for every model at once, and exists
# for one specific emergency: a provider reserves the full `max_tokens` against
# the balance *before* it starts, so a nearly-empty account 402s a large request
# outright ("requires more credits, or fewer max_tokens. You requested up to
# 32768 tokens, but can only afford 268"). Setting it low trades truncated
# replies for a run that happens at all. Prefer topping up, or a `:free` route,
# which is not reserved against and takes the full ceiling.
MAX_TOKENS_ENV = "LLM_MAX_TOKENS"
DEFAULT_BASE_URL = BY_ID["openrouter"].base_url or "https://openrouter.ai/api/v1"

# 429 and 5xx are weather; a colony making hundreds of calls will meet them.
# 400 is our bug, and retrying it hides the mistake while spending the clock.
RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenAICompat:
    """Everything the ant needs, spoken as chat-completions."""

    name = "openai-compat"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        notify=None,
        timeout: float = 120.0,
        key_env: str = "OPENROUTER_API_KEY",
        name: str | None = None,
    ) -> None:
        import httpx

        # `key_env` is named rather than assumed because this one class serves
        # every OpenAI-compatible endpoint -- OpenRouter and Sarvam among them --
        # and telling someone with a Sarvam key that "OPENROUTER_API_KEY is not
        # set" sends them to fix the wrong variable.
        key = api_key or os.environ.get(key_env)
        if not key:
            raise RuntimeError(f"{key_env} is not set")

        # Shadows the class attribute so the console's "model: openrouter /
        # qwen3-coder-next" line names the endpoint the key belongs to rather
        # than the wire format it happens to speak.
        self.name = name or self.name
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Per model, not per class: this one object serves DeepSeek at 16384
        # and MiniMax at 32768, and the difference between them is a 400 rather
        # than a clamp. The env override is read here rather than at import so
        # that a `.env` loaded after this module -- which is every entry point
        # except `probe.py` -- still takes effect.
        override = os.environ.get(MAX_TOKENS_ENV)
        self.max_tokens = int(override) if override else max_output_for(model)
        self._notify = notify or (lambda level, message: None)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                # OpenRouter attributes traffic by these two and they are
                # harmless everywhere else -- an unknown header is ignored.
                "HTTP-Referer": "https://github.com/aivar/agent",
                "X-Title": "AIVAR",
            },
        )

    def _messages(self, system: str, transcript: Transcript) -> list[dict]:
        """Neutral transcript -> chat-completions message list.

        Two differences from the Anthropic shape are load-bearing:

        1. The system prompt is a *message* with `role: "system"`, not a
           top-level field.
        2. Tool results are **one message each**, `role: "tool"`, keyed by
           `tool_call_id`. Anthropic wants every result for a round batched
           into a single user turn; here batching them would be a protocol
           error, not a style choice. This is the exact inverse of the rule in
           `claude.py`, which is why the neutral `Exchange` stores the round
           rather than a flat message list.

        Where the provider handed us its own assistant turn we replay that
        verbatim (`Exchange.opaque`) rather than rebuilding it. Most
        open-weight models carry no reasoning state and it makes no difference;
        the ones that do (`reasoning_details` on some OpenRouter routes) break
        silently -- degraded quality, no error -- if it is dropped.
        """
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": transcript.prompt},
        ]

        for exchange in transcript.exchanges:
            if isinstance(exchange.opaque, dict):
                messages.append(exchange.opaque)
            elif exchange.text or exchange.calls:
                assistant: dict = {"role": "assistant", "content": exchange.text or None}
                if exchange.calls:
                    assistant["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in exchange.calls
                    ]
                messages.append(assistant)

            messages += [
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.content,
                }
                for result in exchange.results
            ]

            # A human follow-up is an ordinary user message here -- unlike the
            # other two providers, chat-completions keeps tool results in their
            # own `tool` role, so this needs no merging.
            if exchange.follow_up:
                messages.append({"role": "user", "content": exchange.follow_up})

        return messages

    @staticmethod
    def _tools(tools: list[Tool]) -> list[dict]:
        """JSON Schema passes through untouched.

        Unlike Gemini -- which accepts a narrow keyword subset and needs
        `Gemini._schema` to prune it -- chat-completions takes plain JSON
        Schema, which is what `Tool.parameters` already is. Nothing to clean.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _arguments(raw) -> dict:
        """A tool call's arguments, whatever the vendor decided that means.

        The spec says `arguments` is a JSON **string**, so it needs decoding --
        never string-matched. But the cheap models this provider exists to
        reach are less disciplined than the spec: some routes return an already
        decoded object, and a model that decides a tool takes no arguments may
        send `""` or `"null"` instead of `"{}"`.

        None of those are our bug and none are worth killing a run over -- a
        malformed argument blob costs one ant action, and the ant's own budget
        already bounds that. Raising here would instead lose the whole map.
        """
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _post(self, payload: dict) -> dict:
        """One request, retried on transient failures.

        Mirrors `Gemini._generate`: only transient statuses are retried, and a
        429 that will never clear is reported as such rather than backed off
        against five times. OpenRouter distinguishes the two by message -- an
        exhausted *credit balance* is permanent until topped up, while a rate
        limit is seconds of waiting.
        """
        import httpx

        delay = 2.0
        for attempt in range(5):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.RequestError as exc:
                if attempt == 4:
                    raise
                self._notify(
                    "warn",
                    f"{self.model}: {type(exc).__name__} reaching the provider, "
                    f"retrying in {delay:.0f}s (attempt {attempt + 1}/5)",
                )
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code == 200:
                return response.json()

            body = response.text[:400]
            if response.status_code not in RETRY_STATUSES or attempt == 4:
                # 402 is the credit reservation, and its message names the
                # number that would have worked ("can only afford 268"). It is
                # not retryable and it is not a bug in the request, so say what
                # to turn rather than leaving the raw body to be read as one.
                hint = ""
                if response.status_code == 402:
                    hint = (
                        f" -- the ceiling is {self.max_tokens} tokens; set "
                        f"{MAX_TOKENS_ENV} below the affordable number above to "
                        "trade truncated replies for a run, or use a `:free` "
                        "route, which is not reserved against"
                    )
                raise RuntimeError(
                    f"{self.model}: {response.status_code} from the provider: "
                    f"{body}{hint}"
                )

            if response.status_code == 429 and (
                "credit" in body.lower() or "quota" in body.lower()
            ):
                raise RuntimeError(
                    f"{self.model}: the key is out of credit. Top it up at "
                    "https://openrouter.ai/credits, switch OPENROUTER_MODEL to "
                    "a `:free` route, or run the deterministic crawler, which "
                    "needs no key at all: python -m agents.explorer.crawler <url>"
                )

            self._notify(
                "warn",
                f"{self.model}: {response.status_code} from the provider, "
                f"retrying in {delay:.0f}s (attempt {attempt + 1}/5)",
            )
            time.sleep(delay)
            delay *= 2

        raise RuntimeError("unreachable")

    def turn(
        self, system: str, transcript: Transcript, tools: list[Tool]
    ) -> Turn:
        body = self._post(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": self._messages(system, transcript),
                "tools": self._tools(tools),
                "tool_choice": "auto",
            }
        )

        # OpenRouter reports upstream failures as a 200 with an `error` key --
        # a provider timed out, a route was unavailable. Reading `choices`
        # first would raise KeyError and bury the actual reason.
        if "error" in body and not body.get("choices"):
            message = body["error"]
            if isinstance(message, dict):
                message = message.get("message", message)
            raise RuntimeError(f"{self.model}: {message}")

        choice = body["choices"][0]
        message = choice["message"]

        # Nothing read `finish_reason` before this, which is what let a ceiling
        # set too low present as a quality problem instead of a config one: the
        # model stopped mid-sentence, `content` was a stump, and the pipeline
        # consumed the stump as a finished answer. A cut-off `tool_calls`
        # payload is worse -- `_arguments` sees truncated JSON and yields {}.
        # Warned rather than raised so one long `finish` cannot kill a colony
        # run that is otherwise complete; the console shows it beside the
        # retry warnings.
        if choice.get("finish_reason") == "length":
            self._notify(
                "warn",
                f"{self.model}: the reply hit the {self.max_tokens}-token "
                f"ceiling and was cut off -- raise it in catalog.py, or set "
                f"{MAX_TOKENS_ENV} higher",
            )

        calls = tuple(
            ToolCall(
                id=call["id"],
                name=call["function"]["name"],
                arguments=self._arguments(call["function"].get("arguments")),
            )
            for call in (message.get("tool_calls") or [])
        )

        return Turn(
            text=(message.get("content") or "").strip(),
            calls=calls,
            opaque=message,
        )


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "Exchange", "OpenAICompat"]
