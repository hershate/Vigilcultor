"""SQLite engine management: WAL mode, one cached engine per database path."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from vigilcultor.config import db_path

_engines: dict[str, Engine] = {}
_session_makers: dict[str, sessionmaker[Session]] = {}


def _engine_for(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def get_engine() -> Engine:
    key = str(db_path())
    if key not in _engines:
        _engines[key] = _engine_for(Path(key))
    return _engines[key]


def get_session_maker() -> sessionmaker[Session]:
    key = str(db_path())
    if key not in _session_makers:
        _session_makers[key] = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_makers[key]


def reset_cache() -> None:
    """Drop cached engines (used by tests that switch VIGILCULTOR_DATA_DIR)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _session_makers.clear()


def init_db() -> None:
    """Create all tables (M1 bootstrap; Alembic migrations arrive with M2)."""
    from vigilcultor.store import models

    models.Base.metadata.create_all(get_engine())
