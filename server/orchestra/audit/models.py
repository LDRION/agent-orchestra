from datetime import datetime

from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, utcnow


class AuditEvent(Base):
    """Журнал аудита. Только добавление; запись связана с предыдущей хешем (цепочка)."""
    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_name: Mapped[str] = mapped_column(String(200), default="система")
    ip: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    target_name: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(12), default="ok")     # ok | denied | error
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
