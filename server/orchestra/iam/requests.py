"""Запрос доступа: человек просит роль в подразделении на срок, визирует тот, кто сам вправе её выдать.

Удобно изнутри (не нужно искать администратора) и безопасно: виза — только от носителя всех прав роли
в этом подразделении и права назначать роли; свой запрос не одобрить; роль временная (до 90 дней).
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.approvals import service as approvals
from orchestra.approvals.models import Approval
from orchestra.audit import service as audit
from orchestra.db.base import utcnow
from orchestra.iam.authz import AccessContext, load_access
from orchestra.iam.models import Membership, OrgUnit, Role, RoleGrant, User
from orchestra.iam.permissions import DENY

KIND = "access_request"
MAX_DAYS = 90
MAX_PENDING = 5


class RequestError(Exception):
    pass


async def role_perms(db: AsyncSession, role_id: str) -> list[str]:
    return [g.permission for g in (await db.execute(select(RoleGrant).where(
        RoleGrant.role_id == role_id, RoleGrant.effect != DENY))).scalars()]


async def create(db: AsyncSession, user: User, unit_id: str, role_id: str, days: int, reason: str) -> Approval:
    unit, role = await db.get(OrgUnit, unit_id), await db.get(Role, role_id)
    if unit is None or role is None:
        raise RequestError("подразделение или роль не найдены")
    if not 1 <= days <= MAX_DAYS:
        raise RequestError(f"срок — от 1 до {MAX_DAYS} дней")
    has = (await db.execute(select(Membership).where(
        Membership.user_id == user.id, Membership.unit_id == unit_id, Membership.role_id == role_id,
        Membership.expires_at.is_(None)))).scalar()
    if has is not None:
        raise RequestError("эта роль у вас уже есть")
    pending = (await db.execute(select(Approval).where(
        Approval.kind == KIND, Approval.requester_id == user.id, Approval.status == "pending"))).scalars().all()
    if len(pending) >= MAX_PENDING:
        raise RequestError("слишком много запросов ждут решения")
    if any(a.payload.get("role_id") == role_id and a.unit_id == unit_id for a in pending):
        raise RequestError("такой запрос уже ждёт решения")
    return await approvals.request(
        db, kind=KIND, title=f"Доступ: «{role.name}» в «{unit.name}» на {days} дн.", requester=user,
        unit_id=unit_id, permissions=[], reasons=[reason], approver_role_ids=[],
        summary=f"{user.display_name} просит роль «{role.name}» в «{unit.name}» на {days} дн.\nЗачем: {reason}",
        payload={"role_id": role_id, "role": role.name, "unit": unit.name, "days": days,
                 "perms": await role_perms(db, role_id)},
        target_type="user", target_id=user.id)


@approvals.can_decide(KIND)
def _who_decides(access: AccessContext, a: Approval) -> str | None:
    if not access.can("iam.users", a.unit_id):
        return "выдавать роли может только тот, у кого есть право назначать роли"
    if any(not access.can(k, a.unit_id) for k in a.payload.get("perms") or []):
        return "у визирующего нет всех прав этой роли в подразделении"
    return None


@approvals.on(KIND)
async def _on_visa(db: AsyncSession, a: Approval, approver: User, approved: bool):
    if not approved:
        return None
    access = await load_access(db, approver)
    # роль могли расширить, пока запрос ждал: проверяем по текущему составу прав
    if any(not access.can(k, a.unit_id) for k in await role_perms(db, a.payload["role_id"])):
        raise approvals.ApprovalError("роль расширили после запроса — у визирующего нет всех её прав")
    until = utcnow() + timedelta(days=int(a.payload.get("days") or 1))
    m = (await db.execute(select(Membership).where(
        Membership.user_id == a.requester_id, Membership.unit_id == a.unit_id,
        Membership.role_id == a.payload["role_id"]))).scalar()
    if m is None:
        db.add(Membership(user_id=a.requester_id, unit_id=a.unit_id, role_id=a.payload["role_id"], expires_at=until))
    elif m.expires_at is not None:
        m.expires_at = until
    target = await db.get(User, a.requester_id)
    audit.defer(db, "iam.user.roles", actor=approver, target_type="user", target_id=a.requester_id,
                target_name=target.login if target else "",
                data={"добавлено": [f"{a.payload.get('role')} · {a.payload.get('unit')} до {until.date().isoformat()}"],
                      "по запросу": a.id})
    return None
