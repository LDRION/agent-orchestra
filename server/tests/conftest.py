"""Общие фикстуры: изолированная папка данных на каждый тест, приложение с поддельной моделью."""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from orchestra.providers.base import ChatProvider, ChatRequest, ChatResponse, ModelInfo, Usage

SETUP_TOKEN = "test-setup-token-0001"
OWNER_PASSWORD = "Arch1tect-Score-2026"


class FakeProvider(ChatProvider):
    """Детерминированная «модель»: план из первых двух агентов, ответы с эхом поручения."""
    kind = "fake"
    title = "Тестовая модель"
    default_trust = "local"
    needs_key = False
    calls: list[ChatRequest] = []
    prefer = ["architect", "editor"]          # editor читает сайты (tools.web) — проверка визы по плану

    async def chat(self, req: ChatRequest) -> ChatResponse:
        FakeProvider.calls.append(req)
        text = req.messages[-1].content
        if req.json_mode and "АГЕНТЫ:" in text:
            slugs = re.findall(r"^- ([a-z0-9\-]+) — ", text, re.M)
            pick = [s for s in self.prefer if s in slugs] or slugs[:2]
            plan = {"approach": "два шага и сводка",
                    "steps": [{"agent": pick[0], "task": "первая часть", "depends_on": []},
                              {"agent": pick[-1], "task": "вторая часть", "depends_on": [0]}]}
            return ChatResponse(text=f"```json\n{json.dumps(plan, ensure_ascii=False)}\n```", model=req.model or "fake",
                                usage=Usage(10, 20))
        if "РЕЗУЛЬТАТЫ:" in text:
            return ChatResponse(text="ИТОГ: всё сделано", model="fake", usage=Usage(5, 5))
        # Разборщик протоколов: пункт с дендропланом — задача, остальное — «без задачи»
        if "JSON Lines" in req.system and "Пункты:" in text:
            items = text.split("Пункты:", 1)[1].split("Пример формата:")[0]
            lines = []
            for m in re.finditer(r"^\s*(\d{1,3})[.)]\s+(.+)$", items, re.M):
                num, body = m.group(1), m.group(2)
                if "дендроплан" in body:
                    lines.append({"type": "task", "item": num, "quote": "Петровой М.А. подготовить откорректированный дендроплан до пятницы",
                                  "title": "Подготовить откорректированный дендроплан", "project": "ЖК «Северная Звезда»",
                                  "project_basis": "explicit", "assignee": "Петрова М.А.", "deadline_text": "до пятницы",
                                  "deadline_date": None, "priority": "medium", "priority_basis": "inferred",
                                  "priority_reason": "замечания заказчика", "risk": None, "risk_sources": [], "ambiguities": []})
                else:
                    lines.append({"type": "skip", "item": num, "quote": body[:40], "reason": "тест"})
            lines.append({"type": "end"})
            return ChatResponse(text="\n".join(json.dumps(x, ensure_ascii=False) for x in lines), model="fake", usage=Usage(20, 40))
        # «создай задачу в битрикс» — агент с инструментом интеграции предлагает запись
        if "<результат_инструмента" not in text and "bitrix.create_task" in req.system and "создай задачу в битрикс" in text:
            return ChatResponse(text=json.dumps({"tool": "bitrix.create_task", "args": {
                "title": "Подготовить визуализации", "responsible_id": "12", "deadline": "2026-11-05"}}, ensure_ascii=False),
                model="fake", usage=Usage(3, 7))
        # «найди в базе …» — агент с инструментом базы знаний сначала ищет, потом отвечает найденным
        if "<результат_инструмента" in text:
            return ChatResponse(text="Нашёл: " + text[:600], model="fake", usage=Usage(3, 7))
        if "knowledge_search" in req.system and "найди в базе" in text:
            query = text.split("найди в базе", 1)[1].strip()[:100]
            return ChatResponse(text=json.dumps({"tool": "knowledge_search", "args": {"query": query}}, ensure_ascii=False),
                                model="fake", usage=Usage(3, 7))
        return ChatResponse(text=f"Готово: {text[:60]}", model=req.model or "fake", usage=Usage(3, 7))

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-1")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from orchestra.core import crypto, ratelimit
    from orchestra.db import session
    from orchestra.providers import registry, service
    from orchestra.security import alerts
    from orchestra.settings import get_settings

    monkeypatch.setenv("ORCHESTRA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRA_SETUP_TOKEN", SETUP_TOKEN)
    monkeypatch.setenv("ORCHESTRA_MASTER_KEY", "")
    monkeypatch.setenv("ORCHESTRA_ALLOWED_HOSTS", "testserver")
    get_settings.cache_clear()
    crypto.set_vault(None)
    session._engine, session._sessionmaker = None, None
    service._cache.clear()
    ratelimit.reset_all()                          # лимиты попыток — общие на процесс; тесты изолируем
    alerts.reset()
    registry.KINDS["fake"] = FakeProvider
    FakeProvider.calls = []
    yield tmp_path
    get_settings.cache_clear()
    crypto.set_vault(None)


@pytest.fixture
def client(env):
    from orchestra.app import create_app
    with TestClient(create_app()) as c:
        yield c


class Api:
    """Клиент с CSRF-заголовком: как браузерный фронтенд."""

    def __init__(self, c: TestClient):
        self.c = c

    def _h(self):
        return {"X-CSRF-Token": self.c.cookies.get("orchestra_csrf", "")}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None):
        return self.c.post(url, json=json, headers=self._h())

    def put(self, url, json=None):
        return self.c.put(url, json=json, headers=self._h())

    def patch(self, url, json=None):
        return self.c.patch(url, json=json, headers=self._h())

    def login(self, login, password):
        self.c.cookies.clear()
        r = self.c.post("/api/auth/login", json={"login": login, "password": password})
        assert r.status_code == 200, r.text
        return r


@pytest.fixture
def owner(client) -> Api:
    r = client.post("/api/auth/setup", json={"token": SETUP_TOKEN, "login": "owner", "display_name": "Владелец",
                                             "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    return Api(client)


def wait_run(api: Api, run_id: str, until=("succeeded", "failed", "awaiting_approval", "rejected"), timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = api.get(f"/api/runs/{run_id}").json()
        if r["status"] in until:
            return r
        time.sleep(0.1)
    raise AssertionError(f"задача не дошла до {until}: {r['status']}")
