"""Аварийное восстановление доступа — только из консоли сервера (нужен доступ к машине, не к сайту).

Сценарий: единственный владелец потерял телефон и коды восстановления или забыл пароль.
Через интерфейс это не решить (так и задумано), поэтому — команда `python -m orchestra recover`.
Новый пароль — случайный и временный (смена при входе), все сессии закрываются, 2FA по желанию
сбрасывается; каждое действие — в журнал аудита от имени «консоль сервера».
"""
import secrets

from sqlalchemy import select

from orchestra.audit import service as audit
from orchestra.core.passwords import hash_password
from orchestra.db.base import utcnow
from orchestra.db.session import init_engine, sessionmaker
from orchestra.iam import service as auth
from orchestra.iam.models import User

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def temp_password() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4))


async def run(login: str, *, reset_2fa: bool = False, make_owner: bool = False) -> str:
    init_engine()
    async with sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.login == auth.norm_login(login)))).scalar()
        if user is None:
            raise SystemExit(f"Логин «{login}» не найден")
        password = temp_password()
        user.password_hash, user.must_change_password, user.password_changed_at = hash_password(password), True, utcnow()
        user.failed_logins, user.locked_until, user.is_active = 0, None, True
        if reset_2fa:
            user.totp_enabled, user.totp_secret_enc, user.recovery_hashes = False, None, []
        if make_owner:
            user.is_owner = True
        await auth.revoke_all(db, user.id)
        await db.commit()
    await audit.record("iam.recovery", ip="консоль сервера", target_type="user", target_id=user.id, target_name=user.login,
                       data={"сброс 2FA": reset_2fa, "сделан владельцем": make_owner})
    return (f"Временный пароль для «{user.login}»: {password}\n"
            "Сменится при первом входе. Все сессии закрыты" + (", второй фактор сброшен." if reset_2fa else "."))
