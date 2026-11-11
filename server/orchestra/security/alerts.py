"""Тревоги безопасности: запись в журнал + (по желанию) сообщение в канал администраторов.

Канал — уже подключённая интеграция (Telegram, почта, исходящий вебхук), выбирается владельцем
в «Безопасности». Это сообщение системы, не агента: в нём нет данных компании — только что
случилось, логин и адрес. Одинаковые тревоги не чаще раза в 10 минут.
"""
import asyncio
import logging
import time

from orchestra.audit import service as audit
from orchestra.db.session import sessionmaker
from orchestra.system import settings_store

log = logging.getLogger("orchestra.security")
KINDS = {
    "lockout": "Блокировка учётной записи",
    "ip_flood": "Подбор паролей с одного адреса",
    "reauth": "Неверный пароль при подтверждении действия",
    "owner": "Изменение владельцев платформы",
    "network": "Запросы из запрещённой сети",
    "test": "Проверка канала тревог",
}
QUIET_SECONDS = 600
_last: dict[tuple[str, str], float] = {}
_tasks: set[asyncio.Task] = set()


def _fresh(kind: str, key: str) -> bool:
    now = time.monotonic()
    if len(_last) > 10_000:                              # перебор тысячами логинов не съест память
        for k in [k for k, t in _last.items() if now - t >= QUIET_SECONDS]:
            _last.pop(k, None)
        if len(_last) > 10_000:
            _last.clear()
    if now - _last.get((kind, key), -QUIET_SECONDS) < QUIET_SECONDS:
        return False
    _last[(kind, key)] = now
    return True


async def raise_alert(kind: str, text: str, *, ip: str = "", key: str = "") -> None:
    """key — что считать «той же» тревогой (логин, адрес); по умолчанию — адрес."""
    if not _fresh(kind, key or ip):
        return
    await audit.record("security.alert", ip=ip, outcome="alert", target_type="alert", target_name=KINDS.get(kind, kind),
                       data={"kind": kind, "text": text})
    t = asyncio.get_running_loop().create_task(_notify(kind, text, ip))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def message(kind: str, text: str, ip: str) -> str:
    return f"⚠ Оркестр · AI — {KINDS.get(kind, kind)}\n{text}" + (f"\nАдрес: {ip}" if ip else "")


async def _notify(kind: str, text: str, ip: str) -> None:
    from orchestra.integrations import service as integrations
    from orchestra.integrations.models import Integration
    try:
        async with sessionmaker()() as db:
            cfg = await settings_store.get(db, "security_alerts") or {}
            row = await db.get(Integration, cfg.get("integration_id") or "") if cfg.get("integration_id") else None
            if row is None or not row.enabled:
                return
            await send(integrations.build(row), message(kind, text, ip), str(cfg.get("to") or ""))
    except Exception as exc:                             # канал недоступен — тревога уже в журнале
        log.warning("тревога безопасности не доставлена: %s", type(exc).__name__)


async def send(conn, text: str, to: str = "") -> dict:
    if conn.kind == "telegram":
        return await conn.run("send_message", {"text": text, **({"chat_id": to} if to else {})})
    if conn.kind == "email":
        return await conn.run("send", {"to": to, "subject": "Тревога безопасности Оркестр · AI", "body": text})
    if conn.kind == "webhook_out":
        return await conn.run("send", {"event": "security.alert", "data": text})
    raise ValueError(f"интеграция «{conn.kind}» не годится для тревог")


def reset() -> None:
    _last.clear()
