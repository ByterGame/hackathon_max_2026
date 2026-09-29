"""Alembic: одна история миграций для пяти схем PostgreSQL."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import AsyncConnection

from src.core.config import load_database_config
from src.db.models import Base
from src.db.session import create_database_engine, create_database_url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = create_database_url(load_database_config()).render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def apply_migrations(connection: AsyncConnection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_async() -> None:
    engine = create_database_engine(load_database_config())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(apply_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_async())
