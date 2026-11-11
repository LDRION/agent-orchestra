"""Скользящее окно в памяти: перебор паролей и злоупотребления — по IP и по ключу.
Для нескольких процессов реализация меняется на Redis без правок у вызывающих."""
import ipaddress
import time
from collections import defaultdict, deque

MAX_KEYS = 50_000
ALL: list["RateLimiter"] = []


class RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        ALL.append(self)

    def _fresh(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def full(self, key: str) -> bool:
        """Лимит исчерпан? Без учёта попытки — для счёта только неудач (вход)."""
        return key in self._hits and len(self._fresh(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> bool:
        """True — можно. False — лимит исчерпан."""
        now = time.monotonic()
        q = self._fresh(key, now)
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > MAX_KEYS:
            self._trim(now)
        return True

    def _trim(self, now: float) -> None:
        """Перебор множеством ключей не должен съесть память: сначала устаревшие, потом самые старые."""
        for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
            self._hits.pop(k, None)
        if len(self._hits) > MAX_KEYS:
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[:len(self._hits) - MAX_KEYS * 4 // 5]:
                self._hits.pop(k, None)


def ip_key(ip: str) -> str:
    """IPv6 — по сети /64: у одного абонента их миллионы, по адресу лимит обходится сменой адреса."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return ip
    if addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


def reset_all() -> None:
    for lim in ALL:
        lim._hits.clear()


login_limiter = RateLimiter(limit=30, window_s=300)        # неудачные входы по IP (офис за NAT — успешные не считаем)
account_limiter = RateLimiter(limit=8, window_s=900)       # неудачные входы по логину — одинаково для существующих и нет
run_limiter = RateLimiter(limit=30, window_s=60)
upload_limiter = RateLimiter(limit=12, window_s=60)        # разбор файлов — тяжёлая операция
reauth_limiter = RateLimiter(limit=6, window_s=900)        # подтверждение паролем — по человеку
hook_limiter = RateLimiter(limit=120, window_s=60)         # входящие вебхуки — по интеграции
api_limiter = RateLimiter(limit=900, window_s=60)          # общий предел запросов с одного адреса
