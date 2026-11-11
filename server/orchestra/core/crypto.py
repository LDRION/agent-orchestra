"""Хранилище секретов: ключи провайдеров и интеграций шифруются AES-256-GCM.

Мастер-ключ (KEK) не лежит в БД. Порядок поиска:
  1. ORCHESTRA_MASTER_KEY (base64, 32 байта) — для сервера: docker secret / переменная окружения;
  2. ORCHESTRA_MASTER_KEY_FILE — файл с base64-ключом;
  3. data_dir/master.key — создаётся при первом запуске. На Windows содержимое
     защищено DPAPI (CryptProtectData): расшифровать может только этот пользователь
     на этом компьютере, копия файла на другой машине бесполезна.

Формат шифротекста: "v1:" + base64(nonce12 ‖ ciphertext ‖ tag). В AAD — назначение
секрета (например "provider:<id>"), чтобы шифротекст нельзя было переставить в чужую строку.
"""
import base64
import ctypes
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "v1:"
_DPAPI_MAGIC = b"DPAPI1"


class VaultError(Exception):
    pass


def _dpapi(data: bytes, protect: bool) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise VaultError(f"DPAPI: ошибка {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _read_key_file(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(_DPAPI_MAGIC):
        raw = _dpapi(raw[len(_DPAPI_MAGIC):], protect=False)
    key = base64.b64decode(raw.strip())
    if len(key) != 32:
        raise VaultError(f"{path}: мастер-ключ должен быть 32 байта")
    return key


def _write_key_file(path: Path, key: bytes) -> None:
    payload = base64.b64encode(key)
    if sys.platform == "win32":
        payload = _DPAPI_MAGIC + _dpapi(payload, protect=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)


def load_master_key(env_key: str, key_file: Path | None, data_dir: Path) -> bytes:
    if env_key:
        key = base64.b64decode(env_key)
        if len(key) != 32:
            raise VaultError("ORCHESTRA_MASTER_KEY должен быть base64 от 32 байт")
        return key
    if key_file:
        return _read_key_file(key_file)
    path = data_dir / "master.key"
    if path.exists():
        return _read_key_file(path)
    key = AESGCM.generate_key(bit_length=256)
    _write_key_file(path, key)
    return key


class Vault:
    def __init__(self, master_key: bytes):
        self._aes = AESGCM(master_key)
        self._master = master_key

    def derive(self, purpose: str) -> bytes:
        """Отдельный ключ под задачу (подпись метки аудита и т. п.): из мастер-ключа, но не он сам."""
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode("utf-8")).derive(self._master)

    def encrypt(self, plaintext: str, purpose: str) -> str:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), purpose.encode("utf-8"))
        return PREFIX + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str, purpose: str) -> str:
        if not token.startswith(PREFIX):
            raise VaultError("неизвестный формат секрета")
        raw = base64.b64decode(token[len(PREFIX):])
        try:
            return self._aes.decrypt(raw[:12], raw[12:], purpose.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # InvalidTag: другой ключ или подмена
            raise VaultError("секрет не расшифровывается: другой мастер-ключ или подмена данных") from exc


def mask(secret: str | None) -> str:
    """Для интерфейса: секрет никогда не возвращается, только хвост."""
    if not secret:
        return ""
    return "••••" + secret[-4:] if len(secret) > 8 else "••••"


_vault: Vault | None = None


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        from orchestra.settings import get_settings
        s = get_settings()
        _vault = Vault(load_master_key(s.master_key, s.master_key_file, s.data_dir))
    return _vault


def set_vault(vault: Vault | None) -> None:
    global _vault
    _vault = vault
