"""Настройки из окружения (префикс ORCHESTRA_) и .env рядом с проектом.

Два режима:
  desktop — лаунчер на одном компьютере: SQLite, слушаем только 127.0.0.1;
  server  — общий сервер компании: PostgreSQL, за обратным прокси с TLS.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SERVER_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRA_", env_file=(PROJECT_ROOT / ".env", SERVER_ROOT / ".env"),
        env_file_encoding="utf-8", extra="ignore",
    )

    mode: Literal["desktop", "server"] = "desktop"
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = ""                       # пусто → SQLite в data_dir
    host: str = "127.0.0.1"
    port: int = 8710
    public_url: str = ""                         # https://ai.example.com — для HSTS и проверки Origin

    # Мастер-ключ хранилища секретов: base64 32 байта. Пусто → файл в data_dir
    # (на Windows защищён DPAPI текущего пользователя).
    master_key: str = ""
    master_key_file: Path | None = None

    # Сессии
    session_idle_minutes: int = 12 * 60
    session_absolute_hours: int = 7 * 24
    cookie_secure: bool | None = None            # None → по схеме public_url

    # Вход: защита от перебора
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    password_min_length: int = 10
    # Опасные действия (права, ключи, интеграции, политики) — только если пароль вводился не дольше N минут назад
    reauth_minutes: int = 10

    # Сетевой периметр — задаётся только конфигурацией сервера (не из интерфейса: захваченная учётка
    # администратора не должна уметь открыть систему наружу).
    allowed_hosts: str = ""          # доп. имена в заголовке Host (к 127.0.0.1/localhost и хосту из public_url)
    allowed_networks: str = ""       # CIDR, с которых пускать вообще; пусто — без ограничения (desktop слушает только 127.0.0.1)
    hook_networks: str = ""          # CIDR для входящих вебхуков; пусто — любые (вебхук подписан)
    api_rate_per_minute: int = 900   # общий предел запросов с одного адреса

    # Оркестрация
    max_parallel_steps: int = 3
    max_plan_steps: int = 12
    step_retries: int = 1
    tool_iterations: int = 4

    # Инструмент http_fetch: разрешённые домены (через запятую); пусто — инструмент выключен
    fetch_allowlist: str = "www.consultant.ru,docs.cntd.ru,minstroyrf.gov.ru,zakupki.gov.ru"

    web_dist: Path = PROJECT_ROOT / "web" / "dist"
    agents_dir: Path = PROJECT_ROOT / "agents"
    # Чьим заголовкам X-Forwarded-For верить (адрес клиента для журнала и ограничения попыток).
    # В docker-compose приложение доступно только из внутренней сети — там это прокси Caddy.
    trusted_proxies: str = "127.0.0.1"
    log_level: str = "INFO"
    # Страница /api/docs грузит скрипты с CDN прямо на домене приложения — только для разработки
    api_docs: bool = False

    # Разовый токен первичной настройки (лаунчер передаёт свой; иначе генерируется и печатается)
    setup_token: str = Field(default="", repr=False)

    @field_validator("data_dir", mode="after")
    @classmethod
    def _make_data_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{(self.data_dir / 'orchestra.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")

    @property
    def fetch_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.fetch_allowlist.split(",") if d.strip()]

    @property
    def hosts(self) -> list[str]:
        """Допустимые имена в Host. Петля — всегда (проверка здоровья в контейнере, лаунчер): при DNS-rebinding
        в Host стоит имя чужого сайта, а не 127.0.0.1."""
        out = {h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()} | {"127.0.0.1", "localhost", "::1"}
        if self.public_url:
            from urllib.parse import urlparse
            out.add((urlparse(self.public_url).hostname or "").lower())
        return sorted(h for h in out if h)

    @staticmethod
    def _nets(raw: str):
        import ipaddress
        return [ipaddress.ip_network(n.strip(), strict=False) for n in raw.split(",") if n.strip()]

    @property
    def networks(self):
        return self._nets(self.allowed_networks)

    @property
    def hook_nets(self):
        return self._nets(self.hook_networks)

    @property
    def cookie_prefix(self) -> str:
        """__Host-: cookie только для этого хоста, только https, без поддоменов — защита от подмены cookie."""
        return "__Host-" if self.secure_cookies else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
