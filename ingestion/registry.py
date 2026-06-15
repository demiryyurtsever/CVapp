"""Source registry (dossier §3.1).

A YAML config listing every target firm-source — one entry per firm-source. The
scheduler iterates it and dispatches each entry to the adapter matching its
``ats_type``. Adding a firm on a supported ATS = one new YAML entry, no code.

This module only *reads* config into typed objects; it never touches the DB.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from ingestion.models import FirmTier

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "config" / "registry.yaml"


class AtsType(str, Enum):
    """Supported applicant-tracking systems (§3.1)."""

    greenhouse = "greenhouse"
    lever = "lever"
    workday = "workday"
    custom = "custom"


class SourceEntry(BaseModel):
    """One firm-source row of the registry (§3.1 fields)."""

    model_config = ConfigDict(extra="forbid")

    firm_name: str
    firm_tier: FirmTier
    ats_type: AtsType
    # The company/tenant token. For greenhouse it is the token in
    # boards-api.greenhouse.io/v1/boards/{company}/jobs; for lever the token in
    # api.lever.co/v0/postings/{company}; for workday the Workday tenant
    # (e.g. "barclays" in barclays.wd3.myworkdayjobs.com).
    endpoint_or_url: str
    region_scope: str
    enabled: bool = True
    polling_notes: str | None = None
    # Optional ATS-specific configuration kept in registry DATA, not code (§2.3).
    # greenhouse/lever need none (a token is enough). Workday's endpoint is more
    # parameterized — its URL is {tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/
    # {site}/jobs — so the per-tenant quirks (dc number "wd{n}", site name, the
    # early-careers search/facet filter) live here. This config-not-subclass choice
    # for Workday tenant variation is the [OPEN] §8.2 decision; see adapters/workday.py.
    config: dict[str, Any] | None = None


def load_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> list[SourceEntry]:
    """Load and validate every registry entry from the YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [SourceEntry(**entry) for entry in raw.get("sources", [])]


def enabled_sources(path: Path | str = DEFAULT_REGISTRY_PATH) -> list[SourceEntry]:
    """Only the entries the scheduler should poll."""
    return [s for s in load_registry(path) if s.enabled]
