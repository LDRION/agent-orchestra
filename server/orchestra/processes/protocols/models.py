from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin


class DirDepartment(Base, IdMixin, TimestampMixin):
    """Отдел для сопоставления «поручить ландшафтному отделу». external_id — id в Битрикс24."""
    __tablename__ = "dir_departments"

    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    head_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    external_id: Mapped[str] = mapped_column(String(60), default="", index=True)


class DirPerson(Base, IdMixin, TimestampMixin):
    __tablename__ = "dir_people"

    last_name: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(100), default="")
    middle_name: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[str] = mapped_column(String(200), default="")
    department_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    external_id: Mapped[str] = mapped_column(String(60), default="", index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DirProject(Base, IdMixin, TimestampMixin):
    __tablename__ = "dir_projects"

    name: Mapped[str] = mapped_column(String(300))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(200), default="")
    external_id: Mapped[str] = mapped_column(String(60), default="", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Protocol(Base, IdMixin, TimestampMixin):
    """Протокол совещания: текст → разбор моделью → правила → проверка человеком → Битрикс24."""
    __tablename__ = "protocols"

    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(300), default="")
    meeting_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True)
    classification: Mapped[str] = mapped_column(String(20), default="internal")
    # processing | ready | partial | error | interrupted
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    skipped: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class ProtocolTask(Base, IdMixin, TimestampMixin):
    """Задача из протокола. data — поля со статусами (факт / предположение / пробел / исправлено)."""
    __tablename__ = "protocol_tasks"

    protocol_id: Mapped[str] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="draft")     # draft | confirmed | sending | sent | failed
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_hash: Mapped[str] = mapped_column(String(32), default="")
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(60), default="")
    send_error: Mapped[str] = mapped_column(Text, default="")
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
