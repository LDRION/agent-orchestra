"""Политика выхода данных: какой гриф задачи можно отправлять в модель какого уровня доверия.

Уровень доверия провайдера (trust tier):
  local          — на этом компьютере или в сети компании (Ollama, LM Studio);
  private        — свой сервер / частное облако по договору (vLLM на арендованном GPU);
  cloud_ru       — облако в РФ (YandexGPT, GigaChat);
  cloud_foreign  — зарубежные облака (OpenAI, Claude, Siberi → Google).

Гриф (classification) задачи/агента: public < internal < confidential < secret.
Политика — максимальный гриф для каждого уровня; меняется в настройках (system.settings).
"""
TIERS = ["local", "private", "cloud_ru", "cloud_foreign"]
EXTERNAL = {"cloud_ru", "cloud_foreign"}
CLASSIFICATIONS = ["public", "internal", "confidential", "secret"]

TIER_TITLES = {"local": "Локально", "private": "Частный сервер", "cloud_ru": "Облако РФ",
               "cloud_foreign": "Зарубежное облако"}
CLASS_TITLES = {"public": "Открыто", "internal": "Для служебного пользования",
                "confidential": "Конфиденциально", "secret": "Строго конфиденциально"}

DEFAULT_POLICY = {"local": "secret", "private": "confidential", "cloud_ru": "internal", "cloud_foreign": "internal"}


def rank(classification: str) -> int:
    return CLASSIFICATIONS.index(classification) if classification in CLASSIFICATIONS else len(CLASSIFICATIONS)


def max_rank(classifications: list[str]) -> str:
    """Самый строгий гриф из набора: задача наследует гриф агента и входных данных."""
    known = [c for c in classifications if c in CLASSIFICATIONS]
    return max(known, key=rank) if known else "internal"


def check(tier: str, classification: str, policy: dict | None = None) -> tuple[bool, str]:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    if tier not in TIERS:
        return False, f"неизвестный уровень доверия «{tier}»"
    ceiling = policy.get(tier, "public")
    if rank(classification) <= rank(ceiling):
        return True, ""
    return False, (f"гриф «{CLASS_TITLES.get(classification, classification)}» нельзя отправлять "
                   f"в «{TIER_TITLES[tier]}» (предел: «{CLASS_TITLES.get(ceiling, ceiling)}»)")
