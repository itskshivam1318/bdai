from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from .config import settings

engine = create_engine(
    settings.database_url,
    # SQLite + FastAPI's threadpool need this; harmless elsewhere.
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    # No migrations on purpose. In a 30-hour build, `rm app.db` is the migration
    # tool -- see CLAUDE.md.
    #
    # The import is load-bearing, not tidiness: `create_all` builds whatever is
    # registered on `SQLModel.metadata`, and a model class registers itself when
    # its module is imported. Without this line `init_db()` silently creates
    # *nothing* for any caller that has not already imported the models -- which
    # is every script that only wanted a database.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
