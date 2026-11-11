"""Второй фактор: TOTP (RFC 6238) — Яндекс Ключ, Google Authenticator, 1Password и т. п.

6 цифр, шаг 30 с, допуск ±1 шаг на расхождение часов. Каждый шаг принимается один раз
(last_step): перехваченный код нельзя повторить. Коды восстановления — одноразовые, храним хеши.
"""
import base64
import hashlib
import hmac
import io
import os
import secrets
import struct
import time
from urllib.parse import quote

import segno

DIGITS, PERIOD, WINDOW = 6, 30, 1
ISSUER = "Оркестр AI"


def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    num = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(num % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // PERIOD)


def verify(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Шаг, которому соответствует код, или None. Шаги не новее last_step отвергаются (повтор)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={PERIOD}"


def qr_svg(data: str) -> str:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=2, dark="#2b2b2b", light="#ffffff",
                                    xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


def recovery_codes(n: int = 8) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(n)]


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()
