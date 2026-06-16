"""Read-API tests (dossier §6.1) — over a DB seeded by the real pipeline.

The ``client`` fixture serves a SQLite DB that was filled by ``run_ingestion``
over the three captured real-board fixtures (Point72/GH, Wealthfront/Lever,
Barclays/WD). Filter and pagination expectations are computed from the seeded DB
(``read_session``) rather than hardcoded, so the assertions track the fixtures and
the §3.8 classifiers, not a snapshot of magic numbers.

Covered: list returns the seeded rows; each §3.10-indexed filter
(firm/program_type/region/status) narrows correctly; pagination is correct and
stably ordered; the detail endpoint returns the full §7 record incl.
``raw_description``; and the non-§7 pipeline bookkeeping columns
(``dedup_key`` / ``consecutive_misses`` / ``source``) never leak into a response.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from ingestion.models import ProgramType, Region, Status
from ingestion.pipeline import run_ingestion
from ingestion.storage import PostingRow

# The three bookkeeping columns on PostingRow that are NOT part of §7 and must
# never surface in a read-API response (api/schemas.py rationale).
BOOKKEEPING_KEYS = {"dedup_key", "consecutive_misses", "source"}

# Every §7 field the detail endpoint is contracted to return.
SEVEN_FIELDS = {
    "id", "firm", "firm_tier", "role_title", "program_type", "division",
    "location", "region", "open_date", "deadline", "rolling", "source_url",
    "source_id", "first_seen", "last_seen", "status", "raw_description",
}


def _db_count(session, *filters) -> int:  # noqa: ANN001, ANN002
    return session.scalar(select(func.count()).select_from(PostingRow).where(*filters))


def _all_items(client, **params) -> list[dict]:  # noqa: ANN001
    """Page through the whole filtered list and return every item (order preserved)."""
    items: list[dict] = []
    offset = 0
    while True:
        resp = client.get("/postings", params={**params, "limit": 200, "offset": offset})
        assert resp.status_code == 200
        body = resp.json()
        items.extend(body["items"])
        offset += len(body["items"])
        if offset >= body["total"] or not body["items"]:
            return items


# --------------------------------------------------------------------------- #
# Sanity / list shape
# --------------------------------------------------------------------------- #

def test_health(client) -> None:  # noqa: ANN001
    assert client.get("/health").json() == {"status": "ok"}


def test_list_returns_all_seeded_postings(client, read_session) -> None:  # noqa: ANN001
    total_in_db = _db_count(read_session)
    assert total_in_db > 0  # the seed actually put rows in

    body = client.get("/postings", params={"limit": 200, "offset": 0}).json()
    assert body["total"] == total_in_db
    assert body["limit"] == 200 and body["offset"] == 0
    # All three seeded firms are represented.
    firms = {item["firm"] for item in _all_items(client)}
    assert {"Point72", "Wealthfront", "Barclays"} <= firms


def test_default_sort_is_first_seen_desc_then_id(client, read_session) -> None:  # noqa: ANN001
    api_ids = [item["id"] for item in _all_items(client)]
    db_ids = [
        str(r.id)
        for r in read_session.execute(
            select(PostingRow).order_by(PostingRow.first_seen.desc(), PostingRow.id.asc())
        ).scalars()
    ]
    assert api_ids == db_ids  # API order matches the documented deterministic sort


# --------------------------------------------------------------------------- #
# Filters — each must narrow to exactly the DB ground truth
# --------------------------------------------------------------------------- #

def test_filter_by_firm(client, read_session) -> None:  # noqa: ANN001
    items = _all_items(client, firm="Point72")
    assert {i["firm"] for i in items} == {"Point72"}
    assert len(items) == _db_count(read_session, PostingRow.firm == "Point72")


def test_filter_by_program_type(client, read_session) -> None:  # noqa: ANN001
    items = _all_items(client, program_type="summer")
    assert {i["program_type"] for i in items} == {"summer"}
    assert len(items) == _db_count(read_session, PostingRow.program_type == ProgramType.summer)


def test_filter_by_region(client, read_session) -> None:  # noqa: ANN001
    items = _all_items(client, region="UK")
    assert {i["region"] for i in items} == {"UK"}
    assert len(items) == _db_count(read_session, PostingRow.region == Region.UK)


def test_filters_compose_with_and(client, read_session) -> None:  # noqa: ANN001
    items = _all_items(client, firm="Point72", program_type="summer")
    assert all(i["firm"] == "Point72" and i["program_type"] == "summer" for i in items)
    assert len(items) == _db_count(
        read_session,
        PostingRow.firm == "Point72",
        PostingRow.program_type == ProgramType.summer,
    )


def test_unknown_firm_returns_empty_not_error(client) -> None:  # noqa: ANN001
    body = client.get("/postings", params={"firm": "NoSuchBank"}).json()
    assert body["total"] == 0 and body["items"] == []


def test_invalid_enum_value_is_422(client) -> None:  # noqa: ANN001
    # program_type is typed with the §7 enum, so a non-member value is rejected
    # before any query runs (no silent empty result).
    assert client.get("/postings", params={"program_type": "internship"}).status_code == 422
    assert client.get("/postings", params={"region": "Mars"}).status_code == 422
    assert client.get("/postings", params={"status": "archived"}).status_code == 422


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #

def test_pagination_pages_are_disjoint_and_complete(client, read_session) -> None:  # noqa: ANN001
    total = _db_count(read_session)

    page1 = client.get("/postings", params={"limit": 10, "offset": 0}).json()
    page2 = client.get("/postings", params={"limit": 10, "offset": 10}).json()

    assert page1["total"] == page2["total"] == total  # total ignores limit/offset
    assert len(page1["items"]) == 10
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)  # no overlap between adjacent pages

    # Walking the list in fixed-size pages reproduces the full ordered set exactly.
    walked = [i["id"] for i in _all_items(client)]
    assert len(walked) == total == len(set(walked))


def test_limit_bounds_enforced(client) -> None:  # noqa: ANN001
    assert client.get("/postings", params={"limit": 0}).status_code == 422       # below min
    assert client.get("/postings", params={"limit": 9999}).status_code == 422    # above max
    assert client.get("/postings", params={"offset": -1}).status_code == 422


# --------------------------------------------------------------------------- #
# Detail endpoint — full §7
# --------------------------------------------------------------------------- #

def test_detail_returns_full_seven_schema(client, read_session) -> None:  # noqa: ANN001
    # Pick a posting that actually has a description so raw_description is exercised.
    row = read_session.execute(
        select(PostingRow).where(PostingRow.raw_description.is_not(None)).limit(1)
    ).scalar_one()

    body = client.get(f"/postings/{row.id}").json()
    assert set(body) == SEVEN_FIELDS  # exactly §7 — no more, no fewer
    assert body["id"] == str(row.id)
    assert body["firm"] == row.firm
    assert body["role_title"] == row.role_title
    assert body["source_id"] == row.source_id
    assert body["raw_description"] == row.raw_description
    assert body["raw_description"]  # non-empty for this row


def test_detail_unknown_id_is_404(client) -> None:  # noqa: ANN001
    assert client.get("/postings/00000000-0000-0000-0000-000000000000").status_code == 404


def test_detail_malformed_id_is_422(client) -> None:  # noqa: ANN001
    assert client.get("/postings/not-a-uuid").status_code == 422


# --------------------------------------------------------------------------- #
# Internal bookkeeping must NOT leak (the core read-surface guarantee)
# --------------------------------------------------------------------------- #

def test_list_items_hide_bookkeeping_fields(client) -> None:  # noqa: ANN001
    items = _all_items(client)
    assert items
    for item in items:
        assert BOOKKEEPING_KEYS.isdisjoint(item)         # no dedup_key/consecutive_misses/source
        assert "source" not in item                      # the registry source-key, specifically
        assert "raw_description" not in item             # heavy blob stays off the list view
        assert "source_id" not in item                   # internal dedup id stays off the list view
        # The public application link IS present (what a student needs to apply).
        assert "source_url" in item and "firm_tier" in item


def test_detail_hides_bookkeeping_but_keeps_public_source_fields(client, read_session) -> None:  # noqa: ANN001
    row = read_session.execute(select(PostingRow).limit(1)).scalar_one()
    body = client.get(f"/postings/{row.id}").json()
    assert BOOKKEEPING_KEYS.isdisjoint(body)
    assert "source" not in body
    # §7 public source fields remain exposed on the detail view.
    assert body["source_url"] == row.source_url
    assert body["source_id"] == row.source_id


# --------------------------------------------------------------------------- #
# Status filter across a real lifecycle transition (open vs closed)
# --------------------------------------------------------------------------- #

def test_status_filter_partitions_open_and_closed(api_tools) -> None:  # noqa: ANN001
    """Drive the Point72 source empty on a second run (with N=1) so every Point72
    posting flips to closed, then assert the status filter partitions the board."""
    t1 = datetime(2026, 6, 15, 9, 0, 0)
    t2 = datetime(2026, 6, 16, 9, 0, 0)

    engine = api_tools.fresh_engine()
    from ingestion.db import make_session_factory

    factory = make_session_factory(engine)
    with factory() as session:
        run_ingestion(session, adapters=api_tools.build_seed_adapters(), now=t1)
        empty_gh = {"jobs": [], "meta": {"total": 0}}
        run_ingestion(
            session,
            adapters=api_tools.build_seed_adapters(gh_raw=empty_gh),
            now=t2,
            closed_after_n_misses=1,
        )

    with api_tools.make_client(engine) as client:
        all_total = client.get("/postings", params={"limit": 1}).json()["total"]
        open_body = client.get("/postings", params={"status": "open", "limit": 200}).json()
        closed_body = client.get("/postings", params={"status": "closed", "limit": 200}).json()

        # The two statuses partition the whole board.
        assert open_body["total"] + closed_body["total"] == all_total
        assert open_body["total"] > 0 and closed_body["total"] > 0

        # Closed = exactly the (now-absent) Point72 postings; open = the rest.
        closed_items = _all_items(client, status="closed")
        assert {i["firm"] for i in closed_items} == {"Point72"}
        assert all(i["status"] == "closed" for i in closed_items)

        open_items = _all_items(client, status="open")
        assert "Point72" not in {i["firm"] for i in open_items}
        assert all(i["status"] == "open" for i in open_items)
