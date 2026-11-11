from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.core import totp
from orchestra.core.crypto import get_vault
from orchestra.core.passwords import hash_password, password_problem, verify_password
from orchestra.core.ratelimit import account_limiter, ip_key, login_limiter, reauth_limiter
from orchestra.core.tokens import same
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam import mfa
from orchestra.iam import service as auth
from orchestra.iam.deps import CSRF_COOKIE, SESSION_COOKIE, Principal, client_ip, cookie, current
from orchestra.iam.models import Membership, OrgUnit, Role, User, UserSession
from orchestra.security import alerts
from orchestra.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(Strict):
    login: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(LoginIn):
    token: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)


class PasswordIn(Strict):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


def set_cookies(resp: Response, issued: auth.IssuedSession) -> None:
    s = get_settings()
    max_age = s.session_absolute_hours * 3600
    resp.set_cookie(cookie(SESSION_COOKIE), issued.token, max_age=max_age, httponly=True, secure=s.secure_cookies,
                    samesite="strict", path="/")
    resp.set_cookie(cookie(CSRF_COOKIE), issued.csrf, max_age=max_age, httponly=False, secure=s.secure_cookies,
                    samesite="strict", path="/")


def clear_cookies(resp: Response) -> None:
    s = get_settings()
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        resp.delete_cookie(cookie(name), path="/", secure=s.secure_cookies, samesite="strict")


@router.get("/state")
async def state(db: AsyncSession = Depends(get_db)):
    return {"setup_required": await auth.count_users(db) == 0}


