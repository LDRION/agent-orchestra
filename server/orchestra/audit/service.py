"""Запись и проверка журнала аудита.

Каждая запись пишется в собственной транзакции: отказ в доступе и сбой тоже фиксируются,
даже если бизнес-транзакция откатилась. Цепочка: hash = sha256(prev_hash ‖ канон. JSON записи).
Правка или удаление любой строки задним числом ломает цепочку — verify() покажет где.
"""
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import event, select, text

from orchestra.audit import anchor
from orchestra.audit.models import AuditEvent
from orchestra.core.redact import scrub
from orchestra.db.base import utcnow
from orchestra.db.session import sessionmaker

log = logging.getLogger("orchestra.audit")
GENESIS = "0" * 64
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """Замок на свой цикл событий: тесты и перезапуски создают новые циклы."""
    loop = asyncio.get_running_loop()
    return _locks.setdefault(id(loop), asyncio.Lock())


def _canon(e: dict) -> bytes:
    return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _ts(dt: datetime) -> str:
    """SQLite возвращает время без пояса, PostgreSQL — с поясом: приводим к UTC без пояса."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


def _body(ev: AuditEvent) -> dict:
    return {"ts": _ts(ev.ts), "actor_id": ev.actor_id, "actor_name": ev.actor_name, "ip": ev.ip,
            "action": ev.action, "target_type": ev.target_type, "target_id": ev.target_id,
            "target_name": ev.target_name, "outcome": ev.outcome, "data": ev.data}


def _digest(prev: str, body: dict) -> str:
    return hashlib.sha256(prev.encode("ascii") + _canon(body)).hexdigest()


async def record(action: str, *, actor=None, ip: str = "", target_type: str = "", target_id: str = "",
                 target_name: str = "", outcome: str = "ok", data: dict | None = None) -> None:
    """actor — User или None (система). data проходит через scrub(): секретов в журнале нет."""
    try:
        async with _lock(), sessionmaker()() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(7410001)"))   # несколько процессов
            prev = (await db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).scalar()
            ev = AuditEvent(
                ts=utcnow().replace(microsecond=0),
                actor_id=getattr(actor, "id", None), actor_name=getattr(actor, "display_name", "система"),
                ip=ip, action=action, target_type=target_type, target_id=str(target_id or ""),
                target_name=(target_name or "")[:300], outcome=outcome, data=scrub(data or {}),
                prev_hash=prev or GENESIS,
            )
            ev.hash = _digest(ev.prev_hash, _body(ev))
            db.add(ev)
            await db.commit()
            try:
                anchor.write(ev.seq, ev.hash)            # метка конца журнала — вне базы, под подписью
            except OSError:
                log.exception("аудит: не удалось обновить метку конца журнала")
    except Exception:                                    # аудит не должен ронять действие, но сбой — громко
        log.exception("аудит: не удалось записать событие %s", action)


def defer(db, action: str, **kw) -> None:
    """Записать событие, когда транзакция db зафиксируется (откат — события нет).
    Нужна там, где транзакция ещё открыта: SQLite не даст второй записи параллельно."""
    sync = db.sync_session
    sync.info.setdefault("audit_pending", []).append((action, kw))
    if sync.info.get("audit_hooked"):
        return
    sync.info["audit_hooked"] = True

    @event.listens_for(sync, "after_commit")
    def _flush(session):
        for act, args in session.info.pop("audit_pending", []):
            asyncio.get_running_loop().create_task(record(act, **args))

    @event.listens_for(sync, "after_rollback")
    def _drop(session):
        session.info.pop("audit_pending", None)


async def verify() -> dict:
    """Пересчитать цепочку и сверить с заверенной меткой конца. → {ok, checked, broken_at, anchor, reason}"""
    async with sessionmaker()() as db:
        prev, n, hashes = GENESIS, 0, {}
        mark = anchor.read()
        rows = await db.stream_scalars(select(AuditEvent).order_by(AuditEvent.seq))
        async for ev in rows:
            n += 1
            if ev.prev_hash != prev or _digest(prev, _body(ev)) != ev.hash:
                return {"ok": False, "checked": n, "broken_at": ev.seq, "anchor": None,
                        "reason": f"запись №{ev.seq} изменена или удалена задним числом"}
            prev = ev.hash
            if mark and ev.seq == mark["seq"]:
                hashes[ev.seq] = ev.hash
    out = {"ok": True, "checked": n, "broken_at": None, "anchor": mark["seq"] if mark else None, "reason": ""}
    if mark is None:
        return out
    if not anchor.genuine(mark):
        return {**out, "ok": False, "reason": "подпись метки конца журнала не сходится — метку подменили"}
    if hashes.get(mark["seq"]) != mark["hash"]:
        return {**out, "ok": False, "reason": f"журнал обрезан или откатан: заверенной записи №{mark['seq']} нет"}
    return out
