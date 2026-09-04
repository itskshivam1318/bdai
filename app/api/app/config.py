"""Runtime configuration.

Every worktree gets its own ports and its own SQLite file, so several copies of
this stack can run side by side without colliding. `scripts/worktree.sh` writes
the values into `.worktree-env`; falling back to the defaults here means a plain
`git clone` still runs with zero setup.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = API_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".worktree-env"), extra="ignore"
    )

    api_port: int = 8000
    web_port: int = 3000
    worktree_name: str = "main"

    # Kept relative to the API directory so each worktree naturally gets its own
    # database file -- worktrees have separate working directories on disk.
    database_url: str = f"sqlite:///{API_DIR / 'app.db'}"
    artifacts_dir: Path = API_DIR / "artifacts"

    @property
    def web_origin(self) -> str:
        return f"http://localhost:{self.web_port}"


settings = Settings()
settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
