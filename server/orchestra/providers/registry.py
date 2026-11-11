"""Реестр типов провайдеров. Сторонний пакет добавляет свой через entry point:

    [project.entry-points."orchestra.providers"]
    gigachat = "my_pkg.gigachat:GigaChatProvider"
"""
import logging
from importlib.metadata import entry_points

from orchestra.providers.base import ChatProvider
from orchestra.providers.claude import ClaudeProvider
from orchestra.providers.ollama import OllamaProvider
from orchestra.providers.openai_compat import OpenAICompatProvider
from orchestra.providers.siberi import SiberiProvider

log = logging.getLogger("orchestra.providers")

KINDS: dict[str, type[ChatProvider]] = {
    c.kind: c for c in (OllamaProvider, OpenAICompatProvider, ClaudeProvider, SiberiProvider)
}

# Готовые пресеты для формы «Подключить модель»: тип + адрес + настройки по умолчанию
PRESETS = [
    {"id": "ollama", "title": "Ollama — локальные модели", "kind": "ollama", "trust": "local",
     "base_url": "http://127.0.0.1:11434", "model": "qwen3:8b", "settings": {"think": False}, "key": False},
    {"id": "lmstudio", "title": "LM Studio — локально", "kind": "openai_compat", "trust": "local",
     "base_url": "http://127.0.0.1:1234/v1", "model": "", "settings": {"no_key": True}, "key": False},
    {"id": "vllm", "title": "vLLM — свой сервер", "kind": "openai_compat", "trust": "private",
     "base_url": "http://gpu.local:8000/v1", "model": "", "settings": {"no_key": True}, "key": False},
    {"id": "anthropic", "title": "Claude (Anthropic)", "kind": "anthropic", "trust": "cloud_foreign",
     "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5",
     "settings": {"effort": "medium", "refusal_fallback": True}, "key": True},
    {"id": "openai", "title": "OpenAI", "kind": "openai_compat", "trust": "cloud_foreign",
     "base_url": "https://api.openai.com/v1", "model": "",
     "settings": {"token_param": "max_completion_tokens", "temperature": False, "reasoning_effort": True}, "key": True},
    {"id": "siberi", "title": "Siberi AI", "kind": "siberi", "trust": "cloud_foreign",
     "base_url": "http://127.0.0.1:5005/api/v1/ai", "model": "siberi", "settings": {}, "key": True},
    {"id": "yandex", "title": "YandexGPT (OpenAI-совместимый)", "kind": "openai_compat", "trust": "cloud_ru",
     "base_url": "https://llm.api.cloud.yandex.net/v1", "model": "gpt://<folder_id>/yandexgpt/latest",
     "settings": {"auth_scheme": "Api-Key"}, "key": True},
    {"id": "deepseek", "title": "DeepSeek", "kind": "openai_compat", "trust": "cloud_foreign",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "settings": {}, "key": True},
    {"id": "openrouter", "title": "OpenRouter", "kind": "openai_compat", "trust": "cloud_foreign",
     "base_url": "https://openrouter.ai/api/v1", "model": "", "settings": {}, "key": True},
]


def load_plugins() -> None:
    for ep in entry_points(group="orchestra.providers"):
        try:
            cls = ep.load()
            if issubclass(cls, ChatProvider) and cls.kind not in KINDS:
                KINDS[cls.kind] = cls
                log.info("провайдер-плагин %s (%s)", cls.kind, ep.value)
        except Exception:
            log.exception("не удалось загрузить провайдер-плагин %s", ep.name)


def kinds_meta() -> list[dict]:
    return [{"kind": k, "title": c.title, "default_trust": c.default_trust, "default_base_url": c.default_base_url}
            for k, c in KINDS.items()]
