"""OpenAI и всё, что говорит на его протоколе /chat/completions: DeepSeek, OpenRouter,
YandexGPT (OpenAI-совместимый шлюз), LM Studio, vLLM, LocalAI. Различия — в настройках:
  token_param  — max_tokens | max_completion_tokens (OpenAI для новых моделей);
  auth_scheme  — Bearer | Api-Key (Yandex Cloud);
  headers      — дополнительные заголовки (например, OpenAI-Project / x-folder-id).
"""
import json
import time
from collections.abc import AsyncIterator

import httpx

from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, ModelInfo, ProviderError, Usage
from orchestra.providers.http import client, raise_for, wrap_transport


class OpenAICompatProvider(ChatProvider):
    kind = "openai_compat"
    title = "OpenAI-совместимый API"
    default_trust = "cloud_foreign"
    default_base_url = "https://api.openai.com/v1"

    @property
    def needs_key(self) -> bool:                      # локальные LM Studio / vLLM ходят без ключа
        return not self.settings.get("no_key", False)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"{self.settings.get('auth_scheme', 'Bearer')} {self.api_key}"
        for k, v in (self.settings.get("headers") or {}).items():
            if str(k).lower() not in ("authorization", "host", "content-length"):
                h[str(k)] = str(v)
        return h

    def _body(self, req: ChatRequest, stream: bool) -> dict:
        msgs = [{"role": "system", "content": req.system}] if req.system else []
        for m in req.messages:
            if m.images:
                parts = [{"type": "text", "text": m.content}]
                parts += [{"type": "image_url", "image_url": {"url": i}} for i in m.images]
                msgs.append({"role": m.role, "content": parts})
            else:
                msgs.append({"role": m.role, "content": m.content})
        body = {"model": req.model, "messages": msgs, "stream": stream,
                self.settings.get("token_param", "max_tokens"): req.max_tokens}
        if req.json_mode and self.settings.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        if req.temperature is not None and self.settings.get("temperature", True):
            body["temperature"] = req.temperature
        if req.effort and self.settings.get("reasoning_effort"):
            body["reasoning_effort"] = req.effort
        return body

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.require_key()
        t0 = time.monotonic()
        try:
            resp = await client().post(f"{self.base_url}/chat/completions", headers=self._headers(),
                                       json=self._body(req, False), timeout=req.timeout)
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, self.title) from exc
        raise_for(resp, self.title)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "content_filter":
            raise ProviderError("refusal", f"{self.title}: ответ остановлен фильтром провайдера")
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            raise ProviderError("provider", f"{self.title}: пустой ответ модели")
        u = data.get("usage") or {}
        return ChatResponse(text=text, model=data.get("model", req.model),
                            usage=Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
                            latency_ms=int((time.monotonic() - t0) * 1000), stop=choice.get("finish_reason") or "")

    async def stream(self, req: ChatRequest) -> AsyncIterator[str]:
        self.require_key()
        try:
            async with client().stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(),
                                       json=self._body(req, True), timeout=req.timeout) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise_for(resp, self.title)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    delta = ((json.loads(payload).get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, self.title) from exc

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await client().get(f"{self.base_url}/models", headers=self._headers(), timeout=8.0)
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, self.title) from exc
        raise_for(resp, self.title)
        return [ModelInfo(id=m["id"], title=m.get("id")) for m in resp.json().get("data", []) if m.get("id")]
