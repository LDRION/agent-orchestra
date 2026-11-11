"""Общая база схем запросов: лишние поля — ошибка, а не молчаливое игнорирование."""
from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None
