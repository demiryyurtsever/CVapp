"""Storage-layer tests (dossier §3.10).

Two jobs:

1. Prove the ``postings`` table matches the §7 Pydantic ``Posting`` field-for-field
   — every canonical field present, and the only extra columns are the three
   documented §3.9/§3.11 bookkeeping columns (drift is flagged, not reconciled).
2. Prove the hand-written Alembic migration applies and produces exactly the
   tables/columns the ORM models declare. Run offline against a temp SQLite file
   (no sockets — CLAUDE.md rule 5).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Date, Uuid, create_engine, inspect

from ingestion.db import Base
from ingestion.models import Posting
from ingestion.storage import IngestionRunRow, PostingRow

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The only non-§7 columns allowed on the postings table — pipeline bookkeeping
# that §3.9/§3.11 require and the canonical schema deliberately does not model.
BOOKKEEPING_COLUMNS = {"dedup_key", "consecutive_misses", "source"}


def test_postings_table_matches_section7_field_for_field() -> None:
    section7 = set(Posting.model_fields)
    columns = set(PostingRow.__table__.columns.keys())

    missing = section7 - columns
    assert not missing, f"§7 fields missing from the postings table: {sorted(missing)}"

    drift = columns - section7 - BOOKKEEPING_COLUMNS
    assert not drift, f"undocumented drift on the postings table: {sorted(drift)}"

    # And nothing unexpected sneaked in under the bookkeeping banner either.
    extras = columns - section7
    assert extras == BOOKKEEPING_COLUMNS, f"bookkeeping columns changed: {sorted(extras)}"


def test_key_column_types_are_portable() -> None:
    # Spot-check the types that differ across backends are the portable ones.
    cols = PostingRow.__table__.columns
    assert isinstance(cols["id"].type, Uuid)
    assert isinstance(cols["open_date"].type, Date)
    assert isinstance(cols["deadline"].type, Date)


def test_postings_indexes_present() -> None:
    index_names = {ix.name for ix in PostingRow.__table__.indexes}
    # Board-UI filter index (§3.10) and the calendar/deadline index (§3.10).
    assert "ix_postings_board_filters" in index_names
    assert "ix_postings_deadline" in index_names
    # One-row-per-dedup-key uniqueness (§3.9).
    dedup_ix = next(ix for ix in PostingRow.__table__.indexes if ix.name == "uq_postings_dedup_key")
    assert dedup_ix.unique


def test_ingestion_runs_has_per_source_and_errors() -> None:
    cols = set(IngestionRunRow.__table__.columns.keys())
    for required in ("found", "new", "closed", "reappeared", "collapsed", "per_source", "errors"):
        assert required in cols


def test_alembic_migration_matches_models(monkeypatch, tmp_path) -> None:
    db_file = tmp_path / "migration_check.db"
    url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", url)

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "ingestion" / "migrations"))
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        created = set(inspector.get_table_names()) - {"alembic_version"}
        assert created == set(Base.metadata.tables), "migration tables != model tables"

        for table_name, table in Base.metadata.tables.items():
            migrated_cols = {c["name"] for c in inspector.get_columns(table_name)}
            model_cols = set(table.columns.keys())
            assert migrated_cols == model_cols, f"{table_name}: migration columns != model columns"
    finally:
        engine.dispose()
