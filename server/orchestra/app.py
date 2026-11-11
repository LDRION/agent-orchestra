"""Сборка приложения: middleware безопасности, API, интерфейс, жизненный цикл."""
import ipaddress
import logging
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from orchestra import __version__
from orchestra.api import (agents, approvals, audit, auth, iam, integrations, knowledge, protocols, providers, runs, security,
                     system)
from orchestra.core.ratelimit import api_limiter, ip_key
from orchestra.db import session as dbs
from orchestra.db.migrate import upgrade_async
from orchestra.settings import get_settings

log = logging.getLogger("orchestra")
MAX_BODY = 2 * 1024 * 1024
MAX_UPLOAD = 16 * 1024 * 1024

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


async def bootstrap(app: FastAPI) -> None:
    from orchestra.agents.service import seed_agents
    from orchestra.iam.seed import seed_org_and_roles
    from orchestra.iam.service import count_users
    from orchestra.orchestration.engine import engine
    from orchestra.providers import registry as providers_registry
    from orchestra.tools import registry as tools_registry

    from orchestra.integrations import registry as integrations_registry
    from orchestra.integrations import service as integrations_service
    from orchestra.processes.protocols.service import engine as protocol_engine

    s = get_settings()
    await upgrade_async(s.db_url)
    providers_registry.load_plugins()
    tools_registry.load_plugins()
    integrations_registry.load_plugins()
    async with dbs.sessionmaker()() as db:
        await seed_org_and_roles(db)
        await seed_agents(db)
        await integrations_service.sync_tools(db)
        users = await count_users(db)
    await integrations_service.recover()             # прерванные записи во внешние системы — до восстановления процессов
    await protocol_engine.recover()
    app.state.setup_token = ""
    if users == 0:
        app.state.setup_token = s.setup_token or secrets.token_urlsafe(18)
        if not s.setup_token:
            log.warning("Первичная настройка: откройте интерфейс и введите токен %s", app.state.setup_token)
    interrupted = await engine.recover()
    if interrupted:
        log.warning("прервано перезапуском задач: %s", interrupted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from orchestra.integrations.base import close_http as close_integrations_http
    from orchestra.orchestration.engine import engine
    from orchestra.processes.protocols.service import engine as protocol_engine
    from orchestra.providers import http as provider_http

    dbs.init_engine()
    await bootstrap(app)
    yield
    await engine.shutdown()
    await protocol_engine.shutdown()
    await provider_http.close()
    await close_integrations_http()
    await dbs.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Оркестр · AI", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if s.api_docs else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if s.api_docs else None)
    app.state.setup_token = ""
    allowed_origins = {o for o in (s.public_url.rstrip("/"),) if o}
    hosts, networks, hook_nets = set(s.hosts), s.networks, s.hook_nets
    api_limiter.limit = s.api_rate_per_minute
    csp = CSP + ("; upgrade-insecure-requests" if s.public_url.startswith("https://") else "")

    def in_nets(ip: str, nets) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in nets)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Периметр. Host — защита от DNS-rebinding: чужой сайт, подменивший DNS на 127.0.0.1, сюда не попадёт
        host = request.headers.get("host", "").lower()
        host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host not in hosts:
            return JSONResponse({"detail": "Неизвестный адрес сервера"}, status_code=421)
        ip = request.client.host if request.client else ""
        path = request.url.path
        nets = hook_nets if path.startswith("/api/hooks/") else networks
        if nets and not in_nets(ip, nets):
            from orchestra.security import alerts
            await alerts.raise_alert("network", f"Запрос {request.method} {path[:80]} из сети вне списка разрешённых", ip=ip)
            return JSONResponse({"detail": "Доступ из этой сети закрыт"}, status_code=403)
        if path.startswith("/api/") and not api_limiter.hit(ip_key(ip)):
            return JSONResponse({"detail": "Слишком много запросов — подождите минуту"}, status_code=429,
                                headers={"Retry-After": "60"})
        # Тело больше лимита — отказ до чтения; загрузка документов — свой предел
        limit = MAX_UPLOAD if request.url.path == "/api/knowledge/upload" else MAX_BODY
        if int(request.headers.get("content-length") or 0) > limit:
            return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
        if "chunked" in request.headers.get("transfer-encoding", "").lower():   # без длины лимит не проверить
            return JSONResponse({"detail": "Нужен заголовок Content-Length"}, status_code=411)
        # Изменяющий запрос с чужого сайта — отказ (вдобавок к CSRF-токену и SameSite)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                same_host = urlparse(origin).netloc == host
                if not same_host and origin.rstrip("/") not in allowed_origins:
                    return JSONResponse({"detail": "Запрос с чужого источника"}, status_code=403)
        response: Response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/docs"):
            h["Content-Security-Policy"] = CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net") \
                .replace("style-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net") \
                .replace("img-src 'self' data:", "img-src 'self' data: fastapi.tiangolo.com")
        else:
            h["Content-Security-Policy"] = csp
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
        if s.public_url.startswith("https://"):
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", [])[1:])
        return JSONResponse({"detail": f"Неверные данные: {where} — {first.get('msg', '')}".strip(" —")},
                            status_code=422)

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        log.exception("необработанная ошибка %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)

    for r in (auth, iam, agents, knowledge, integrations, protocols, providers, runs, approvals, audit, security, system):
        app.include_router(r.router)
    app.include_router(integrations.hooks)
    app.include_router(protocols.dir_router)

    dist = s.web_dist

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Не найдено"}, status_code=404)
        if dist.exists():
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:     # без выхода за папку
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(target, headers={"Cache-Control": cache})
            index = dist / "index.html"
            if index.exists():
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Интерфейс не собран: cd web; npm run build"}, status_code=404)

    return app
