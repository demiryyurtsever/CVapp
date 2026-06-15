"""Ingestion pipeline orchestrator (dossier §3.7 / §3.9 / §3.11).

The single component that turns adapter output into persisted state. It is the
ONLY place postings are written to the database — adapters stay stateless and
DB-free (§3.2).

Per run (§3.7):

    load registry -> for each enabled source dispatch to its adapter
    -> collect normalized §7 Postings -> dedup within the run (§3.9)
    -> diff against the DB (new / present / absent) -> write -> log a run summary.

Error isolation (§3.2): an adapter that raises is caught, logged with its source
id, and skipped — one broken source never halts the run.

Lifecycle / change detection (§3.9):

    * new dedup key                      -> insert, first_seen + last_seen = now
    * existing key present this run       -> update last_seen, misses reset to 0
    * key absent for N consecutive runs   -> status = closed (N config, default 2)
    * previously-closed key reappears     -> status = reappeared

A per-posting ``consecutive_misses`` counter (stored on the row) means a single
missed run never closes a live posting: a key must be absent for N runs in a row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ingestion.adapters.base import Adapter
from ingestion.adapters.greenhouse import GreenhouseAdapter
from ingestion.adapters.lever import LeverAdapter
from ingestion.models import Posting, Status
from ingestion.registry import (
    DEFAULT_REGISTRY_PATH,
    AtsType,
    SourceEntry,
    enabled_sources,
)
from ingestion.storage import FirmRow, IngestionRunRow, PostingRow

# §3.9: a key must be absent this many consecutive runs before it flips to closed.
# Config knob (default 2); override per call via run_ingestion(closed_after_n_misses=).
DEFAULT_CLOSED_AFTER_N_MISSES = 2

# ats_type -> adapter class. Adding a firm on a supported ATS is a registry entry,
# not code (§3.1); adding a new ATS family is one line here.
ADAPTER_REGISTRY: dict[AtsType, type[Adapter]] = {
    AtsType.greenhouse: GreenhouseAdapter,
    AtsType.lever: LeverAdapter,
}

_COUNT_KEYS = ("found", "new", "closed", "reappeared", "collapsed")


class UnsupportedAtsType(Exception):
    """No adapter is registered for a source's ats_type."""


def build_adapter(entry: SourceEntry) -> Adapter:
    """Instantiate the adapter matching ``entry.ats_type`` (§3.1 dispatch)."""
    cls = ADAPTER_REGISTRY.get(entry.ats_type)
    if cls is None:
        raise UnsupportedAtsType(
            f"no adapter registered for ats_type={entry.ats_type.value!r} "
            f"(source {entry.firm_name!r})"
        )
    return cls(entry)


def source_key(entry: SourceEntry) -> str:
    """Stable identifier for a registry source, used in the run log (§3.11)."""
    return f"{entry.ats_type.value}:{entry.endpoint_or_url}"


def normalize_title(title: str) -> str:
    """Normalize a role title for the dedup key: case-fold + collapse whitespace."""
    return re.sub(r"\s+", " ", title or "").strip().lower()


def dedup_key(posting: Posting) -> str:
    """§3.9 dedup key = firm + normalized_title + program_type + region.

    Three of the four fields are classifier-derived, so this key is only as stable
    as the classifiers are deterministic — see the stability guard in the tests.
    """
    return "|".join(
        [
            posting.firm,
            normalize_title(posting.role_title),
            posting.program_type.value,
            posting.region.value,
        ]
    )


@dataclass
class RunSummary:
    """What a single ``run_ingestion`` call did — returned to the caller and
    mirrored into the ``ingestion_runs`` row (§3.11)."""

    run_id: UUID
    found: int = 0
    new: int = 0
    closed: int = 0
    reappeared: int = 0
    collapsed: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)


def _zero_counts() -> dict[str, int]:
    return {key: 0 for key in _COUNT_KEYS}


def _upsert_firm(session: Session, entry: SourceEntry) -> None:
    """Ensure a firms row exists for this source (§3.10 / §6.4 firm_tier seeding)."""
    firm = session.get(FirmRow, entry.firm_name)
    if firm is None:
        session.add(
            FirmRow(name=entry.firm_name, tier=entry.firm_tier, notes=entry.polling_notes)
        )
    else:
        firm.tier = entry.firm_tier
        firm.notes = entry.polling_notes


def _new_row(posting: Posting, key: str, skey: str, now: datetime) -> PostingRow:
    return PostingRow(
        firm=posting.firm,
        firm_tier=posting.firm_tier,
        role_title=posting.role_title,
        program_type=posting.program_type,
        division=posting.division,
        location=posting.location,
        region=posting.region,
        open_date=posting.open_date,
        deadline=posting.deadline,
        rolling=posting.rolling,
        source_url=posting.source_url,
        source_id=posting.source_id,
        first_seen=now,
        last_seen=now,
        status=Status.open,
        raw_description=posting.raw_description,
        dedup_key=key,
        consecutive_misses=0,
        source=skey,
    )


