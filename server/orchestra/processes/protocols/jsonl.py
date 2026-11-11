"""Разбор JSON-объектов из текста модели — в том числе пока он ещё пишется."""
import json
import re


def _objects(text: str) -> list[str]:
    """Все сбалансированные {...} верхнего уровня, с учётом строк и экранирования."""
    found, depth, start, in_str, esc = [], 0, -1, False, False
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0:
                found.append(text[start:i + 1])
    return found


def _load(raw: str) -> dict | None:
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def _expand(obj: dict) -> list[dict]:
    """Если модель вернула один объект с массивами — раскладываем в строки."""
    if "type" in obj:
        return [obj]
    out = []
    for key, kind in (("decisions", "decision"), ("tasks", "task"), ("skipped", "skip"), ("skip", "skip")):
        for item in obj.get(key) or []:
            if isinstance(item, dict):
                out.append({"type": kind, **item})
    return out


def parse_all(text: str) -> list[dict]:
    items = []
    for raw in _objects(text):
        obj = _load(raw)
        if obj:
            items.extend(_expand(obj))
    return items


class Incremental:
    """Отдаёт только новые завершённые объекты по мере роста текста."""

    def __init__(self):
        self.emitted = 0

    def feed(self, text: str) -> list[dict]:
        raws = _objects(text)
        new = raws[self.emitted:]
        self.emitted = len(raws)
        items = []
        for raw in new:
            obj = _load(raw)
            if obj:
                items.extend(_expand(obj))
        return items
