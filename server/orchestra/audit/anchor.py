"""Заверенная метка конца журнала аудита — вне базы данных.

Хеш-цепочка ловит правку и удаление записей в середине, но не обрезку хвоста и не откат базы
к старой копии: оставшаяся часть цепочки остаётся «целой». Поэтому после каждой записи номер и хеш
последнего события подписываются (HMAC, ключ выводится из мастер-ключа) и кладутся в файл рядом
с данными приложения. На сервере база — в отдельном контейнере PostgreSQL, файл — в томе приложения:
кто правит базу, метку не подделает (нет ключа) и не откатит вместе с базой.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

from orchestra.core.crypto import Vault, get_vault
from orchestra.settings import get_settings

FILE = "audit.anchor"
_PURPOSE = "audit-anchor"


def path() -> Path:
    return get_settings().data_dir / FILE


def _mac(vault: Vault, seq: int, digest: str) -> str:
    return hmac.new(vault.derive(_PURPOSE), f"{seq}:{digest}".encode("ascii"), hashlib.sha256).hexdigest()


def write(seq: int, digest: str, vault: Vault | None = None) -> None:
    vault = vault or get_vault()
    p = path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": seq, "hash": digest, "mac": _mac(vault, seq, digest)}), encoding="ascii")
    os.replace(tmp, p)                                   # атомарно: метка либо старая, либо новая


def read() -> dict | None:
    try:
        data = json.loads(path().read_text(encoding="ascii"))
        return {"seq": int(data["seq"]), "hash": str(data["hash"]), "mac": str(data["mac"])}
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
        return {"seq": -1, "hash": "", "mac": ""}           # испорчена — проверка это покажет


def genuine(a: dict, vault: Vault | None = None) -> bool:
    return a["seq"] >= 0 and hmac.compare_digest(a["mac"], _mac(vault or get_vault(), a["seq"], a["hash"]))


def resign(old: Vault, new: Vault) -> bool:
    """Ротация мастер-ключа: подлинная метка переподписывается новым ключом (поддельную не «отмываем»)."""
    a = read()
    if a is None or not genuine(a, old):
        return False
    write(a["seq"], a["hash"], new)
    return True


async def reanchor(reason: str) -> str:
    """Законное восстановление базы из копии: подписать её текущий конец. Прежняя метка — в журнале,
    поэтому «замести следы» так незаметно не выйдет: событие о переподписи стоит сразу после восстановленного конца."""
    from sqlalchemy import select

    from orchestra.audit import service as audit
    from orchestra.audit.models import AuditEvent
    from orchestra.db.session import init_engine, sessionmaker
    init_engine()
    old = read()
    async with sessionmaker()() as db:
        head = (await db.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1))).scalar()
    if head is not None:
        write(head.seq, head.hash)
    await audit.record("audit.reanchor", ip="консоль сервера", target_type="audit", target_name="метка конца журнала",
                       data={"причина": reason, "прежняя метка": old["seq"] if old else None,
                             "конец восстановленной базы": head.seq if head else 0})
    return f"Метка конца журнала переподписана: №{head.seq if head else 0} (прежняя — №{old['seq'] if old else '—'})"
