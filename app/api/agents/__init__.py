"""The agent pipeline.

Planner, Generator, Healer and the meta-agent that coordinates them. See
`app/CLAUDE.md` for what each owns.

`explorer/` is the observation substrate underneath the Planner: it turns a live
page into evidence the rest of the pipeline can reason over. It is deliberately
model-free -- see `explorer/__init__.py`.
"""

from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> Path | None:
    """Load the nearest `.env`, walking up from this file.

    Keys used to live only in the shell that happened to run `make dev`. Open a
    new terminal and `llm.load()` raised "no model configured", every run died
    in 200ms, and the reason was visible only in the `event` table. Reading a
    file instead means the stack survives a shell restart.

    This lives at package import rather than in one entry point because there
    are four of them -- the API background task, `explorer.crawler`, `probe.py`
    and `smoke_run.py` -- and all of them read `ANTHROPIC_API_KEY`,
    `GEMINI_API_KEY` or the `AIVAR_*` credentials out of `os.environ`.

    Walking up (bounded to the repo) rather than hardcoding one path lets a
    worktree keep its own `api/.env` while a plain checkout is served by one at
    the root. `override=False` keeps an explicitly exported variable winning, so
    `ANTHROPIC_API_KEY=sk-... make dev` still does what it looks like it does.
    """
    for directory in Path(__file__).resolve().parents[:4]:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


ENV_FILE = _load_env()
