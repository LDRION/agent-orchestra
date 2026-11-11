"""Единый интерфейс модели. Новый провайдер = класс ChatProvider + регистрация в registry.

Провайдер ничего не знает о правах, грифах и агентах — это забота ModelRouter (service.py).
Его дело: превратить ChatRequest в запрос своего API и вернуть ChatResponse или ProviderError.
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import ClassVar

from orchestra.core.redact import redact_secrets


@dataclass
class Message:
    role: str                                   # user | assistant
    content: str
    images: list[str] = field(default_factory=list)   # data URL: data:image/png;base64,...


@dataclass
class ChatRequest:
    system: str
    messages: list[Message]
    model: str = ""
    max_tokens: int = 4096
    json_mode: bool = False
    effort: str | None = None                   # low | medium | high — где поддерживается
    temperature: float | None = None            # только где API ещё принимает
    timeout: float = 180.0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatResponse:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    stop: str = ""


@dataclass
class ModelInfo:
    id: str
    title: str = ""
    size: str = ""


class ProviderError(Exception):
    """kind: config | auth | rate_limit | timeout | network | provider | refusal | bad_request | not_found."""

    RETRYABLE = {"rate_limit", "timeout", "network", "provider"}

    def __init__(self, kind: str, message: str):
        message = redact_secrets(message)                # ответ провайдера может повторить ключ или адрес с ним
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE


class ChatProvider(ABC):
    kind: ClassVar[str]
    title: ClassVar[str]
    default_trust: ClassVar[str]
    default_base_url: ClassVar[str] = ""
    needs_key: ClassVar[bool] = True
    supports_images: ClassVar[bool] = True

    def __init__(self, base_url: str, api_key: str | None, settings: dict):
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.api_key = api_key
        self.settings = settings or {}

    @abstractmethod
    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    async def stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """По умолчанию — без потока: один кусок с полным ответом."""
        resp = await self.chat(req)
        yield resp.text

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def aclose(self) -> None:
        return None

    def require_key(self) -> str:
        if self.needs_key and not self.api_key:
            raise ProviderError("config", f"{self.title}: не задан ключ API")
        return self.api_key or ""
