"""Canonical posting schema (dossier §7) and shared enums.

Every adapter's ``parse()`` returns ``list[Posting]``; no raw source shape ever
crosses the adapter boundary (§2.3 / §3.2). This model *is* the boundary.

Pipeline-owned fields (``id``, ``first_seen``, ``last_seen``) are optional and
default to ``None``: a stateless adapter cannot know them at parse time — the
pipeline orchestrator assigns them on persistence (§3.9, a later session).
``status`` defaults to ``open`` because a posting present on a live board is open
by definition; the §3.9 change-detection logic owns transitions to
``closed``/``reappeared``. The fields are declared here so the model matches §7
exactly (``extra="forbid"`` keeps anything else from leaking in).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FirmTier(str, Enum):
    """§7 firm_tier — seeded from the registry, powers later filtering (§6.4)."""

    BB = "BB"
    EB = "EB"
    MM = "MM"
    BOUTIQUE = "boutique"


class ProgramType(str, Enum):
    """§7 program_type — classified from the title (§3.8); unknown -> review queue."""

    spring_week = "spring_week"
    summer = "summer"
    off_cycle = "off_cycle"
    graduate = "graduate"
    unclassified = "unclassified"


class Region(str, Enum):
    """§7 region — normalized from the posting location (§3.8)."""

    UK = "UK"
    EMEA = "EMEA"
    US = "US"
    APAC = "APAC"
    unknown = "unknown"


class Status(str, Enum):
    """§7 status — lifecycle assigned by change detection (§3.9)."""

    open = "open"
    closed = "closed"
    reappeared = "reappeared"


class Posting(BaseModel):
    """The canonical posting (§7). Adapters emit these and nothing else."""

    model_config = ConfigDict(extra="forbid")

    # --- pipeline-owned: assigned on persistence, not by the adapter ---
    id: UUID | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    status: Status = Status.open

    # --- from the registry config entry ---
    firm: str
    firm_tier: FirmTier

    # --- read directly from the source payload ---
    role_title: str
    location: str
    source_url: str
    source_id: str
    open_date: date | None = None
    deadline: date | None = None
    raw_description: str | None = None

    # --- derived by the shared classifiers (§3.8) ---
    program_type: ProgramType
    division: str | None = None
    region: Region
    rolling: bool = False
