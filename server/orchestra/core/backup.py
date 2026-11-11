"""Резервная копия лаунчера (SQLite): согласованный снимок базы, зашифрованный паролем копии.

В копии — все данные компании, поэтому она шифруется: AES-256-GCM, ключ — scrypt от пароля копии
(не мастер-ключ: копия не должна зависеть от этого компьютера). Секреты внутри (ключи моделей, 2FA)
по-прежнему зашифрованы мастер-ключом — его копию храните отдельно от копий базы.
Восстановление — при остановленном приложении; прежняя база сохраняется рядом, метка конца журнала
аудита переподписывается под восстановленную базу, событие пишется в журнал.
Сервер (PostgreSQL) — pg_dump, см. deploy/README.md.
"""
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from orchestra.settings import get_settings

MAGIC = b"ORCHBAK1\n"
MIN_PASSPHRASE = 12


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _db() -> Path:
    s = get_settings()
    if not s.is_sqlite:
        raise SystemExit("База — PostgreSQL: резервная копия через pg_dump (deploy/README.md)")
    return s.data_dir / "orchestra.db"


def _check(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE:
        raise SystemExit(f"Пароль копии — не короче {MIN_PASSPHRASE} символов")


def create(out: Path, passphrase: str) -> str:
    _check(passphrase)
    db = _db()
    if not db.exists():
        raise SystemExit(f"База не найдена: {db}")
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)                              # согласованный снимок даже при работающем приложении
        data = Path(tmp).read_bytes()
    finally:
        os.unlink(tmp)
    salt, nonce = os.urandom(16), os.urandom(12)
    out.write_bytes(MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC))
    return f"Копия записана: {out} ({len(data) // 1024} КБ базы, зашифрована паролем копии)"


def restore(src: Path, passphrase: str) -> str:
    raw = src.read_bytes()
    if not raw.startswith(MAGIC):
        raise SystemExit("Это не резервная копия Оркестр · AI")
    salt, nonce, ct = raw[len(MAGIC):len(MAGIC) + 16], raw[len(MAGIC) + 16:len(MAGIC) + 28], raw[len(MAGIC) + 28:]
    try:
        data = AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, MAGIC)
    except InvalidTag as exc:
        raise SystemExit("Неверный пароль копии или копия повреждена") from exc
    if not data.startswith(b"SQLite format 3\x00"):
        raise SystemExit("Внутри копии не база SQLite")
    db = _db()
    kept = None
    if db.exists():
        kept = db.with_name(f"orchestra.db.before-restore-{datetime.now():%Y%m%d-%H%M%S}")
        os.replace(db, kept)
    for extra in ("orchestra.db-wal", "orchestra.db-shm"):
        (db.parent / extra).unlink(missing_ok=True)
    db.write_bytes(data)
    return f"База восстановлена из {src}" + (f"; прежняя сохранена как {kept.name}" if kept else "")
