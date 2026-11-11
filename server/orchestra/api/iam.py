"""Люди, оргструктура, роли и матрица прав.

Защита от эскалации: тот, кто не владелец, может выдать роли право (allow/approval) или назначить
человеку роль, только если сам обладает всеми этими правами (allow). Иначе администратор доступов
выдал бы себе всё через свою же роль.
"""
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.core.passwords import hash_password, password_problem
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam import guard, requests
from orchestra.iam import service as auth
from orchestra.iam.authz import load_access
from orchestra.iam.deps import Principal, current, deny, fresh, require, sudo
from orchestra.iam.models import Membership, OrgUnit, Role, RoleGrant, User
from orchestra.iam.permissions import BY_KEY, CATALOG, DENY, EFFECTS, GROUPS
from orchestra.security import alerts
from orchestra.settings import get_settings

MAX_TEMP = timedelta(days=366)
router = APIRouter(prefix="/api/iam", tags=["iam"])


# ── каталог и проверка ─────────────────────────────────────────────────────────
@router.get("/catalog")
async def catalog(p: Principal = Depends(current)):
    return {"groups": GROUPS, "effects": list(EFFECTS), "permissions": [
        {"key": x.key, "group": x.group, "title": x.title, "hint": x.hint, "level": x.level,
         "approvable": x.approvable, "risky": x.risky} for x in CATALOG]}


