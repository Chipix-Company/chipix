import os
from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./chipverify.db")

Base = declarative_base()


def _build_engine_and_session(database_url: str):
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}

    new_engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    if is_sqlite:

        # Enable WAL mode and busy timeout for better concurrent access.
        @event.listens_for(new_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    new_session_local = sessionmaker(autocommit=False, autoflush=False, bind=new_engine)
    return new_engine, new_session_local, is_sqlite


engine, SessionLocal, IS_SQLITE = _build_engine_and_session(DATABASE_URL)


def rebuild_sqlite_engine(database_url: str) -> None:
    """Rebuild the module-level engine/session for a fresh SQLite file."""

    global DATABASE_URL, engine, SessionLocal, IS_SQLITE

    DATABASE_URL = database_url
    engine, SessionLocal, IS_SQLITE = _build_engine_and_session(DATABASE_URL)


def get_sqlite_database_path() -> Path | None:
    """Return the on-disk SQLite database path when SQLite is in use."""

    if not IS_SQLITE:
        return None

    database_name = engine.url.database
    if not database_name or database_name in {":memory:", ":memory"}:
        return None

    db_path = Path(database_name)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    return db_path


def _sqlite_sidecar_paths(db_path: Path) -> list[Path]:
    return [
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ]


def quarantine_sqlite_database(reason: str | None = None) -> Path | None:
    """Move a corrupted SQLite database aside and remove its sidecar files."""

    db_path = get_sqlite_database_path()
    if db_path is None or not db_path.exists():
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    reason_slug = ""
    if reason:
        reason_slug = "-" + "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in reason.lower().strip()
        ).strip("-")
    quarantine_path = db_path.with_name(
        f"{db_path.stem}.{timestamp}{reason_slug}.corrupt{db_path.suffix}"
    )

    for sidecar in _sqlite_sidecar_paths(db_path):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # If the sidecar is locked, continue and let the main file move still
            # attempt to succeed. The next startup can clean it up.
            pass

    shutil.move(str(db_path), str(quarantine_path))
    return quarantine_path


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
