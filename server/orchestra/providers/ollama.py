"""Ollama — локальные модели на этом компьютере или сервере компании. Данные никуда не уходят."""
import json
import time
from collections.abc import AsyncIterator

import httpx

from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, ModelInfo, ProviderError, Usage
from orchestra.providers.http import client, raise_for, wrap_transport


def _b64(data_url: str) -> str:
    return data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url


class OllamaProvider(ChatProvider):
    kind = "ollama"
    title = "Ollama (локально)"
    default_trust = "local"
    default_base_url = "http://127.0.0.1:11434"
    needs_key = False

    def _body(self, req: ChatRequest, stream: bool) -> dict:
        msgs = [{"role": "system", "content": req.system}] if req.system else []
        for m in req.messages:
            item = {"role": m.role, "content": m.content}
            if m.images:
                item["images"] = [_b64(i) for i in m.images]
            msgs.append(item)
        options = {"num_predict": req.max_tokens, "num_ctx": int(self.settings.get("num_ctx", 8192))}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        body = {"model": req.model, "messages": msgs, "stream": stream, "options": options,
                "keep_alive": self.settings.get("keep_alive", "30m")}
        if req.json_mode:
            body["format"] = "json"
        if self.settings.get("think") is not None:          # qwen3: think=false — без рассуждений
            body["think"] = bool(self.settings["think"])
        return body

    async def chat(self, req: ChatRequest) -> ChatResponse:
        t0 = time.monotonic()
        try:
            resp = await client().post(f"{self.base_url}/api/chat", json=self._body(req, False), timeout=req.timeout)
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, "Ollama") from exc
        raise_for(resp, "Ollama")
        data = resp.json()
        text = (data.get("message") or {}).get("content", "").strip()
        if not text:
            raise ProviderError("provider", "Ollama: пустой ответ модели")
        return ChatResponse(text=text, model=data.get("model", req.model),
                            usage=Usage(data.get("prompt_eval_count", 0), data.get("eval_count", 0)),
                            latency_ms=int((time.monotonic() - t0) * 1000), stop=data.get("done_reason", ""))

    async def stream(self, req: ChatRequest) -> AsyncIterator[str]:
        try:
            async with client().stream("POST", f"{self.base_url}/api/chat", json=self._body(req, True),
                                       timeout=req.timeout) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise_for(resp, "Ollama")
                async for line in resp.aiter_lines():
                    if line.strip():
                        chunk = json.loads(line)
                        piece = (chunk.get("message") or {}).get("content", "")
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, "Ollama") from exc

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await client().get(f"{self.base_url}/api/tags", timeout=4.0)
        except httpx.HTTPError as exc:
            raise wrap_transport(exc, "Ollama") from exc
        raise_for(resp, "Ollama")
        out = []
        for m in resp.json().get("models", []):
            size = m.get("size", 0)
            out.append(ModelInfo(id=m["name"], title=m["name"], size=f"{size / 1e9:.1f} ГБ" if size else ""))
        return out
