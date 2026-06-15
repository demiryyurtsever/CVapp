"""Workday dedup-collapse REPORT (dossier §3.9, Option A — measure, do NOT fix).

Session 7 predicted the coarse-region collapse (first seen on Point72's APAC roles
in Session 4) would become acute on the Workday BBs, which post one early-careers
programme across many offices under near-identical titles. This test produces the
EVIDENCE the later [OPEN] §8.2 dedup-key region-grain revisit needs — it runs the
pipeline over the real Barclays Workday fixture and surfaces how many postings
collapse under the LOCKED §3.9 dedup key (firm + normalized_title + program_type +
region). The key is **not** changed here (CLAUDE.md rule 2); this is measurement.

MEASURED RESULT on ``workday_barclays.json`` (Barclays / barclays.wd3 /
External_Career_Site_Barclays, "graduate" early-careers search, 23 postings):

    found 23  ->  unique 21  ->  COLLAPSED 2

Characterisation (the substantive finding): the 2 collapses are NOT coarse-region
flattening of distinct offices. They are three identical "Third Party Risk Manager"
postings in the *same* office ("Noida, Candor TechSpace") collapsing 3 -> 1 — a
genuine same-title/same-location duplicate, exactly what the dedup key SHOULD merge.
Zero genuinely-distinct office/city openings were flattened by region on this slice.

Why the predicted region-flattening is NOT exercised here (recorded for the revisit):
this Barclays external site is lateral/experienced-role heavy and Workday free-text
"graduate" search returns mostly India-based roles with distinct titles; and the
region classifier currently maps several cities (Pune/Noida/Chennai/Prague) to
``unknown`` (a config gap), so even repeated titles do not all share a region. To
properly stress-test the region grain the revisit wants a true multi-office UK
early-careers programme (e.g. a Summer Analyst across London/Glasgow/Belfast),
which this site's free-text early-careers search did not surface. See docs/PROGRESS.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ingestion.adapters.workday import WorkdayAdapter
from ingestion.models import Status
from ingestion.pipeline import run_ingestion, source_key
from ingestion.registry import AtsType, SourceEntry, load_registry
from ingestion.storage import FirmRow, IngestionRunRow, PostingRow

FIXTURE = Path(__file__).parent / "fixtures" / "workday_barclays.json"

# The measured properties of the Workday fixture under the locked §3.9 dedup key.
WD_FOUND = 23
WD_UNIQUE = 21
WD_COLLAPSED = 2  # all from one same-office triplicate; see module docstring.

T1 = datetime(2026, 6, 15, 9, 0, 0)


class _FixtureWorkday(WorkdayAdapter):
    """Real Workday parse over the captured payload — fetch() returns the assembled
    fixture (listing + the one attached detail) instead of hitting the network."""

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
        if job["bulletFields"][0] == info["jobReqId"]:
            job["_detail"] = info
    return {"total": data["total"], "jobPostings": job_postings}


@pytest.fixture
def wd_entry() -> SourceEntry:
    return next(s for s in load_registry() if s.ats_type == AtsType.workday)


def _count(session) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(PostingRow))


def test_workday_fixture_collapse_count(session, wd_entry, raw_payload) -> None:  # noqa: ANN001
    """REPORT: the pipeline over real Barclays Workday data collapses 23 -> 21
    (2 collapsed) under the locked dedup key. Surfaces the number for the revisit."""
    wd = _FixtureWorkday(wd_entry, raw_payload)

    summary = run_ingestion(session, adapters=[wd], now=T1)

    assert summary.found == WD_FOUND
    assert summary.new == WD_UNIQUE
    assert summary.collapsed == WD_COLLAPSED
    assert summary.closed == 0 and summary.reappeared == 0
    assert summary.errors == []
    assert _count(session) == WD_UNIQUE

    # The Barclays firm row is seeded (§3.10 / §6.4), tier = BB.
    firm = session.get(FirmRow, "Barclays")
    assert firm is not None and firm.tier.value == "BB"


def test_collapse_is_logged_on_the_run_row(session, wd_entry, raw_payload) -> None:  # noqa: ANN001
    """The collapse is observable, not invisible: it is persisted on the run row
    (ingestion_runs.collapsed) and in the per-source breakdown (§3.11)."""
    summary = run_ingestion(session, adapters=[_FixtureWorkday(wd_entry, raw_payload)], now=T1)

    run = session.get(IngestionRunRow, summary.run_id)
    assert run is not None
    assert run.collapsed == WD_COLLAPSED
    assert run.per_source[source_key(wd_entry)]["collapsed"] == WD_COLLAPSED
    assert run.per_source[source_key(wd_entry)]["found"] == WD_FOUND


def test_collapsed_group_is_a_same_office_duplicate_not_region_flattening(
    session, wd_entry, raw_payload  # noqa: ANN001
) -> None:
    """Characterise the collapse so the revisit reads it correctly: the surviving
    "Third Party Risk Manager" row is one of three IDENTICAL same-office postings —
    a genuine duplicate, not a distinct office flattened by coarse region."""
    run_ingestion(session, adapters=[_FixtureWorkday(wd_entry, raw_payload)], now=T1)

    rows = session.execute(
        select(PostingRow).where(PostingRow.role_title == "Third Party Risk Manager")
    ).scalars().all()
    # Three identical postings collapsed to one surviving row (the 2 collapsed).
    assert len(rows) == 1
    assert rows[0].location == "Noida, Candor TechSpace"
    assert rows[0].status == Status.open
