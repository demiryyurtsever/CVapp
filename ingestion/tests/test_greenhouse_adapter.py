"""Greenhouse adapter tests (dossier §3.3) — run against the captured fixture
only; never the live endpoint (CLAUDE.md rule 5, enforced in conftest.py)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.models import Posting, ProgramType, Status
from ingestion.registry import AtsType, SourceEntry, load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_point72.json"


@pytest.fixture(scope="module")
def raw_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def entry() -> SourceEntry:
    # Use the real registry entry: proves the loader and adapter agree on shape.
    return next(s for s in load_registry() if s.ats_type == AtsType.greenhouse)


@pytest.fixture
def postings(entry: SourceEntry, raw_payload: dict) -> list[Posting]:
    return GreenhouseAdapter(entry).parse(raw_payload)


def test_registry_has_one_greenhouse_entry(entry: SourceEntry) -> None:
    assert entry.firm_name == "Point72"
    assert entry.ats_type == AtsType.greenhouse
    assert entry.endpoint_or_url == "point72"


def test_adapter_rejects_mismatched_ats_type() -> None:
    bad = SourceEntry(
        firm_name="X",
        firm_tier="BB",
        ats_type="lever",
        endpoint_or_url="x",
        region_scope="UK",
    )
    with pytest.raises(ValueError):
        GreenhouseAdapter(bad)


def test_parse_returns_one_posting_per_job(postings: list[Posting], raw_payload: dict) -> None:
    # Nothing is dropped at the adapter boundary.
    assert len(postings) == len(raw_payload["jobs"]) == raw_payload["meta"]["total"]


def test_every_posting_validates_against_schema(postings: list[Posting]) -> None:
    # Round-trip each posting through the §7 model.
    for posting in postings:
        Posting.model_validate(posting.model_dump())


def test_boundary_fields_mapped_from_payload(postings: list[Posting], raw_payload: dict) -> None:
    by_source_id = {p.source_id: p for p in postings}
    job = raw_payload["jobs"][0]
    posting = by_source_id[str(job["id"])]
    assert posting.firm == "Point72"
    assert posting.role_title == job["title"]
    assert posting.source_url == job["absolute_url"]
    assert posting.raw_description == job["content"]
    assert posting.status == Status.open
    # Pipeline-owned fields are not set by the stateless adapter.
    assert posting.id is None
    assert posting.first_seen is None
    assert posting.last_seen is None


def test_open_date_parsed_as_date(postings: list[Posting]) -> None:
    dated = [p for p in postings if p.open_date is not None]
    assert dated, "expected at least one posting with a parsed open_date"
    assert all(isinstance(p.open_date, date) for p in dated)


def test_deadline_absent_in_this_payload(postings: list[Posting]) -> None:
    # application_deadline is empty for every job in this fixture (documented).
    assert all(p.deadline is None for p in postings)


def test_known_real_titles_are_classified(postings: list[Posting]) -> None:
    insight = next(
        p for p in postings if "Insight" in p.role_title and "Japan" in p.role_title
    )
    assert insight.program_type == ProgramType.spring_week

    summer = next(p for p in postings if "Summer Internship Program" in p.role_title)
    assert summer.program_type == ProgramType.summer


def test_unclassifiable_postings_kept_not_dropped(postings: list[Posting]) -> None:
    unclassified = [p for p in postings if p.program_type == ProgramType.unclassified]
    assert unclassified, "expected ambiguous titles to be retained as unclassified"
    # They keep their core data so they can surface in the review queue.
    assert all(p.role_title and p.source_url for p in unclassified)
