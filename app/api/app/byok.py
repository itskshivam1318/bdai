"""The caller's own model credentials, carried on the request.

    X-AIVAR-Provider: openrouter
    X-AIVAR-Key:      sk-or-v1-...
    X-AIVAR-Model:    qwen/qwen3-coder-next

**Headers, not a body field.** Three endpoints start model work -- explore,
dispatch an ant, answer a chat -- and each already has its own request model
saying something about the *task*. Which key pays for it is not part of any of
those questions, and adding the same three fields to three schemas is three
places to forget the fourth. `web/lib/api.ts` has exactly one `request()`
function, so the browser side is one place too.

**Nothing is stored.** The keys live in the browser's `localStorage` and travel
one request at a time; no row, no log line, and no `os.environ` write ever holds
one. `Choice.redacted` is what goes into an `Event`, because a timeline that
prints a key is a timeline someone screenshots.

**Absent is the normal case.** A console with an empty Advanced panel sends no
headers and `Choice.empty` resolves to whatever `api/.env` holds -- which is how
the demo machine has always worked and must keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from agents.llm.catalog import BY_ID, resolve


@dataclass(frozen=True)
class Choice:
    """Provider, key and model as the caller asked for them. Any may be unset.

    Frozen because this is handed to a `BackgroundTasks` job that outlives the
    request: a mutable object shared between the handler and a worker thread is
    a race waiting for the second concurrent run.
    """

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.provider or self.api_key or self.model)

    @property
    def redacted(self) -> str:
        """Safe to emit. Says enough to debug, not enough to spend."""
        if self.empty:
            return "server keys"
        where = "your key" if self.api_key else "server key"
        return f"{self.provider or 'auto'} / {self.model or 'default'} ({where})"

    def kwargs(self) -> dict:
        """What to splat into `agents.llm.load`."""
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
        }


def byok(
    x_aivar_provider: str | None = Header(default=None),
    x_aivar_key: str | None = Header(default=None),
    x_aivar_model: str | None = Header(default=None),
) -> Choice:
    """FastAPI dependency. Validates the provider name and nothing else.

    The provider is checked here because it is the one field we can be sure
    about without spending anything -- an unknown name is a typo or a version
    skew between this server and the tab talking to it, and finding out at
    `load()` time means the failure lands in a background task's traceback
    rather than in the dialog that caused it.

    The *key* is deliberately not validated: the only way to know a key works is
    to spend a call on it, and a settings dialog that bills you to press Save is
    worse than one that lets the first run report the failure.
    """
    provider = (x_aivar_provider or "").strip() or None
    if provider is not None:
        try:
            provider = resolve(provider).id
        except ValueError as exc:
            raise HTTPException(
                400, f"{exc}. This server knows: {', '.join(sorted(BY_ID))}"
            ) from exc

    key = (x_aivar_key or "").strip() or None
    if key and provider is None:
        # `load()` raises on this too, but there it is a 500 inside a worker
        # thread; here it is the dialog's own mistake, answered in the dialog.
        raise HTTPException(400, "a key was sent without a provider")

    return Choice(
        provider=provider,
        api_key=key,
        model=(x_aivar_model or "").strip() or None,
    )
