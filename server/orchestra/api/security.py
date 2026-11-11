"""Центр безопасности: состояние защиты, входы, сессии всех сотрудников, тревоги и их канал."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.audit.models import AuditEvent
from orchestra.core.redact import safe_error
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam import guard, mfa
from orchestra.iam.authz import load_access
from orchestra.iam.deps import Principal, require, sudo
from orchestra.iam.models import User, UserSession
from orchestra.integrations import service as integrations
from orchestra.integrations.models import Integration
from orchestra.security import alerts
from orchestra.settings import get_settings
from orchestra.system import settings_store

router = APIRouter(prefix="/api/security", tags=["security"])
ALERT_KINDS = ("telegram", "email", "webhook_out")
# Изменения, которые надо видеть службе безопасности: права, люди, ключи, политика, восстановление
CHANGES = ("iam.role.grants", "iam.role.create", "iam.role.delete", "iam.user.create", "iam.user.roles",
           "iam.user.owner", "iam.user.password_reset", "iam.user.2fa_reset", "iam.user.update", "iam.recovery",
           "iam.unit.update", "settings.update", "providers.create", "providers.update", "providers.delete",
           "integrations.create", "integrations.update", "integrations.delete", "audit.reanchor",
           "auth.2fa_disabled", "security.session_revoked")


def event_out(e: AuditEvent) -> dict:
    return {"seq": e.seq, "ts": iso(e.ts), "actor": e.actor_name, "ip": e.ip, "action": e.action,
            "target": e.target_name, "outcome": e.outcome, "data": e.data}


async def posture(db: AsyncSession) -> list[dict]:
    """Чек-лист: что уже защищено и что стоит поправить. Только факты конфигурации, без секретов."""
    s = get_settings()
    server = s.mode == "server"
    users = (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    privileged_no_2fa = []
    for u in users:
        if u.totp_enabled:
            continue
        access = await load_access(db, u)
        if u.is_owner or any(access.can(k) for k in mfa.PRIVILEGED):
            privileged_no_2fa.append(u.display_name)
    policy = await settings_store.get(db, "require_2fa")
    channel = (await settings_store.get(db, "security_alerts") or {}).get("integration_id")
    owners = [u for u in users if u.is_owner]
    items = [
        ("tls", "Доступ только по HTTPS", not server or s.public_url.startswith("https://"),
         "ORCHESTRA_PUBLIC_URL=https://… — Caddy выпускает сертификат сам" if server else "лаунчер: сервер слушает только этот компьютер"),
        ("cookies", "Cookie сессии защищены (Secure, __Host-, SameSite=Strict)", not server or s.secure_cookies,
         "включается вместе с HTTPS"),
        ("networks", "Вход только из сетей компании", not server or bool(s.allowed_networks),
         "ORCHESTRA_ALLOWED_NETWORKS=10.0.0.0/8,… (офис, VPN)"),
        ("mfa_policy", "Второй фактор обязателен для управляющих", policy in ("privileged", "all"),
         "Настройки → Безопасность входа"),
        ("mfa_users", "У всех управляющих включён второй фактор", not privileged_no_2fa,
         "без 2FA: " + ", ".join(privileged_no_2fa[:5]) if privileged_no_2fa else "ок"),
        ("owners", "Владельцев платформы — не больше двух", 1 <= len(owners) <= 2,
         f"сейчас: {len(owners)}"),
        ("alerts", "Тревоги приходят администраторам", bool(channel), "выберите канал ниже"),
        ("master_key", "Мастер-ключ хранится отдельно от базы", bool(s.master_key or s.master_key_file) or not server,
         "ORCHESTRA_MASTER_KEY_FILE — секрет Docker" if server else "лаунчер: ключ защищён DPAPI Windows"),
        ("reauth", "Опасные действия — с подтверждением паролем", s.reauth_minutes <= 15,
         f"окно подтверждения: {s.reauth_minutes} мин"),
    ]
    return [{"key": k, "title": t, "ok": bool(ok), "hint": h} for k, t, ok, h in items]


@router.get("/overview")
async def overview(p: Principal = Depends(require("security.view")), db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)

    async def count(*where) -> int:
        return (await db.execute(select(func.count(AuditEvent.seq)).where(AuditEvent.ts >= since, *where))).scalar() or 0

    recent = lambda *where: select(AuditEvent).where(*where).order_by(AuditEvent.seq.desc()).limit(30)  # noqa: E731
    head = (await db.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1))).scalar()
    cfg = await settings_store.get(db, "security_alerts") or {}
    return {
        "posture": await posture(db),
        "day": {"failed_logins": await count(AuditEvent.action == "auth.login", AuditEvent.outcome == "denied"),
                "denied": await count(AuditEvent.outcome == "denied"),
                "alerts": await count(AuditEvent.action == "security.alert"),
                "logins": await count(AuditEvent.action == "auth.login", AuditEvent.outcome == "ok")},
        "alerts": [event_out(e) for e in (await db.execute(recent(AuditEvent.action == "security.alert"))).scalars()],
        "changes": [event_out(e) for e in (await db.execute(recent(
            AuditEvent.action.in_(CHANGES), AuditEvent.outcome == "ok"))).scalars()],
        "failed": [event_out(e) for e in (await db.execute(recent(
            AuditEvent.action.in_(("auth.login", "auth.reauth", "auth.password", "auth.2fa_disabled")),
            AuditEvent.outcome == "denied"))).scalars()],
        "chain": {"seq": head.seq if head else 0, "hash": head.hash[:16] if head else ""},
        "channel": {"integration_id": cfg.get("integration_id"), "to": cfg.get("to") or "",
                    "options": [{"id": r.id, "name": r.name, "kind": r.kind} for r in (await db.execute(
                        select(Integration).where(Integration.kind.in_(ALERT_KINDS)))).scalars()]},
        "can_manage": p.access.can("security.manage"),
    }


@router.get("/sessions")
async def sessions(p: Principal = Depends(require("security.view")), db: AsyncSession = Depends(get_db)):
    now = utcnow()
    idle = now - timedelta(minutes=get_settings().session_idle_minutes)
    rows = (await db.execute(select(UserSession, User).join(User, User.id == UserSession.user_id).where(
        UserSession.revoked_at.is_(None), UserSession.expires_at > now, UserSession.last_seen_at > idle)
        .order_by(UserSession.last_seen_at.desc()).limit(500))).all()
    return [{"id": s.id, "user_id": u.id, "user": u.display_name, "login": u.login, "ip": s.ip,
             "user_agent": s.user_agent, "created_at": iso(s.created_at), "last_seen_at": iso(s.last_seen_at),
             "current": s.id == p.session.id, "mfa": u.totp_enabled} for s, u in rows]


@router.delete("/sessions/{session_id}")
async def revoke(session_id: str, p: Principal = Depends(sudo("security.manage")), db: AsyncSession = Depends(get_db)):
    s = await db.get(UserSession, session_id)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    owner = await db.get(User, s.user_id)
    if owner is not None and owner.id != p.user.id:
        await guard.manageable(db, p.access, owner)      # выкидывать из системы того, у кого прав больше, нельзя
    s.revoked_at = utcnow()
    await db.commit()
    await audit.record("security.session_revoked", actor=p.user, ip=p.ip, target_type="session", target_id=s.id,
                       target_name=owner.login if owner else "", data={"ip": s.ip})
    return {"ok": True}


class ChannelIn(Strict):
    integration_id: str | None = Field(default=None, max_length=36)
    to: str = Field(default="", max_length=300)


async def channel_row(db: AsyncSession, integration_id: str | None) -> Integration | None:
    if not integration_id:
        return None
    row = await db.get(Integration, integration_id)
    if row is None or row.kind not in ALERT_KINDS:
        raise HTTPException(422, "Для тревог подходят Telegram, почта или исходящий вебхук")
    return row


@router.put("/channel")
async def set_channel(body: ChannelIn, p: Principal = Depends(sudo("security.manage")),
                      db: AsyncSession = Depends(get_db)):
    row = await channel_row(db, body.integration_id)
    if row is not None and row.kind == "email" and not body.to:
        raise HTTPException(422, "Для почты укажите адрес, куда слать тревоги")
    old = await settings_store.get(db, "security_alerts")
    value = {"integration_id": row.id if row else None, "to": body.to.strip()}
    await settings_store.put(db, "security_alerts", value, p.user.id)
    await db.commit()
    await audit.record("settings.update", actor=p.user, ip=p.ip, target_type="setting", target_id="security_alerts",
                       target_name="Канал тревог", data={"было": old, "стало": value})
    return {"ok": True}


@router.post("/channel/test")
async def test_channel(p: Principal = Depends(require("security.manage")), db: AsyncSession = Depends(get_db)):
    cfg = await settings_store.get(db, "security_alerts") or {}
    row = await channel_row(db, cfg.get("integration_id"))
    if row is None:
        raise HTTPException(409, "Канал тревог не выбран")
    try:
        await alerts.send(integrations.build(row), alerts.message("test", f"Проверка канала тревог ({p.user.display_name})", p.ip),
                          str(cfg.get("to") or ""))
    except Exception as exc:                             # noqa: BLE001 — человеку нужен понятный ответ
        raise HTTPException(502, f"Не доставлено: {safe_error(exc, 200)}") from exc
    await audit.record("security.channel_test", actor=p.user, ip=p.ip, target_name=row.name)
    return {"ok": True}
