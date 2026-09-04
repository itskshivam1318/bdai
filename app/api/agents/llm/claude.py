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

# Default per the Anthropic API guidance bundled with this repo's tooling.
# Overridable because the ants are worker threads and a cheaper model may be
# the right call for a long crawl -- but that is a decision to make with a
# measurement, not a default to assume.
DEFAULT_MODEL = "claude-opus-5"


class Claude:
    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self._client = anthropic.Anthropic()

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

            if exchange.results:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": result.call_id,
                                "content": result.content,
                            }
                            for result in exchange.results
                        ],
                    }
                )

        return messages

    def turn(
        self, system: str, transcript: Transcript, tools: list[Tool]
    ) -> Turn:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
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
