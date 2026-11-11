"""Манифест агента — markdown с YAML-заголовком (тот же формат, что в AI Vision):

    ---
    name: Нормоконтролёр
    slug: normcontrol
    description: Проверяет документацию на соответствие ГОСТ Р 21.101 и СПДС
    role: specialist
    department: Документооборот
    reports_to: orchestrator
    capabilities: [text]
    tools: [datetime]
    classification: internal
    ---
    Ты нормоконтролёр проектной организации. …системная роль…

Определения агентов живут в git (папка agents/), рабочее состояние — в БД.
"""
import re

import yaml

# service — служебный: его вызывает процесс (например, «Протоколы»), оркестратор в план не берёт
ROLES = ("orchestrator", "lead", "specialist", "service")
FIELDS = ("name", "slug", "description", "role", "department", "reports_to", "capabilities", "tools",
          "classification", "effort", "max_tokens")


def slugify(name: str) -> str:
    translit = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya"})
    s = re.sub(r"[^a-z0-9]+", "-", (name or "agent").lower().translate(translit)).strip("-")
    return s[:60] or "agent"


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.replace(",", " ").split() if s.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def parse(text: str) -> dict:
    text = text.lstrip("﻿")
    meta, body = {}, text.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            loaded = yaml.safe_load(parts[1]) or {}          # safe_load: манифест — недоверенный ввод
            meta = loaded if isinstance(loaded, dict) else {}
            body = parts[2].strip()
    name = str(meta.get("name") or "Агент").strip()[:120]
    return {
        "name": name,
        "slug": slugify(str(meta.get("slug") or name)),
        "description": str(meta.get("description", "")).strip()[:2000],
        "role": meta.get("role") if meta.get("role") in ROLES else (
            "lead" if "orchestrate" in _as_list(meta.get("tools")) else "specialist"),
        "department": str(meta.get("department", "")).strip(),
        "reports_to": str(meta.get("reports_to", "")).strip(),
        "capabilities": _as_list(meta.get("capabilities")),
        "tools": [t for t in _as_list(meta.get("tools")) if t != "orchestrate"],
        "classification": str(meta.get("classification", "internal")),
        "effort": str(meta.get("effort", "medium")),
        "max_tokens": int(meta.get("max_tokens", 4096) or 4096),
        "prompt": body,
    }


def render(spec: dict) -> str:
    meta = {k: spec[k] for k in FIELDS if spec.get(k) not in (None, "", [])}
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=None).strip()
    return f"---\n{front}\n---\n{str(spec.get('prompt', '')).strip()}\n"
