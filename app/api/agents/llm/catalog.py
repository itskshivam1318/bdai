"""Which providers a user may bring a key for, and what each one can run.

**One table, two consumers.** `load()` resolves a provider from here, and
`GET /api/providers` serves the same rows to the settings dialog. The
alternative -- a `MODELS` array in `SettingsDialog.tsx` beside a `if provider
== ...` ladder in `load()` -- is two lists that drift, and the drift is
invisible until someone picks a model the backend cannot construct.

`default_model` is the cheap one on purpose. Someone opening this dialog has
told us their provider, not their budget, and a colony run is ~78 model calls:
measured 2026-09-04, that is ~$0.09 on `qwen/qwen3-coder-next` against ~$3.42 on
`claude-opus-5`. Defaulting the undecided to the 38x cheaper option is the
difference between a demo that runs all afternoon and one that dies on a spent
key -- which is exactly how the last two live runs ended.

The lists are not exhaustive and are not meant to be: OpenRouter alone routes
hundreds of models. The dialog offers a free-text escape hatch, and anything
typed there is passed through to the provider verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass


#: What we send as `max_tokens` for a model the catalogue does not list -- the
#: lowest true cap among the models it does (DeepSeek's 16384). Asking a model
#: for more output than it can produce is a 400 from the provider, not a clamp,
#: so an unknown model gets the value that is safe for the smallest thing we
#: have ever pointed this code at.
FALLBACK_MAX_OUTPUT = 16384


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    note: str = ""
    #: Ceiling on one reply, sent as `max_tokens`. This is *not* the model's
    #: advertised cap -- `qwen3-coder-next` will emit 235929 tokens and
    #: `minimax-m3` 943718 -- because two other limits bind first:
    #:
    #: 1. `max_tokens` + prompt must fit the context window, and the provider
    #:    errors rather than clamping ("This endpoint's maximum context length
    #:    is 262144 tokens. However, you requested about 1000001"). The largest
    #:    transcript this repo has ever recorded is ~8.4k tokens, so 32768
    #:    leaves >120k of headroom on every model listed here.
    #: 2. A paid route reserves the full `max_tokens` against the balance
    #:    before it starts, so the number is also a spend ceiling per call.
    #:
    #: Where a model's true cap is *below* that budget it wins -- DeepSeek
    #: tops out at 16384 and 32768 would simply fail. Verified against
    #: `GET https://openrouter.ai/api/v1/models`, 2026-09-05.
    max_output: int = FALLBACK_MAX_OUTPUT


@dataclass(frozen=True)
class ProviderSpec:
    """One provider, as both `load()` and the settings dialog see it."""

    id: str
    label: str
    #: The server's own key for this provider. A run with no BYOK key falls back
    #: to whatever is in `api/.env`, which is what makes the dialog optional.
    key_env: str
    #: Shown as the placeholder, so a Gemini key pasted into the Claude field
    #: looks wrong before it is saved rather than after the run fails.
    key_hint: str
    models: tuple[ModelChoice, ...]
    #: The cheapest listed model. See the module docstring.
    default_model: str
    #: Set for the OpenAI-compatible providers; None for the two native SDKs.
    base_url: str | None = None
    #: An environment variable that changes this provider's default model. It
    #: sits *between* the catalogue default and an explicit choice: a `.env` may
    #: move the default, and a model named in the console still wins over it --
    #: otherwise the dialog says one model and the run prints another.
    model_env: str | None = None


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        key_env="OPENROUTER_API_KEY",
        key_hint="sk-or-v1-…",
        default_model="qwen/qwen3-coder-next",
        base_url="https://openrouter.ai/api/v1",
        model_env="OPENROUTER_MODEL",
        models=(
            ModelChoice("qwen/qwen3-coder-next", "Qwen3 Coder Next", "cheap, good at tools", max_output=32768),
            ModelChoice("minimax/minimax-m3:free", "MiniMax M3 (free)", "free tier, rate limited", max_output=32768),
            ModelChoice("deepseek/deepseek-chat", "DeepSeek Chat", max_output=16384),
            ModelChoice("anthropic/claude-haiku-4.5", "Claude Haiku 4.5 (routed)", max_output=32768),
            ModelChoice("google/gemini-2.5-flash", "Gemini 2.5 Flash (routed)", max_output=32768),
        ),
    ),
    ProviderSpec(
        id="claude",
        label="Claude (Anthropic)",
        key_env="ANTHROPIC_API_KEY",
        key_hint="sk-ant-…",
        default_model="claude-haiku-4-5-20251001",
        models=(
            ModelChoice("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "cheapest", max_output=32768),
            ModelChoice("claude-sonnet-5", "Claude Sonnet 5", max_output=32768),
            ModelChoice("claude-opus-5", "Claude Opus 5", "~$3.42 per colony run", max_output=32768),
        ),
    ),
    ProviderSpec(
        id="google",
        label="Google (Gemini)",
        key_env="GEMINI_API_KEY",
        key_hint="AIza…",
        default_model="gemini-3.8-flash",
        model_env="GEMINI_MODEL",
        models=(
            ModelChoice("gemini-3.8-flash", "Gemini 3.8 Flash", "cheapest", max_output=32768),
            ModelChoice("gemini-2.5-pro", "Gemini 2.5 Pro", max_output=32768),
        ),
    ),
    ProviderSpec(
        id="sarvam",
        label="Sarvam AI",
        key_env="SARVAM_API_KEY",
        key_hint="sk_…",
        default_model="sarvam-m",
        base_url="https://api.sarvam.ai/v1",
        models=(ModelChoice("sarvam-m", "Sarvam-M", max_output=8192),),
    ),
)

BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in PROVIDERS}

# `gemini` is what `load()` has always been called with internally and what
# `agents/` docstrings name; `google` is what the vendor calls itself and what
# the dialog shows. Both resolve to the same provider rather than one of them
# being renamed, because renaming would break every `.env` and every caller.
# `openai-compat` is the generic name for the OpenRouter class behind a
# different base URL -- see `openai_compat.py`.
ALIASES: dict[str, str] = {"gemini": "google", "openai-compat": "openrouter"}


def resolve(provider: str) -> ProviderSpec:
    """The spec for a provider name, accepting either side of an alias."""
    canonical = ALIASES.get(provider, provider)
    spec = BY_ID.get(canonical)
    if spec is None:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of "
            f"{', '.join(sorted(BY_ID))}"
        )
    return spec


def max_output_for(model: str) -> int:
    """The `max_tokens` ceiling for a model, from whichever provider lists it.

    Searched across every provider rather than within one because the same
    model reaches us by two routes -- `anthropic/claude-haiku-4.5` through
    OpenRouter and `claude-haiku-4-5-20251001` through Anthropic's own SDK --
    and a caller that has only the model string should not have to know which.

    An unlisted model is the normal case, not an error: the settings dialog
    offers a free-text box and OpenRouter routes hundreds of models. Those get
    `FALLBACK_MAX_OUTPUT`, which is low enough to be safe everywhere we have
    looked and still 4x what this code sent before.
    """
    for spec in PROVIDERS:
        for choice in spec.models:
            if choice.id == model:
                return choice.max_output
    return FALLBACK_MAX_OUTPUT


def as_json() -> list[dict]:
    """The catalogue as the settings dialog consumes it. No keys, only names."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "key_env": spec.key_env,
            "key_hint": spec.key_hint,
            "default_model": spec.default_model,
            "models": [
                {"id": m.id, "label": m.label, "note": m.note} for m in spec.models
            ],
        }
        for spec in PROVIDERS
    ]
