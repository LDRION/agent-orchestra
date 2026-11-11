"""Второй фактор: политика, проверка кода, токен «пароль верен — ждём код».

Токен подтверждения — шифротекст Vault (AES-GCM) с id человека и сроком: не хранится на сервере,
работает и при нескольких процессах, подделать или продлить его нельзя.
"""
import json
import time

from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.core import totp
from orchestra.core.crypto import VaultError, get_vault
from orchestra.iam.authz import AccessContext
from orchestra.iam.models import User
from orchestra.system import settings_store

CHALLENGE_TTL = 300
PRIVILEGED = ("iam.users", "iam.roles", "providers.manage", "integrations.manage", "system.settings", "security.manage")
_PURPOSE = "mfa-challenge"


def secret_purpose(user_id: str) -> str:
    return f"user:{user_id}:totp"


async def required(db: AsyncSession, access: AccessContext) -> bool:
    policy = await settings_store.get(db, "require_2fa")
    if policy == "all":
        return True
    if policy == "privileged":
        return access.user.is_owner or any(access.can(p) for p in PRIVILEGED)
    return False


def new_challenge(user_id: str) -> str:
    payload = json.dumps({"u": user_id, "exp": int(time.time()) + CHALLENGE_TTL})
    return get_vault().encrypt(payload, _PURPOSE)


def open_challenge(token: str) -> str | None:
    try:
        data = json.loads(get_vault().decrypt(token, _PURPOSE))
    except (VaultError, ValueError, TypeError):
        return None
    return data["u"] if data.get("exp", 0) >= time.time() else None


def check(user: User, code: str) -> bool:
    """TOTP или одноразовый код восстановления. Меняет user (last_step / список кодов) — нужен commit."""
    if not user.totp_enabled or not user.totp_secret_enc:
        return False
    secret = get_vault().decrypt(user.totp_secret_enc, secret_purpose(user.id))
    step = totp.verify(secret, code, user.totp_last_step or 0)
    if step is not None:
        user.totp_last_step = step
        return True
    h = totp.code_hash(code)
    if h in (user.recovery_hashes or []):
        user.recovery_hashes = [x for x in user.recovery_hashes if x != h]
        return True
    return False
