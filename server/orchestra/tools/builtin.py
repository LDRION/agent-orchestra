"""Встроенные инструменты."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx

from orchestra.core import netguard
from orchestra.settings import get_settings
from orchestra.tools.base import Tool, ToolContext, ToolError, ToolSpec

MAX_BYTES = 512 * 1024
MAX_CHARS = 8000
# Москва без перехода на летнее время с 2014 г.; базы tzdata на Windows нет
MSK = timezone(timedelta(hours=3), "MSK")


class DateTimeTool(Tool):
    spec = ToolSpec("datetime", "Дата и время", "Текущие дата, время и день недели по Москве — для сроков.",
                    "tools.read", {})

    async def run(self, args: dict, ctx: ToolContext) -> str:
        now = datetime.now(MSK)
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        return f"{now:%d.%m.%Y %H:%M}, {days[now.weekday()]} (Москва)"


def _host_allowed(host: str, allow: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allow)


async def _public(host: str) -> None:
    """Ранняя понятная причина отказа; само подключение всё равно проверяет адрес (netguard.GuardedBackend)."""
    try:
        await netguard.public_ip(host)
    except netguard.NetError as exc:
        raise ToolError(str(exc)) from exc


def _to_text(body: str, ctype: str) -> str:
    if "html" in ctype:
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


class HttpFetchTool(Tool):
    spec = ToolSpec("http_fetch", "Чтение сайта",
                    "Скачать текст страницы с разрешённого сайта (нормативные базы, сайт компании, закупки).",
                    "tools.web", {"url": "полный адрес https://… страницы"})

    async def run(self, args: dict, ctx: ToolContext) -> str:
        url = str(args.get("url", "")).strip()
        allow = get_settings().fetch_domains
        if not allow:
            raise ToolError("чтение сайтов выключено администратором")
        # SSRF: только публичные адреса, проверка — в момент подключения, без системного прокси
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False,
                                     transport=netguard.transport(public_only=True),
                                     headers={"User-Agent": "Orchestra-AI"}) as client:
            for _ in range(4):                               # редиректы — вручную, с повторной проверкой
                u = urlparse(url)
                if u.scheme not in ("http", "https") or not u.hostname:
                    raise ToolError("нужен адрес http(s)://")
                if u.username or u.password:
                    raise ToolError("адреса с логином запрещены")
                if not _host_allowed(u.hostname, allow):
                    raise ToolError(f"сайт {u.hostname} не в списке разрешённых")
                await _public(u.hostname)
                try:
                    async with client.stream("GET", url) as resp:
                        if resp.is_redirect:
                            url = urljoin(url, resp.headers.get("location", ""))
                            continue
                        if resp.status_code >= 400:
                            raise ToolError(f"сайт ответил {resp.status_code}")
                        ctype = resp.headers.get("content-type", "")
                        if not any(t in ctype for t in ("text/", "json", "xml")):
                            raise ToolError(f"не текст ({ctype or 'тип неизвестен'})")
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                break
                            chunks.append(chunk)
                        text = _to_text(b"".join(chunks).decode(resp.encoding or "utf-8", "replace"), ctype)
                        return text[:MAX_CHARS] + (" …[обрезано]" if len(text) > MAX_CHARS else "")
                except httpx.HTTPError as exc:
                    raise ToolError(f"сайт недоступен: {exc}") from exc
        raise ToolError("слишком много перенаправлений")