@router.post("/setup")
async def setup(body: SetupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Первый владелец. Работает только пока людей нет и только с разовым токеном из консоли/лаунчера."""
    ip = client_ip(request)
    if not login_limiter.hit(f"setup:{ip_key(ip)}"):
        raise HTTPException(429, "Слишком много попыток")
    if await auth.count_users(db) > 0:
        raise HTTPException(409, "Платформа уже настроена")
    expected = request.app.state.setup_token
    if not expected or not same(body.token, expected):
        await audit.record("auth.setup", ip=ip, outcome="denied", data={"reason": "неверный токен"})
        raise HTTPException(403, "Неверный токен первичной настройки")
    problem = password_problem(body.password, get_settings().password_min_length, body.login, body.display_name)
    if problem:
        raise HTTPException(422, problem)
    user = User(login=auth.norm_login(body.login), display_name=body.display_name, position="Владелец платформы",
                password_hash=hash_password(body.password), is_owner=True, password_changed_at=utcnow())
    db.add(user)
    await db.flush()
    company = (await db.execute(select(OrgUnit).where(OrgUnit.parent_id.is_(None)).limit(1))).scalar()
    admin = (await db.execute(select(Role).where(Role.key == "admin_ai"))).scalar()
    if company and admin:
        db.add(Membership(user_id=user.id, unit_id=company.id, role_id=admin.id))
    await db.commit()
    request.app.state.setup_token = ""                    # токен одноразовый
    set_cookies(response, await auth.issue_session(db, user, ip, request.headers.get("user-agent", "")))
    await audit.record("auth.setup", actor=user, ip=ip, target_type="user", target_id=user.id,
                       target_name=user.login)
    return {"ok": True}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    login_key = auth.norm_login(body.login)
    # Считаются только неудачи: утром весь офис за одним NAT входит без помех, перебор — упирается в лимит
    ip_slot = f"ip:{ip_key(ip)}"
    by_ip = not login_limiter.full(ip_slot)
    if not by_ip or account_limiter.full(login_key):
        await audit.record("auth.login", ip=ip, outcome="denied", target_name=login_key[:120],
                           data={"reason": "лимит попыток"})
        if not by_ip:
            await alerts.raise_alert("ip_flood", "Много неудачных попыток входа с одного адреса — адрес временно отсечён",
                                     ip=ip)
        else:
            await alerts.raise_alert("lockout", f"Серия попыток входа под логином «{login_key[:60]}» — вход временно закрыт",
                                     ip=ip, key=login_key)
        raise HTTPException(429, auth.TOO_MANY)
    try:
        user = await auth.authenticate(db, body.login, body.password)
    except auth.AuthError as exc:
        login_limiter.hit(ip_slot)
        account_limiter.hit(login_key)
        await audit.record("auth.login", ip=ip, outcome="denied", target_name=login_key[:120],
                           data={"reason": str(exc)})
        if exc.status == 429:
            await alerts.raise_alert("lockout", f"Учётная запись «{login_key}» заблокирована после серии неверных паролей",
                                     ip=ip, key=login_key)
        raise HTTPException(exc.status, str(exc)) from exc
    if user.totp_enabled:                                  # пароль верен — нужен второй фактор
        await db.commit()
        await audit.record("auth.password_ok", actor=user, ip=ip, target_type="user", target_id=user.id,
                           target_name=user.login)
        return {"ok": False, "need_2fa": True, "challenge": mfa.new_challenge(user.id)}
    auth.mark_success(user)
    await db.commit()
    set_cookies(response, await auth.issue_session(db, user, ip, request.headers.get("user-agent", "")))
    await audit.record("auth.login", actor=user, ip=ip, target_type="user", target_id=user.id, target_name=user.login)
    return {"ok": True, "must_change_password": user.must_change_password}


class SecondFactorIn(Strict):
    challenge: str = Field(min_length=10, max_length=600)
    code: str = Field(min_length=6, max_length=20)


@router.post("/login/2fa")
async def login_2fa(body: SecondFactorIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    ip_slot = f"ip:{ip_key(ip)}"
    if login_limiter.full(ip_slot):
        raise HTTPException(429, auth.TOO_MANY)
    user_id = mfa.open_challenge(body.challenge)
    user = await db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(401, "Срок подтверждения истёк — войдите заново")
    try:
        auth.check_locked(user)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if not mfa.check(user, body.code):
        login_limiter.hit(ip_slot)
        await auth.mark_failure(db, user)
        await audit.record("auth.login", actor=user, ip=ip, outcome="denied", target_type="user", target_id=user.id,
                           target_name=user.login, data={"reason": "неверный код второго фактора"})
        raise HTTPException(401, "Неверный код")
    auth.mark_success(user)
    await db.commit()
    set_cookies(response, await auth.issue_session(db, user, ip, request.headers.get("user-agent", "")))
    await audit.record("auth.login", actor=user, ip=ip, target_type="user", target_id=user.id, target_name=user.login,
                       data={"второй фактор": True, "кодов восстановления осталось": len(user.recovery_hashes or [])})
    return {"ok": True, "must_change_password": user.must_change_password}


@router.post("/logout")
async def logout(request: Request, response: Response, p: Principal = Depends(current),
                 db: AsyncSession = Depends(get_db)):
    await auth.revoke_session(db, request.cookies.get(cookie(SESSION_COOKIE), ""))
    clear_cookies(response)
    await audit.record("auth.logout", actor=p.user, ip=p.ip)
    return {"ok": True}


@router.get("/me")
async def me(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    tree = p.access.tree
    roles = dict((await db.execute(select(Role.id, Role.name))).all())
    u = p.user
    return {
        "user": {"id": u.id, "login": u.login, "display_name": u.display_name, "position": u.position,
                 "is_owner": u.is_owner, "must_change_password": u.must_change_password,
                 "last_login_at": iso(u.last_login_at)},
        "memberships": [{"unit_id": un, "unit": tree.names.get(un, ""), "role_id": r, "role": roles.get(r, "")}
                        for un, r in p.access.memberships],
        "permissions": p.access.summary(),
        "mfa": {"enabled": u.totp_enabled, "required": await mfa.required(db, p.access),
                "recovery_left": len(u.recovery_hashes or [])},
    }


# ── подтверждение паролем перед опасным действием ───────────────────────────────
class ReauthIn(Strict):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=20)


async def guess_failed(db: AsyncSession, p: Principal, request: Request, what: str) -> None:
    """Неверный пароль внутри сессии — тоже перебор. Исчерпал попытки — сессия закрывается."""
    if reauth_limiter.hit(p.user.id):
        return
    await auth.revoke_session(db, request.cookies.get(cookie(SESSION_COOKIE), ""))
    await alerts.raise_alert("reauth", f"«{p.user.login}»: серия неверных паролей при «{what}» — сессия закрыта",
                             ip=p.ip, key=p.user.id)
    raise HTTPException(401, "Слишком много неверных попыток — войдите заново")


@router.post("/reauth")
async def reauth(body: ReauthIn, request: Request, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, p.user.id)
    ok = verify_password(user.password_hash, body.password)
    if ok and user.totp_enabled:
        ok = mfa.check(user, body.code)
    if not ok:
        await audit.record("auth.reauth", actor=user, ip=p.ip, outcome="denied")
        await guess_failed(db, p, request, "подтверждение действия")
        raise HTTPException(403, "Неверный пароль или код")
    sess = await db.get(UserSession, p.session.id)
    sess.reauth_at = utcnow()
    await db.commit()
    await audit.record("auth.reauth", actor=user, ip=p.ip, target_type="session", target_id=sess.id)
    return {"ok": True, "minutes": get_settings().reauth_minutes}


# ── второй фактор ──────────────────────────────────────────────────────────────
class CodeIn(Strict):
    code: str = Field(min_length=6, max_length=20)


class DisableIn(CodeIn):
    password: str = Field(min_length=1, max_length=256)


@router.post("/2fa/setup")
async def mfa_setup(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Новый секрет (ещё не включён): QR для приложения-аутентификатора."""
    user = await db.get(User, p.user.id)
    if user.totp_enabled:
        raise HTTPException(409, "Второй фактор уже включён")
    secret = totp.new_secret()
    user.totp_secret_enc = get_vault().encrypt(secret, mfa.secret_purpose(user.id))
    user.totp_last_step = 0
    await db.commit()
    link = totp.uri(secret, user.login)
    return {"secret": secret, "uri": link, "qr_svg": totp.qr_svg(link)}


@router.post("/2fa/enable")
async def mfa_enable(body: CodeIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, p.user.id)
    if user.totp_enabled or not user.totp_secret_enc:
        raise HTTPException(409, "Сначала получите QR-код")
    secret = get_vault().decrypt(user.totp_secret_enc, mfa.secret_purpose(user.id))
    step = totp.verify(secret, body.code, 0)
    if step is None:
        raise HTTPException(422, "Код не подошёл — проверьте время на телефоне и введите новый")
    codes = totp.recovery_codes()
    user.totp_enabled, user.totp_last_step = True, step
    user.recovery_hashes = [totp.code_hash(c) for c in codes]
    await auth.revoke_all(db, user.id, except_hash=p.session.token_hash)
    await db.commit()
    await audit.record("auth.2fa_enabled", actor=user, ip=p.ip, target_type="user", target_id=user.id,
                       target_name=user.login)
    return {"ok": True, "recovery_codes": codes}


@router.post("/2fa/disable")
async def mfa_disable(body: DisableIn, request: Request, p: Principal = Depends(current),
                      db: AsyncSession = Depends(get_db)):
    user = await db.get(User, p.user.id)
    if await mfa.required(db, p.access):
        raise HTTPException(409, "Политика безопасности требует второй фактор для вашей роли")
    if not verify_password(user.password_hash, body.password) or not mfa.check(user, body.code):
        await audit.record("auth.2fa_disabled", actor=user, ip=p.ip, outcome="denied")
        await guess_failed(db, p, request, "отключение второго фактора")
        raise HTTPException(403, "Неверный пароль или код")
    user.totp_enabled, user.totp_secret_enc, user.recovery_hashes = False, None, []
    await db.commit()
    await audit.record("auth.2fa_disabled", actor=user, ip=p.ip, target_type="user", target_id=user.id,
                       target_name=user.login)
    return {"ok": True}


# ── сессии ─────────────────────────────────────────────────────────────────────
@router.get("/sessions")
async def sessions(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(UserSession).where(
        UserSession.user_id == p.user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > utcnow())
        .order_by(UserSession.last_seen_at.desc()))).scalars()
    return [{"id": s.id, "created_at": iso(s.created_at), "last_seen_at": iso(s.last_seen_at), "ip": s.ip,
             "user_agent": s.user_agent, "current": s.id == p.session.id} for s in rows]


