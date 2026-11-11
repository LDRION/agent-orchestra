"""Siberi AI: POST /generate {wait:false} → длинный опрос /result/{taskId}.

Siberi снимает ответ со страницы провайдера (Google «Режим ИИ»), поэтому:
  • данные уходят во внешнее облако — уровень доверия по умолчанию cloud_foreign;
  • отказ провайдера приходит как «успешный» текст — распознаём по шаблонам;
  • JSON-режима нет — формат задаёт системный промт.
"""
import asyncio
import re
import time

import httpx

from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, ProviderError, Usage
from orchestra.providers.http import client, raise_for, wrap_transport

_ERROR_PATTERNS = [
    r"не удалось (?:создать|сгенерировать|получить) ответ",
    r"Искусственному интеллекту не удалось",
    r"лимит\s+на\s+(?:ответы|запросы|сообщения)\s+(?:от\s+)?(?:ии|ai|нейросети)",
]
_SHORT_ERROR_PATTERNS = [
    r"(?:^|[\s—-])Что-то пошло не так", r"исчерпал[аи]?\s+(?:[а-яё]+\s+)?лимит", r"повторите попытку позже",
    r"quota (?:exceeded|reached)|too many requests|try again later",
]


def is_ai_error_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if any(re.search(p, t, re.I) for p in _ERROR_PATTERNS):
        return True
    return len(t) <= 300 and any(re.search(p, t, re.I) for p in _SHORT_ERROR_PATTERNS)


class SiberiProvider(ChatProvider):
    kind = "siberi"
    title = "Siberi AI"
    default_trust = "cloud_foreign"
    default_base_url = "http://127.0.0.1:5005/api/v1/ai"  # свой сервер Siberi, см. github.com/LDRION/siberi

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.require_key()}"}

    async def _start(self, body: dict) -> str:
        last = "неизвестная ошибка"
        for attempt in range(1, 4):
            try:
                resp = await client().post(f"{self.base_url}/generate", headers=self._auth(), json=body, timeout=30)
            except httpx.HTTPError as exc:
                last = wrap_transport(exc, "Siberi").message
                await asyncio.sleep(0.6 * attempt)
                continue
            if resp.status_code == 429 and attempt < 3:
                await asyncio.sleep(float(resp.headers.get("Retry-After", "3") or 3))
                continue
            raise_for(resp, "Siberi")
            task_id = resp.json().get("taskId")
            if not task_id:
                raise ProviderError("provider", "Siberi: нет taskId в ответе")
            return task_id
        raise ProviderError("network", f"Siberi недоступен: {last}")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        t0 = time.monotonic()
        # Siberi принимает одно сообщение: историю сворачиваем в текст
        parts = [m.content if m.role == "user" else f"[Ответ ассистента]\n{m.content}" for m in req.messages]
        body = {"prompt": "\n\n".join(parts), "system": req.system, "wait": False}
        images = [i for m in req.messages for i in m.images][:4]
        if len(images) == 1:
            body["image"] = images[0]
        elif images:
            body["images"] = images
        task_id = await self._start(body)
        wait_ms = int(self.settings.get("wait_ms", 4000))
        deadline = t0 + req.timeout
        while time.monotonic() < deadline:
            try:
                resp = await client().get(f"{self.base_url}/result/{task_id}", params={"waitMs": wait_ms},
                                          headers=self._auth(), timeout=wait_ms / 1000 + 10)
            except httpx.HTTPError:
                await asyncio.sleep(1.0)
                continue
            if resp.status_code != 200:
                await asyncio.sleep(1.0)
                continue
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                text = str(data.get("result") or "").strip()
                if not text:
                    raise ProviderError("provider", "Siberi: пустой ответ модели")
                if is_ai_error_text(text):
                    raise ProviderError("provider", f"Siberi: провайдер модели отказал: «{text[:140]}»")
                return ChatResponse(text=text, model=req.model or "siberi", usage=Usage(),
                                    latency_ms=int((time.monotonic() - t0) * 1000), stop="completed")
            if status == "error":
                raise ProviderError("provider", f"Siberi: {str(data.get('error') or 'ошибка задачи')[:200]}")
        raise ProviderError("timeout", "Siberi: превышено время ожидания ответа")
