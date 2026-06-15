"""Cross-source dedup tests (dossier §3.9) — the first real test of the
multi-source premise.

These drive the pipeline over the captured Greenhouse (Point72) AND Lever
(Wealthfront) fixtures TOGETHER (CLAUDE.md rule 5: fixtures only, network blocked
in conftest.py). The stubs override only ``fetch()`` so the real adapter
``parse()`` + the real §3.8 classifiers + the real §3.9 dedup all run.

Two halves of §3.9 are asserted explicitly:

1. Different firms never merge. The dedup key is firm-scoped
   (firm + normalized_title + program_type + region), so Point72 and Wealthfront
   postings stay distinct even when titles/regions coincide: combined unique ==
   greenhouse_unique + lever_unique, exactly.
2. The SAME role on two surfaces DOES collapse. §3.9's whole point — "the same role
   frequently appears on multiple surfaces" — is exercised by presenting the same
   real Wealthfront postings through a second source: they collapse to one row,
   first-seen wins, and the collapse is logged against the duplicate's source.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ingestion.adapters.base import Adapter
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.adapters.lever import LeverAdapter
from ingestion.models import Posting, Status
from ingestion.pipeline import build_adapter, dedup_key, run_ingestion, source_key
from ingestion.registry import AtsType, SourceEntry, load_registry
from ingestion.storage import FirmRow, PostingRow

GH_FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_point72.json"
LEV_FIXTURE = Path(__file__).parent / "fixtures" / "lever_wealthfront.json"

# Known properties of the two fixtures under the locked §3.9 dedup key
# (see docs/PROGRESS.md sessions 4 and 5).
GH_FOUND, GH_UNIQUE, GH_COLLAPSED = 249, 233, 16
LEV_FOUND, LEV_UNIQUE = 15, 15
COMBINED_FOUND = GH_FOUND + LEV_FOUND          # 264
COMBINED_NEW = GH_UNIQUE + LEV_UNIQUE          # 248

T1 = datetime(2026, 6, 15, 9, 0, 0)
T2 = datetime(2026, 6, 16, 9, 0, 0)


class _FixtureGreenhouse(GreenhouseAdapter):
    def __init__(self, entry: SourceEntry, raw: dict) -> None:
        super().__init__(entry)
        self._raw = raw

    def fetch(self) -> dict:
        return self._raw


class _FixtureLever(LeverAdapter):
    def __init__(self, entry: SourceEntry, raw: list) -> None:
        super().__init__(entry)
        self._raw = raw

    def fetch(self) -> list:
        return self._raw


class _StaticSource(Adapter):
    """A second surface that re-emits already-parsed §7 Postings (not a raw source
    payload). Used to simulate the same role appearing on another source."""

    ats_type = AtsType.greenhouse

    def __init__(self, entry: SourceEntry, postings: list[Posting]) -> None:
        super().__init__(entry)
        self._postings = postings

    def fetch(self) -> list[Posting]:
        return self._postings

    def parse(self, raw: list[Posting]) -> list[Posting]:
        return list(raw)


@pytest.fixture(scope="module")
def gh_raw() -> dict:
    return json.loads(GH_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lev_raw() -> list:
    return json.loads(LEV_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def gh_entry() -> SourceEntry:
    return next(s for s in load_registry() if s.ats_type == AtsType.greenhouse)


@pytest.fixture
def lev_entry() -> SourceEntry:
    return next(s for s in load_registry() if s.ats_type == AtsType.lever)


def _count(session) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(PostingRow))


# --------------------------------------------------------------------------- #
# 0. Dispatch: the registry source actually reaches the LeverAdapter
# --------------------------------------------------------------------------- #

def test_registry_sources_dispatch_to_their_adapters() -> None:
    """§3.1 dispatch guarantee: each registry entry builds the adapter matching its
    ats_type. This is what makes Lever *used by* the pipeline, not merely present in
    the codebase — a deleted ADAPTER_REGISTRY line would fail here."""
    by_ats = {s.ats_type: s for s in load_registry()}
    assert AtsType.lever in by_ats, "registry has no ats_type: lever source"
    assert isinstance(build_adapter(by_ats[AtsType.lever]), LeverAdapter)
    assert isinstance(build_adapter(by_ats[AtsType.greenhouse]), GreenhouseAdapter)
    # Every enabled registry source resolves to a concrete adapter (no unsupported
    # ats_type slips through).
    for entry in load_registry():
        assert isinstance(build_adapter(entry), Adapter)


# --------------------------------------------------------------------------- #
# 1. Greenhouse + Lever together: the multi-source run
# --------------------------------------------------------------------------- #

def test_pipeline_runs_greenhouse_and_lever_together(
    session, gh_entry, lev_entry, gh_raw, lev_raw  # noqa: ANN001
) -> None:
    gh = _FixtureGreenhouse(gh_entry, gh_raw)
    lev = _FixtureLever(lev_entry, lev_raw)

    summary = run_ingestion(session, adapters=[gh, lev], now=T1)

    # The two sources are processed in one pass and their postings persisted.
    assert summary.found == COMBINED_FOUND
    assert summary.new == COMBINED_NEW
    assert summary.collapsed == GH_COLLAPSED   # all intra-Greenhouse; Lever adds none
    assert summary.closed == 0
    assert summary.reappeared == 0
    assert summary.errors == []
    assert _count(session) == COMBINED_NEW

    # Per-source breakdown is kept separate in the run log (§3.11).
    gh_key, lev_key = source_key(gh_entry), source_key(lev_entry)
    assert summary.per_source[gh_key] == {
        "found": GH_FOUND, "new": GH_UNIQUE, "closed": 0, "reappeared": 0, "collapsed": GH_COLLAPSED
    }
    assert summary.per_source[lev_key] == {
        "found": LEV_FOUND, "new": LEV_UNIQUE, "closed": 0, "reappeared": 0, "collapsed": 0
    }

    # Both firms were seeded (§3.10 / §6.4), and rows from both coexist.
    assert session.get(FirmRow, "Point72") is not None
    assert session.get(FirmRow, "Wealthfront") is not None
    firms = {r.firm for r in session.execute(select(PostingRow)).scalars()}
    assert firms == {"Point72", "Wealthfront"}


def test_different_firms_never_merge_firm_scoped_key(
    session, gh_entry, lev_entry, gh_raw, lev_raw  # noqa: ANN001
) -> None:
    # §3.9 key is firm-scoped: parse both sources and confirm zero key overlap, so
    # the combined unique set is exactly the sum — no cross-firm collapse can occur.
    gh_posts = GreenhouseAdapter(gh_entry).parse(gh_raw)
    lev_posts = LeverAdapter(lev_entry).parse(lev_raw)
    gh_keys = {dedup_key(p) for p in gh_posts}
    lev_keys = {dedup_key(p) for p in lev_posts}

    assert gh_keys & lev_keys == set()
    assert len(gh_keys | lev_keys) == GH_UNIQUE + LEV_UNIQUE

    # And the pipeline agrees: the combined row count equals the disjoint union.
    run_ingestion(session, adapters=[_FixtureGreenhouse(gh_entry, gh_raw),
                                     _FixtureLever(lev_entry, lev_raw)], now=T1)
    assert _count(session) == len(gh_keys | lev_keys)


def test_combined_run_is_stable_on_rerun(
    session, gh_entry, lev_entry, gh_raw, lev_raw  # noqa: ANN001
) -> None:
    # §5 stability guard across BOTH sources: a second identical run flips nothing.
    def both():
        return [_FixtureGreenhouse(gh_entry, gh_raw), _FixtureLever(lev_entry, lev_raw)]

    run_ingestion(session, adapters=both(), now=T1)
    summary2 = run_ingestion(session, adapters=both(), now=T2)

    assert (summary2.new, summary2.closed, summary2.reappeared) == (0, 0, 0)
    assert _count(session) == COMBINED_NEW
    rows = session.execute(select(PostingRow)).scalars().all()
    assert {r.status for r in rows} == {Status.open}
    assert all(r.last_seen == T2 for r in rows)   # only last_seen bumped


# --------------------------------------------------------------------------- #
# 2. Same role on two surfaces collapses to one (the core §3.9 premise)
# --------------------------------------------------------------------------- #

def test_same_role_on_two_sources_collapses_to_one(
    session, lev_entry, lev_raw  # noqa: ANN001
) -> None:
    # Source A: Wealthfront's real Lever board.
    lever = _FixtureLever(lev_entry, lev_raw)
    parsed = LeverAdapter(lev_entry).parse(lev_raw)

    # Source B: a SECOND surface for the same firm (e.g. a career-page board) that
    # lists three of the SAME roles. Same firm/title/program_type/region -> same
    # §3.9 key; only the non-key source_url differs (a different surface link).
    other_entry = SourceEntry(
        firm_name="Wealthfront",
        firm_tier="boutique",
        ats_type="greenhouse",
        endpoint_or_url="wealthfront-careers",
        region_scope="US",
    )
    dupes = [
        p.model_copy(update={"source_url": f"https://careers.wealthfront.com/{p.source_id}"})
        for p in parsed[:3]
    ]
    other = _StaticSource(other_entry, dupes)

    # A is listed first, so A wins each collision; B's three rows collapse into it.
    summary = run_ingestion(session, adapters=[lever, other], now=T1)

    assert summary.found == LEV_FOUND + 3
    assert summary.new == LEV_UNIQUE                 # 15 distinct roles, not 18
    assert summary.collapsed == 3
    assert _count(session) == LEV_UNIQUE

    # The collapse is attributed to the duplicate's source (§3.11 observability).
    other_key = source_key(other_entry)
    assert summary.per_source[other_key]["collapsed"] == 3
    assert summary.per_source[other_key]["new"] == 0

    # First-seen wins: the surviving rows kept source A's (Lever) URL, not B's.
    for dupe in dupes:
        row = session.execute(
            select(PostingRow).where(PostingRow.dedup_key == dedup_key(dupe))
        ).scalar_one()
        assert row.source_url.startswith("https://jobs.lever.co/")