def _refresh_row(row: PostingRow, posting: Posting, skey: str, now: datetime) -> None:
    """Update a present posting's mutable fields and bump last_seen (§3.9).

    region/program_type/firm are part of the dedup key so they cannot change for a
    matched row; the volatile fields (location, deadline, description, …) can, so
    they are refreshed to the latest board state. Status/misses are handled by the
    caller's lifecycle logic.
    """
    row.role_title = posting.role_title
    row.division = posting.division
    row.location = posting.location
    row.open_date = posting.open_date
    row.deadline = posting.deadline
    row.rolling = posting.rolling
    row.source_url = posting.source_url
    row.source_id = posting.source_id
    row.raw_description = posting.raw_description
    row.last_seen = now
    row.source = skey


def run_ingestion(
    session: Session,
    *,
    adapters: Sequence[Adapter] | None = None,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    closed_after_n_misses: int = DEFAULT_CLOSED_AFTER_N_MISSES,
    now: datetime | None = None,
) -> RunSummary:
    """Run one ingestion pass and persist the results.

    ``adapters`` lets a caller (notably the test-suite) inject pre-built adapters;
    when ``None`` the enabled registry sources are dispatched to their adapters.
    ``now`` is injectable so lifecycle timestamps are deterministic in tests.
    """
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

    errors: list[dict[str, str]] = []
    per_source: dict[str, dict[str, int]] = {}

    # Resolve the (entry, adapter) work items. A registry entry whose adapter
    # cannot even be built is recorded as a source error, not a crash.
    specs: list[tuple[SourceEntry, Adapter | None, Exception | None]] = []
    if adapters is None:
        for entry in enabled_sources(registry_path):
            try:
                specs.append((entry, build_adapter(entry), None))
            except Exception as exc:  # noqa: BLE001 — isolate config gaps per source
                specs.append((entry, None, exc))
    else:
        specs = [(adapter.entry, adapter, None) for adapter in adapters]

    # --- collect normalized postings, one source at a time ---
    collected: list[tuple[str, Posting]] = []
    for entry, adapter, build_error in specs:
        skey = source_key(entry)
        per_source.setdefault(skey, _zero_counts())
        _upsert_firm(session, entry)

        if build_error is not None:
            errors.append({"source": skey, "error": repr(build_error)})
            continue

        # [OPEN] §8.2 adapter concurrency: SEQUENTIAL dispatch only. Sources are
        # fetched one after another. Bounded-concurrent dispatch across sources is
        # an open decision (§8.2) and is intentionally NOT built here.
        try:
            postings = adapter.parse(adapter.fetch())
        except Exception as exc:  # noqa: BLE001
            # §3.2: one broken source is caught, logged with its id, and skipped —
            # it never halts the run.
            errors.append({"source": skey, "error": repr(exc)})
            continue

        per_source[skey]["found"] += len(postings)
        collected.extend((skey, posting) for posting in postings)

    # --- dedup within this run (§3.9): collapse duplicate keys, first wins ---
    # A collapse is attributed to the source of the dropped duplicate and logged
    # (§3.11) so the dropped rows are observable, not silently invisible.
    incoming: dict[str, tuple[str, Posting]] = {}
    for skey, posting in collected:
        key = dedup_key(posting)
        if key in incoming:
            per_source[skey]["collapsed"] += 1
        else:
            incoming[key] = (skey, posting)

    # --- diff against the DB ---
    existing: dict[str, PostingRow] = {
        row.dedup_key: row for row in session.execute(select(PostingRow)).scalars()
    }
    present = set(incoming)

    for key, (skey, posting) in incoming.items():
        row = existing.get(key)
        if row is None:
            session.add(_new_row(posting, key, skey, now))
            per_source[skey]["new"] += 1
            continue

        _refresh_row(row, posting, skey, now)
        if row.status == Status.closed:
            # A closed key returning is a useful signal to surface to users (§3.9).
            row.status = Status.reappeared
            per_source[skey]["reappeared"] += 1
        row.consecutive_misses = 0

    for key, row in existing.items():
        if key in present or row.status == Status.closed:
            continue
        row.consecutive_misses += 1
        if row.consecutive_misses >= closed_after_n_misses:
            row.status = Status.closed
            # Attribute the closure to the source the posting last appeared on.
            per_source.setdefault(row.source, _zero_counts())["closed"] += 1

    # --- log the run (§3.11) ---
    totals = {key: sum(counts[key] for counts in per_source.values()) for key in _COUNT_KEYS}
    run = IngestionRunRow(
        started_at=now,
        finished_at=now,
        per_source=per_source,
        errors=errors,
        **totals,
    )
    session.add(run)
    session.commit()

    return RunSummary(run_id=run.id, per_source=per_source, errors=errors, **totals)
