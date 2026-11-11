"""Зависимости FastAPI: текущий человек, его права, CSRF, проверка права с аудитом отказа."""
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.audit import service as audit
from orchestra.core.tokens import same, token_hash
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam import mfa
from orchestra.iam.authz import AccessContext, Decision, load_access
from orchestra.iam.models import User, UserSession
from orchestra.iam.service import aware, resolve_session
from orchestra.settings import get_settings

SESSION_COOKIE = "orchestra_session"
CSRF_COOKIE = "orchestra_csrf"
CSRF_HEADER = "X-CSRF-Token"
REAUTH_HEADER = "X-Orchestra-Reauth"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MFA_REQUIRED = "Включите двухфакторный вход: этого требует политика безопасности для вашей роли"
REAUTH = "Подтвердите вход паролем: действие меняет доступы, ключи или политику"
MUST_CHANGE = "Смените временный пароль: его знает тот, кто его выдал"


def cookie(name: str) -> str:
    return get_settings().cookie_prefix + name


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@dataclass
class Principal:
    user: User
    session: UserSession
    access: AccessContext
    ip: str


async def current(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    found = await resolve_session(db, request.cookies.get(cookie(SESSION_COOKIE), ""))
    if found is None:
        raise HTTPException(401, "Нужно войти")
    sess, user = found
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not same(token_hash(header), sess.csrf_hash):
            raise HTTPException(403, "Проверка CSRF не пройдена — обновите страницу")
    # Временный пароль (выдан администратором) — до смены доступен только раздел входа: проверка на сервере,
    # не только в интерфейсе
    if user.must_change_password and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(403, MUST_CHANGE)
    access = await load_access(db, user)
    # Политика требует второй фактор, а он не включён — доступен только вход/настройка 2FA/выход
    if not user.totp_enabled and not request.url.path.startswith("/api/auth/") and await mfa.required(db, access):
        raise HTTPException(403, MFA_REQUIRED)
    return Principal(user=user, session=sess, access=access, ip=client_ip(request))


async def deny(p: Principal, d: Decision, action: str, target: str = "") -> HTTPException:
    await audit.record(action, actor=p.user, ip=p.ip, target_name=target, outcome="denied",
                       data={"permission": d.permission, "reason": d.reason})
    return HTTPException(403, f"Нет доступа: {d.reason}")


def require(permission: str):
    """Право без привязки к подразделению: «может ли человек это где-нибудь»."""
    async def dep(p: Principal = Depends(current)) -> Principal:
        d = p.access.decide(permission)
        if not d.allowed:
            raise await deny(p, d, f"access.{permission}")
        return p
    return dep


def fresh(p: Principal) -> None:
    """Опасное действие — только если пароль вводили недавно: украденной сессии (cookie) для него мало."""
    at = aware(p.session.reauth_at)
    if at is None or at + timedelta(minutes=get_settings().reauth_minutes) < utcnow():
        raise HTTPException(403, REAUTH, headers={REAUTH_HEADER: "1"})


def sudo(permission: str):
    """require() + свежее подтверждение паролем. Сначала право: без права — обычный отказ, не запрос пароля."""
    async def dep(p: Principal = Depends(require(permission))) -> Principal:
        fresh(p)
        return p
    return dep
