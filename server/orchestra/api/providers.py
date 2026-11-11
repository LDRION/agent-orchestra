from datetime import timedelta
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.core import netguard
from orchestra.core.crypto import get_vault, mask
from orchestra.core.redact import safe_error
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, current, require, sudo
from orchestra.iam.models import User
from orchestra.system import settings_store
from orchestra.providers import egress
from orchestra.providers import service as models
from orchestra.providers.base import ChatRequest, Message, ProviderError
from orchestra.providers.models import LlmCall, Provider
from orchestra.providers.registry import KINDS, PRESETS, kinds_meta

router = APIRouter(prefix="/api/providers", tags=["providers"])

Tier = Literal["local", "private", "cloud_ru", "cloud_foreign"]
# Настройки провайдера — только известные ключи и простые типы (без произвольных структур)
SETTING_KEYS = {"effort", "refusal_fallback", "token_param", "auth_scheme", "temperature", "reasoning_effort",
                "no_key", "json_mode", "think", "num_ctx", "keep_alive", "wait_ms", "headers", "daily_tokens"}


class ProviderIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    base_url: str = Field(default="", max_length=300)
    trust_tier: Tier
    default_model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)
    redact_pii: bool = True
    enabled: bool = True
    settings: dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


class ProviderPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=300)
    trust_tier: Tier | None = None
    default_model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)
    clear_key: bool = False
    redact_pii: bool | None = None
    enabled: bool | None = None
    settings: dict | None = None
    notes: str | None = Field(default=None, max_length=2000)


async def check_url(url: str, tier: str) -> str:
    if not url:
        return url
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise HTTPException(422, "Адрес должен быть http(s)://хост без логина и пароля")
    if tier in egress.EXTERNAL and u.scheme != "https":
        raise HTTPException(422, "Облачные модели — только по https")
    try:
        await netguard.check_company_target(u.hostname)
    except netguard.NetError as exc:
        raise HTTPException(422, f"Адрес модели: {exc}") from exc
    return url.rstrip("/")


def clean_settings(s: dict) -> dict:
    bad = set(s) - SETTING_KEYS
    if bad:
        raise HTTPException(422, f"Неизвестные настройки: {', '.join(sorted(bad))}")
    if "headers" in s and not (isinstance(s["headers"], dict) and all(
            isinstance(k, str) and isinstance(v, str) and len(v) < 500 for k, v in s["headers"].items())):
        raise HTTPException(422, "headers — словарь строк")
    if "daily_tokens" in s and not (isinstance(s["daily_tokens"], int) and 0 <= s["daily_tokens"] <= 10**10):
        raise HTTPException(422, "daily_tokens — целое число токенов в сутки (0 — без лимита)")
    return s


def out(p: Provider) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url, "trust_tier": p.trust_tier,
            "default_model": p.default_model, "has_key": bool(p.secret_enc), "key_hint": p.secret_hint,
            "redact_pii": p.redact_pii, "enabled": p.enabled, "settings": p.settings, "notes": p.notes,
            "updated_at": iso(p.updated_at)}


@router.get("")
async def list_providers(p: Principal = Depends(require("providers.view")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Provider).order_by(Provider.name))).scalars()
    return {"providers": [out(x) for x in rows], "kinds": kinds_meta(), "presets": PRESETS,
            "tiers": [{"id": t, "title": egress.TIER_TITLES[t]} for t in egress.TIERS],
            "classifications": [{"id": c, "title": egress.CLASS_TITLES[c]} for c in egress.CLASSIFICATIONS]}


@router.post("")
async def create(body: ProviderIn, p: Principal = Depends(sudo("providers.manage")), db: AsyncSession = Depends(get_db)):
    if body.kind not in KINDS:
        raise HTTPException(422, "Неизвестный тип провайдера")
    prov = Provider(name=body.name, kind=body.kind, base_url=await check_url(body.base_url, body.trust_tier),
                    trust_tier=body.trust_tier, default_model=body.default_model, redact_pii=body.redact_pii,
                    enabled=body.enabled, settings=clean_settings(body.settings), notes=body.notes,
                    created_by=p.user.id)
    db.add(prov)
    await db.flush()
    if body.api_key:
        prov.secret_enc = get_vault().encrypt(body.api_key, models.secret_purpose(prov.id))
        prov.secret_hint = mask(body.api_key)
    await db.commit()
    await audit.record("providers.create", actor=p.user, ip=p.ip, target_type="provider", target_id=prov.id,
                       target_name=prov.name, data={"kind": prov.kind, "tier": prov.trust_tier, "key": bool(body.api_key)})
    return out(prov)


