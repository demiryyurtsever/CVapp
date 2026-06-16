"""Read-API response models — projections of the canonical §7 posting schema.

The canonical schema is defined ONCE, in ``ingestion/models.py`` (``Posting``).
These models do not redefine it: they import its enums (``FirmTier``,
``ProgramType``, ``Region``, ``Status``) and re-expose a chosen subset of §7 as
the read-API surface. A storage row (``ingestion.storage.PostingRow``) carries
every §7 field plus three non-§7 pipeline bookkeeping columns — ``dedup_key``,
``consecutive_misses`` and ``source`` (§3.9 / §3.11). What a read-API consumer
sees vs. that internal bookkeeping is decided here:

* EXPOSED (what a student hunting roles needs): firm + tier, role title, program
  type, division, location, region, the dates, the rolling flag, lifecycle
  status, the primary-source application link, and the first/last-seen timestamps
  (so the UI can surface "new this week" / freshness — §6.2).
* HIDDEN from consumers — the three bookkeeping columns above. They are how the
  pipeline deduplicates and ages postings; they are not product data. Because
  these models only declare §7 fields, the bookkeeping columns are *structurally*
  unable to appear in a response (``from_attributes`` reads only declared fields,
  and FastAPI's ``response_model`` filters output to them). ``source`` in
  particular (the registry source-key) must not be confused with ``source_url`` /
  ``source_id``, which are the §7 public application link and ATS-native id.
* ``source_id`` (§7, "ATS-native id … for dedup") is internal plumbing, so it is
  kept OFF the list view and surfaced only on the single-posting detail, which
  returns the full §7 record.

``from_attributes=True`` lets both models be built straight from a ``PostingRow``
ORM object via ``model_validate(row)``.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ingestion.models import FirmTier, ProgramType, Region, Status


class PostingSummary(BaseModel):
    """List-view projection of a §7 posting (the board/calendar row).

    Omits the heavy ``raw_description`` blob and the internal ``source_id`` — both
    belong on the detail view, not in a list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm: str
    firm_tier: FirmTier
    role_title: str
    program_type: ProgramType
    division: str | None = None
    location: str
    region: Region
    open_date: date | None = None
    deadline: date | None = None
    rolling: bool
    source_url: str
    status: Status
    first_seen: datetime
    last_seen: datetime


class PostingDetail(PostingSummary):
    """Single-posting view: the full §7 record (adds ``source_id`` and the
    ``raw_description`` the Layer 2 posting parser will eventually consume)."""

    source_id: str
    raw_description: str | None = None


class PostingPage(BaseModel):
    """A paginated slice of the postings list.

    ``total`` is the count matching the active filters (ignoring limit/offset), so
    a board UI can render page controls; ``items`` is the current page.
    """

    total: int
    limit: int
    offset: int
    items: list[PostingSummary]
