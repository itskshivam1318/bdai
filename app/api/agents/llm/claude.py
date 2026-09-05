"""Anthropic provider.

Manual tool loop rather than `client.beta.messages.tool_runner`. The runner is
the recommended path for a single-provider agent and would be less code, but it
owns the loop, and the loop is the thing shared with Gemini. The SDK's own
guidance names a custom transport as the case for dropping to the manual loop.

Verified against `anthropic` 1.3.0.
"""

from __future__ import annotations

import os

from . import Exchange, Tool, ToolCall, Transcript, Turn

# One answer, and it lives in the catalogue that the console's model select is
# also drawn from -- see `catalog.py`. It was `claude-opus-5` here while the
# dialog offered something else, which is a disagreement a run reports only in
# the one line naming the model it actually used.
#
# The measurement that settles which: a colony run is ~78 model calls, ~$3.42
# on Opus against ~$0.09 on a cheap route. Opus is still one option away.
from .catalog import BY_ID, max_output_for  # noqa: E402

DEFAULT_MODEL = BY_ID["claude"].default_model


class Claude:
    name = "claude"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        import anthropic

        # A key passed in comes from the console's Advanced panel and belongs to
        # this run only, so it is handed to the client rather than exported --
        # see the note in `llm.load`. Absent one we fall back to the server's.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model = model or DEFAULT_MODEL
        # Anthropic *requires* `max_tokens`, so this cannot be omitted the way
        # a chat-completions call could; the number comes from the same table
        # the OpenRouter path reads, so "how long may one reply be" has one
        # answer per model rather than one per provider class. See
        # `openai_compat.MAX_TOKENS_ENV` for why the override exists.
        override = os.environ.get("LLM_MAX_TOKENS")
        self.max_tokens = int(override) if override else max_output_for(self.model)
        self._client = anthropic.Anthropic(api_key=key)

    def _messages(self, transcript: Transcript) -> list[dict]:
        """Neutral transcript -> Anthropic's message shape.

        The shape is load-bearing: tool calls are echoed back inside the
        assistant turn, and their results arrive as a *single* following user
        turn. Splitting results across several user messages teaches the model
        to stop making parallel calls, so all results for one round go together
        even when the ant only ever makes one at a time.
        """
        messages: list[dict] = [{"role": "user", "content": transcript.prompt}]

        for exchange in transcript.exchanges:
            content: list[dict] = []
            if exchange.text:
                content.append({"type": "text", "text": exchange.text})
            content += [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in exchange.calls
            ]
            if content:
                messages.append({"role": "assistant", "content": content})

            # Tool results and a human follow-up are the same thing to the API
            # -- the user turn that answers the model -- so they go in one
            # message. Appending two `user` messages instead would break the
            # alternation the Messages API requires the moment a caller has
            # both, which is why this is built as one content list.
            answering: list[dict] = [
                {
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": result.content,
                }
                for result in exchange.results
            ]
            if exchange.follow_up:
                answering.append({"type": "text", "text": exchange.follow_up})
            if answering:
                messages.append({"role": "user", "content": answering})

        return messages

    def turn(
        self, system: str, transcript: Transcript, tools: list[Tool]
    ) -> Turn:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ],
            messages=self._messages(transcript),
        )

        # Populated only on a refusal; guard rather than assume.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise RuntimeError(
                f"model declined: {getattr(detail, 'explanation', 'no reason given')}"
            )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        calls = tuple(
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        )

        return Turn(text=text.strip(), calls=calls)


__all__ = ["Claude", "DEFAULT_MODEL", "Exchange"]
