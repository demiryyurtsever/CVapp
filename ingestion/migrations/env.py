"""Alembic environment for the ingestion layer (dossier §3.10).

Target metadata is ``ingestion.db.Base.metadata``; importing ``ingestion.storage``
registers every ORM table on it. The URL comes from ``DATABASE_URL`` (production:
``postgresql+psycopg://…``) and falls back to a local SQLite file so the migration
can be applied and verified offline without a Postgres server.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from ingestion.db import Base
from ingestion import storage  # noqa: F401 — registers tables on Base.metadata

config = context.config

_url = (
    os.environ.get("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
    or "sqlite:///./ingestion_dev.db"
)
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
