"""Пароли: Argon2id (параметры OWASP), перехеширование при смене параметров."""
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

# Хеш для выравнивания времени ответа, когда пользователя нет: перебор логинов
# не должен различаться по скорости ответа.
_DUMMY = _hasher.hash("orchestra-dummy-password-for-timing")

COMMON = {
    "password", "password1", "qwerty123", "1234567890", "12345678910", "qwertyuiop",
    "йцукенгшщз", "admin12345", "orchestra2026", "оркестр2026", "parol12345", "1q2w3e4r5t",
    "password123", "qwerty12345", "iloveyou12", "1qaz2wsx3edc", "zaq12wsxcde", "q1w2e3r4t5y6",
}
# Клавиатурные «змейки» и последовательности: пароль из одной такой цепочки подбирается за секунды
WALKS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю",
         "1234567890", "0987654321", "abcdefghijklmnopqrstuvwxyz", "абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
# Слова, которые подставят первыми при атаке именно на этот продукт
COMPANY = ("orchestra", "оркестр", "orkestr", "0rchestra")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored or _DUMMY, password) and stored is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return _hasher.check_needs_rehash(stored)


def _walk(low: str) -> bool:
    """Пароль — сплошной кусок клавиатурного ряда или последовательности (с повтором и цифрами в конце)."""
    core = low.rstrip("0123456789!.?_-")
    return len(core) >= 6 and any(core in w or core in w[::-1] for w in WALKS)


def password_problem(password: str, min_length: int, login: str = "", name: str = "") -> str | None:
    """Причина отказа или None. Правила NIST 800-63B: длина и непредсказуемость важнее «спецсимволов»."""
    if len(password) < min_length:
        return f"Пароль короче {min_length} символов"
    if len(password) > 256:
        return "Пароль длиннее 256 символов"
    low = password.lower()
    if low in COMMON or len(set(low)) < 4 or low.isdigit() or _walk(low):
        return "Слишком простой пароль: его подберут по словарю первым"
    if login and len(login) >= 3 and login.lower().split("@")[0] in low:
        return "Пароль не должен содержать логин"
    if any(len(part) >= 4 and part in low for part in (name or "").lower().replace(".", " ").split()):
        return "Пароль не должен содержать имя"
    compact = low.replace(" ", "")
    for word in COMPANY:
        # Слабо, если кроме названия в пароле только цифры и знаки: «Orchestra2026!!» и т. п.
        if word in compact and len(re.sub(r"[\d\W_]+", "", compact.replace(word, ""))) < 4:
            return "Название компании с цифрами — первое, что попробуют. Возьмите фразу из нескольких слов"
    return None
