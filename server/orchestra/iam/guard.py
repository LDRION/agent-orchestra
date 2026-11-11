"""Защита от эскалации прав через администрирование.

Три пути, которые закрывает этот модуль:
  1. Управление человеком с бОльшими правами: сброс пароля / 2FA / отключение / смена ролей такого
     человека = захват его прав. Управлять можно только тем, чьи управляющие и рискованные права
     (модели, ключи, доступы, конфиденциальное) есть и у тебя. Обычные рабочие права (ставить задачи,
     читать базу) не мешают «Безопасности» помогать рядовым сотрудникам.
  2. Снятие запрета (deny): «можно» из одной роли начинает действовать — это расширение, а не сужение.
  3. Перенос подразделения под своё: роли действуют вниз по дереву, перенос дарит права на чужой отдел.
Владелец платформы ограничений не имеет (каждое его действие в аудите).
"""
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.iam.authz import AccessContext, OrgTree, load_access
from orchestra.iam.models import User
from orchestra.iam.permissions import BY_KEY, CATALOG


def missing_for(actor: AccessContext, perms) -> list[str]:
    """Права из perms, которых у actor нет (allow хоть где-то)."""
    if actor.user.is_owner:
        return []
    return [p for p in perms if not actor.can(p)]


async def manageable(db: AsyncSession, actor: AccessContext, target: User) -> None:
    """Можно ли actor управлять учётной записью target (пароль, 2FA, роли, отключение, сессии)."""
    if actor.user.is_owner:
        return
    if target.is_owner:
        raise HTTPException(403, "Владельца платформы меняет только владелец")
    t = await load_access(db, target)
    extra = [p.key for p in CATALOG if (p.risky or p.level == "manage")
             and t.decide(p.key).allowed and not actor.can(p.key)]
    if extra:
        raise HTTPException(403, "У сотрудника есть права, которых нет у вас — управлять его учётной записью нельзя: "
                                 + ", ".join(BY_KEY[k].title for k in extra[:4]) + ("…" if len(extra) > 4 else ""))


def reparent_gains(actor: AccessContext, unit_id: str, new_parent: str | None) -> list[str]:
    """Что actor получил бы в переносимом поддереве после переноса (пусто — безопасно)."""
    if actor.user.is_owner:
        return []
    tree = OrgTree(parent={**actor.tree.parent, unit_id: new_parent}, names=actor.tree.names)
    after = replace(actor, tree=tree)
    gains = set()
    for u in actor.tree.descendants(unit_id):
        for p in CATALOG:
            if after.decide(p.key, u).allowed and not actor.decide(p.key, u).allowed:
                gains.add(p.title)
    return sorted(gains)
