"""API dependency wiring (dossier §6.1).

The read API reuses the ingestion layer's database foundation (``ingestion.db``)
rather than standing up its own — there is one openings database and the §7
storage models live in ``ingestion.storage``. This module owns only the
request-scoped SQLAlchemy ``Session`` dependency.

The engine + sessionmaker are built lazily once, the first time a request needs
them, from ``DATABASE_URL`` (the same resolution ingestion uses). Tests override
``get_session`` to bind to their in-memory SQLite database, so this module never
opens a real connection under pytest (CLAUDE.md rule 5).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ingestion import storage  # noqa: F401 — registers §7 tables on Base.metadata
from ingestion.db import create_db_engine, make_session_factory

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """The process-wide sessionmaker, created on first use from ``DATABASE_URL``."""
    global _engine, _session_factory
    if _session_factory is None:
        _engine = create_db_engine()
        _session_factory = make_session_factory(_engine)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a read-scoped ``Session`` per request.

    The API only reads, so the session is never committed here; it is closed when
    the request ends. Tests override this dependency with one bound to the test DB.
    """
    factory = get_session_factory()
    with factory() as session:
        yield session
