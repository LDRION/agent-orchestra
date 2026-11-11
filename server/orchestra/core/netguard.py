"""Защита от SSRF: куда платформе можно ходить по сети.

  • агент (чтение сайтов) — только публичные адреса; соединение — на проверенный IP, без второго DNS-запроса;
  • модели и интеграции — можно в сеть компании (Ollama, 1С), но никогда в служебные адреса:
    метаданные облака (169.254.169.254 и др.), 0.0.0.0, multicast, зарезервированные сети.

Проверка — в момент подключения (GuardedBackend): имя разрешается один раз, проверяется и соединение
открывается ровно на этот IP; TLS (SNI и сертификат) — по исходному имени. Подмена DNS между проверкой
и запросом (DNS-rebinding) ничего не даёт. Проверка при сохранении адреса — лишь ранняя подсказка.
"""
import asyncio
import ipaddress
import socket

import httpcore
import httpx

IP = ipaddress.IPv4Address | ipaddress.IPv6Address
METADATA_NAMES = {"metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal",
                  "metadata.azure.internal"}


class NetError(Exception):
    pass


def _plain(ip: IP) -> IP:
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def service_ip(ip: IP) -> bool:
    """Адреса, куда нельзя никому: метаданные облака, «любой адрес», широковещание, резерв."""
    ip = _plain(ip)
    return ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def internal_ip(ip: IP) -> bool:
    ip = _plain(ip)
    return service_ip(ip) or ip.is_private or ip.is_loopback or not ip.is_global


async def resolve(host: str, timeout: float = 5.0) -> list[IP]:
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout)
    except (socket.gaierror, UnicodeError, TimeoutError) as exc:
        raise NetError(f"адрес {host} не найден") from exc
    return list(dict.fromkeys(ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos))


async def check_company_target(host: str) -> None:
    """Для моделей и интеграций: сеть компании можно, служебные адреса — нет. Имя, которое пока
    не разрешается (сервер выключен), пропускаем — проверены будут адреса-литералы и имена метаданных."""
    h = host.lower().rstrip(".")
    if h in METADATA_NAMES:
        raise NetError(f"{host} — служебный адрес облака")
    try:
        ips = await resolve(h, timeout=2.0)
    except NetError:
        return
    if any(service_ip(ip) for ip in ips):
        raise NetError(f"{host} ведёт в служебную сеть (метаданные облака, широковещание) — запрещено")


async def public_ip(host: str) -> IP:
    """Для агента: все адреса имени — публичные; возвращает тот, к которому подключаться."""
    ips = await resolve(host)
    if not ips or any(internal_ip(ip) for ip in ips):
        raise NetError(f"{host} ведёт во внутреннюю сеть — запрещено")
    return ips[0]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Сетевой слой httpx: каждое TCP-подключение — только на проверенный адрес."""

    def __init__(self, public_only: bool):
        self.public_only = public_only
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options=None) -> httpcore.AsyncNetworkStream:
        try:
            ips = await resolve(host, timeout=timeout or 5.0)
        except NetError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        bad = internal_ip if self.public_only else service_ip
        if not ips or any(bad(ip) for ip in ips):
            raise httpcore.ConnectError(f"{host}: адрес запрещён сетевой политикой")
        return await self._inner.connect_tcp(str(ips[0]), port, timeout=timeout, local_address=local_address,
                                             socket_options=socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise httpcore.ConnectError("unix-сокеты запрещены")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def transport(public_only: bool = False, **kw) -> httpx.AsyncHTTPTransport:
    """Транспорт httpx с проверкой адреса при подключении. public_only — только интернет (агент)."""
    t = httpx.AsyncHTTPTransport(**kw)
    t._pool._network_backend = GuardedBackend(public_only)       # httpx не даёт задать слой иначе
    return t
