"""Визы. Правила:
  • решать может тот, у кого approvals.decide в подразделении запроса;
  • если у права указаны визирующие роли — только носитель одной из них;
  • свой запрос одобрить нельзя никому, включая владельца (принцип четырёх глаз);
  • после одобрения вызывается обработчик своего типа (запуск задачи, публикация промта…).
"""
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.approvals.models import Approval
from orchestra.audit import service as audit
from orchestra.db.base import utcnow
from orchestra.iam.authz import AccessContext
from orchestra.iam.models import User

# Обработчик меняет данные в той же транзакции и может вернуть действие «после фиксации»
# (запустить задачу, разослать событие) — оно выполнится, только когда виза записана.
Handler = Callable[[AsyncSession, Approval, User, bool], Awaitable[Callable[[], Awaitable[None]] | None]]
HANDLERS: dict[str, Handler] = {}
# Дополнительное условие «кто вправе решать» для своего типа запроса (например, выдача роли)
CHECKS: dict[str, Callable[[AccessContext, Approval], str | None]] = {}
TTL = timedelta(days=7)


class ApprovalError(Exception):
    pass


def on(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


def can_decide(kind: str):
    def deco(fn):
        CHECKS[kind] = fn
        return fn
    return deco


async def request(db: AsyncSession, *, kind: str, title: str, requester: User, unit_id: str | None,
                  permissions: list[str], reasons: list[str], approver_role_ids: list[str],
                  summary: str = "", payload: dict | None = None, target_type: str = "",
                  target_id: str = "") -> Approval:
    a = Approval(kind=kind, title=title[:300], summary=summary, permissions=permissions, reasons=reasons,
                 payload=payload or {}, target_type=target_type, target_id=target_id,
                 requester_id=requester.id, unit_id=unit_id, approver_role_ids=approver_role_ids,
                 expires_at=utcnow() + TTL)
    db.add(a)
    await db.flush()
    audit.defer(db, "approval.requested", actor=requester, target_type="approval", target_id=a.id,
                target_name=a.title, data={"kind": kind, "permissions": permissions, "reasons": reasons})
    return a


def why_cannot_decide(access: AccessContext, a: Approval) -> str | None:
    if a.status != "pending":
        return "запрос уже рассмотрен"
    if a.expires_at and _aware(a.expires_at) < utcnow():
        return "срок запроса истёк — пусть автор отправит его заново"
    if a.requester_id == access.user.id:
        return "свой запрос одобрить нельзя"
    d = access.decide("approvals.decide", a.unit_id)
    if not d.allowed:
        return d.reason
    if a.approver_role_ids and not access.has_role_in_scope(a.approver_role_ids, a.unit_id):
        return "визирует другая роль"
    check = CHECKS.get(a.kind)
    return check(access, a) if check else None


async def inbox(db: AsyncSession, access: AccessContext) -> list[Approval]:
    rows = (await db.execute(select(Approval).where(Approval.status == "pending")
                             .order_by(Approval.created_at.desc()).limit(200))).scalars().all()
    now = utcnow()
    out = []
    for a in rows:
        if a.expires_at and _aware(a.expires_at) < now:
            a.status = "expired"
            continue
        if why_cannot_decide(access, a) is None:
            out.append(a)
    await db.commit()
    return out


def _aware(dt):
    from datetime import UTC
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def decide(db: AsyncSession, access: AccessContext, approval_id: str, approve: bool, note: str = "",
                 ip: str = "") -> Approval:
    a = await db.get(Approval, approval_id, with_for_update=True)
    if a is None:
        raise ApprovalError("запрос не найден")
    why = why_cannot_decide(access, a)
    if why:
        await audit.record("approval.decide", actor=access.user, ip=ip, target_type="approval", target_id=a.id,
                           target_name=a.title, outcome="denied", data={"reason": why})
        raise ApprovalError(why)
    # атомарно: два визирующих одновременно — решение одно, обработчик вызывается один раз
    values = {"status": "approved" if approve else "rejected", "decided_by": access.user.id, "decided_at": utcnow(),
              "decision_note": note[:2000]}
    res = await db.execute(update(Approval).where(Approval.id == a.id, Approval.status == "pending").values(**values)
                           .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise ApprovalError("запрос уже рассмотрен")
    for k, v in values.items():
        setattr(a, k, v)
    await db.flush()
    handler = HANDLERS.get(a.kind)
    after = await handler(db, a, access.user, approve) if handler else None
    await db.commit()
    await audit.record("approval.approved" if approve else "approval.rejected", actor=access.user, ip=ip,
                       target_type="approval", target_id=a.id, target_name=a.title, data={"note": note[:500]})
    if after is not None:
        await after()
    return a
