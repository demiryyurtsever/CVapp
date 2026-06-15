"""Database foundation for the ingestion layer (dossier §3.10).

All persistence for Layer 1 lives behind this module and ``storage.py``.
Adapters NEVER import either: they are stateless and DB-free (§3.2). Only the
pipeline orchestrator (``pipeline.py``) writes postings.

Production target is PostgreSQL (``postgresql+psycopg://…``); the SQLAlchemy
models are written to be backend-portable so the test-suite can drive them on an
in-memory SQLite database (the test conftest blocks sockets, so a real Postgres
server is unreachable from tests by design — CLAUDE.md rule 5).
"""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Sensible local default; real deployments set DATABASE_URL.
DEFAULT_DATABASE_URL = "postgresql+psycopg://localhost:5432/ibplatform"


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the ingestion layer."""


def get_database_url() -> str:
    """Resolve the database URL from the environment, falling back to the default."""
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """SQLite ignores foreign keys unless explicitly switched on per-connection.

    Postgres enforces them natively, so this is a no-op there. It keeps the
    ``postings.firm -> firms.name`` reference honest under the SQLite test DB.
    """
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_db_engine(url: str | None = None, **kwargs) -> Engine:
    """Create an engine for ``url`` (default: ``get_database_url()``)."""
    return create_engine(url or get_database_url(), **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a configured ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)
