"""Вход, сессии, первичная настройка.

Сессия — случайный токен в cookie (HttpOnly, SameSite=Strict, Secure под HTTPS); в БД — его хеш.
CSRF — двойная отправка: второй токен в читаемой cookie, клиент повторяет его в заголовке
X-CSRF-Token; сервер сверяет с хешем в сессии. Запросы с чужих сайтов его прочитать не могут.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.core.passwords import hash_password, needs_rehash, verify_password
from orchestra.core.tokens import new_token, token_hash
from orchestra.db.base import utcnow
from orchestra.iam.models import User, UserSession
from orchestra.settings import get_settings

GENERIC_LOGIN_ERROR = "Неверный логин или пароль"
# Одинаково для блокировки учётной записи и лимита попыток по логину: по ответу нельзя понять, есть ли логин
TOO_MANY = "Слишком много попыток входа. Повторите через 15 минут."


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass
class IssuedSession:
    token: str
    csrf: str
    expires_at: datetime


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def norm_login(login: str) -> str:
    return (login or "").strip().lower()


async def count_users(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(User.id)))).scalar() or 0


async def authenticate(db: AsyncSession, login: str, password: str) -> User:
    s = get_settings()
    user = (await db.execute(select(User).where(User.login == norm_login(login)))).scalar()
    now = utcnow()
    if user is not None and user.locked_until and aware(user.locked_until) > now:
        verify_password(None, password)                  # то же время ответа, что и при неверном пароле
        raise AuthError(TOO_MANY, 429)
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        if user is not None:
            await mark_failure(db, user)
        raise AuthError(GENERIC_LOGIN_ERROR)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    # Счётчик ошибок обнуляет только полный вход (mark_success): иначе верный пароль
    # позволял бы бесконечно перебирать второй фактор.
    return user


def check_locked(user: User) -> None:
    if user.locked_until and aware(user.locked_until) > utcnow():
        raise AuthError(TOO_MANY, 429)


async def mark_failure(db: AsyncSession, user: User) -> None:
    s = get_settings()
    user.failed_logins += 1
    if user.failed_logins >= s.login_max_failures:
        user.locked_until = utcnow() + timedelta(minutes=s.login_lock_minutes)
        user.failed_logins = 0
    await db.commit()


def mark_success(user: User) -> None:
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, utcnow()


async def issue_session(db: AsyncSession, user: User, ip: str, user_agent: str) -> IssuedSession:
    s = get_settings()
    token, csrf = new_token(), new_token()
    expires = utcnow() + timedelta(hours=s.session_absolute_hours)
    db.add(UserSession(token_hash=token_hash(token), csrf_hash=token_hash(csrf), user_id=user.id,
                       expires_at=expires, ip=ip[:64], user_agent=user_agent[:300], reauth_at=utcnow()))
    # старые сессии этого человека чистим заодно
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at < utcnow()))
    await db.commit()
    return IssuedSession(token=token, csrf=csrf, expires_at=expires)


async def resolve_session(db: AsyncSession, token: str) -> tuple[UserSession, User] | None:
    if not token:
        return None
    sess = (await db.execute(select(UserSession).where(UserSession.token_hash == token_hash(token)))).scalar()
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if aware(sess.expires_at) < now or aware(sess.last_seen_at) + idle < now:
        return None
    user = await db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if (now - aware(sess.last_seen_at)).total_seconds() > 60:      # не пишем в БД на каждый запрос
        sess.last_seen_at = now
        await db.commit()
    return sess, user


async def revoke_session(db: AsyncSession, token: str) -> None:
    await db.execute(update(UserSession).where(UserSession.token_hash == token_hash(token))
                     .values(revoked_at=utcnow()))
    await db.commit()


async def revoke_all(db: AsyncSession, user_id: str, except_hash: str | None = None) -> None:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_hash:
        q = q.where(UserSession.token_hash != except_hash)
    await db.execute(q.values(revoked_at=utcnow()))