@router.delete("/sessions/{session_id}")
async def revoke(session_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    s = await db.get(UserSession, session_id)
    if s is None or s.user_id != p.user.id:
        raise HTTPException(404, "Сессия не найдена")
    s.revoked_at = utcnow()
    await db.commit()
    await audit.record("auth.session_revoked", actor=p.user, ip=p.ip, target_type="session", target_id=session_id)
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_others(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    await auth.revoke_all(db, p.user.id, except_hash=p.session.token_hash)
    await db.commit()
    await audit.record("auth.session_revoked", actor=p.user, ip=p.ip, data={"все, кроме текущей": True})
    return {"ok": True}


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, p: Principal = Depends(current),
                          db: AsyncSession = Depends(get_db)):
    user = await db.get(User, p.user.id)
    if not verify_password(user.password_hash, body.current):
        await audit.record("auth.password", actor=user, ip=p.ip, outcome="denied")
        await guess_failed(db, p, request, "смена пароля")
        raise HTTPException(403, "Текущий пароль неверен")
    problem = password_problem(body.new, get_settings().password_min_length, user.login, user.display_name)
    if problem:
        raise HTTPException(422, problem)
    if verify_password(user.password_hash, body.new):
        raise HTTPException(422, "Новый пароль совпадает со старым")
    user.password_hash, user.password_changed_at, user.must_change_password = hash_password(body.new), utcnow(), False
    (await db.get(UserSession, p.session.id)).reauth_at = utcnow()
    await auth.revoke_all(db, user.id, except_hash=p.session.token_hash)    # выйти на остальных устройствах
    await db.commit()
    await audit.record("auth.password", actor=user, ip=p.ip, target_type="user", target_id=user.id)
    return {"ok": True}
