"""Ротация мастер-ключа: все секреты перешифровываются новым ключом в одной транзакции.

Ключ из файла data/master.key (лаунчер) заменяется автоматически: новый пишется рядом,
подменяет старый только после фиксации транзакции. Ключ из ORCHESTRA_MASTER_KEY / ORCHESTRA_MASTER_KEY_FILE
(сервер) выводится или пишется в --out — оператор обновляет секрет и перезапускает сервис.
"""
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from orchestra.audit import anchor
from orchestra.core.crypto import Vault, VaultError, _write_key_file, get_vault, set_vault
from orchestra.db.session import init_engine, sessionmaker
from orchestra.iam.mfa import secret_purpose as totp_purpose
from orchestra.iam.models import User
from orchestra.providers.models import Provider
from orchestra.providers.service import secret_purpose as provider_purpose
from orchestra.settings import get_settings


async def reencrypt(old: Vault, new: Vault) -> dict:
    from orchestra.integrations.models import Integration
    from orchestra.integrations.service import secret_purpose as integration_purpose
    counts = {"providers": 0, "totp": 0, "integrations": 0}
    async with sessionmaker()() as db:
        for p in (await db.execute(select(Provider).where(Provider.secret_enc.is_not(None)))).scalars():
            p.secret_enc = new.encrypt(old.decrypt(p.secret_enc, provider_purpose(p.id)), provider_purpose(p.id))
            counts["providers"] += 1
        for u in (await db.execute(select(User).where(User.totp_secret_enc.is_not(None)))).scalars():
            u.totp_secret_enc = new.encrypt(old.decrypt(u.totp_secret_enc, totp_purpose(u.id)), totp_purpose(u.id))
            counts["totp"] += 1
        for i in (await db.execute(select(Integration).where(Integration.secret_enc.is_not(None)))).scalars():
            i.secret_enc = new.encrypt(old.decrypt(i.secret_enc, integration_purpose(i.id)), integration_purpose(i.id))
            counts["integrations"] += 1
        await db.commit()                                # всё или ничего: ошибка расшифровки — отката хватает
    return counts


async def run(out: Path | None = None) -> str:
    s = get_settings()
    init_engine()
    old = get_vault()
    key = AESGCM.generate_key(bit_length=256)
    managed = not s.master_key and not s.master_key_file
    path = s.data_dir / "master.key"
    tmp = path.with_suffix(".key.new")
    if managed:
        tmp.unlink(missing_ok=True)
        _write_key_file(tmp, key)
    try:
        counts = await reencrypt(old, Vault(key))
    except VaultError as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Ротация отменена, ничего не изменено: {exc}") from exc
    resigned = anchor.resign(old, Vault(key))           # метка конца журнала — под новым ключом
    summary = (f"перешифровано: ключей моделей — {counts['providers']}, секретов интеграций — {counts['integrations']}, "
               f"секретов 2FA — {counts['totp']}; метка журнала аудита {'переподписана' if resigned else 'не найдена'}")
    if managed:
        os.replace(tmp, path)
        set_vault(None)
        return f"{summary}. Новый ключ записан в {path} (DPAPI). Перезапустите сервер."
    encoded = base64.b64encode(key).decode("ascii")
    if out:
        _write_key_file(Path(out), key) if os.name == "nt" else Path(out).write_text(encoded)
        return f"{summary}. Новый ключ — в {out}. Обновите ORCHESTRA_MASTER_KEY / секрет и перезапустите сервер."
    return f"{summary}.\nНОВЫЙ КЛЮЧ (сохраните в секрет и перезапустите сервер; старый больше не подходит):\n{encoded}"
