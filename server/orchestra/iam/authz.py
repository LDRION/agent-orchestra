"""Движок решений о доступе.

    ctx = await load_access(db, user)
    d = ctx.decide("agents.edit", unit_id=agent.unit_id)
    d.effect → allow | approval | deny, d.reason — человеческое объяснение для интерфейса.

Правила сведения нескольких ролей:
  1. Владелец платформы — allow (аудит всё равно пишется).
  2. Явный deny в любой действующей роли → deny.
  3. Любой allow → allow.
  4. Любой approval → approval (визируют роли из approver_role_id или любой с approvals.decide).
  5. Иначе deny: чего не выдали — нельзя.
Роль действует в своём подразделении и во всех вложенных. Проверка без подразделения
учитывает все назначения человека («может ли он это где-нибудь»).
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.iam.models import Membership, OrgUnit, Role, RoleGrant, User
from orchestra.iam.permissions import ALLOW, APPROVAL, BY_KEY, DENY


@dataclass
class Decision:
    effect: str
    permission: str
    reason: str
    approver_role_ids: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.effect == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.effect == APPROVAL

    def as_dict(self) -> dict:
        return {"effect": self.effect, "permission": self.permission, "reason": self.reason,
                "approver_role_ids": self.approver_role_ids}


@dataclass
class OrgTree:
    parent: dict[str, str | None]
    names: dict[str, str]

    def chain(self, unit_id: str) -> list[str]:
        """Подразделение и все его предки до корня (защита от циклов)."""
        out, cur, seen = [], unit_id, set()
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def descendants(self, unit_id: str) -> set[str]:
        children: dict[str, list[str]] = {}
        for u, p in self.parent.items():
            if p:
                children.setdefault(p, []).append(u)
        out, stack = set(), [unit_id]
        while stack:
            u = stack.pop()
            if u in out:
                continue
            out.add(u)
            stack.extend(children.get(u, []))
        return out


async def load_tree(db: AsyncSession) -> OrgTree:
    rows = (await db.execute(select(OrgUnit.id, OrgUnit.parent_id, OrgUnit.name))).all()
    return OrgTree(parent={r.id: r.parent_id for r in rows}, names={r.id: r.name for r in rows})


@dataclass
class AccessContext:
    user: User
    tree: OrgTree
    memberships: list[tuple[str, str]]                   # (unit_id, role_id)
    grants: dict[str, dict[str, tuple[str, str | None]]]  # role_id → perm → (effect, approver_role_id)
    role_names: dict[str, str]

    def _roles_in_scope(self, unit_id: str | None) -> set[str]:
        if unit_id is None:
            return {r for _, r in self.memberships}
        chain = set(self.tree.chain(unit_id))
        return {r for u, r in self.memberships if u in chain}

    def decide(self, permission: str, unit_id: str | None = None) -> Decision:
        if permission not in BY_KEY:
            return Decision(DENY, permission, f"неизвестное право «{permission}»")
        if not self.user.is_active:
            return Decision(DENY, permission, "учётная запись отключена")
        if self.user.is_owner:
            return Decision(ALLOW, permission, "владелец платформы")
        effects: dict[str, list[tuple[str, str | None]]] = {ALLOW: [], APPROVAL: [], DENY: []}
        for rid in self._roles_in_scope(unit_id):
            g = self.grants.get(rid, {}).get(permission)
            if g:
                effects[g[0]].append((rid, g[1]))
        title = BY_KEY[permission].title.lower()
        if effects[DENY]:
            who = ", ".join(self.role_names.get(r, r) for r, _ in effects[DENY])
            return Decision(DENY, permission, f"запрещено ролью: {who}")
        if effects[ALLOW]:
            who = ", ".join(self.role_names.get(r, r) for r, _ in effects[ALLOW])
            return Decision(ALLOW, permission, f"разрешено ролью: {who}")
        if effects[APPROVAL]:
            approvers = sorted({a for _, a in effects[APPROVAL] if a})
            return Decision(APPROVAL, permission, f"«{title}» — только с одобрением", approvers)
        return Decision(DENY, permission, f"нет права «{title}»")

    def can(self, permission: str, unit_id: str | None = None) -> bool:
        return self.decide(permission, unit_id).allowed

    def units_with(self, permission: str) -> set[str] | None:
        """Подразделения, где право = allow. None — везде (владелец)."""
        if self.user.is_owner:
            return None
        allowed: set[str] = set()
        denied: set[str] = set()
        for unit, rid in self.memberships:
            g = self.grants.get(rid, {}).get(permission)
            if not g:
                continue
            if g[0] == ALLOW:
                allowed |= self.tree.descendants(unit)
            elif g[0] == DENY:
                denied |= self.tree.descendants(unit)
        return allowed - denied

    def has_role_in_scope(self, role_ids: list[str], unit_id: str | None) -> bool:
        return self.user.is_owner or bool(self._roles_in_scope(unit_id) & set(role_ids))

    def summary(self) -> dict:
        """Для интерфейса: что человеку можно в целом — прячем недоступные разделы."""
        from orchestra.iam.permissions import CATALOG
        return {p.key: self.decide(p.key).effect for p in CATALOG}


async def load_access(db: AsyncSession, user: User) -> AccessContext:
    tree = await load_tree(db)
    ms = (await db.execute(select(Membership.unit_id, Membership.role_id)
                           .where(Membership.user_id == user.id, Membership.active()))).all()
    memberships = [(m.unit_id, m.role_id) for m in ms]
    role_ids = {r for _, r in memberships}
    grants: dict[str, dict[str, tuple[str, str | None]]] = {}
    if role_ids:
        rows = (await db.execute(select(RoleGrant).where(RoleGrant.role_id.in_(role_ids)))).scalars()
        for g in rows:
            grants.setdefault(g.role_id, {})[g.permission] = (g.effect, g.approver_role_id)
    names = dict((await db.execute(select(Role.id, Role.name))).all())
    return AccessContext(user=user, tree=tree, memberships=memberships, grants=grants, role_names=names)
