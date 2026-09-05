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
    _add_missing_columns()
    adopt_orphan_chat()


# Columns `create_all` will never add, because it only creates tables it cannot
# find. The rule for this list: a column may go here only if an existing row is
# *correct* without it -- adding a nullable field or one with a default. Anything
# that needs a value computed from the old row is a real migration, and `rm
# app.db` is still the tool for that.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "chatmessage": [("thread_id", "INTEGER")],
    "testsession": [("context", "TEXT")],
    "testcase": [("node", "TEXT"), ("suite_version", "TEXT")],
}


def _add_missing_columns(target=None) -> None:
    """The narrowest thing that deserves the name migration.

    A hackathon database is disposable right up until it holds the map of a
    twenty-minute crawl, and then deleting it to add a nullable column costs the
    demo. This adds the column instead, and does nothing at all on a fresh
    database where `create_all` has already put it there.

    `target` exists so `app.probe` can point this at a deliberately old-shaped
    database and watch the column appear. Production callers pass nothing and
    get the module engine, which is the only one this should ever touch by
    default.
    """
    with (target or engine).connect() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not existing:  # table not created yet -- nothing to alter
                continue
            for name, ddl in columns:
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )
            conn.commit()


def adopt_orphan_chat() -> None:
    """Give pre-threads messages a thread, one per session, oldest first.

    The alternative was to let them be: a null `thread_id` is not an error, and
    nothing crashes. But those rows are somebody's actual questions about a map
    that is still on screen, and a console that silently stops showing them is
    indistinguishable from one that lost them.
    """
    from sqlmodel import Session as DbSession
    from sqlmodel import select

    from .models import ChatMessage, ChatThread

    with DbSession(engine) as db:
        orphans = db.exec(
            select(ChatMessage)
            .where(ChatMessage.thread_id == None)  # noqa: E711 -- SQL, not Python
            .order_by(ChatMessage.id)
        ).all()
        if not orphans:
            return

        threads: dict[int | None, ChatThread] = {}
        for message in orphans:
            thread = threads.get(message.session_id)
            if thread is None:
                thread = ChatThread(
                    session_id=message.session_id, title="Earlier questions"
                )
                db.add(thread)
                db.commit()
                db.refresh(thread)
                threads[message.session_id] = thread
            message.thread_id = thread.id
            db.add(message)
        db.commit()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