@router.patch("/{provider_id}")
async def patch(provider_id: str, body: ProviderPatch, p: Principal = Depends(sudo("providers.manage")),
                db: AsyncSession = Depends(get_db)):
    prov = await db.get(Provider, provider_id)
    if prov is None:
        raise HTTPException(404, "Модель не найдена")
    data = body.model_dump(exclude_unset=True)
    key = data.pop("api_key", None)
    clear = data.pop("clear_key", False)
    tier = data.get("trust_tier", prov.trust_tier)
    if "base_url" in data or "trust_tier" in data:
        data["base_url"] = await check_url(data.get("base_url", prov.base_url), tier)
    if "settings" in data:
        data["settings"] = clean_settings(data["settings"] or {})
    for k, v in data.items():
        setattr(prov, k, v)
    if clear:
        prov.secret_enc, prov.secret_hint = None, ""
    elif key:
        prov.secret_enc = get_vault().encrypt(key, models.secret_purpose(prov.id))
        prov.secret_hint = mask(key)
    prov.updated_at = utcnow()
    await db.commit()
    models.forget(prov.id)
    await audit.record("providers.update", actor=p.user, ip=p.ip, target_type="provider", target_id=prov.id,
                       target_name=prov.name, data={**data, "key_changed": bool(key) or clear})
    return out(prov)


@router.delete("/{provider_id}")
async def remove(provider_id: str, p: Principal = Depends(sudo("providers.manage")), db: AsyncSession = Depends(get_db)):
    prov = await db.get(Provider, provider_id)
    if prov is None:
        raise HTTPException(404, "Модель не найдена")
    await db.delete(prov)
    await db.commit()
    models.forget(provider_id)
    await audit.record("providers.delete", actor=p.user, ip=p.ip, target_type="provider", target_id=provider_id,
                       target_name=prov.name)
    return {"ok": True}


@router.post("/{provider_id}/test")
async def test(provider_id: str, p: Principal = Depends(require("providers.manage")), db: AsyncSession = Depends(get_db)):
    """Короткий живой запрос без данных компании: проверяет адрес, ключ и модель."""
    prov = await db.get(Provider, provider_id)
    if prov is None:
        raise HTTPException(404, "Модель не найдена")
    try:
        inst = models.build(prov)
        resp = await inst.chat(ChatRequest(system="Отвечай одним словом.", messages=[Message("user", "Скажи: готово")],
                                           model=prov.default_model, max_tokens=512, timeout=90))
    except ProviderError as exc:
        return {"ok": False, "kind": exc.kind, "error": exc.message}
    except Exception as exc:
        return {"ok": False, "kind": "config", "error": safe_error(exc)}
    return {"ok": True, "text": resp.text[:200], "model": resp.model, "latency_ms": resp.latency_ms}


@router.get("/{provider_id}/models")
async def provider_models(provider_id: str, p: Principal = Depends(require("providers.view")),
                          db: AsyncSession = Depends(get_db)):
    if not (p.access.can("providers.manage") or p.access.can("agents.edit")):
        raise HTTPException(403, "Список моделей нужен тем, кто настраивает модели или агентов")
    prov = await db.get(Provider, provider_id)
    if prov is None:
        raise HTTPException(404, "Модель не найдена")
    try:
        items = await models.build(prov).list_models()
    except ProviderError as exc:
        return {"ok": False, "error": exc.message, "models": []}
    except Exception as exc:
        return {"ok": False, "error": safe_error(exc), "models": []}
    return {"ok": True, "models": [{"id": m.id, "title": m.title, "size": m.size} for m in items]}


@router.get("/usage/today")
async def usage_today(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Расход за текущие сутки (по Москве) против лимитов. Свой — видит каждый, общий — с system.usage."""
    personal = int(await settings_store.get(db, "user_daily_tokens") or 0)
    out = {"me": {"tokens": await models.tokens_today(db, user_id=p.user.id), "limit": personal}}
    if p.access.can("system.usage"):
        since = models.day_start()
        provs = (await db.execute(select(Provider).order_by(Provider.name))).scalars().all()
        out["providers"] = [{"id": x.id, "name": x.name, "tokens": await models.tokens_today(db, provider_id=x.id),
                             "limit": int((x.settings or {}).get("daily_tokens") or 0)} for x in provs]
        rows = (await db.execute(select(User.display_name, func.sum(LlmCall.input_tokens + LlmCall.output_tokens))
                                 .join(User, User.id == LlmCall.user_id).where(LlmCall.ts >= since)
                                 .group_by(User.display_name).order_by(func.sum(LlmCall.input_tokens + LlmCall.output_tokens).desc())
                                 .limit(15))).all()
        out["users"] = [{"name": n, "tokens": int(t or 0)} for n, t in rows]
    return out


@router.get("/usage/summary")
async def usage(days: int = 30, p: Principal = Depends(require("system.usage")), db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = (await db.execute(select(LlmCall.provider_name, LlmCall.model, func.count(LlmCall.id),
                                    func.sum(LlmCall.input_tokens), func.sum(LlmCall.output_tokens),
                                    func.avg(LlmCall.latency_ms), func.sum(case((LlmCall.ok.is_(False), 1), else_=0)))
                             .where(LlmCall.ts >= since).group_by(LlmCall.provider_name, LlmCall.model))).all()
    return [{"provider": r[0], "model": r[1], "calls": r[2], "tokens_in": r[3] or 0, "tokens_out": r[4] or 0,
             "avg_ms": int(r[5] or 0), "errors": int(r[6] or 0)} for r in rows]
