from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, utcnow


class SystemSetting(Base):
    """Настройки платформы, которые меняются из интерфейса (политика данных, модели по умолчанию)."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
