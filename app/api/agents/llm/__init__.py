"""One tool-calling interface, every provider behind it.

The ant does not know which model is driving it. That is the point: `agent.md`
is the tunable part, and it has to mean the same thing to Claude, to Gemini, and
to a Claude Code subagent handed the same file. A harness written against one
SDK's message shape quietly bakes that provider into the prompt as well as the
code.

**Why not the Anthropic SDK's tool runner.** It is the recommended path for a
single-provider agent and it would be less code. But it owns the loop, and the
loop is exactly what has to be shared here -- the same turn structure, the same
transcript, the same stopping rule, whichever provider answers. So this is the
manual loop, which the SDK's own guidance says is the case for a custom
transport.

The transcript is provider-neutral and each provider serialises it on the way
out. That costs a conversion per call and buys the thing worth having: the
transcript is a plain Python object we can log, replay, diff between providers,
and hand to a Claude Code subagent without translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Tool:
    """A tool the model may call. `parameters` is plain JSON Schema.

    JSON Schema is the common denominator: Anthropic calls this field
    `input_schema` and Google calls it `parameters`, but the schema itself is
    the same document. Keeping it neutral here means a new tool is defined once.
    """

    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class Exchange:
    """One round trip: what the model said and did, and what came back.

    A list of these plus the opening prompt *is* the transcript. Storing the
    round rather than a flat message list is what makes the two serialisers
    simple -- Anthropic wants the tool calls echoed inside the assistant turn
    and the results in the following user turn, Google wants function calls and
    function responses as separate parts. Both are reconstructible from this;
    neither is reconstructible from the other's flattened form.

    **`opaque` is the provider's own assistant turn, kept verbatim.** Both
    providers attach reasoning state to a tool call that has to come back
    unchanged on the next request, and a transcript that rebuilds the turn from
    its own fields silently drops it. Gemini 3.x rejects the request outright --
    *"Function call is missing a thought_signature in functionCall parts"* --
    which is the good failure; Anthropic's equivalent is thinking blocks, where
    dropping them degrades quality without erroring.

    Reconstructing from `text` and `calls` is still the fallback, because the
    same transcript has to serialise for a provider that never produced it --
    that is what makes running one ant on Claude and the same ant on Gemini
    possible.
    """

    text: str
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()
    opaque: object | None = None
    # What the *person* said next, for the one caller that has a person in the
    # loop. An ant's round ends with tool results; a chat's round ends with a
    # follow-up question, and both are "the user turn that answers the model".
    # Modelling it here rather than widening `Transcript` into a flat message
    # list keeps the round-trip shape the two serialisers depend on -- see the
    # note above on why the round and not the message is the unit.
    #
    # Empty on every exchange an ant produces, so nothing in the colony changes.
    follow_up: str = ""

    @property
    def acted(self) -> bool:
        return bool(self.calls)


@dataclass
class Transcript:
    prompt: str
    exchanges: list[Exchange] = field(default_factory=list)


@dataclass(frozen=True)
class Turn:
    """What the model produced when asked to take one turn."""

    text: str
    calls: tuple[ToolCall, ...] = ()
    opaque: object | None = None  # the provider's raw turn; see Exchange.opaque

    @property
    def done(self) -> bool:
        """No tool calls means the model has nothing further to do."""
        return not self.calls


class Provider(Protocol):
    """Everything the ant needs from a model, and nothing else."""

    name: str
    model: str

    def turn(
        self, system: str, transcript: Transcript, tools: list[Tool]
    ) -> Turn: ...


def load(
    provider: str | None = None,
    model: str | None = None,
    notify=None,
    api_key: str | None = None,
) -> Provider:
    """Pick a provider by name, or by whichever API key is present.

    Auto-detection exists because the two of us are unlikely to have the same
    keys exported, and a harness that hard-fails on the wrong one wastes a
    minute every time. Explicit beats implicit when `provider` is passed.

    `api_key` is the bring-your-own-key path: the console sends the key the
    person typed into Advanced, and it is used *instead of* the environment for
    this one provider, never written to it. Writing it to `os.environ` would be
    the shorter fix and it is the wrong one -- exploration runs as a background
    task in a worker thread, so two runs started a second apart share one
    process, and the second person's key would silently drive the first
    person's colony. Passing it down the call is what keeps a run's key
    belonging to that run.

    A caller who passes `api_key` without `provider` is naming a secret and not
    saying what it opens, which we cannot guess from the string -- so that is an
    error rather than a key quietly ignored while the server's own is used.
    """
    import os

    from .catalog import resolve

    if api_key and provider is None:
        raise ValueError("api_key given without a provider; say which one it is")

    if provider is None:
        # OpenRouter is probed first *on cost*, not on quality. A full colony
        # run is ~78 model calls; measured against this budget that is ~$2.15
        # on `claude-opus-5` and ~$0.06 on `qwen/qwen3-coder-next`. Auto-detect
        # is the path taken by someone who has not thought about which model
        # they want, and defaulting that person to the option that buys ~160
        # runs a day rather than ~5 is the useful default for a workspace whose
        # last two live runs both died on an exhausted key.
        #
        # This only decides ties. An explicit `provider=` still wins, which is
        # what the mixed-colony split -- a strong orchestrator over cheap ants
        # -- would use.
        if os.environ.get("OPENROUTER_API_KEY"):
            provider = "openrouter"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "claude"
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            provider = "google"
        elif os.environ.get("SARVAM_API_KEY"):
            provider = "sarvam"
        else:
            raise RuntimeError(
                "No model configured. Add a key under Advanced in the console, "
                "or export OPENROUTER_API_KEY, ANTHROPIC_API_KEY or "
                "GEMINI_API_KEY, or run the deterministic crawler instead: "
                "python -m agents.explorer.crawler <url>"
            )

    spec = resolve(provider)

    # Resolved here for every provider rather than inside each one, because the
    # catalogue is what the console's Advanced panel offers -- a provider module
    # holding a second default is how the dialog came to show "Claude Haiku 4.5
    # (default)" over a run that used Opus. Precedence, widest to narrowest: the
    # catalogue's cheap default, then the provider's own `*_MODEL` variable if a
    # `.env` sets one, then whatever the caller explicitly asked for.
    chosen = model or (
        (spec.model_env and os.environ.get(spec.model_env)) or spec.default_model
    )

    if spec.id == "claude":
        from .claude import Claude

        return Claude(model=chosen, api_key=api_key)
    if spec.id == "google":
        from .gemini import Gemini

        return Gemini(model=chosen, notify=notify, api_key=api_key)

    # Every OpenAI-compatible endpoint is this one class behind a base URL, so
    # `openrouter` and `sarvam` are named defaults rather than distinct
    # providers. `OPENROUTER_BASE_URL` repoints the same code path at DeepSeek,
    # Groq, Cerebras or a local Ollama; only the key and the model string
    # change. It overrides only OpenRouter's own URL -- pointing it at Sarvam's
    # would make one variable mean two endpoints.
    from .openai_compat import OpenAICompat

    base_url = spec.base_url or ""
    if spec.id == "openrouter":
        base_url = os.environ.get("OPENROUTER_BASE_URL") or base_url

    return OpenAICompat(
        model=chosen,
        base_url=base_url,
        api_key=api_key or os.environ.get(spec.key_env),
        key_env=spec.key_env,
        name=spec.id,
        notify=notify,
    )


__all__ = [
    "Exchange",
    "Provider",
    "Tool",
    "ToolCall",
    "ToolResult",
    "Transcript",
    "Turn",
    "load",
]
