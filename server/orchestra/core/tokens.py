"""Случайные токены. В БД хранится только SHA-256 токена — утечка БД не даёт войти."""
import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
