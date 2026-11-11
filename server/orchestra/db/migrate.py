"""Миграции схемы при старте. Alembic запускается в отдельном потоке: у него свой цикл asyncio."""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

INI = Path(__file__).with_name("alembic.ini")


def config(url: str | None = None) -> Config:
    cfg = Config(str(INI))
    if url:
        cfg.attributes["url"] = url
    return cfg


def upgrade(url: str | None = None) -> None:
    command.upgrade(config(url), "head")


async def upgrade_async(url: str | None = None) -> None:
    await asyncio.to_thread(upgrade, url)
