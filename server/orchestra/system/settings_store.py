"""Настройки платформы с значениями по умолчанию. Ключи перечислены явно — чужие не принимаем."""
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.providers.egress import DEFAULT_POLICY
from orchestra.system.models import SystemSetting

DEFAULTS: dict[str, object] = {
    "egress_policy": DEFAULT_POLICY,
    "default_chain": [],                 # [{provider_id, model}] — модели по умолчанию для агентов
    "planner_chain": [],                 # модели оркестратора (планирование и сводка); пусто — default_chain
    "default_classification": "internal",
    "max_parallel_steps": 3,
    "company_name": "Компания",
    "user_daily_tokens": 0,              # дневной лимит токенов на человека; 0 — без лимита
    "require_2fa": None,                 # none | privileged | all; None — по режиму (см. default_of)
    "security_alerts": {},               # {integration_id, to} — куда слать тревоги безопасности
    "seeded_agents": None,               # служебное: slug агентов, уже посеянных из agents/*.md
}


def default_of(key: str):
    if key == "require_2fa":
        # Сервер в сети — второй фактор обязателен для тех, кто управляет доступами и моделями.
        # Лаунчер на одном компьютере — по желанию (вход в Windows уже защищает).
        from orchestra.settings import get_settings
        return "privileged" if get_settings().mode == "server" else "none"
    return DEFAULTS.get(key)


async def get(db: AsyncSession, key: str):
    row = await db.get(SystemSetting, key)
    return row.value if row is not None and row.value is not None else default_of(key)


async def get_all(db: AsyncSession) -> dict:
    return {k: await get(db, k) for k in DEFAULTS}


async def put(db: AsyncSession, key: str, value, user_id: str | None) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = await db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value, updated_by=user_id))
    else:
        row.value = value
        row.updated_by = user_id
