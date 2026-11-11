from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin, utcnow

ACTIVE = ("planning", "queued", "running")
FINAL = ("succeeded", "failed", "cancelled", "rejected", "interrupted")


class Run(Base, IdMixin, TimestampMixin):
    """Задача команде агентов: запрос → (виза) → план → исполнение шагов → итог."""
    __tablename__ = "runs"

    title: Mapped[str] = mapped_column(String(200))
    request: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(10), default="team")        # team — план оркестратора; direct — один агент
    # откуда задача: "" — поставил человек; webhook — входящее событие (недоверенный текст, без выхода в интернет)
    origin: Mapped[str] = mapped_column(String(20), default="", server_default="")
    direct_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    initiator_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True, index=True)
    classification: Mapped[str] = mapped_column(String(20), default="internal")
    # planning | awaiting_approval | queued | running | succeeded | failed | cancelled | rejected | interrupted
    status: Mapped[str] = mapped_column(String(20), default="planning", index=True)
    stage: Mapped[str] = mapped_column(String(10), default="start")       # start — до плана; plan — план готов
    approach: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    # Права «с одобрением», выданные визой на ЭТУ задачу (providers.external, tools.web, …)
    preapproved: Mapped[list] = mapped_column(JSON, default=list)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)


class RunStep(Base, IdMixin):
    __tablename__ = "run_steps"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(120), default="")
    task: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="pending")   # pending | running | done | failed | skipped | cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    provider_name: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)   # какой версией промта сделано
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)


class RunEvent(Base):
    """Поток событий задачи — для живого просмотра (SSE) и истории."""
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    type: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
