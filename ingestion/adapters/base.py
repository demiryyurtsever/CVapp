"""Adapter interface (dossier §3.2).

Every adapter implements exactly two operations:

* ``fetch()``     — return the raw, unmodified source payload.
* ``parse(raw)``  — return normalized ``Posting`` objects (the §7 canonical schema).

Adapters are STATELESS and never touch the database — the pipeline orchestrator
is the only component that persists postings. An adapter holds only its immutable
registry entry as configuration; it does no caching and reads no config from the
DB. Isolation is the point: when a bank changes its page, exactly one adapter
breaks and the run log shows which (§3.2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ingestion.models import Posting
from ingestion.registry import AtsType, SourceEntry


class Adapter(ABC):
    """Base class shared by every ATS adapter."""

    #: The ATS this adapter handles; set by each subclass.
    ats_type: ClassVar[AtsType]

    def __init__(self, entry: SourceEntry) -> None:
        if entry.ats_type != self.ats_type:
            raise ValueError(
                f"{type(self).__name__} handles ats_type={self.ats_type.value!r}, "
                f"but registry entry for {entry.firm_name!r} is {entry.ats_type.value!r}"
            )
        self.entry = entry

    @abstractmethod
    def fetch(self) -> Any:
        """Return the raw source payload (one polite request, no retry loop)."""

    @abstractmethod
    def parse(self, raw: Any) -> list[Posting]:
        """Map a raw payload to canonical ``Posting`` objects. Pure; no I/O, no DB."""
