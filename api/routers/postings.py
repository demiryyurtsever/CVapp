"""Postings read endpoints (dossier §6.1) — the first read path over §3 storage.

``GET /postings``       — filtered, paginated list (the board/calendar feed).
``GET /postings/{id}``  — one posting's full §7 record (incl. raw_description).

Read-only by construction: both handlers only ``SELECT`` and never mutate. The
list filters are exactly the columns the §3.10 board-filter index already covers
(``firm, program_type, region, status``) — no new indexes or schema fields are
introduced here. Filter values are typed with the canonical §7 enums, so an
invalid value (e.g. ``program_type=internship``) is rejected with a 422 by
FastAPI before any query runs.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import get_session
from api.schemas import PostingDetail, PostingPage, PostingSummary
from ingestion.models import ProgramType, Region, Status
from ingestion.storage import PostingRow

router = APIRouter(prefix="/postings", tags=["postings"])

# Pagination bounds. A sane default page with a hard ceiling so a single request
# can never ask the DB for the whole table.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("", response_model=PostingPage, summary="List postings (filtered, paginated)")
def list_postings(
    session: Session = Depends(get_session),
    firm: str | None = Query(default=None, description="Exact canonical firm name (§7 firm)."),
    program_type: ProgramType | None = Query(default=None, description="§7 program_type."),
    region: Region | None = Query(default=None, description="§7 normalized region."),
    status: Status | None = Query(default=None, description="§7 lifecycle status."),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> PostingPage:
    """List postings, newest first.

    Each provided filter is ANDed onto the query over an indexed §3.10 column.
    Default sort is ``first_seen`` descending so the freshest postings lead (§6.2
    "new this week"); ``id`` is the deterministic tiebreaker because a bulk
    ingestion run stamps every row with the same ``first_seen``, which would
    otherwise make pagination order unstable.
    """
    filters = []
    if firm is not None:
        filters.append(PostingRow.firm == firm)
    if program_type is not None:
        filters.append(PostingRow.program_type == program_type)
    if region is not None:
        filters.append(PostingRow.region == region)
    if status is not None:
        filters.append(PostingRow.status == status)

    total = session.scalar(select(func.count()).select_from(PostingRow).where(*filters))

    rows = (
        session.execute(
            select(PostingRow)
            .where(*filters)
            .order_by(PostingRow.first_seen.desc(), PostingRow.id.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return PostingPage(
        total=total or 0,
        limit=limit,
        offset=offset,
        items=[PostingSummary.model_validate(row) for row in rows],
    )


@router.get("/{posting_id}", response_model=PostingDetail, summary="One posting (full §7)")
def get_posting(
    posting_id: UUID,
    session: Session = Depends(get_session),
) -> PostingDetail:
    """Return one posting's full §7 record, or 404 if no such id."""
    row = session.get(PostingRow, posting_id)
    if row is None:
        raise HTTPException(status_code=404, detail="posting not found")
    return PostingDetail.model_validate(row)
