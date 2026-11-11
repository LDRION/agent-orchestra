"""Claude (Anthropic) через официальный SDK.

Особенности текущих моделей (claude-opus-5-5 и семейство):
  • мышление всегда адаптивное — глубину задаёт output_config.effort (у Opus 5.5 по умолчанию medium);
  • temperature/top_p/top_k API больше не принимает — не отправляем;
  • отказ по политике приходит как stop_reason="refusal" — проверяем до чтения текста;
  • серверный fallback ("default") повторяет отклонённый запрос на рекомендованной модели.
Повторы и переключение между провайдерами делает ModelRouter, поэтому max_retries=0.
"""
import time
from collections.abc import AsyncIterator

import anthropic

from orchestra.core import netguard
from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, ModelInfo, ProviderError, Usage

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# У Haiku серверного fallback нет — параметр ему не отправляем
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def _image_block(data_url: str) -> dict:
    head, _, data = data_url.partition(",")
    media = head.removeprefix("data:").split(";")[0] or "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


class ClaudeProvider(ChatProvider):
    kind = "anthropic"
    title = "Claude (Anthropic)"
    default_trust = "cloud_foreign"
    default_base_url = "https://api.anthropic.com"

    def __init__(self, base_url: str, api_key: str | None, settings: dict):
        super().__init__(base_url, api_key, settings)
        self._client: anthropic.AsyncAnthropic | None = None

    def _sdk(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self.require_key(), base_url=self.base_url, max_retries=0,
                http_client=anthropic.DefaultAsyncHttpxClient(transport=netguard.transport()))   # адрес — по сетевой политике
        return self._client

    def _params(self, req: ChatRequest) -> dict:
        messages = []
        for m in req.messages:
            if m.images:
                content = [_image_block(i) for i in m.images[:20]] + [{"type": "text", "text": m.content}]
            else:
                content = m.content
            messages.append({"role": m.role, "content": content})
        model = req.model or DEFAULT_MODEL
        params = {"model": model, "max_tokens": req.max_tokens, "messages": messages,
                  "output_config": {"effort": req.effort or self.settings.get("effort", "medium")}}
        if req.system:
            params["system"] = req.system
        if self.settings.get("refusal_fallback", True) and model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    @staticmethod
    def _error(exc: Exception) -> ProviderError:
        # от частного к общему: иначе повторяемое смешается с неповторяемым
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return ProviderError("auth", "Claude: ключ отклонён или нет доступа к модели")
        if isinstance(exc, anthropic.NotFoundError):
            return ProviderError("not_found", "Claude: модель не найдена")
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderError("rate_limit", "Claude: превышен лимит запросов")
        if isinstance(exc, anthropic.BadRequestError):
            return ProviderError("bad_request", f"Claude: запрос отклонён: {exc.message[:200]}")
        if isinstance(exc, anthropic.APITimeoutError):
            return ProviderError("timeout", "Claude: превышено время ожидания")
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderError("network", "Claude: нет соединения с API")
        if isinstance(exc, anthropic.APIStatusError):
            return ProviderError("provider", f"Claude: ошибка {exc.status_code}")
        return ProviderError("provider", f"Claude: {type(exc).__name__}")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        t0 = time.monotonic()
        try:
            msg = await self._sdk().with_options(timeout=req.timeout).beta.messages.create(**self._params(req))
        except anthropic.APIError as exc:
            raise self._error(exc) from exc
        if msg.stop_reason == "refusal":
            category = getattr(getattr(msg, "stop_details", None), "category", None)
            raise ProviderError("refusal", f"Claude отказался выполнять запрос{f' ({category})' if category else ''}")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not text:
            raise ProviderError("provider", f"Claude: пустой ответ (stop_reason={msg.stop_reason})")
        return ChatResponse(text=text, model=msg.model,
                            usage=Usage(msg.usage.input_tokens, msg.usage.output_tokens),
                            latency_ms=int((time.monotonic() - t0) * 1000), stop=msg.stop_reason or "")

    async def stream(self, req: ChatRequest) -> AsyncIterator[str]:
        try:
            async with self._sdk().with_options(timeout=req.timeout).beta.messages.stream(**self._params(req)) as s:
                async for piece in s.text_stream:
                    yield piece
                final = await s.get_final_message()
        except anthropic.APIError as exc:
            raise self._error(exc) from exc
        if final.stop_reason == "refusal":
            raise ProviderError("refusal", "Claude отказался выполнять запрос")

    async def list_models(self) -> list[ModelInfo]:
        try:
            page = await self._sdk().models.list(limit=100)
        except anthropic.APIError as exc:
            raise self._error(exc) from exc
        return [ModelInfo(id=m.id, title=getattr(m, "display_name", m.id)) for m in page.data]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
