from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra import __version__
from orchestra.agents.models import Agent
from orchestra.approvals import service as approvals
from orchestra.audit import service as audit
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, current, deny, fresh
from orchestra.orchestration.models import Run
from orchestra.providers import egress
from orchestra.providers.models import LlmCall, Provider
from orchestra.settings import get_settings
from orchestra.system import settings_store

router = APIRouter(prefix="/api/system", tags=["system"])

# Какое право нужно, чтобы менять настройку
SETTING_PERMS = {"egress_policy": "system.settings", "default_chain": "providers.manage",
                 "planner_chain": "providers.manage", "default_classification": "system.settings",
                 "max_parallel_steps": "system.settings", "company_name": "system.settings",
                 "user_daily_tokens": "system.settings", "require_2fa": "system.settings"}


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/info")
async def info(p: Principal = Depends(current)):
    s = get_settings()
    return {"version": __version__, "mode": s.mode, "database": "sqlite" if s.is_sqlite else "postgresql"}


@router.get("/settings")
async def get_settings_(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if not (p.access.can("providers.view") or p.access.can("system.settings")):
        raise HTTPException(403, "Нет доступа к настройкам")
    values = await settings_store.get_all(db)
    values.pop("seeded_agents", None)
    if not p.access.can("security.view"):
        values.pop("security_alerts", None)               # куда уходят тревоги — дело службы безопасности
    return {"values": values,
            "editable": {k: p.access.can(v) for k, v in SETTING_PERMS.items()},
            "tiers": [{"id": t, "title": egress.TIER_TITLES[t]} for t in egress.TIERS],
            "classifications": [{"id": c, "title": egress.CLASS_TITLES[c]} for c in egress.CLASSIFICATIONS]}


def validate(key: str, value):
    if key == "egress_policy":
        if not isinstance(value, dict) or set(value) - set(egress.TIERS) or any(
                v not in egress.CLASSIFICATIONS for v in value.values()):
            raise HTTPException(422, "Политика: уровень доверия → гриф")
    elif key in ("default_chain", "planner_chain"):
        if not isinstance(value, list) or len(value) > 6 or not all(
                isinstance(x, dict) and set(x) <= {"provider_id", "model"} and isinstance(x.get("provider_id"), str)
                for x in value):
            raise HTTPException(422, "Цепочка: список {provider_id, model}")
    elif key == "default_classification":
        if value not in egress.CLASSIFICATIONS:
            raise HTTPException(422, "Неизвестный гриф")
    elif key == "max_parallel_steps":
        if not isinstance(value, int) or not 1 <= value <= 8:
            raise HTTPException(422, "От 1 до 8")
    elif key == "company_name":
        if not isinstance(value, str) or not 1 <= len(value) <= 120:
            raise HTTPException(422, "Название — строка до 120 символов")
    elif key == "user_daily_tokens":
        if not isinstance(value, int) or not 0 <= value <= 10**10:
            raise HTTPException(422, "Лимит — целое число токенов (0 — без лимита)")
    elif key == "require_2fa":
        if value not in ("none", "privileged", "all"):
            raise HTTPException(422, "none | privileged | all")
    return value


@router.put("/settings/{key}")
async def put_setting(key: str, value: dict | list | str | int = Body(...), p: Principal = Depends(current),
                      db: AsyncSession = Depends(get_db)):
    perm = SETTING_PERMS.get(key)
    if perm is None:
        raise HTTPException(404, "Нет такой настройки")
    d = p.access.decide(perm)
    if not d.allowed:
        raise await deny(p, d, f"settings.{key}")
    fresh(p)
    old = await settings_store.get(db, key)
    await settings_store.put(db, key, validate(key, value), p.user.id)
    await db.commit()
    await audit.record("settings.update", actor=p.user, ip=p.ip, target_type="setting", target_id=key,
                       target_name=key, data={"было": old, "стало": value})
    return {"ok": True}


@router.get("/overview")
async def overview(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Сводка для «Пульта»: только то, что человеку можно видеть."""
    q = select(Run.status, func.count(Run.id)).group_by(Run.status)
    units = p.access.units_with("runs.view_all")
    if units is not None:
        q = q.where((Run.initiator_id == p.user.id) | Run.unit_id.in_(units))
    runs = dict((await db.execute(q)).all())
    since = utcnow() - timedelta(days=13)
    day = func.date(LlmCall.ts)
    series = []
    if p.access.can("system.usage"):
        series = [{"day": str(d), "calls": c, "tokens": int(t or 0)} for d, c, t in (await db.execute(
            select(day, func.count(LlmCall.id), func.sum(LlmCall.input_tokens + LlmCall.output_tokens))
            .where(LlmCall.ts >= since).group_by(day).order_by(day))).all()]
    by_tier = dict((await db.execute(select(Provider.trust_tier, func.count(Provider.id))
                                     .where(Provider.enabled.is_(True)).group_by(Provider.trust_tier))).all())
    return {
        "runs": runs,
        "approvals": len(await approvals.inbox(db, p.access)),
        "agents": (await db.execute(select(func.count(Agent.id)).where(Agent.enabled.is_(True)))).scalar(),
        "providers": sum(by_tier.values()),
        "providers_by_tier": by_tier,
        "usage": series,
    }
