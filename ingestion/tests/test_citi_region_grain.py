"""Citi multi-office early-careers REGION-GRAIN measurement (dossier §3.9 / §8.2).

This is the durable evidence for the **[OPEN] §8.2 dedup-key region-grain decision**.
It measures a real multi-office early-careers programme (the captured Citi fixture
``workday_citi_earlycareers.json`` — "Summer Analyst Program" across Sydney, Melbourne,
New York, Tokyo, Dubai, Budapest, Mississauga, Manila) under the LOCKED §3.9 dedup key
(firm + normalized_title + program_type + region). The key is **not** changed here
(CLAUDE.md rule 2); this characterises whether it is adequate.

MEASURED RESULT (over all 46 captured postings):

    found 46  ->  unique 45  ->  COLLAPSED 1

The single collapse is two identical "Apps Dev Tech Lead Analyst" postings in the
*same* office ("Jersey City New Jersey United States") — a genuine same-title/
same-office duplicate, exactly what the key SHOULD merge. It is a lateral
(``unclassified``) role, not even early-careers. ZERO genuinely-distinct openings
collapse.

THE CRUX — why the multi-office programme survives the coarse region grain:
Citi disambiguates by putting the **city in the title**. The "Corporate Advisory
Summer Analyst" programme is posted in BOTH Sydney AND Melbourne — same firm, same
program_type (summer), same region (APAC). Coarse region provides ZERO separation
between them; the ONLY thing keeping them as two distinct rows is that "Sydney" vs
"Melbourne" appears in normalize_title. Strip the city from each title and the two
keys collide. So **the title — not the region grain — is the lever** doing the
distinct-opening separation on real multi-office data. Finer region grain would be
redundant here. See docs/PROGRESS.md (Session 10) for the full decision.

NB: the Citi registry entry is DISABLED (captured-for-revisit, not polled). This test
drives the fixture through an injected adapter, so it neither enables nor polls it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ingestion.adapters.workday import WorkdayAdapter
from ingestion.models import ProgramType, Region
from ingestion.pipeline import dedup_key, normalize_title, run_ingestion
from ingestion.registry import SourceEntry, load_registry
from ingestion.storage import FirmRow, PostingRow

FIXTURE = Path(__file__).parent / "fixtures" / "workday_citi_earlycareers.json"

# Measured properties of the Citi multi-office fixture under the locked §3.9 key.
CITI_FOUND = 46
CITI_UNIQUE = 45
CITI_COLLAPSED = 1  # one same-office "Apps Dev Tech Lead Analyst" duplicate (Jersey City).

# The genuine early-careers subset (the multi-office Summer Analyst Program), as
# opposed to the lateral/VP rows the loose free-text search also dragged in.
EARLY_CAREERS = {
    ProgramType.summer,
    ProgramType.spring_week,
    ProgramType.graduate,
    ProgramType.off_cycle,
}
CITI_EC_COUNT = 18  # all classify as `summer`; see docstring.

T1 = datetime(2026, 6, 15, 9, 0, 0)


class _FixtureWorkday(WorkdayAdapter):
    """Real Workday parse over the captured Citi payload — fetch() returns the
    assembled fixture (listing + the one attached detail) instead of the network."""

    def __init__(self, entry: SourceEntry, raw: dict) -> None:
        super().__init__(entry)
        self._raw = raw

    def fetch(self) -> dict:
        return self._raw


@pytest.fixture(scope="module")
def raw_payload() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    info = data["example_detail"]["jobPostingInfo"]
    job_postings = [dict(p) for p in data["jobPostings"]]
    for job in job_postings:
        if job.get("bulletFields") and job["bulletFields"][0] == info["jobReqId"]:
            job["_detail"] = info
    return {"total": data["total"], "jobPostings": job_postings}


@pytest.fixture
def citi_entry() -> SourceEntry:
    """The (disabled) Citi registry entry, fetched by name — not enabled, not polled."""
    return next(s for s in load_registry() if s.firm_name == "Citi")


def _parsed(citi_entry: SourceEntry, raw_payload: dict):
    return _FixtureWorkday(citi_entry, raw_payload).parse(raw_payload)


def test_citi_fixture_is_the_disabled_second_bb(citi_entry: SourceEntry) -> None:
    """Guard: Citi is the captured-for-revisit BB and is NOT enabled (scope rule)."""
    assert citi_entry.firm_tier.value == "BB"
    assert citi_entry.enabled is False


def test_full_fixture_collapse_is_one_same_office_duplicate(
    session, citi_entry, raw_payload  # noqa: ANN001
) -> None:
    """REPORT: over all 46 captured postings the pipeline collapses 46 -> 45 (1
    collapsed) under the locked key — and that one collapse is a same-office (Jersey
    City) duplicate, NOT a distinct office flattened by coarse region."""
    summary = run_ingestion(session, adapters=[_FixtureWorkday(citi_entry, raw_payload)], now=T1)

    assert summary.found == CITI_FOUND
    assert summary.new == CITI_UNIQUE
    assert summary.collapsed == CITI_COLLAPSED
    assert summary.closed == 0 and summary.reappeared == 0
    assert summary.errors == []
    assert session.scalar(select(func.count()).select_from(PostingRow)) == CITI_UNIQUE

    firm = session.get(FirmRow, "Citi")
    assert firm is not None and firm.tier.value == "BB"

    # The surviving row of the collapsed pair is the same-office Jersey City duplicate.
    rows = session.execute(
        select(PostingRow).where(PostingRow.role_title == "Apps Dev Tech Lead Analyst")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].location == "Jersey City New Jersey United States"
    assert rows[0].program_type == ProgramType.unclassified  # lateral, not early-careers


def test_early_careers_programme_produces_no_collapse(citi_entry, raw_payload) -> None:  # noqa: ANN001
    """The 18 genuine early-careers postings (the real multi-office Summer Analyst
    Program) produce 18 DISTINCT dedup keys — zero collapse. The multi-office shape
    does not hide a single opening under the current key."""
    posts = _parsed(citi_entry, raw_payload)
    ec = [p for p in posts if p.program_type in EARLY_CAREERS]

    assert len(ec) == CITI_EC_COUNT
    keys = {dedup_key(p) for p in ec}
    assert len(keys) == CITI_EC_COUNT  # one unique key per early-careers posting


def test_title_not_region_grain_is_the_lever(citi_entry, raw_payload) -> None:  # noqa: ANN001
    """ISOLATE THE LEVER (the [OPEN] §8.2 crux). The Corporate Advisory Summer Analyst
    programme runs in BOTH Sydney and Melbourne — same firm, same program_type, same
    region (APAC). They stay two distinct rows. Prove WHY: the only differing key
    component is the title (the city is *in* it); strip the city and the keys collide.
    So the title separates the offices, not the region grain — finer region is redundant
    on real multi-office data."""
    posts = _parsed(citi_entry, raw_payload)
    syd = next(p for p in posts if p.location.startswith("Sydney") and "Corporate Advisory" in p.role_title)
    mel = next(p for p in posts if p.location.startswith("Melbourne") and "Corporate Advisory" in p.role_title)

    # Same coarse-region cell — region provides zero separation here.
    assert syd.region == mel.region == Region.APAC
    assert syd.program_type == mel.program_type == ProgramType.summer
    assert (syd.firm, syd.program_type, syd.region) == (mel.firm, mel.program_type, mel.region)

    # Yet the keys differ -> they are kept distinct (the key does NOT hide the opening).
    assert dedup_key(syd) != dedup_key(mel)

    # The lever: remove the city from each title and the normalized residuals are equal,
    # so the city-in-title is the SOLE thing separating them. Region grain is not.
    assert normalize_title(syd.role_title.replace("Sydney", "")) == normalize_title(
        mel.role_title.replace("Melbourne", "")
    )
