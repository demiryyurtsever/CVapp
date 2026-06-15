"""Test guards and shared fixtures.

CLAUDE.md rule 5 / dossier §3.12: tests never touch live endpoints. This blocks
outbound connections at the socket layer for every test, so an accidental
``fetch()`` (or any network call) fails loudly instead of hitting a real board.

The pipeline/storage tests run against a fresh in-memory SQLite database — never
the real Postgres (which the socket block makes unreachable anyway). SQLite uses
no sockets, so the guard above and a real test DB coexist (the §7 models are
written to be backend-portable for exactly this reason).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ingestion import storage  # noqa: F401 — registers tables on Base.metadata
from ingestion.db import Base, make_session_factory


class _NoNetworkSocket(socket.socket):
    def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")

    def connect_ex(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A fresh in-memory SQLite DB per test (StaticPool keeps the one connection
    alive so the schema persists across sessions within the test)."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A Session bound to the per-test database, reused across pipeline runs so
    change detection sees prior committed state."""
    factory = make_session_factory(engine)
    with factory() as db_session:
        yield db_session
