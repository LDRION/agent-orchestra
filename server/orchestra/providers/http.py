"""Общий HTTP-клиент провайдеров и разбор ошибок HTTP в ProviderError."""
import httpx

from orchestra.core import netguard
from orchestra.providers.base import ProviderError

try:  # корпоративные антивирусы подменяют сертификат — доверяем хранилищу ОС, как браузер
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0), follow_redirects=False,
                                    transport=netguard.transport(limits=httpx.Limits(max_connections=50)))
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def raise_for(resp: httpx.Response, who: str) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:240].replace("\n", " ")
    code = resp.status_code
    if code in (401, 403):
        raise ProviderError("auth", f"{who}: ключ отклонён ({code})")
    if code == 404:
        raise ProviderError("not_found", f"{who}: не найдено — проверьте модель и адрес ({body})")
    if code == 429:
        raise ProviderError("rate_limit", f"{who}: превышен лимит запросов")
    if code in (400, 413, 422):
        raise ProviderError("bad_request", f"{who}: запрос отклонён ({code}): {body}")
    raise ProviderError("provider", f"{who}: ошибка {code}: {body}")


def wrap_transport(exc: Exception, who: str) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("timeout", f"{who}: превышено время ожидания")
    return ProviderError("network", f"{who}: недоступен ({type(exc).__name__})")
