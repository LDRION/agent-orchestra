from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin, utcnow


class Agent(Base, IdMixin, TimestampMixin):
    __tablename__ = "agents"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # orchestrator — планирует и сводит; lead — руководит отделом; specialist — исполняет
    role: Mapped[str] = mapped_column(String(20), default="specialist")
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True, index=True)
    reports_to_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    model_chain: Mapped[list] = mapped_column(JSON, default=list)       # [{provider_id, model}] по порядку
    classification: Mapped[str] = mapped_column(String(20), default="internal")
    effort: Mapped[str] = mapped_column(String(10), default="medium")
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active_prompt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PromptVersion(Base, IdMixin):
    """Системный промт агента. Каждая правка — новая версия; работает только опубликованная."""
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft | pending | active | archived
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    published_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
