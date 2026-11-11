"""Маскирование секретов и персональных данных.

1. В журналах и аудите — всегда: ключи API, токены, пароли не должны оседать в логах.
2. Перед отправкой во внешнюю модель — если у провайдера включено «маскировать ПДн»:
   телефоны, e-mail, паспорт, СНИЛС, ИНН, номера карт.
"""
import logging
import re

SECRET_PATTERNS = [
    re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{30,}"),                 # токен Telegram-бота в адресе API
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|пароль)\s*[:=]\s*\S+"),
    re.compile(r"https://[\w.-]+\.bitrix24\.\w+/rest/\d+/[A-Za-z0-9]+"),
    re.compile(r"/rest/\d+/[A-Za-z0-9]{10,}"),                    # вебхук Битрикс24 «в коробке» — любой домен
    re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^/\s@]+@"),             # логин:пароль в адресе
    re.compile(r"(?i)[?&](?:key|apikey|access_token|auth|sig|signature)=[^&\s#]+"),
]

PII_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){15,18}\d\b")),
    ("PHONE", re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")),
    ("SNILS", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b")),
    ("PASSPORT", re.compile(r"\b\d{2}\s?\d{2}\s?№?\s?\d{6}\b")),
    ("INN", re.compile(r"(?i)\bинн\s*:?\s*\d{10,12}\b")),
]

SENSITIVE_KEYS = {"password", "api_key", "secret", "token", "authorization", "webhook_url", "key"}


def redact_secrets(text: str) -> str:
    for p in SECRET_PATTERNS:
        text = p.sub("[скрыто]", text)
    return text


def redact_pii(text: str) -> tuple[str, int]:
    """→ (текст, сколько замен). Метка сохраняет смысл для модели: [ТЕЛЕФОН]."""
    total = 0
    labels = {"EMAIL": "[E-MAIL]", "CARD": "[КАРТА]", "PHONE": "[ТЕЛЕФОН]", "SNILS": "[СНИЛС]",
              "PASSPORT": "[ПАСПОРТ]", "INN": "[ИНН]"}
    for name, p in PII_PATTERNS:
        text, n = p.subn(labels[name], text)
        total += n
    return text, total


def scrub(obj):
    """Рекурсивно для словарей аудита: значения чувствительных ключей → [скрыто]."""
    if isinstance(obj, dict):
        return {k: ("[скрыто]" if str(k).lower() in SENSITIVE_KEYS and v else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return redact_secrets(obj)
    return obj


def safe_error(exc: BaseException | str, limit: int = 300) -> str:
    """Текст ошибки для интерфейса, журнала и БД: без секретов (адреса с ключами, токены) и не длиннее limit."""
    return redact_secrets(str(exc))[:limit]


class SecretFilter(logging.Filter):
    """Последний рубеж: даже если секрет попал в сообщение лога (адрес запроса, текст ошибки) — он скрыт."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 3:
            # журнал доступа — без строки запроса: в ней поисковые фразы по документам и прочие данные
            args = list(record.args)
            args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        try:
            msg = record.getMessage()
        except Exception:                                # noqa: BLE001 — кривое сообщение не роняет логирование
            return True
        clean = redact_secrets(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def guard_logging() -> None:
    f = SecretFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(f)
    logging.getLogger("httpx").setLevel(logging.WARNING)       # адреса запросов (с токенами в пути) — не в лог
