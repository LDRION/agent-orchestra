from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin, utcnow


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    login: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Владелец платформы: полный доступ (каждое действие всё равно в аудите). Минимум один.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Второй фактор (TOTP): секрет — шифротекстом в Vault; коды восстановления — только хеши
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovery_hashes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class OrgUnit(Base, IdMixin, TimestampMixin):
    """Подразделение: компания → отдел → группа / проект. Роли наследуются вниз по дереву."""
    __tablename__ = "org_units"

    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="department")   # company | department | group | project
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="RESTRICT"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Role(Base, IdMixin, TimestampMixin):
    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="#8c8c8c")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)       # системную нельзя удалить
    sort: Mapped[int] = mapped_column(Integer, default=0)


class RoleGrant(Base, IdMixin):
    __tablename__ = "role_grants"
    __table_args__ = (UniqueConstraint("role_id", "permission"),)

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    permission: Mapped[str] = mapped_column(String(80))
    effect: Mapped[str] = mapped_column(String(10))                       # allow | approval | deny
    # Кто визирует, если effect=approval. Пусто — любой с правом approvals.decide в подразделении.
    approver_role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)


class Membership(Base, IdMixin):
    """Человек × подразделение × роль. Роль действует в подразделении и во вложенных."""
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "unit_id", "role_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("org_units.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)    # временная роль: после срока не действует

    @classmethod
    def active(cls):
        return (cls.expires_at.is_(None)) | (cls.expires_at > utcnow())


class UserSession(Base, IdMixin):
    """Сессия входа. Храним только хеши токенов: утечка БД не даёт войти."""
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reauth_at: Mapped[datetime | None] = mapped_column(nullable=True)      # последний ввод пароля в этой сессии
