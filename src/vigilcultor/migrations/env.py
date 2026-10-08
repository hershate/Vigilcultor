"""Alembic migration environment bound to the application engine and metadata."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from vigilcultor.store import models
from vigilcultor.store.db import get_engine

target_metadata = models.Base.metadata


def run_migrations_online() -> None:
    engine: Engine = get_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly ALTERs
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
