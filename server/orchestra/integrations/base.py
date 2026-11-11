"""Коннектор к внешней системе. Новый коннектор = класс Connector + регистрация в registry
(или entry point "orchestra.integrations" из отдельного пакета).

Коннектор описывает себя: поля настройки (форма в интерфейсе строится по ним), секрет,
действия. Каждое действие — либо чтение (выполняется сразу, с правами человека и в пределах
грифа), либо запись (агент только предлагает, выполняет человек). Права, гриф, аудит и
предложения — забота integrations.service, а не коннектора.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from orchestra.core import netguard
from orchestra.core.redact import redact_secrets


@dataclass(frozen=True)
class Field:
    key: str
    title: str
    kind: str = "text"                    # text | url | number | bool | list | select | textarea
    hint: str = ""
    required: bool = False
    default: Any = None
    options: tuple = ()


@dataclass(frozen=True)
class Action:
    key: str
    title: str
    description: str                      # для модели: когда и зачем вызывать
    write: bool = False
    args: dict = field(default_factory=dict)
    required: tuple = ()


class IntegrationError(Exception):
    """Текст уходит в интерфейс и журнал — секреты (адрес вебхука, токены) вычищаются сразу."""

    def __init__(self, message: object = ""):
        super().__init__(redact_secrets(str(message)))


_client: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False,
                                    headers={"User-Agent": "Orchestra-AI"}, transport=netguard.transport())
    return _client


async def close_http() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class Connector(ABC):
    kind: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    icon: ClassVar[str] = "plug"
    secret: ClassVar[Field | None] = None
    fields: ClassVar[list[Field]] = []
    actions: ClassVar[list[Action]] = []
    default_trust: ClassVar[str] = "private"          # куда уходят данные при записи (политика выхода данных)
    default_classification: ClassVar[str] = "internal"  # гриф данных, которые система отдаёт
    demo: ClassVar[bool] = False                      # есть демо-режим без настоящей системы

    def __init__(self, integration_id: str, settings: dict, secret: str | None, mode: str):
        self.id = integration_id
        self.settings = {**{f.key: f.default for f in self.fields}, **(settings or {})}
        self.secret_value = secret
        self.mode = mode

    def action(self, key: str) -> Action:
        for a in self.actions:
            if a.key == key:
                return a
        raise IntegrationError(f"у «{self.title}» нет действия «{key}»")

    def need_secret(self) -> str:
        if not self.secret_value:
            raise IntegrationError(f"{self.title}: не задан «{self.secret.title if self.secret else 'секрет'}»")
        return self.secret_value

    async def run(self, action: str, args: dict) -> dict:
        if self.mode == "demo":
            return await self.demo_run(action, args)
        return await self.live_run(action, args)

    async def check(self) -> str:
        """Проверка подключения: человеческое описание или IntegrationError."""
        if self.mode == "demo":
            return "демо-режим: настоящая система не вызывается"
        return await self.live_check()

    async def live_check(self) -> str:
        return "проверка не предусмотрена"

    @abstractmethod
    async def live_run(self, action: str, args: dict) -> dict: ...

    async def demo_run(self, action: str, args: dict) -> dict:
        raise IntegrationError(f"у «{self.title}» нет демо-режима")

    @staticmethod
    def to_text(result: dict, limit: int = 6000) -> str:
        """Результат для модели — компактный JSON."""
        text = json.dumps(result, ensure_ascii=False, default=str)
        return text[:limit] + (" …[обрезано]" if len(text) > limit else "")


def require_args(action: Action, args: dict) -> dict:
    if not isinstance(args, dict):
        raise IntegrationError("аргументы должны быть объектом")
    unknown = set(args) - set(action.args)
    if unknown:
        raise IntegrationError(f"лишние аргументы: {', '.join(sorted(unknown))}")
    missing = [a for a in action.required if args.get(a) in (None, "", [])]
    if missing:
        raise IntegrationError(f"не хватает: {', '.join(missing)}")
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 20000:
            raise IntegrationError(f"{k}: слишком длинно")
    return args
