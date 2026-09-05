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
    "testsession": [("context", "TEXT"), ("uid", "TEXT")],
    "testcase": [("node", "TEXT"), ("suite_version", "TEXT")],
    # A row with neither is correct: it is an edge no kept suite has healed
    # onto, which is every edge until a replay says otherwise.
    "statetransition": [("healed_from", "TEXT"), ("healed_rung", "TEXT")],
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
    _backfill_session_uids(target)


def _backfill_session_uids(target=None) -> None:
    """Give every session that predates `uid` one, and only those.

    The rule above the column list still holds -- a row with a null `uid` is
    *correct*, nothing crashes -- but it is not useful: `directory_for` falls
    back to the target-only path for a session with no uid, which is the shared
    suite this exists to stop. A random value is not computed from the old row,
    so this stays a backfill rather than the real migration `make reset` is for.

    One statement, and a no-op on a fresh database.
    """
    from uuid import uuid4

    with (target or engine).connect() as conn:
        names = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(testsession)")
        }
        if "uid" not in names:
            return
        rows = conn.exec_driver_sql(
            "SELECT id FROM testsession WHERE uid IS NULL OR uid = ''"
        ).fetchall()
        for (row_id,) in rows:
            conn.exec_driver_sql(
                "UPDATE testsession SET uid = ? WHERE id = ?",
                (uuid4().hex[:12], row_id),
            )
        if rows:
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
