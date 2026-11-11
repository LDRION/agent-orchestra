"""Все вызовы внешних систем — только здесь.

Правила:
  • чтение: право tools.read у человека (инициатора задачи); данные системы с грифом выше грифа
    задачи не отдаются — иначе данные 1С ушли бы в облачную модель;
  • запись: агент только ПРЕДЛАГАЕТ (IntegrationAction). Выполняет человек кнопкой (tools.write = allow)
    или по визе (tools.write = approval). Перед выполнением — политика выхода данных: гриф задачи
    должен быть допустим для уровня доверия системы;
  • каждое чтение, предложение, выполнение и отказ — в аудите (аргументы — без секретов).
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.approvals import service as approvals
from orchestra.audit import service as audit
from orchestra.core.crypto import get_vault
from orchestra.db.base import utcnow
from orchestra.db.session import sessionmaker
from orchestra.iam.authz import AccessContext, load_access
from orchestra.iam.models import User
from orchestra.integrations.base import Action, Connector, IntegrationError, require_args
from orchestra.integrations.models import Integration, IntegrationAction
from orchestra.integrations.registry import CONNECTORS
from orchestra.providers import egress
from orchestra.system import settings_store
from orchestra.tools.base import Tool, ToolContext, ToolSpec
from orchestra.tools.registry import TOOLS, register

log = logging.getLogger("orchestra.integrations")
TIMEOUT = 40
OriginHook = Callable[[AsyncSession, IntegrationAction], Awaitable[None]]
ORIGIN_HOOKS: dict[str, OriginHook] = {}
_cache: dict[str, tuple[str, Connector]] = {}
_dynamic: set[str] = set()


def secret_purpose(integration_id: str) -> str:
    return f"integration:{integration_id}"


def on_done(origin_type: str):
    """Модуль-источник узнаёт об исходе своего действия (например, задача протокола → «в Битрикс24»)."""
    def deco(fn: OriginHook) -> OriginHook:
        ORIGIN_HOOKS[origin_type] = fn
        return fn
    return deco


def build(row: Integration) -> Connector:
    stamp = row.updated_at.isoformat() if row.updated_at else ""
    hit = _cache.get(row.id)
    if hit and hit[0] == stamp:
        return hit[1]
    cls = CONNECTORS.get(row.kind)
    if cls is None:
        raise IntegrationError(f"коннектор «{row.kind}» не установлен")
    secret = get_vault().decrypt(row.secret_enc, secret_purpose(row.id)) if row.secret_enc else None
    conn = cls(row.id, dict(row.settings or {}), secret, row.mode)
    _cache[row.id] = (stamp, conn)
    return conn


def forget(integration_id: str) -> None:
    _cache.pop(integration_id, None)


def summary(action: Action, args: dict) -> str:
    what = next((str(args[k]) for k in ("title", "subject", "event", "text", "entity") if args.get(k)), "")
    return f"{action.title}: {what}"[:300] if what else action.title


def check_rules(access: AccessContext, row: Integration, action: Action, classification: str, policy: dict,
                preapproved: tuple = ()) -> None:
    if not row.enabled:
        raise IntegrationError(f"«{row.name}» отключена")
    perm = "tools.write" if action.write else "tools.read"
    d = access.decide(perm)
    if not (d.allowed or (d.needs_approval and perm in preapproved)):
        raise IntegrationError(f"«{row.name}»: {d.reason}")
    if action.write:
        ok, why = egress.check(row.trust_tier, classification, policy)
        if not ok:
            raise IntegrationError(f"«{row.name}»: {why}")
    elif egress.rank(row.data_classification) > egress.rank(classification):
        raise IntegrationError(f"данные «{row.name}» имеют гриф «{egress.CLASS_TITLES[row.data_classification]}», "
                               f"а задача — «{egress.CLASS_TITLES.get(classification, classification)}»: "
                               "поставьте задачу с нужным грифом (она пойдёт на локальной модели)")


async def execute(db: AsyncSession, access: AccessContext, row: Integration, action_key: str, args: dict, *,
                  classification: str, run_id: str | None = None, preapproved: tuple = (), ip: str = "") -> dict:
    conn = build(row)
    action = conn.action(action_key)
    require_args(action, args)
    check_rules(access, row, action, classification, await settings_store.get(db, "egress_policy"), preapproved)
    target = {"target_type": "integration", "target_id": row.id, "target_name": f"{row.name} · {action.title}"}
    try:
        result = await asyncio.wait_for(conn.run(action_key, args), timeout=TIMEOUT)
    except (IntegrationError, TimeoutError) as exc:
        msg = str(exc) or f"не ответила за {TIMEOUT} с"
        await audit.record("integration.call", actor=access.user, ip=ip, outcome="error", **target,
                           data={"action": action_key, "mode": row.mode, "run_id": run_id, "error": msg[:300]})
        raise IntegrationError(msg) from exc
    await audit.record("integration.call", actor=access.user, ip=ip, **target,
                       data={"action": action_key, "mode": row.mode, "run_id": run_id, "write": action.write,
                             "args": {k: (str(v)[:200]) for k, v in args.items()}})
    return result


async def propose(db: AsyncSession, access: AccessContext, row: Integration, action_key: str, args: dict, *,
                  classification: str, run_id: str | None = None, step_idx: int | None = None,
                  origin_type: str = "agent", origin_id: str = "") -> IntegrationAction:
    conn = build(row)
    action = conn.action(action_key)
    if not action.write:
        raise IntegrationError("предлагаются только записи; чтение выполняется сразу")
    require_args(action, args)
    d = access.decide("tools.write")
    if d.effect == "deny":
        raise IntegrationError(f"изменения во внешних системах: {d.reason}")
    act = IntegrationAction(integration_id=row.id, integration_name=row.name, action=action_key,
                            title=summary(action, args), args=args, classification=classification,
                            requested_by=access.user.id, run_id=run_id, step_idx=step_idx,
                            origin_type=origin_type, origin_id=origin_id)
    db.add(act)
    await db.flush()
    audit.defer(db, "integration.proposed", actor=access.user, target_type="integration_action", target_id=act.id,
                target_name=f"{row.name} · {act.title}", data={"run_id": run_id, "origin": origin_type})
    return act


async def claim(db: AsyncSession, act: IntegrationAction, frm: tuple[str, ...], to: str) -> None:
    """Атомарный переход статуса: два нажатия «Выполнить» или две визы подряд не выполнят запись дважды."""
    res = await db.execute(update(IntegrationAction).where(IntegrationAction.id == act.id, IntegrationAction.status.in_(frm))
                           .values(status=to).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise IntegrationError("действие уже выполняется, выполнено или ждёт визы")
    act.status = to


async def recover() -> int:
    """После перезапуска: прерванные выполнения — в «ошибку» (повтор — только осознанно, человеком)."""
    async with sessionmaker()() as db:
        res = await db.execute(update(IntegrationAction).where(IntegrationAction.status == "executing").values(
            status="failed", error="прервано перезапуском сервера — проверьте во внешней системе, прежде чем повторять"))
        await db.commit()
        return res.rowcount


async def perform(db: AsyncSession, access: AccessContext, act: IntegrationAction, *, preapproved: tuple = (),
                  ip: str = "") -> IntegrationAction:
    row = await db.get(Integration, act.integration_id) if act.integration_id else None
    if row is None:
        act.status, act.error = "failed", "интеграция удалена"
    else:
        try:
            act.result = await execute(db, access, row, act.action, act.args, classification=act.classification,
                                       run_id=act.run_id, preapproved=preapproved, ip=ip)
            act.status, act.error = "executed", ""
        except IntegrationError as exc:
            act.status, act.error = "failed", str(exc)
    act.executed_by, act.executed_at = access.user.id, utcnow()
    hook = ORIGIN_HOOKS.get(act.origin_type)
    if hook:
        await hook(db, act)
    return act


async def submit(db: AsyncSession, access: AccessContext, act: IntegrationAction, ip: str = "") -> IntegrationAction:
    """Человек нажал «Выполнить»: сразу (allow) или на визу (approval)."""
    if act.status not in ("proposed", "failed"):
        raise IntegrationError("действие уже выполнено или ждёт визы")
    d = access.decide("tools.write")
    if d.allowed:
        await claim(db, act, ("proposed", "failed"), "executing")
        await db.commit()                                # захват — до сетевого вызова
        return await perform(db, access, act, ip=ip)
    if d.needs_approval:
        await claim(db, act, ("proposed", "failed"), "pending_approval")
        # визирует руководитель подразделения задачи (или самого просящего), а не любой — где угодно
        unit = None
        if act.run_id:
            from orchestra.orchestration.models import Run
            run = await db.get(Run, act.run_id)
            unit = run.unit_id if run else None
        unit = unit or (access.memberships[0][0] if access.memberships else None)
        a = await approvals.request(
            db, kind="integration_action", title=f"{act.integration_name}: {act.title}", requester=access.user,
            unit_id=unit, permissions=["tools.write"], reasons=[f"запись во внешнюю систему «{act.integration_name}»"],
            approver_role_ids=d.approver_role_ids, summary=act.title,
            payload={"integration": act.integration_name, "action": act.action, "args": act.args},
            target_type="integration_action", target_id=act.id)
        act.approval_id = a.id
        hook = ORIGIN_HOOKS.get(act.origin_type)
        if hook:
            await hook(db, act)
        return act
    raise IntegrationError(f"изменения во внешних системах: {d.reason}")


@approvals.on("integration_action")
async def _on_visa(db: AsyncSession, a, approver: User, approved: bool):
    act = await db.get(IntegrationAction, a.target_id)
    if act is None or act.status != "pending_approval":
        return None
    if not approved:
        act.status, act.error = "rejected", f"виза отклонена: {a.decision_note or 'без комментария'}"
        hook = ORIGIN_HOOKS.get(act.origin_type)
        if hook:
            await hook(db, act)
        return None

    async def after():                                   # сеть — после фиксации визы, не внутри транзакции
        async with sessionmaker()() as s:
            fresh = await s.get(IntegrationAction, act.id)
            try:
                await claim(s, fresh, ("pending_approval",), "executing")
            except IntegrationError:
                return
            await s.commit()
            requester = await s.get(User, fresh.requested_by)
            await perform(s, await load_access(s, requester), fresh, preapproved=("tools.write",))
            await s.commit()
    return after


# ── инструменты агентов из подключённых систем ─────────────────────────────────
class IntegrationTool(Tool):
    def __init__(self, row: Integration, action: Action):
        self.spec = ToolSpec(f"{row.key}.{action.key}", f"{row.name}: {action.title}",
                             action.description + (" Запись не выполняется сразу: человек подтвердит." if action.write else ""),
                             "tools.write" if action.write else "tools.read", dict(action.args))
        self.integration_id, self.action_key, self.deferred = row.id, action.key, action.write

    def validate(self, args: dict) -> dict:
        return args                                       # аргументы проверяет коннектор

    async def run(self, args: dict, ctx: ToolContext) -> str:
        from orchestra.tools.base import ToolError
        async with sessionmaker()() as db:
            row = await db.get(Integration, self.integration_id)
            if row is None:
                raise ToolError("интеграция удалена")
            try:
                if self.deferred:
                    act = await propose(db, ctx.access, row, self.action_key, args, classification=ctx.classification,
                                        run_id=ctx.run_id)
                    await db.commit()
                    return (f"Предложено действие «{act.title}» (№{act.id[-6:]}). Оно НЕ выполнено: его подтвердит "
                            "человек после задачи. Не пиши, что действие сделано.")
                result = await execute(db, ctx.access, row, self.action_key, args, classification=ctx.classification,
                                       run_id=ctx.run_id)
            except IntegrationError as exc:
                raise ToolError(str(exc)) from exc
        return Connector.to_text(result)


async def sync_tools(db: AsyncSession) -> int:
    """Пересобрать инструменты из включённых интеграций (при старте и после любой правки)."""
    for key in list(_dynamic):
        TOOLS.pop(key, None)
    _dynamic.clear()
    rows = (await db.execute(select(Integration).where(Integration.enabled.is_(True)))).scalars()
    for row in rows:
        cls = CONNECTORS.get(row.kind)
        for action in cls.actions if cls else []:
            tool = IntegrationTool(row, action)
            register(tool)
            _dynamic.add(tool.spec.key)
    return len(_dynamic)
