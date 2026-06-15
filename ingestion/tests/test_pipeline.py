"""Pipeline orchestrator tests (dossier §3.7 / §3.9 / §3.11).

The pipeline is driven with the captured ``greenhouse_point72.json`` fixture as
the adapter output — never the live endpoint (CLAUDE.md rule 5, enforced by the
socket block in conftest.py). The stub below overrides ONLY ``fetch()`` so the
REAL ``GreenhouseAdapter.parse`` + the real §3.8 classifiers run; that is what
makes the §5 derived-key stability guard meaningful.

Counts note (see docs/PROGRESS.md, Session 4): the §3.9 dedup key
(firm + normalized_title + program_type + region) collapses the 249 parsed
postings to 233 unique keys — 16 are same-title/same-region duplicates (some
genuine, some distinct offices flattened by coarse region). The key is locked, so
233 new on run 1 is the correct, spec-faithful outcome; the 16 collapsed rows are
logged for observability.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import json

import pytest
from sqlalchemy import func, select

from ingestion.adapters.base import Adapter
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.models import Status
from ingestion.pipeline import dedup_key, run_ingestion, source_key
from ingestion.registry import AtsType, SourceEntry, load_registry
from ingestion.storage import FirmRow, IngestionRunRow, PostingRow

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_point72.json"

# Known properties of the fixture under the locked §3.9 dedup key.
TOTAL_FOUND = 249
UNIQUE_KEYS = 233
COLLAPSED = 16

# Distinct logical run timestamps so lifecycle transitions are deterministic.
T1 = datetime(2026, 6, 15, 9, 0, 0)
T2 = datetime(2026, 6, 16, 9, 0, 0)
T3 = datetime(2026, 6, 17, 9, 0, 0)
T4 = datetime(2026, 6, 18, 9, 0, 0)


class _FixtureGreenhouse(GreenhouseAdapter):
    """Real Greenhouse parse over a captured payload — fetch() returns the fixture
    instead of hitting the network. parse()/classifiers are the production ones."""

    def __init__(self, entry: SourceEntry, raw: dict) -> None:
        super().__init__(entry)
        self._raw = raw

    def fetch(self) -> dict:
        return self._raw


class _BoomAdapter(Adapter):
    """A source whose fetch() always fails — to prove one broken source is logged
    and skipped without halting the run (§3.2)."""

    ats_type = AtsType.lever

    def fetch(self):  # noqa: ANN201
        raise RuntimeError("boom: source endpoint down")

    def parse(self, raw):  # noqa: ANN001, ANN201
        return []


@pytest.fixture(scope="module")
def raw_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def gh_entry() -> SourceEntry:
    return next(s for s in load_registry() if s.ats_type == AtsType.greenhouse)


@pytest.fixture
def unique_target(gh_entry: SourceEntry, raw_payload: dict) -> tuple[str, str]:
    """A posting whose dedup key is unique in the run, so removing it genuinely
    removes that key (a colliding one would survive via its twin)."""
    postings = GreenhouseAdapter(gh_entry).parse(raw_payload)
    counts = Counter(dedup_key(p) for p in postings)
    for posting in postings:
        if counts[dedup_key(posting)] == 1:
            return posting.source_id, dedup_key(posting)
    raise AssertionError("expected at least one uniquely-keyed posting in the fixture")


def _without_job(raw: dict, source_id: str) -> dict:
    jobs = [j for j in raw["jobs"] if str(j["id"]) != str(source_id)]
    return {**raw, "jobs": jobs, "meta": {**raw.get("meta", {}), "total": len(jobs)}}


def _gh(gh_entry: SourceEntry, raw: dict) -> _FixtureGreenhouse:
    return _FixtureGreenhouse(gh_entry, raw)


def _count_postings(session) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(PostingRow))


# --------------------------------------------------------------------------- #
# Run 1: insert
# --------------------------------------------------------------------------- #

def test_run1_inserts_unique_postings_as_new(session, gh_entry, raw_payload) -> None:  # noqa: ANN001
    summary = run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)

    assert summary.found == TOTAL_FOUND          # 249 found on the board
    assert summary.new == UNIQUE_KEYS            # 233 distinct under the §3.9 key
    assert summary.collapsed == COLLAPSED        # 16 collapsed within the run
    assert summary.closed == 0
    assert summary.reappeared == 0
    assert summary.errors == []

    assert _count_postings(session) == UNIQUE_KEYS
    rows = session.execute(select(PostingRow)).scalars().all()
    assert {r.status for r in rows} == {Status.open}
    # Fresh inserts: first_seen == last_seen == run time; no misses.
    assert all(r.first_seen == T1 and r.last_seen == T1 for r in rows)
    assert all(r.consecutive_misses == 0 for r in rows)


def test_firm_row_seeded_from_registry(session, gh_entry, raw_payload) -> None:  # noqa: ANN001
    run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)
    firm = session.get(FirmRow, gh_entry.firm_name)
    assert firm is not None
    assert firm.tier == gh_entry.firm_tier


def test_run_is_logged_with_per_source_counts(session, gh_entry, raw_payload) -> None:  # noqa: ANN001
    summary = run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)

    run = session.get(IngestionRunRow, summary.run_id)
    assert run is not None
    assert (run.found, run.new, run.closed, run.collapsed) == (TOTAL_FOUND, UNIQUE_KEYS, 0, COLLAPSED)
    assert run.errors == []

    skey = source_key(gh_entry)
    assert run.per_source[skey]["found"] == TOTAL_FOUND
    assert run.per_source[skey]["new"] == UNIQUE_KEYS
    assert run.per_source[skey]["collapsed"] == COLLAPSED


# --------------------------------------------------------------------------- #
# Run 2 (identical): the §5 derived-key stability guard
# --------------------------------------------------------------------------- #

def test_rerun_same_fixture_is_stable(session, gh_entry, raw_payload) -> None:  # noqa: ANN001
    run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)
    summary2 = run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T2)

    # §5 GUARD: a second run over the SAME fixture must flip nothing and insert
    # nothing — otherwise the dedup key or classifiers are non-deterministic.
    assert summary2.new == 0
    assert summary2.closed == 0
    assert summary2.reappeared == 0

    assert _count_postings(session) == UNIQUE_KEYS
    rows = session.execute(select(PostingRow)).scalars().all()
    assert {r.status for r in rows} == {Status.open}
    assert all(r.first_seen == T1 for r in rows)   # unchanged
    assert all(r.last_seen == T2 for r in rows)    # only last_seen bumped
    assert all(r.consecutive_misses == 0 for r in rows)


# --------------------------------------------------------------------------- #
# Lifecycle: closed / reappeared
# --------------------------------------------------------------------------- #

def test_absent_for_n_runs_flips_to_closed(session, gh_entry, raw_payload, unique_target) -> None:  # noqa: ANN001
    target_id, target_key = unique_target
    reduced = _without_job(raw_payload, target_id)

    run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)  # present

    # Absent run 1: a single missed run must NOT close a live posting.
    s2 = run_ingestion(session, adapters=[_gh(gh_entry, reduced)], now=T2)
    row = session.execute(
        select(PostingRow).where(PostingRow.dedup_key == target_key)
    ).scalar_one()
    assert row.status == Status.open
    assert row.consecutive_misses == 1
    assert s2.closed == 0

    # Absent run 2: N=2 consecutive misses -> closed.
    s3 = run_ingestion(session, adapters=[_gh(gh_entry, reduced)], now=T3)
    row = session.execute(
        select(PostingRow).where(PostingRow.dedup_key == target_key)
    ).scalar_one()
    assert row.status == Status.closed
    assert row.consecutive_misses == 2
    assert s3.closed == 1
    # last_seen frozen at the last present run — not bumped while absent.
    assert row.last_seen == T1


def test_single_missed_run_closes_nothing(session, gh_entry, raw_payload, unique_target) -> None:  # noqa: ANN001
    target_id, target_key = unique_target
    reduced = _without_job(raw_payload, target_id)

    run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)
    s2 = run_ingestion(session, adapters=[_gh(gh_entry, reduced)], now=T2)

    # A single missed run closes nothing — the absent posting just accrues a miss.
    assert s2.closed == 0
    closed = session.scalar(
        select(func.count()).select_from(PostingRow).where(PostingRow.status == Status.closed)
    )
    assert closed == 0

    # The absent posting is still open, now with exactly one consecutive miss.
    target = session.execute(
        select(PostingRow).where(PostingRow.dedup_key == target_key)
    ).scalar_one()
    assert target.status == Status.open
    assert target.consecutive_misses == 1

    # Every still-present posting stays open with its miss counter reset to 0.
    present = session.execute(
        select(PostingRow).where(PostingRow.dedup_key != target_key)
    ).scalars().all()
    assert len(present) == UNIQUE_KEYS - 1
    assert all(r.status == Status.open and r.consecutive_misses == 0 for r in present)


def test_closed_posting_that_returns_reappears(session, gh_entry, raw_payload, unique_target) -> None:  # noqa: ANN001
    target_id, target_key = unique_target
    reduced = _without_job(raw_payload, target_id)

    run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T1)  # present
    run_ingestion(session, adapters=[_gh(gh_entry, reduced)], now=T2)      # miss 1
    run_ingestion(session, adapters=[_gh(gh_entry, reduced)], now=T3)      # miss 2 -> closed

    row = session.execute(
        select(PostingRow).where(PostingRow.dedup_key == target_key)
    ).scalar_one()
    assert row.status == Status.closed

    # The posting returns -> reappeared (a useful "bank reopened this role" signal).
    s4 = run_ingestion(session, adapters=[_gh(gh_entry, raw_payload)], now=T4)
    row = session.execute(
        select(PostingRow).where(PostingRow.dedup_key == target_key)
    ).scalar_one()
    assert row.status == Status.reappeared
    assert row.consecutive_misses == 0
    assert row.last_seen == T4
    assert s4.reappeared == 1
    # No new row was created for the returning posting.
    assert _count_postings(session) == UNIQUE_KEYS


# --------------------------------------------------------------------------- #
# Error isolation (§3.2)
# --------------------------------------------------------------------------- #

def test_one_broken_source_is_logged_and_skipped(session, gh_entry, raw_payload) -> None:  # noqa: ANN001
    good = _gh(gh_entry, raw_payload)
    boom_entry = SourceEntry(
        firm_name="BoomCo",
        firm_tier="BB",
        ats_type="lever",
        endpoint_or_url="boomco",
        region_scope="UK",
    )
    boom = _BoomAdapter(boom_entry)

    summary = run_ingestion(session, adapters=[good, boom], now=T1)

    # The healthy source still wrote all its postings — the run did not halt.
    assert summary.new == UNIQUE_KEYS
    assert _count_postings(session) == UNIQUE_KEYS

    # The broken source is logged with its id and error.
    boom_key = source_key(boom_entry)
    boom_errors = [e for e in summary.errors if e["source"] == boom_key]
    assert len(boom_errors) == 1
    assert "boom" in boom_errors[0]["error"]

    # And the error is persisted on the run row (§3.11).
    run = session.get(IngestionRunRow, summary.run_id)
    assert any(e["source"] == boom_key for e in run.errors)
