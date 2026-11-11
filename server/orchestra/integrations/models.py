from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin, utcnow


class Integration(Base, IdMixin, TimestampMixin):
    """Подключённая внешняя система. Секрет — шифротекстом (Vault), в интерфейс — только хвост."""
    __tablename__ = "integrations"

    key: Mapped[str] = mapped_column(String(40), unique=True)              # префикс инструментов: bitrix.create_task
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(10), default="live")          # live | demo
    trust_tier: Mapped[str] = mapped_column(String(20))                    # куда уходят данные при записи
    data_classification: Mapped[str] = mapped_column(String(20))           # гриф данных, которые система отдаёт
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hint: Mapped[str] = mapped_column(String(20), default="")
    run_as_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_message: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class IntegrationAction(Base, IdMixin, TimestampMixin):
    """Запись во внешнюю систему. Агент её только предлагает; выполняет человек (или по визе)."""
    __tablename__ = "integration_actions"

    integration_id: Mapped[str | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True)
    integration_name: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300), default="")
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    classification: Mapped[str] = mapped_column(String(20), default="internal")
    # proposed | pending_approval | executed | failed | rejected
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    step_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_type: Mapped[str] = mapped_column(String(30), default="")       # agent | protocol_task | manual
    origin_id: Mapped[str] = mapped_column(String(36), default="")
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    executed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class HookEvent(Base):
    """Отпечаток принятого входящего события: повтор перехваченного запроса отклоняется и после перезапуска,
    и при нескольких процессах сервера."""
    __tablename__ = "hook_events"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    integration_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
