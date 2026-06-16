"""Shared fixtures for the read-API tests (dossier §6.1).

The API tests do NOT mock the database out — they seed a real (in-memory SQLite)
DB by driving the REAL ingestion pipeline (``run_ingestion``) over the same
captured fixtures the ingestion layer is tested on, then point a FastAPI
``TestClient`` at that DB via a dependency override. So the read path is exercised
end-to-end over genuinely-ingested §7 rows, with no schema or pipeline change.

No network: like the ingestion conftest, this blocks the socket layer (CLAUDE.md
rule 5). The seed adapters subclass the real GH/Lever/Workday adapters and
override only ``fetch()`` to return the captured payload — ``parse()`` and the
§3.8 classifiers are the production ones, so what lands in the DB is exactly what
a real poll would have produced.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app import create_app
from api.deps import get_session
from ingestion import storage  # noqa: F401 — registers §7 tables on Base.metadata
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.adapters.lever import LeverAdapter
from ingestion.adapters.workday import WorkdayAdapter
from ingestion.db import Base, make_session_factory
from ingestion.pipeline import run_ingestion
from ingestion.registry import SourceEntry, load_registry

# The ingestion fixtures captured from real boards (same files the §3 tests use).
_INGESTION_FIXTURES = Path(__file__).resolve().parents[2] / "ingestion" / "tests" / "fixtures"

# A fixed seed time so first_seen/last_seen are deterministic across the suite.
SEED_TIME = datetime(2026, 6, 15, 9, 0, 0)


# --------------------------------------------------------------------------- #
# No-network guard (CLAUDE.md rule 5) — scoped to the api test tree.
#
# Unlike the ingestion tests, these drive a Starlette TestClient, whose in-process
# event loop opens a loopback self-pipe socket on Windows. So this guard blocks
# only NON-loopback connects (the thing rule 5 cares about: live boards), while
# letting 127.0.0.1/::1 through for the test harness's own machinery. No adapter
# fetch() is ever called here anyway — payloads are injected — so nothing reaches
# a real endpoint regardless.
# --------------------------------------------------------------------------- #
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_loopback(address) -> bool:  # noqa: ANN001
    host = address[0] if isinstance(address, (tuple, list)) and address else address
    return host in _LOOPBACK_HOSTS


class _NoNetworkSocket(socket.socket):
    def connect(self, address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if _is_loopback(address):
            return super().connect(address, *args, **kwargs)
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")

    def connect_ex(self, address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if _is_loopback(address):
            return super().connect_ex(address, *args, **kwargs)
        raise RuntimeError("Network access is disabled in tests (CLAUDE.md rule 5).")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)


# --------------------------------------------------------------------------- #
# Fixture-backed adapters: real parse(), fetch() returns the captured payload.
# --------------------------------------------------------------------------- #
def _fixture_adapter(adapter_cls: type):
    class _Fixture(adapter_cls):  # type: ignore[valid-type, misc]
        def __init__(self, entry: SourceEntry, raw) -> None:  # noqa: ANN001
            super().__init__(entry)
            self._raw = raw

        def fetch(self):  # noqa: ANN201
            return self._raw

    return _Fixture


_FixtureGreenhouse = _fixture_adapter(GreenhouseAdapter)
_FixtureLever = _fixture_adapter(LeverAdapter)
_FixtureWorkday = _fixture_adapter(WorkdayAdapter)


def _load(name: str):  # noqa: ANN202
    return json.loads((_INGESTION_FIXTURES / name).read_text(encoding="utf-8"))


def _workday_fetch_shape(fixture: dict) -> dict:
    """Assemble the ``fetch()``-shaped Workday payload: the aggregated listing
    pages with the one captured detail attached under ``"_detail"`` (exactly what
    ``WorkdayAdapter.fetch()`` returns — mirrors the §3.5 adapter test)."""
    info = fixture["example_detail"]["jobPostingInfo"]
    job_postings = [dict(p) for p in fixture["jobPostings"]]
    for job in job_postings:
        if job["bulletFields"][0] == info["jobReqId"]:
            job["_detail"] = info
    return {"total": fixture["total"], "jobPostings": job_postings}


def _entry(firm_name: str) -> SourceEntry:
    return next(s for s in load_registry() if s.firm_name == firm_name)


def build_seed_adapters(*, gh_raw=None, lever_raw=None, workday_raw=None) -> list:  # noqa: ANN001
    """Three fixture-backed adapters (Point72/GH, Wealthfront/Lever, Barclays/WD).

    Callers can pass a mutated ``*_raw`` to simulate a board that changed between
    runs (e.g. a posting dropped, to exercise the closed lifecycle + status filter).
    """
    return [
        _FixtureGreenhouse(_entry("Point72"), _load("greenhouse_point72.json") if gh_raw is None else gh_raw),
        _FixtureLever(_entry("Wealthfront"), _load("lever_wealthfront.json") if lever_raw is None else lever_raw),
        _FixtureWorkday(
            _entry("Barclays"),
            _workday_fetch_shape(_load("workday_barclays.json")) if workday_raw is None else workday_raw,
        ),
    ]


# --------------------------------------------------------------------------- #
# Database + client fixtures.
# --------------------------------------------------------------------------- #
def fresh_engine() -> Engine:
    """A new in-memory SQLite DB with the §7 schema created (StaticPool keeps the
    single connection alive across sessions within one test)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def make_client(engine: Engine) -> TestClient:
    """A TestClient whose ``get_session`` dependency is bound to ``engine``."""
    app = create_app()
    factory = make_session_factory(engine)

    def _override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


@pytest.fixture
def seeded_engine() -> Iterator[Engine]:
    """An engine seeded once (at SEED_TIME) with all three real-board fixtures via
    the real pipeline. Everything lands ``status=open`` on a single run."""
    engine = fresh_engine()
    factory = make_session_factory(engine)
    with factory() as session:
        run_ingestion(session, adapters=build_seed_adapters(), now=SEED_TIME)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def read_session(seeded_engine: Engine) -> Iterator[Session]:
    """A Session on the seeded DB for computing ground-truth expectations to assert
    the API against (so filter/pagination tests are not hardcoded magic numbers)."""
    factory = make_session_factory(seeded_engine)
    with factory() as session:
        yield session


@pytest.fixture
def client(seeded_engine: Engine) -> Iterator[TestClient]:
    with make_client(seeded_engine) as test_client:
        yield test_client


@pytest.fixture
def api_tools() -> Iterator["SimpleNamespace"]:
    """Building blocks for tests that need custom multi-run seeding (e.g. driving
    a posting through the closed lifecycle to exercise the status filter). Tracks
    any engines created so they are disposed at test end."""
    from types import SimpleNamespace

    engines: list[Engine] = []

    def _fresh() -> Engine:
        engine = fresh_engine()
        engines.append(engine)
        return engine

    try:
        yield SimpleNamespace(
            fresh_engine=_fresh,
            build_seed_adapters=build_seed_adapters,
            make_client=make_client,
        )
    finally:
        for engine in engines:
            engine.dispose()
