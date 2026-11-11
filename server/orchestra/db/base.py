"""Базовые классы моделей: id, сортируемые по времени, и метки создания/изменения."""
import os
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Стабильные имена ограничений — иначе Alembic не сможет их менять на SQLite/PostgreSQL одинаково
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    """UUIDv7-подобный: 48 бит миллисекунд + случайность. Индексы не фрагментируются."""
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | rand
    value = (value & ~(0xF << 76)) | (0x7 << 76)          # версия 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)          # вариант RFC 4122
    return str(uuid.UUID(int=value))


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: DateTime(timezone=True)}


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
