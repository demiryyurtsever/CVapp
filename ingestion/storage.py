"""SQLAlchemy storage models (dossier §3.10).

Three tables:

* ``postings``        — the canonical §7 schema, one row per deduplicated posting.
* ``firms``           — name, tier, notes (§3.10 / §6.4).
* ``ingestion_runs``  — one row per pipeline run with per-source found/new/closed
                        counts and adapter errors (§3.11).

``PostingRow`` mirrors the §7 Pydantic ``Posting`` (``ingestion/models.py``)
field-for-field. The ONLY extra columns are pipeline bookkeeping that §3.9
requires and that the canonical schema deliberately does not model:

* ``dedup_key``          — firm + normalized_title + program_type + region (§3.9).
* ``consecutive_misses`` — runs in a row this key has been absent; drives the
                           "absent for N consecutive runs -> closed" lifecycle so
                           a single missed run never closes a live posting (§3.9).
* ``source``             — the registry source-key the posting was last seen from,
                           so a closure can be attributed to a source in the run
                           log (§3.11).

These three are flagged below so a future reader can tell intentional bookkeeping
apart from accidental schema drift. ``test_storage.py`` asserts that every §7
field is present and that nothing else but these three has crept in.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ingestion.db import Base
from ingestion.models import FirmTier, ProgramType, Region, Status

# native_enum=False stores enums as a portable VARCHAR + CHECK constraint on both
# Postgres and SQLite, and avoids the migration friction of native PG enum types.
# values_callable persists the enum *value* ("spring_week"), not its member name.
_ENUM_KW = {"native_enum": False, "values_callable": lambda e: [m.value for m in e]}


class FirmRow(Base):
    """A firm (§3.10 firms: name, tier, notes). ``firm_tier`` seeded from registry."""

    __tablename__ = "firms"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    tier: Mapped[FirmTier] = mapped_column(SAEnum(FirmTier, name="firm_tier", **_ENUM_KW))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PostingRow(Base):
    """The canonical posting (§7), persisted. One row per dedup key (§3.9)."""

    __tablename__ = "postings"

    __table_args__ = (
        # Board-UI filter queries (§3.10).
        Index("ix_postings_board_filters", "firm", "program_type", "region", "status"),
        # Calendar/deadline view (§3.10).
        Index("ix_postings_deadline", "deadline"),
        # One row per dedup key — the change-detection upsert target (§3.9).
        Index("uq_postings_dedup_key", "dedup_key", unique=True),
    )

    # --- §7 canonical fields (field-for-field with ingestion/models.py:Posting) ---
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    firm: Mapped[str] = mapped_column(String, ForeignKey("firms.name"), nullable=False)
    firm_tier: Mapped[FirmTier] = mapped_column(SAEnum(FirmTier, name="firm_tier", **_ENUM_KW))
    role_title: Mapped[str] = mapped_column(String, nullable=False)
    program_type: Mapped[ProgramType] = mapped_column(
        SAEnum(ProgramType, name="program_type", **_ENUM_KW)
    )
    division: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[Region] = mapped_column(SAEnum(Region, name="region", **_ENUM_KW))
    open_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    rolling: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[Status] = mapped_column(
        SAEnum(Status, name="status", **_ENUM_KW), nullable=False, default=Status.open
    )
    raw_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- non-§7 pipeline bookkeeping (§3.9 / §3.11) — see module docstring ---
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)
    consecutive_misses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String, nullable=False)


class IngestionRunRow(Base):
    """One ingestion run (§3.11): per-source found/new/closed counts + errors."""

    __tablename__ = "ingestion_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Run totals (sums across sources), for quick reads.
    found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reappeared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Duplicates collapsed within the run by the §3.9 dedup key (same firm +
    # normalized_title + program_type + region). Logged so the dropped rows are
    # observable and not silently invisible (§3.11).
    collapsed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Per-source breakdown: {source_key: {found, new, closed, reappeared, collapsed}} (§3.11).
    per_source: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Adapter errors with source id: [{"source": ..., "error": ...}] (§3.2 / §3.11).
    errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
