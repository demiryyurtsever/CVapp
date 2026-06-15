"""Workday adapter tests (dossier §3.5) — run against the captured fixture only;
never the live endpoint (CLAUDE.md rule 5, enforced in conftest.py).

Fixture: ``workday_barclays.json`` — a real capture from the confirmed Barclays
tenant (``barclays``.wd3, site ``External_Career_Site_Barclays``) via an
early-careers ("graduate") paginated POST (23 postings across two pages), plus one
verbatim per-posting **detail** follow-up. Barclays is the first real IB firm (a
bulge bracket) in the registry.

The Workday listing is SHALLOW (title / externalPath / locationsText / a *relative*
postedOn / bulletFields). Description and an absolute date come from the per-posting
detail endpoint, which ``fetch()`` attaches under ``"_detail"``. The helper below
assembles that fetch()-shaped payload from the fixture (attaching the one captured
detail to its matching posting) so ``parse()`` is exercised exactly as in
production — both the detail-enriched path and the listing-only fallback.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ingestion.adapters.workday import WorkdayAdapter
from ingestion.classifiers import classify_program_type, extract_division, map_region
from ingestion.models import Posting, ProgramType, Region, Status
from ingestion.registry import AtsType, SourceEntry, load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "workday_barclays.json"

# The req id of the one posting the fixture captured a detail follow-up for.
ENRICHED_REQ_ID = "JR-0000101091"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_payload(fixture: dict) -> dict:
    """Assemble the fetch()-shaped payload: the aggregated listing pages, with the
    one captured detail attached to its matching posting under ``"_detail"`` (this
    is exactly what ``WorkdayAdapter.fetch()`` returns)."""
    info = fixture["example_detail"]["jobPostingInfo"]
    job_postings = [dict(p) for p in fixture["jobPostings"]]
    for job in job_postings:
        if job["bulletFields"][0] == info["jobReqId"]:
            job["_detail"] = info
    return {"total": fixture["total"], "jobPostings": job_postings}


@pytest.fixture
def entry() -> SourceEntry:
    # Use the real registry entry: proves the loader and adapter agree on shape.
    return next(s for s in load_registry() if s.ats_type == AtsType.workday)


@pytest.fixture
def postings(entry: SourceEntry, raw_payload: dict) -> list[Posting]:
    return WorkdayAdapter(entry).parse(raw_payload)


def _by_req(postings: list[Posting], req_id: str) -> Posting:
    return next(p for p in postings if p.source_id == req_id)


def test_registry_has_a_workday_entry(entry: SourceEntry) -> None:
    assert entry.firm_name == "Barclays"
    assert entry.firm_tier.value == "BB"          # first real IB firm: a bulge bracket
    assert entry.ats_type == AtsType.workday
    assert entry.endpoint_or_url == "barclays"    # the Workday tenant token
    # Per-tenant quirks live in registry config (the [OPEN] §8.2 config choice).
    assert entry.config["dc"] == "wd3"
    assert entry.config["site"] == "External_Career_Site_Barclays"


def test_adapter_rejects_mismatched_ats_type() -> None:
    bad = SourceEntry(
        firm_name="X",
        firm_tier="BB",
        ats_type="greenhouse",
        endpoint_or_url="x",
        region_scope="UK",
    )
    with pytest.raises(ValueError):
        WorkdayAdapter(bad)


def test_payload_shape_is_jobpostings_wrapper(raw_payload: dict) -> None:
    # Workday returns a {"total": N, "jobPostings": [...]} object (POST result),
    # not a flat array (Lever) or a {"jobs": [...]} wrapper (Greenhouse).
    assert isinstance(raw_payload, dict)
    assert "jobPostings" in raw_payload and isinstance(raw_payload["jobPostings"], list)
    assert "title" in raw_payload["jobPostings"][0]


def test_pagination_aggregates_all_pages(fixture: dict, raw_payload: dict) -> None:
    # The 23 postings span TWO listing pages (limit=20 -> 20 + 3); parse handles the
    # aggregated set as one shape, nothing dropped at the page boundary.
    assert fixture["total"] == 23
    assert len(raw_payload["jobPostings"]) == 23


def test_parse_handles_empty_and_missing_jobpostings(entry: SourceEntry) -> None:
    # Shape robustness: an empty board or a missing key yields no postings, not a crash.
    assert WorkdayAdapter(entry).parse({"total": 0, "jobPostings": []}) == []
    assert WorkdayAdapter(entry).parse({}) == []


def test_parse_returns_one_posting_per_job(postings: list[Posting], raw_payload: dict) -> None:
    # Nothing is dropped at the adapter boundary (§3.2 / boundary rule).
    assert len(postings) == len(raw_payload["jobPostings"]) == 23


def test_every_posting_validates_against_schema(postings: list[Posting]) -> None:
    # Round-trip each posting through the §7 model — no raw shape leaks past parse().
    for posting in postings:
        Posting.model_validate(posting.model_dump())


def test_boundary_fields_mapped_from_listing(postings: list[Posting], fixture: dict) -> None:
    # A listing-only posting (no detail follow-up): fields come from the shallow
    # listing; source_url is built from the relative externalPath.
    grad = _by_req(postings, "JR-0000068287")
    raw = next(p for p in fixture["jobPostings"] if p["bulletFields"][0] == "JR-0000068287")
    assert grad.firm == "Barclays"
    assert grad.firm_tier.value == "BB"
    assert grad.role_title == raw["title"]
    assert grad.location == raw["locationsText"]
    assert grad.source_id == raw["bulletFields"][0]               # the JR- req id
    assert grad.source_url == (
        "https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays"
        + raw["externalPath"]
    )
    assert grad.status == Status.open
    # Pipeline-owned fields are not set by the stateless adapter.
    assert grad.id is None and grad.first_seen is None and grad.last_seen is None
    # Listing carries no description / absolute date -> honest None (not guessed).
    assert grad.raw_description is None
    assert grad.open_date is None


def test_detail_followup_populates_description_url_and_open_date(
    postings: list[Posting], fixture: dict
) -> None:
    # The one posting with a captured detail: raw_description from jobDescription,
    # open_date from the detail's absolute startDate, source_url from externalUrl.
    info = fixture["example_detail"]["jobPostingInfo"]
    enriched = _by_req(postings, ENRICHED_REQ_ID)
    assert enriched.raw_description == info["jobDescription"]
    assert enriched.raw_description and "<h1>" in enriched.raw_description
    assert enriched.open_date == date(2026, 6, 15)               # detail startDate
    assert enriched.source_url == info["externalUrl"]            # canonical public link


def test_deadline_absent_and_rolling_false(postings: list[Posting]) -> None:
    # Workday postings carry no deadline field (documented in the adapter).
    assert all(p.deadline is None for p in postings)
    assert all(p.rolling is False for p in postings)


def test_derived_fields_use_the_shared_classifiers(postings: list[Posting]) -> None:
    # program_type / division / region come from the §3.8 helpers, not Workday logic:
    # every posting's derived fields equal the shared classifier output for its inputs.
    for p in postings:
        assert p.program_type == classify_program_type(p.role_title)
        assert p.division == extract_division(p.role_title)
        assert p.region == map_region(p.location)
    # Spot-checks on real fixture rows:
    grad = _by_req(postings, "JR-0000068287")
    assert grad.program_type == ProgramType.graduate            # "...Graduate Programme..."
    assert grad.division == "Technology"                        # "Technology Developer..."
    # A Mumbai role resolves to APAC; a Prague/Pune location has no region keyword and
    # is honestly left unknown (a classifier-config gap, not an adapter bug).
    assert map_region("Mumbai, Nirlon Knowledge Park") == Region.APAC
    assert grad.region == Region.unknown                        # Prague: config gap


def test_unclassifiable_postings_kept_not_dropped(postings: list[Posting]) -> None:
    # This lateral-heavy early-careers slice is mostly unclassifiable titles; §3.8
    # keeps them (review queue), never silently drops them.
    unclassified = [p for p in postings if p.program_type == ProgramType.unclassified]
    assert len(unclassified) == 22                              # only the Graduate Programme classifies
    assert all(p.role_title and p.source_url and p.source_id for p in postings)
