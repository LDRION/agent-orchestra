from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin, utcnow


class Provider(Base, IdMixin, TimestampMixin):
    """Подключённая модель/сервис. Ключ — только шифротекстом (Vault), в интерфейс — только хвост."""
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(300), default="")
    trust_tier: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_model: Mapped[str] = mapped_column(String(200), default="")
    secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hint: Mapped[str] = mapped_column(String(20), default="")
    redact_pii: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class LlmCall(Base, IdMixin):
    """Каждый вызов модели: кто, зачем, какая модель, токены, время, исход. Основа учёта расходов."""
    __tablename__ = "llm_calls"

    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    purpose: Mapped[str] = mapped_column(String(20), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_kind: Mapped[str] = mapped_column(String(20), default="")
    redactions: Mapped[int] = mapped_column(Integer, default=0)
