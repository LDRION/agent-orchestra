"""Движок и сессии. SQLite — WAL и внешние ключи; PostgreSQL — пул соединений."""
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from orchestra.settings import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    url = url or get_settings().db_url
    if url.startswith("sqlite"):
        _engine = create_async_engine(url, connect_args={"timeout": 30})
        event.listen(_engine.sync_engine, "connect", _sqlite_pragmas)
    else:
        _engine = create_async_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None
