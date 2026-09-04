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
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
