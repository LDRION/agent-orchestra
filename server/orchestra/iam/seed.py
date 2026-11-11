"""Стартовая оргструктура и роли (пример: проектная организация). Создаются один раз, дальше — правятся в интерфейсе."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.iam.models import OrgUnit, Role, RoleGrant
from orchestra.iam.permissions import ALLOW, APPROVAL, DENY

COMPANY = "Компания"
DEPARTMENTS = [
    ("Руководство", "Стратегия, приоритеты, итоговые решения"),
    ("Архитектура", "Концепции, планировочные решения, АР"),
    ("Конструкции и инженерия", "КР, ОВ, ВК, ЭОМ, СС"),
    ("BIM-отдел", "Информационные модели, координация, стандарты BIM"),
    ("Управление проектами", "ГИПы, сроки, совещания, заказчики"),
    ("Тендеры и договоры", "Закупки, ТЗ заказчиков, договоры"),
    ("Выпуск и нормоконтроль", "Оформление, ГОСТ Р 21.101, СПДС, выпуск документации"),
    ("Маркетинг", "Сайт, портфолио, публикации"),
    ("ИТ и безопасность", "Инфраструктура, доступы, защита данных"),
]

# key, name, color, description, grants {permission: effect | (effect, approver_role_key)}
ROLES = [
    ("admin_ai", "Администратор ИИ", "#2b2b2b", "Настраивает модели, агентов и промты. Людьми и правами не управляет.", {
        "runs.view": ALLOW, "runs.view_all": ALLOW, "runs.start": ALLOW, "runs.cancel_any": ALLOW,
        "agents.view": ALLOW, "agents.edit": ALLOW, "agents.delete": ALLOW,
        "prompts.view": ALLOW, "prompts.edit": ALLOW, "prompts.publish": ALLOW,
        "providers.view": ALLOW, "providers.manage": ALLOW, "providers.external": ALLOW,
        "tools.read": ALLOW, "tools.web": ALLOW, "tools.write": APPROVAL,
        "approvals.decide": ALLOW, "iam.view": ALLOW, "audit.view": ALLOW, "system.usage": ALLOW,
        "knowledge.view": ALLOW, "knowledge.edit": ALLOW,
        "integrations.view": ALLOW, "integrations.manage": ALLOW, "directory.edit": ALLOW,
    }),
    ("security", "Безопасность", "#b2452f", "Люди, роли, оргструктура, аудит и политика данных. Агентов не настраивает.", {
        "runs.view": ALLOW, "runs.view_all": ALLOW, "agents.view": ALLOW, "prompts.view": ALLOW,
        "providers.view": ALLOW, "approvals.decide": ALLOW,
        "iam.view": ALLOW, "iam.users": ALLOW, "iam.roles": ALLOW, "iam.org": ALLOW,
        "audit.view": ALLOW, "system.settings": ALLOW, "system.usage": ALLOW,
        "security.view": ALLOW, "security.manage": ALLOW,
        "providers.manage": DENY, "knowledge.view": ALLOW, "integrations.view": ALLOW,
    }),
    ("head", "Руководитель отдела", "#7c7a12", "Видит задачи отдела, визирует запросы сотрудников, правит промты по согласованию.", {
        "runs.view": ALLOW, "runs.view_all": ALLOW, "runs.start": ALLOW, "runs.cancel_any": ALLOW,
        "agents.view": ALLOW, "agents.edit": (APPROVAL, "admin_ai"),
        "prompts.view": ALLOW, "prompts.edit": ALLOW, "prompts.publish": (APPROVAL, "admin_ai"),
        "providers.view": ALLOW, "providers.external": ALLOW, "data.confidential": ALLOW,
        "tools.read": ALLOW, "tools.web": ALLOW, "tools.write": APPROVAL,
        "approvals.decide": ALLOW, "iam.view": ALLOW, "system.usage": ALLOW,
        "knowledge.view": ALLOW, "knowledge.edit": ALLOW, "integrations.view": ALLOW, "directory.edit": ALLOW,
    }),
    ("gip", "ГИП", "#5a6b2e", "Главный инженер проекта: задачи по своим проектам, визы команды проекта.", {
        "runs.view": ALLOW, "runs.view_all": ALLOW, "runs.start": ALLOW,
        "agents.view": ALLOW, "prompts.view": ALLOW, "providers.view": ALLOW,
        "providers.external": ALLOW, "data.confidential": ALLOW,
        "tools.read": ALLOW, "tools.web": ALLOW, "tools.write": APPROVAL, "approvals.decide": ALLOW,
        "knowledge.view": ALLOW, "knowledge.edit": ALLOW, "integrations.view": ALLOW, "directory.edit": ALLOW,
    }),
    ("employee", "Сотрудник", "#6b6b6b", "Ставит задачи агентам. Внешние модели, интернет и конфиденциальное — по визе руководителя.", {
        "runs.view": ALLOW, "runs.start": ALLOW, "agents.view": ALLOW, "providers.view": ALLOW,
        "providers.external": APPROVAL, "data.confidential": APPROVAL,
        "tools.read": ALLOW, "tools.web": APPROVAL, "tools.write": APPROVAL, "knowledge.view": ALLOW,
    }),
    ("trainee", "Стажёр", "#9a9a9a", "Только локальные модели; каждая задача — по визе наставника.", {
        "runs.view": ALLOW, "runs.start": APPROVAL, "agents.view": ALLOW, "tools.read": ALLOW,
        "providers.external": DENY, "data.confidential": DENY, "tools.web": DENY, "tools.write": DENY,
        "knowledge.view": ALLOW,
    }),
    ("auditor", "Аудитор", "#3f5f7f", "Только чтение: задачи, агенты, промты, журнал аудита. Ничего не меняет.", {
        "runs.view": ALLOW, "runs.view_all": ALLOW, "agents.view": ALLOW, "prompts.view": ALLOW,
        "providers.view": ALLOW, "iam.view": ALLOW, "audit.view": ALLOW, "system.usage": ALLOW,
        "knowledge.view": ALLOW, "integrations.view": ALLOW, "security.view": ALLOW,
    }),
]
# Право, добавленное после первого выпуска, выдаётся системным ролям существующих установок
# миграцией (снимок соответствия — внутри неё, см. 0002), а новым установкам — отсюда.


async def seed_org_and_roles(db: AsyncSession) -> bool:
    """True — если что-то создано. Повторный вызов ничего не меняет."""
    if (await db.execute(select(func.count(Role.id)))).scalar():
        return False
    company = OrgUnit(name=COMPANY, kind="company", description="Проектирование зданий и интерьеров, BIM", sort=0)
    db.add(company)
    await db.flush()
    for i, (name, desc) in enumerate(DEPARTMENTS, start=1):
        db.add(OrgUnit(name=name, kind="department", parent_id=company.id, description=desc, sort=i))
    roles: dict[str, Role] = {}
    for i, (key, name, color, desc, _) in enumerate(ROLES):
        roles[key] = Role(key=key, name=name, color=color, description=desc, is_system=True, sort=i)
        db.add(roles[key])
    await db.flush()
    for key, *_rest, grants in ROLES:
        for perm, spec in grants.items():
            effect, approver = (spec, None) if isinstance(spec, str) else spec
            db.add(RoleGrant(role_id=roles[key].id, permission=perm, effect=effect,
                             approver_role_id=roles[approver].id if approver else None))
    await db.commit()
    return True
