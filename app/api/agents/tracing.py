"""Local observability for the colony: every prompt, response and tool call.

    cd app/api && uv run python -m agents.tracing      # launch the UI and wait

Two consumers, deliberately separate, because they want different things:

    Phoenix        "why did the ant do that?"  -- full prompts, raw responses,
                   tool calls, latency, token counts. Developer-facing, for
                   tuning `prompts/*.md`. Ephemeral by default.

    transcripts/   "show me the agent's reasoning" -- a durable JSON record per
                   ant, written beside the run's other evidence. This is the
                   one the demo needs: the brief pays 15% for presenting the
                   agent's decisions, and a trace UI on a laptop is not that.

**Phoenix runs entirely locally.** `arize-phoenix` starts a UI on :6006 and
collects over OpenTelemetry; nothing leaves the machine, which matters when the
traces contain the credentials an ant typed into a login form.

**Instrumentation is automatic and provider-agnostic.** `register(auto_instrument=
True)` discovers whichever OpenInference instrumentors are installed, so both
the Gemini and Anthropic SDKs are captured without either provider module
importing anything from here. That keeps `llm/` free of observability code --
the tracing is a decision made once, at startup, by whoever launches the run.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

# Resolved from __file__ so it follows `api/` wherever it moves -- the same
# rule `app/config.py` follows. Deliberately not imported from there: `agents/`
# must not depend on the web layer, so that a colony can run with no API at all.
TRANSCRIPTS = Path(__file__).resolve().parent.parent / "artifacts" / "transcripts"

_ENDPOINT = os.environ.get("PHOENIX_URL", "http://localhost:6006")
_started = False


def _collector_at(url: str) -> bool:
    """Is a Phoenix already listening there? A short timeout on purpose --
    this runs before every traced run and must never be the thing that hangs."""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=1.5)
        return True
    except (urllib.error.URLError, OSError):
        return False


def start(project: str = "aivar-explorer", launch_ui: bool | None = None) -> str | None:
    """Turn on tracing. Returns the UI URL, or None if tracing is off.

    Off unless asked for: `AIVAR_TRACE=1`, or an explicit call. A colony run
    should not silently start a web server, and a teammate running `make dev`
    should not find port 6006 taken by something they did not ask for.

    Safe to call more than once -- instrumenting twice produces duplicate spans
    for every call, which is worse than not tracing at all because it makes the
    latency numbers lie.
    """
    global _started

    if launch_ui is None:
        launch_ui = os.environ.get("AIVAR_TRACE", "").lower() in ("1", "true", "yes")
    if not launch_ui or _started:
        return None

    from phoenix.otel import register

    # Attach to a Phoenix that is already listening before starting one of our
    # own. An in-process Phoenix dies with the run that launched it, taking the
    # traces with it -- which is precisely backwards, since the run you most
    # want to inspect is the one that just ended. `make trace` starts a durable
    # one; this finds it.
    existing = _collector_at(_ENDPOINT)
    if existing:
        url = _ENDPOINT
    else:
        import phoenix as px

        url = str(px.launch_app().url)

    # auto_instrument discovers every installed OpenInference package, so adding
    # a third provider later needs a dependency and no code.
    register(project_name=project, auto_instrument=True, verbose=False)
    _started = True
    return url


def _plain(value):
    """Make a transcript JSON-safe without losing what it says.

    Provider `opaque` payloads (Gemini's `thought_signature` is raw bytes) and
    SDK content objects are deliberately rendered as short placeholders rather
    than dropped: their *presence* is the interesting fact when debugging a
    replay failure, and their contents are unreadable anyway.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if is_dataclass(value):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return f"<{type(value).__name__}>"


def save_transcript(
    transcript,
    *,
    run_id: int | None,
    role: str,
    system: str,
    label: str = "",
) -> Path:
    """Write one agent's whole conversation to disk. Returns the path.

    The system prompt is stored alongside the exchanges, and that is not
    redundant: `prompts/*.md` is edited constantly during a hackathon, so a
    transcript that only records the conversation cannot tell you which version
    of the instructions produced it. A week later that is the only question
    anyone asks.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    directory = TRANSCRIPTS / (f"run-{run_id}" if run_id is not None else "adhoc")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}-{role}{('-' + label) if label else ''}.json"

    path.write_text(
        json.dumps(
            {
                "role": role,
                "run_id": run_id,
                "label": label,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "system": system,
                "prompt": transcript.prompt,
                "exchanges": [
                    {
                        "text": exchange.text,
                        "calls": [
                            {
                                "name": call.name,
                                "arguments": _plain(call.arguments),
                            }
                            for call in exchange.calls
                        ],
                        "results": [
                            {"name": result.name, "content": result.content}
                            for result in exchange.results
                        ],
                        # Not the payload -- just whether one survived. A missing
                        # signature here is the shape of a replay bug.
                        "provider_state": _plain(exchange.opaque) is not None,
                    }
                    for exchange in transcript.exchanges
                ],
            },
            indent=2,
        )
    )
    return path


def main() -> int:
    url = start(launch_ui=True)
    print(f"PHOENIX     {url}")
    print(f"TRANSCRIPTS {TRANSCRIPTS}")
    print()
    print("Leave this running, then in another shell:")
    print("  AIVAR_TRACE=1 uv run python -m agents.orchestrator <url>")
    print()
    print("Ctrl-C to stop.")
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
