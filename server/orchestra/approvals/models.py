from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin


class Approval(Base, IdMixin, TimestampMixin):
    """Запрос на визу. kind определяет, что произойдёт после одобрения (обработчик в service.HANDLERS)."""
    __tablename__ = "approvals"

    kind: Mapped[str] = mapped_column(String(30), index=True)          # run | prompt_publish | …
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    # Права, которые человек получает на этот один объект после визы (например, providers.external)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)          # почему нужна виза — для визирующего
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    target_type: Mapped[str] = mapped_column(String(30), default="")
    target_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True)
    approver_role_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
