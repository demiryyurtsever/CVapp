"""Lever adapter tests (dossier §3.4) — run against the captured fixture only;
never the live endpoint (CLAUDE.md rule 5, enforced in conftest.py).

Fixture: ``lever_wealthfront.json`` — a real ``api.lever.co/v0/postings/wealthfront
?mode=json`` capture (15 postings). Wealthfront is a fintech wealth-management firm
used as the reference live Lever source; it carries no IB early-careers titles, so
every posting classifies as ``unclassified`` — which makes it a good explicit check
that the §3.8 "ambiguous title is kept, not dropped" rule holds at the Lever
boundary too.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingestion.adapters.lever import LeverAdapter
from ingestion.models import Posting, ProgramType, Region, Status
from ingestion.registry import AtsType, SourceEntry, load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "lever_wealthfront.json"


@pytest.fixture(scope="module")
def raw_payload() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def entry() -> SourceEntry:
    # Use the real registry entry: proves the loader and adapter agree on shape.
    return next(s for s in load_registry() if s.ats_type == AtsType.lever)


@pytest.fixture
def postings(entry: SourceEntry, raw_payload: list[dict]) -> list[Posting]:
    return LeverAdapter(entry).parse(raw_payload)


def _by_title(postings: list[Posting], title: str) -> Posting:
    return next(p for p in postings if p.role_title == title)


def test_registry_has_a_lever_entry(entry: SourceEntry) -> None:
    assert entry.firm_name == "Wealthfront"
    assert entry.ats_type == AtsType.lever
    assert entry.endpoint_or_url == "wealthfront"


def test_adapter_rejects_mismatched_ats_type() -> None:
    bad = SourceEntry(
        firm_name="X",
        firm_tier="BB",
        ats_type="greenhouse",
        endpoint_or_url="x",
        region_scope="UK",
    )
    with pytest.raises(ValueError):
        LeverAdapter(bad)


def test_payload_is_a_flat_list(raw_payload: list[dict]) -> None:
    # Lever's ?mode=json returns a flat array, not a {"jobs": [...]} wrapper.
    assert isinstance(raw_payload, list)
    assert raw_payload and "text" in raw_payload[0]


def test_parse_returns_one_posting_per_job(postings: list[Posting], raw_payload: list[dict]) -> None:
    # Nothing is dropped at the adapter boundary (§3.2 / §4 boundary rule).
    assert len(postings) == len(raw_payload)


def test_every_posting_validates_against_schema(postings: list[Posting]) -> None:
    # Round-trip each posting through the §7 model — no raw shape leaks past parse().
    for posting in postings:
        Posting.model_validate(posting.model_dump())


def test_boundary_fields_mapped_from_payload(postings: list[Posting], raw_payload: list[dict]) -> None:
    by_source_id = {p.source_id: p for p in postings}
    job = raw_payload[0]
    posting = by_source_id[str(job["id"])]
    assert posting.firm == "Wealthfront"
    assert posting.role_title == job["text"]
    assert posting.location == job["categories"]["location"]
    assert posting.source_url == job["hostedUrl"]
    assert posting.source_id == str(job["id"])
    assert posting.status == Status.open
    # Pipeline-owned fields are not set by the stateless adapter.
    assert posting.id is None
    assert posting.first_seen is None
    assert posting.last_seen is None


def test_created_at_parsed_as_open_date(postings: list[Posting]) -> None:
    # Lever's createdAt is epoch milliseconds; every posting in the fixture has one.
    assert all(isinstance(p.open_date, date) for p in postings)


def test_deadline_absent_in_this_payload(postings: list[Posting]) -> None:
    # Lever postings carry no deadline field (documented in the adapter).
    assert all(p.deadline is None for p in postings)
    assert all(p.rolling is False for p in postings)


def test_raw_description_rejoins_split_fragments(postings: list[Posting]) -> None:
    # Lever splits the body across description / lists / additional; parse() rejoins
    # them so the Layer 2 parser sees the whole posting (§7 raw_description).
    android = _by_title(postings, "Android Engineer")
    assert android.raw_description is not None
    assert "<h3>" in android.raw_description          # a list section heading
    assert "salary" in android.raw_description.lower()  # the closing "additional" block
    assert all(p.raw_description for p in postings)


def test_derived_fields_use_the_shared_classifiers(postings: list[Posting]) -> None:
    # division/region come from the §3.8 helpers, not Lever-specific logic.
    assert _by_title(postings, "Android Engineer").division == "Technology"
    assert _by_title(postings, "Brokerage Operations Specialist").division == "Operations"
    assert _by_title(postings, "Corporate Counsel").division == "Compliance"
    # region: a US-remote location resolves to US; a bare "Palo Alto, CA" does not
    # match any region keyword and is honestly left unknown (a config gap, not code).
    assert _by_title(postings, "Senior iOS Engineer").region == Region.US
    assert _by_title(postings, "Android Engineer").region == Region.unknown


def test_unclassifiable_postings_kept_not_dropped(postings: list[Posting]) -> None:
    # Wealthfront has no IB early-careers titles -> all unclassified, none dropped
    # (§3.8: ambiguous titles surface in the review queue, never silently discarded).
    assert all(p.program_type == ProgramType.unclassified for p in postings)
    assert all(p.role_title and p.source_url for p in postings)
