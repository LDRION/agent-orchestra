"""Инструмент агента. Каждый объявляет право, которое нужно ЧЕЛОВЕКУ, поставившему задачу:
агент никогда не получает больше прав, чем у инициатора (защита от «запутанного заместителя»).

Новый инструмент = класс Tool + регистрация (registry.register или entry point "orchestra.tools").
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from orchestra.core.redact import redact_secrets


@dataclass(frozen=True)
class ToolSpec:
    key: str
    title: str
    description: str                  # для модели: когда и зачем вызывать
    permission: str                   # tools.read | tools.web | tools.write
    args: dict = field(default_factory=dict)   # имя аргумента → описание (для промта и проверки)


@dataclass
class ToolContext:
    user_id: str
    run_id: str | None
    agent_slug: str
    access: Any = None                 # AccessContext инициатора — права человека, а не агента
    classification: str = "internal"   # гриф шага: выше него инструмент данных не отдаёт


class ToolError(Exception):
    def __init__(self, message: object = ""):
        super().__init__(redact_secrets(str(message)))


class Tool(ABC):
    spec: ClassVar[ToolSpec]
    # True — инструмент не действует сам, а только предлагает действие (запись во внешнюю систему):
    # выполняет его человек или визирующий
    deferred: bool = False

    def validate(self, args: dict) -> dict:
        if not isinstance(args, dict):
            raise ToolError("аргументы должны быть объектом")
        unknown = set(args) - set(self.spec.args)
        if unknown:
            raise ToolError(f"лишние аргументы: {', '.join(sorted(unknown))}")
        return args

    @abstractmethod
    async def run(self, args: dict, ctx: ToolContext) -> str: ...
