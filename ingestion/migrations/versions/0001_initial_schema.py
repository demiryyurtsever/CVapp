"""initial ingestion schema: firms, postings, ingestion_runs

Mirrors the SQLAlchemy models in ``ingestion/storage.py``; the ``postings`` table
matches the §7 canonical ``Posting`` (``ingestion/models.py``) field-for-field,
plus the three documented §3.9/§3.11 bookkeeping columns.

Revision ID: 0001
Revises:
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Portable enums: VARCHAR + CHECK on both Postgres and SQLite (matches storage.py).
_firm_tier = sa.Enum("BB", "EB", "MM", "boutique", name="firm_tier", native_enum=False)
_program_type = sa.Enum(
    "spring_week", "summer", "off_cycle", "graduate", "unclassified",
    name="program_type", native_enum=False,
)
_region = sa.Enum("UK", "EMEA", "US", "APAC", "unknown", name="region", native_enum=False)
_status = sa.Enum("open", "closed", "reappeared", name="status", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "firms",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("tier", _firm_tier, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "postings",
        # --- §7 canonical fields ---
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("firm", sa.String(), sa.ForeignKey("firms.name"), nullable=False),
        sa.Column("firm_tier", _firm_tier, nullable=False),
        sa.Column("role_title", sa.String(), nullable=False),
        sa.Column("program_type", _program_type, nullable=False),
        sa.Column("division", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("region", _region, nullable=False),
        sa.Column("open_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("rolling", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("status", _status, nullable=False),
        sa.Column("raw_description", sa.Text(), nullable=True),
        # --- non-§7 pipeline bookkeeping (§3.9 / §3.11) ---
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("consecutive_misses", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_postings_board_filters",
        "postings",
        ["firm", "program_type", "region", "status"],
    )
    op.create_index("ix_postings_deadline", "postings", ["deadline"])
    op.create_index("uq_postings_dedup_key", "postings", ["dedup_key"], unique=True)

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("found", sa.Integer(), nullable=False),
        sa.Column("new", sa.Integer(), nullable=False),
        sa.Column("closed", sa.Integer(), nullable=False),
        sa.Column("reappeared", sa.Integer(), nullable=False),
        sa.Column("collapsed", sa.Integer(), nullable=False),
        sa.Column("per_source", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_index("uq_postings_dedup_key", table_name="postings")
    op.drop_index("ix_postings_deadline", table_name="postings")
    op.drop_index("ix_postings_board_filters", table_name="postings")
    op.drop_table("postings")
    op.drop_table("firms")
