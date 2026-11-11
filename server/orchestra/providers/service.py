"""ModelRouter — единственная дверь к моделям. Любой вызов модели в платформе идёт через него.

Для каждой модели из цепочки (агента, оркестратора или по умолчанию) по порядку:
  1. провайдер включён;
  2. гриф данных допустим для уровня доверия провайдера (egress-политика);
  3. для внешнего облака у человека, поставившего задачу, есть право providers.external
     (или задача одобрена визирующим);
  4. ПДн маскируются, если у провайдера включено и он внешний;
  5. вызов с одним повтором на временных сбоях; не вышло — следующая модель цепочки.
Каждая попытка пишется в llm_calls. Всё отклонённое объясняется человеческим языком.
"""
import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.core.crypto import get_vault
from orchestra.core.redact import redact_pii
from orchestra.db.session import sessionmaker
from orchestra.iam.authz import AccessContext
from orchestra.providers import egress
from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, Message, ProviderError
from orchestra.providers.models import LlmCall, Provider
from orchestra.providers.registry import KINDS
from orchestra.system import settings_store

log = logging.getLogger("orchestra.models")


class RoutingError(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons) or "нет доступных моделей")


@dataclass
class CallContext:
    access: AccessContext
    classification: str = "internal"
    purpose: str = "step"                 # plan | step | summary | test
    unit_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    external_preapproved: bool = False


_cache: dict[str, tuple[str, ChatProvider]] = {}


def secret_purpose(provider_id: str) -> str:
    return f"provider:{provider_id}"


def build(p: Provider) -> ChatProvider:
    stamp = f"{p.updated_at.isoformat() if p.updated_at else ''}"
    hit = _cache.get(p.id)
    if hit and hit[0] == stamp:
        return hit[1]
    cls = KINDS.get(p.kind)
    if cls is None:
        raise ProviderError("config", f"тип провайдера «{p.kind}» не установлен")
    key = get_vault().decrypt(p.secret_enc, secret_purpose(p.id)) if p.secret_enc else None
    inst = cls(p.base_url, key, dict(p.settings or {}))
    if hit:
        asyncio.get_running_loop().create_task(hit[1].aclose())
    _cache[p.id] = (stamp, inst)
    return inst


def forget(provider_id: str) -> None:
    _cache.pop(provider_id, None)


async def default_chain(db: AsyncSession, key: str = "default_chain") -> list[dict]:
    chain = await settings_store.get(db, key) or []
    if not chain and key != "default_chain":
        chain = await settings_store.get(db, "default_chain") or []
    return list(chain)


def _redacted(req: ChatRequest) -> tuple[ChatRequest, int]:
    system, n = redact_pii(req.system)
    msgs = []
    for m in req.messages:
        text, k = redact_pii(m.content)
        n += k
        msgs.append(Message(role=m.role, content=text, images=m.images))
    return replace(req, system=system, messages=msgs), n


async def _log_call(ctx: CallContext, p: Provider, model: str, resp: ChatResponse | None,
                    err: ProviderError | None, redactions: int) -> None:
    try:
        async with sessionmaker()() as s:
            s.add(LlmCall(user_id=ctx.access.user.id, run_id=ctx.run_id, step_id=ctx.step_id,
                          agent_id=ctx.agent_id, provider_id=p.id, provider_name=p.name, model=model,
                          purpose=ctx.purpose, ok=err is None, error_kind=err.kind if err else "",
                          input_tokens=resp.usage.input_tokens if resp else 0,
                          output_tokens=resp.usage.output_tokens if resp else 0,
                          latency_ms=resp.latency_ms if resp else 0, redactions=redactions))
            await s.commit()
    except Exception:
        log.exception("не удалось записать вызов модели")


MSK = timezone(timedelta(hours=3))


def day_start() -> datetime:
    """Начало текущих суток по Москве — граница дневных лимитов."""
    now = datetime.now(MSK)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def tokens_today(db: AsyncSession, *, user_id: str | None = None, provider_id: str | None = None) -> int:
    q = select(func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0)).where(LlmCall.ts >= day_start())
    if user_id:
        q = q.where(LlmCall.user_id == user_id)
    if provider_id:
        q = q.where(LlmCall.provider_id == provider_id)
    return int((await db.execute(q)).scalar() or 0)


async def check_route(db: AsyncSession, ctx: CallContext, p: Provider | None, policy: dict) -> str | None:
    """Причина, по которой этот провайдер нельзя использовать, или None."""
    if p is None:
        return "модель удалена"
    if not p.enabled:
        return f"{p.name}: отключён"
    limit = int((p.settings or {}).get("daily_tokens") or 0)
    if limit and await tokens_today(db, provider_id=p.id) >= limit:
        return f"{p.name}: дневной лимит {limit:,} токенов исчерпан".replace(",", " ")
    ok, why = egress.check(p.trust_tier, ctx.classification, policy)
    if not ok:
        return f"{p.name}: {why}"
    if p.trust_tier in egress.EXTERNAL:
        d = ctx.access.decide("providers.external", ctx.unit_id)
        if not (d.allowed or (d.needs_approval and ctx.external_preapproved)):
            return f"{p.name}: внешнее облако — {d.reason}"
    return None


async def complete(db: AsyncSession, ctx: CallContext, chain: list[dict], req: ChatRequest) -> tuple[ChatResponse, Provider]:
    policy = await settings_store.get(db, "egress_policy")
    personal = int(await settings_store.get(db, "user_daily_tokens") or 0)
    if personal and await tokens_today(db, user_id=ctx.access.user.id) >= personal:
        raise RoutingError([f"ваш дневной лимит {personal:,} токенов исчерпан — продолжите завтра или попросите "
                            "администратора поднять лимит".replace(",", " ")])
    reasons: list[str] = []
    for ref in chain:
        p = await db.get(Provider, ref.get("provider_id", ""))
        why = await check_route(db, ctx, p, policy)
        if why:
            reasons.append(why)
            continue
        try:
            provider = build(p)
        except Exception as exc:                          # нет ключа, ключ не расшифровался, нет типа
            reasons.append(f"{p.name}: {getattr(exc, 'message', None) or exc}")
            continue
        r = replace(req, model=ref.get("model") or p.default_model or req.model)
        redactions = 0
        if p.redact_pii and p.trust_tier in egress.EXTERNAL:
            r, redactions = _redacted(r)
        for attempt in (1, 2):
            try:
                resp = await provider.chat(r)
            except ProviderError as exc:
                await _log_call(ctx, p, r.model, None, exc, redactions)
                if exc.retryable and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                reasons.append(f"{p.name} · {r.model}: {exc.message}")
                break
            await _log_call(ctx, p, r.model, resp, None, redactions)
            return resp, p
    raise RoutingError(reasons or ["не настроена ни одна модель — подключите модель в разделе «Модели»"])
