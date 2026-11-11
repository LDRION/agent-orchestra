"""Alembic: асинхронный движок; SQLite — batch-режим (ALTER TABLE через пересоздание)."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from orchestra.db.models import Base
from orchestra.settings import get_settings

config = context.config
target_metadata = Base.metadata


def url() -> str:
    return config.attributes.get("url") or get_settings().db_url


def do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(url())
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async())