@router.get("/check")
async def check(user_id: str, permission: str, unit_id: str | None = None,
                p: Principal = Depends(require("iam.view")), db: AsyncSession = Depends(get_db)):
    """«Может ли Иванов публиковать промты в BIM-отделе?» — с объяснением почему."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Человек не найден")
    return (await load_access(db, user)).decide(permission, unit_id).as_dict()


@router.get("/users/{user_id}/effective")
async def effective(user_id: str, unit_id: str | None = None, p: Principal = Depends(require("iam.view")),
                    db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Человек не найден")
    ctx = await load_access(db, user)
    return {x.key: ctx.decide(x.key, unit_id).as_dict() for x in CATALOG}


# ── оргструктура ───────────────────────────────────────────────────────────────
class UnitIn(Strict):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["company", "department", "group", "project"] = "department"
    parent_id: str | None = None
    description: str = Field(default="", max_length=2000)
    sort: int = 0


class UnitPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["company", "department", "group", "project"] | None = None
    parent_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    sort: int | None = None


def unit_out(u: OrgUnit, members: int = 0) -> dict:
    return {"id": u.id, "name": u.name, "kind": u.kind, "parent_id": u.parent_id, "description": u.description,
            "sort": u.sort, "members": members}


@router.get("/units")
async def units(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    counts = dict((await db.execute(select(Membership.unit_id, func.count(func.distinct(Membership.user_id)))
                                    .where(Membership.active()).group_by(Membership.unit_id))).all())
    rows = (await db.execute(select(OrgUnit).order_by(OrgUnit.sort, OrgUnit.name))).scalars()
    return [unit_out(u, counts.get(u.id, 0)) for u in rows]


@router.post("/units")
async def create_unit(body: UnitIn, p: Principal = Depends(require("iam.org")), db: AsyncSession = Depends(get_db)):
    if body.parent_id and await db.get(OrgUnit, body.parent_id) is None:
        raise HTTPException(422, "Родительское подразделение не найдено")
    u = OrgUnit(**body.model_dump())
    db.add(u)
    await db.commit()
    await audit.record("iam.unit.create", actor=p.user, ip=p.ip, target_type="unit", target_id=u.id, target_name=u.name)
    return unit_out(u)


@router.patch("/units/{unit_id}")
async def patch_unit(unit_id: str, body: UnitPatch, p: Principal = Depends(require("iam.org")),
                     db: AsyncSession = Depends(get_db)):
    u = await db.get(OrgUnit, unit_id)
    if u is None:
        raise HTTPException(404, "Подразделение не найдено")
    data = body.model_dump(exclude_unset=True)
    if "parent_id" in data and data["parent_id"] != u.parent_id:
        fresh(p)
        if not data["parent_id"]:
            raise HTTPException(422, "У подразделения должен быть родитель — компания одна")
        if data["parent_id"] in p.access.tree.descendants(unit_id):
            raise HTTPException(422, "Нельзя вложить подразделение в самого себя")
        if await db.get(OrgUnit, data["parent_id"]) is None:
            raise HTTPException(422, "Родительское подразделение не найдено")
        gains = guard.reparent_gains(p.access, unit_id, data["parent_id"])
        if gains:
            raise HTTPException(403, "После переноса у вас появились бы новые права в этом подразделении: "
                                     + ", ".join(gains[:4]) + ". Перенос делает владелец платформы.")
    for k, v in data.items():
        setattr(u, k, v)
    await db.commit()
    await audit.record("iam.unit.update", actor=p.user, ip=p.ip, target_type="unit", target_id=u.id,
                       target_name=u.name, data=data)
    return unit_out(u)


@router.delete("/units/{unit_id}")
async def delete_unit(unit_id: str, p: Principal = Depends(sudo("iam.org")), db: AsyncSession = Depends(get_db)):
    u = await db.get(OrgUnit, unit_id)
    if u is None:
        raise HTTPException(404, "Подразделение не найдено")
    if (await db.execute(select(func.count(OrgUnit.id)).where(OrgUnit.parent_id == unit_id))).scalar():
        raise HTTPException(409, "Сначала перенесите или удалите вложенные подразделения")
    if (await db.execute(select(func.count(Membership.id)).where(Membership.unit_id == unit_id))).scalar():
        raise HTTPException(409, "В подразделении есть люди — сначала снимите их роли")
    await db.delete(u)
    await db.commit()
    await audit.record("iam.unit.delete", actor=p.user, ip=p.ip, target_type="unit", target_id=unit_id, target_name=u.name)
    return {"ok": True}


# ── роли и матрица ─────────────────────────────────────────────────────────────
class RoleIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    key: str = Field(default="", max_length=60, pattern=r"^[a-z0-9_\-]*$")
    description: str = Field(default="", max_length=2000)
    color: str = Field(default="#8c8c8c", pattern=r"^#[0-9a-fA-F]{6}$")
    copy_from: str | None = None


class RolePatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    sort: int | None = None


class GrantIn(Strict):
    permission: str
    effect: Literal["allow", "approval", "deny"]
    approver_role_id: str | None = None


class GrantsIn(Strict):
    grants: list[GrantIn] = Field(max_length=200)


async def roles_payload(db: AsyncSession) -> list[dict]:
    roles = (await db.execute(select(Role).order_by(Role.sort, Role.name))).scalars().all()
    grants = (await db.execute(select(RoleGrant))).scalars().all()
    members = dict((await db.execute(select(Membership.role_id, func.count(func.distinct(Membership.user_id)))
                                     .where(Membership.active()).group_by(Membership.role_id))).all())
    by_role: dict[str, list] = {}
    for g in grants:
        by_role.setdefault(g.role_id, []).append({"permission": g.permission, "effect": g.effect,
                                                  "approver_role_id": g.approver_role_id})
    return [{"id": r.id, "key": r.key, "name": r.name, "description": r.description, "color": r.color,
             "is_system": r.is_system, "sort": r.sort, "members": members.get(r.id, 0),
             "grants": by_role.get(r.id, [])} for r in roles]


@router.get("/roles")
async def roles(p: Principal = Depends(require("iam.view")), db: AsyncSession = Depends(get_db)):
    return await roles_payload(db)


def escalation(p: Principal, perms: list[str]) -> list[str]:
    """Права, которых у самого действующего нет (allow) — выдавать их другим он не может."""
    if p.user.is_owner:
        return []
    return [x for x in perms if not p.access.can(x)]


@router.post("/roles")
async def create_role(body: RoleIn, p: Principal = Depends(require("iam.roles")), db: AsyncSession = Depends(get_db)):
    from orchestra.agents.manifest import slugify
    key = body.key or slugify(body.name).replace("-", "_")
    if (await db.execute(select(Role).where(Role.key == key))).scalar():
        raise HTTPException(409, "Роль с таким ключом уже есть")
    role = Role(key=key, name=body.name, description=body.description, color=body.color, sort=100)
    db.add(role)
    await db.flush()
    if body.copy_from:
        src = (await db.execute(select(RoleGrant).where(RoleGrant.role_id == body.copy_from))).scalars().all()
        blocked = escalation(p, [g.permission for g in src if g.effect != DENY])
        if blocked:
            raise HTTPException(403, f"Нельзя скопировать права, которых нет у вас: {', '.join(blocked)}")
        for g in src:
            db.add(RoleGrant(role_id=role.id, permission=g.permission, effect=g.effect,
                             approver_role_id=g.approver_role_id))
    await db.commit()
    await audit.record("iam.role.create", actor=p.user, ip=p.ip, target_type="role", target_id=role.id,
                       target_name=role.name, data={"copy_from": body.copy_from})
    return {"id": role.id}


@router.patch("/roles/{role_id}")
async def patch_role(role_id: str, body: RolePatch, p: Principal = Depends(require("iam.roles")),
                     db: AsyncSession = Depends(get_db)):
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "Роль не найдена")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(role, k, v)
    await db.commit()
    await audit.record("iam.role.update", actor=p.user, ip=p.ip, target_type="role", target_id=role.id,
                       target_name=role.name, data=data)
    return {"ok": True}


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, p: Principal = Depends(sudo("iam.roles")), db: AsyncSession = Depends(get_db)):
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "Роль не найдена")
    if role.is_system:
        raise HTTPException(409, "Системную роль удалить нельзя — можно поменять её права")
    if (await db.execute(select(func.count(Membership.id)).where(Membership.role_id == role_id))).scalar():
        raise HTTPException(409, "Роль назначена людям — сначала снимите её")
    await db.delete(role)
    await db.commit()
    await audit.record("iam.role.delete", actor=p.user, ip=p.ip, target_type="role", target_id=role_id, target_name=role.name)
    return {"ok": True}


@router.put("/roles/{role_id}/grants")
async def put_grants(role_id: str, body: GrantsIn, p: Principal = Depends(sudo("iam.roles")),
                     db: AsyncSession = Depends(get_db)):
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "Роль не найдена")
    new: dict[str, GrantIn] = {}
    for g in body.grants:
        if g.permission not in BY_KEY:
            raise HTTPException(422, f"Неизвестное право: {g.permission}")
        if g.effect == "approval" and not BY_KEY[g.permission].approvable:
            raise HTTPException(422, f"«{BY_KEY[g.permission].title}» не бывает «с одобрением»")
        if g.approver_role_id and await db.get(Role, g.approver_role_id) is None:
            raise HTTPException(422, "Визирующая роль не найдена")
        new[g.permission] = g
    old = {g.permission: g for g in (await db.execute(select(RoleGrant).where(RoleGrant.role_id == role_id))).scalars()}
    changed = [k for k in set(old) | set(new)
               if (old.get(k) and (old[k].effect, old[k].approver_role_id)) != (new.get(k) and (new[k].effect, new[k].approver_role_id))]
    # Снимать права и ставить запреты можно всегда; расширять — только в пределах своих прав.
    # Снятие запрета — тоже расширение: «можно» из соседней роли начинает действовать.
    widened = [k for k in changed if (k in new and new[k].effect != DENY)
               or (k in old and old[k].effect == DENY and k not in new)]
    blocked = escalation(p, widened)
    if blocked:
        raise await deny(p, p.access.decide(blocked[0]), "iam.role.grants", role.name)
    await db.execute(delete(RoleGrant).where(RoleGrant.role_id == role_id))
    for g in new.values():
        db.add(RoleGrant(role_id=role_id, permission=g.permission, effect=g.effect, approver_role_id=g.approver_role_id))
    await db.commit()
    diff = {k: {"было": old[k].effect if k in old else "—", "стало": new[k].effect if k in new else "—"} for k in changed}
    await audit.record("iam.role.grants", actor=p.user, ip=p.ip, target_type="role", target_id=role.id,
                       target_name=role.name, data={"изменения": diff})
    return {"ok": True, "changed": len(changed)}


# ── люди ───────────────────────────────────────────────────────────────────────
class MembershipIn(Strict):
    unit_id: str
    role_id: str
    expires_at: datetime | None = None                   # временная роль (до года)

    def key(self) -> tuple[str, str, str]:
        return self.unit_id, self.role_id, iso(auth.aware(self.expires_at)) or ""


class UserIn(Strict):
    login: str = Field(min_length=2, max_length=120, pattern=r"^[\w.@\-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    position: str = Field(default="", max_length=200)
    email: str | None = Field(default=None, max_length=200)
    password: str = Field(max_length=256)
    memberships: list[MembershipIn] = Field(default_factory=list, max_length=50)


class UserPatch(Strict):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class ResetIn(Strict):
    password: str = Field(max_length=256)


class OwnerIn(Strict):
    is_owner: bool


async def users_payload(db: AsyncSession, ids: list[str] | None = None) -> list[dict]:
    q = select(User).order_by(User.display_name)
    if ids is not None:
        q = q.where(User.id.in_(ids))
    users = (await db.execute(q)).scalars().all()
    ms = (await db.execute(select(Membership).where(Membership.active()))).scalars().all()
    by_user: dict[str, list] = {}
    for m in ms:
        by_user.setdefault(m.user_id, []).append({"unit_id": m.unit_id, "role_id": m.role_id,
                                                  "expires_at": iso(m.expires_at)})
    return [{"id": u.id, "login": u.login, "display_name": u.display_name, "position": u.position,
             "email": u.email, "is_owner": u.is_owner, "is_active": u.is_active,
             "locked": bool(u.locked_until and auth.aware(u.locked_until) > utcnow()),
             "must_change_password": u.must_change_password, "last_login_at": iso(u.last_login_at),
             "mfa": u.totp_enabled, "memberships": by_user.get(u.id, [])} for u in users]


@router.get("/users")
async def users(p: Principal = Depends(require("iam.view")), db: AsyncSession = Depends(get_db)):
    return await users_payload(db)


async def check_memberships(p: Principal, db: AsyncSession, items: list[MembershipIn]) -> None:
    for m in items:
        if await db.get(OrgUnit, m.unit_id) is None or await db.get(Role, m.role_id) is None:
            raise HTTPException(422, "Подразделение или роль не найдены")
        if m.expires_at is not None and not utcnow() < auth.aware(m.expires_at) <= utcnow() + MAX_TEMP:
            raise HTTPException(422, "Срок временной роли — от сегодня и не дальше чем через год")
        if not p.user.is_owner:
            perms = [g.permission for g in (await db.execute(select(RoleGrant).where(
                RoleGrant.role_id == m.role_id, RoleGrant.effect != DENY))).scalars()]
            # права нужны именно в том подразделении, где назначается роль: иначе руководитель отдела
            # выдал бы свою роль на всю компанию — шире, чем у него самого
            blocked = [x for x in perms if not p.access.can(x, m.unit_id)]
            if blocked:
                unit = p.access.tree.names.get(m.unit_id, "")
                raise HTTPException(403, f"Нельзя назначить роль в «{unit}»: у вас там нет прав " +
                                    ", ".join(BY_KEY[b].title for b in blocked[:5]))


@router.post("/users")
async def create_user(body: UserIn, p: Principal = Depends(sudo("iam.users")), db: AsyncSession = Depends(get_db)):
    login = auth.norm_login(body.login)
    if (await db.execute(select(User).where(User.login == login))).scalar():
        raise HTTPException(409, "Такой логин уже есть")
    problem = password_problem(body.password, get_settings().password_min_length, login, body.display_name)
    if problem:
        raise HTTPException(422, problem)
    await check_memberships(p, db, body.memberships)
    u = User(login=login, display_name=body.display_name, position=body.position, email=body.email,
             password_hash=hash_password(body.password), must_change_password=True)
    db.add(u)
    await db.flush()
    for m in {(m.unit_id, m.role_id): m for m in body.memberships}.values():
        db.add(Membership(user_id=u.id, unit_id=m.unit_id, role_id=m.role_id, expires_at=m.expires_at))
    await db.commit()
    await audit.record("iam.user.create", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login, data={"memberships": [m.model_dump() for m in body.memberships]})
    return (await users_payload(db, [u.id]))[0]


async def target_user(p: Principal, db: AsyncSession, user_id: str) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Человек не найден")
    try:
        await guard.manageable(db, p.access, u)          # сброс пароля человеку с бОльшими правами = захват его прав
    except HTTPException as exc:
        await audit.record("iam.user.manage", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                           target_name=u.login, outcome="denied", data={"reason": exc.detail})
        raise
    return u


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, p: Principal = Depends(sudo("iam.users")),
                     db: AsyncSession = Depends(get_db)):
    u = await target_user(p, db, user_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_active") is False:
        if u.id == p.user.id:
            raise HTTPException(409, "Нельзя отключить самого себя")
        await auth.revoke_all(db, u.id)
    for k, v in data.items():
        setattr(u, k, v)
    await db.commit()
    await audit.record("iam.user.update", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login, data=data)
    return (await users_payload(db, [u.id]))[0]


@router.post("/users/{user_id}/password")
async def reset_password(user_id: str, body: ResetIn, p: Principal = Depends(sudo("iam.users")),
                         db: AsyncSession = Depends(get_db)):
    u = await target_user(p, db, user_id)
    problem = password_problem(body.password, get_settings().password_min_length, u.login, u.display_name)
    if problem:
        raise HTTPException(422, problem)
    u.password_hash, u.must_change_password = hash_password(body.password), True
    u.failed_logins, u.locked_until, u.password_changed_at = 0, None, utcnow()
    await auth.revoke_all(db, u.id)
    await db.commit()
    await audit.record("iam.user.password_reset", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login)
    return {"ok": True}


@router.put("/users/{user_id}/memberships")
async def put_memberships(user_id: str, body: list[MembershipIn], p: Principal = Depends(sudo("iam.users")),
                          db: AsyncSession = Depends(get_db)):
    u = await target_user(p, db, user_id)
    if u.id == p.user.id and not p.user.is_owner:
        raise HTTPException(403, "Свои роли меняет другой администратор — принцип четырёх глаз")
    if len(body) > 50:
        raise HTTPException(422, "Слишком много назначений")
    rows = (await db.execute(select(Membership).where(Membership.user_id == u.id, Membership.active()))).scalars()
    old = {MembershipIn(unit_id=m.unit_id, role_id=m.role_id, expires_at=m.expires_at).key() for m in rows}
    items = {(m.unit_id, m.role_id): m for m in body}.values()
    new = {m.key() for m in items}
    # новое назначение или продление срока — расширение прав: проверяем как выдачу
    await check_memberships(p, db, [m for m in items if m.key() not in old])
    await db.execute(delete(Membership).where(Membership.user_id == u.id))
    for m in items:
        db.add(Membership(user_id=u.id, unit_id=m.unit_id, role_id=m.role_id, expires_at=m.expires_at))
    await db.commit()
    tree = p.access.tree
    names = dict((await db.execute(select(Role.id, Role.name))).all())
    fmt = lambda s: sorted(f"{names.get(b, b)} · {tree.names.get(a, a)}" + (f" до {e[:10]}" if e else "")  # noqa: E731
                           for a, b, e in s)
    await audit.record("iam.user.roles", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login, data={"добавлено": fmt(new - old), "снято": fmt(old - new)})
    return (await users_payload(db, [u.id]))[0]


@router.post("/users/{user_id}/owner")
async def set_owner(user_id: str, body: OwnerIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if not p.user.is_owner:
        raise HTTPException(403, "Назначать владельцев может только владелец")
    fresh(p)
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Человек не найден")
    if not body.is_owner:
        owners = (await db.execute(select(func.count(User.id)).where(User.is_owner.is_(True), User.is_active.is_(True)))).scalar()
        if u.is_owner and owners <= 1:
            raise HTTPException(409, "Должен остаться хотя бы один владелец")
    u.is_owner = body.is_owner
    await db.commit()
    await audit.record("iam.user.owner", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login, data={"is_owner": body.is_owner})
    await alerts.raise_alert("owner", f"{u.display_name} ({u.login}) {'получает' if body.is_owner else 'теряет'} права владельца. "
                             f"Кто изменил: {p.user.display_name}",
                             ip=p.ip, key=f"{u.id}:{body.is_owner}")
    return {"ok": True}


@router.post("/users/{user_id}/logout")
async def logout_user(user_id: str, p: Principal = Depends(require("iam.users")), db: AsyncSession = Depends(get_db)):
    u = await target_user(p, db, user_id)
    await auth.revoke_all(db, u.id)
    await db.commit()
    await audit.record("iam.user.logout", actor=p.user, ip=p.ip, target_type="user", target_id=u.id, target_name=u.login)
    return {"ok": True}


@router.post("/users/{user_id}/2fa/reset")
async def reset_mfa(user_id: str, p: Principal = Depends(sudo("iam.users")), db: AsyncSession = Depends(get_db)):
    """Человек потерял телефон: второй фактор снимается, все сессии завершаются, при входе — настроить заново."""
    u = await target_user(p, db, user_id)
    if u.id == p.user.id:
        raise HTTPException(409, "Свой второй фактор отключают в настройках профиля")
    u.totp_enabled, u.totp_secret_enc, u.recovery_hashes, u.totp_last_step = False, None, [], 0
    await auth.revoke_all(db, u.id)
    await db.commit()
    await audit.record("iam.user.2fa_reset", actor=p.user, ip=p.ip, target_type="user", target_id=u.id,
                       target_name=u.login)
    return {"ok": True}


@router.get("/stats")
async def stats(p: Principal = Depends(require("iam.view")), db: AsyncSession = Depends(get_db)):
    return {"users": (await db.execute(select(func.count(User.id)))).scalar(),
            "active": (await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))).scalar(),
            "roles": (await db.execute(select(func.count(Role.id)))).scalar()}


# ── запрос доступа ─────────────────────────────────────────────────────────────
class AccessRequestIn(Strict):
    unit_id: str = Field(max_length=36)
    role_id: str = Field(max_length=36)
    days: int = Field(ge=1, le=requests.MAX_DAYS)
    reason: str = Field(min_length=5, max_length=1000)


@router.get("/request-options")
async def request_options(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Какие роли можно попросить: названия и описания, без матрицы прав."""
    roles = (await db.execute(select(Role).order_by(Role.name))).scalars()
    return {"roles": [{"id": r.id, "name": r.name, "description": r.description, "color": r.color} for r in roles],
            "max_days": requests.MAX_DAYS}


@router.post("/access-requests")
async def request_access(body: AccessRequestIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    try:
        a = await requests.create(db, p.user, body.unit_id, body.role_id, body.days, body.reason)
    except requests.RequestError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return {"ok": True, "approval_id": a.id}
