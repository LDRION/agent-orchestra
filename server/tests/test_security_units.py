"""Хранилище секретов, пароли, маскирование, политика данных, разбор плана, SSRF."""
import asyncio
import os

import pytest

from orchestra.agents.models import Agent
from orchestra.core.crypto import Vault, VaultError, mask
from orchestra.core.passwords import hash_password, password_problem, verify_password
from orchestra.core.redact import redact_pii, redact_secrets, scrub
from orchestra.orchestration.executor import parse_tool_call
from orchestra.orchestration.planner import PlanError, extract_json, parse_plan
from orchestra.providers import egress
from orchestra.tools.base import ToolContext, ToolError
from orchestra.tools.builtin import DateTimeTool, HttpFetchTool


def test_vault_roundtrip_and_binding():
    v = Vault(os.urandom(32))
    token = v.encrypt("sk-ant-secret-value", "provider:1")
    assert "secret" not in token and v.decrypt(token, "provider:1") == "sk-ant-secret-value"
    with pytest.raises(VaultError):                       # шифротекст нельзя переставить в чужую строку
        v.decrypt(token, "provider:2")
    with pytest.raises(VaultError):                       # другой мастер-ключ
        Vault(os.urandom(32)).decrypt(token, "provider:1")


def test_mask_never_shows_secret():
    assert mask("sk-ant-1234567890abcd") == "••••abcd"
    assert mask("short") == "••••"


def test_passwords():
    h = hash_password("Correct-Horse-9")
    assert verify_password(h, "Correct-Horse-9") and not verify_password(h, "wrong")
    assert not verify_password(None, "anything")
    assert password_problem("short", 10) and password_problem("qwerty123", 5)
    assert password_problem("ivanov-very-long-pass", 10, "ivanov@example.com")
    assert password_problem("Мостовой-Пролёт-77", 10) is None


def test_redaction():
    text, n = redact_pii("Звоните +7 (495) 727-05-88 или пишите info@example.com, ИНН 7701234567")
    assert "727" not in text and "example.com" not in text and n == 3
    assert "sk-ant" not in redact_secrets("ключ sk-ant-abcdefghijklmnop")
    assert scrub({"api_key": "x", "nested": {"password": "y", "ok": "z"}}) == {
        "api_key": "[скрыто]", "nested": {"password": "[скрыто]", "ok": "z"}}


def test_egress_policy():
    assert egress.check("local", "secret")[0]
    assert egress.check("cloud_foreign", "internal")[0]
    ok, why = egress.check("cloud_foreign", "confidential")
    assert not ok and "Конфиденциально" in why
    assert not egress.check("cloud_ru", "internal", {"cloud_ru": "public"})[0]
    assert egress.max_rank(["public", "confidential", "internal"]) == "confidential"


def agents():
    return [Agent(id=str(i), slug=s, name=n, tools=[]) for i, (s, n) in
            enumerate([("normcontrol", "Нормоконтролёр"), ("secretary", "Секретарь"), ("lawyer", "Юрист")])]


def test_plan_parsing_fences_names_and_renumbering():
    text = """Вот план:
```json
{"approach": "так", "steps": [
  {"agent": "secretary", "task": "протокол", "depends_on": []},
  {"agent": "ghost", "task": "лишний", "depends_on": [0]},
  {"agent": "Юрист", "task": "риски", "depends_on": [0, 1, 5, 2]},
  {"agent": "normcontrol", "task": "проверка", "depends_on": "2"}
]}
```"""
    approach, steps = parse_plan(text, agents(), max_steps=10)
    assert approach == "так" and [s.agent.slug for s in steps] == ["secretary", "lawyer", "normcontrol"]
    assert steps[1].depends_on == [0]                      # 1 выброшен, 5 и 2 — вперёд или в никуда
    assert steps[2].depends_on == [1]                      # исходный шаг 2 стал шагом 1


def test_plan_rejects_unknown_only_and_no_json():
    with pytest.raises(PlanError):
        parse_plan('{"steps": [{"agent": "ghost", "task": "x"}]}', agents(), 10)
    with pytest.raises(PlanError):
        extract_json("нет тут json")


def test_tool_call_detection_is_strict():
    assert parse_tool_call('{"tool": "datetime", "args": {}}') == ("datetime", {})
    assert parse_tool_call('```json\n{"tool": "http_fetch", "args": {"url": "https://example.com"}}\n```')[0] == "http_fetch"
    assert parse_tool_call('Итог: используйте {"tool": "x"} осторожно') is None


def test_datetime_tool_works_without_tzdata():
    out = asyncio.run(DateTimeTool().run({}, ToolContext("u", None, "a")))
    assert "Москва" in out


def test_crashing_tool_does_not_kill_step(monkeypatch):
    """Регрессия: исключение внутри инструмента роняло всю задачу «внутренней ошибкой»."""
    from types import SimpleNamespace

    from orchestra.orchestration import executor
    from orchestra.providers.base import ChatResponse
    from orchestra.tools.base import Tool, ToolSpec
    from orchestra.tools.registry import TOOLS

    class Boom(Tool):
        spec = ToolSpec("boom", "Сломанный", "", "tools.read", {})

        async def run(self, args, ctx):
            raise ValueError("сломался")

    replies = iter(['{"tool": "boom", "args": {}}', "итог без инструмента"])
    seen = []

    async def fake_complete(db, ctx, chain, req):
        seen.append(req.messages[-1].content)
        return ChatResponse(text=next(replies), model="m"), SimpleNamespace(name="p")

    async def emit(t, d):
        pass

    monkeypatch.setitem(TOOLS, "boom", Boom())
    monkeypatch.setattr(executor.models, "complete", fake_complete)
    ctx = SimpleNamespace(access=SimpleNamespace(user=SimpleNamespace(id="u")), run_id=None, classification="internal")
    agent = Agent(slug="a", name="А", max_tokens=100, effort="low")
    res = asyncio.run(executor.run_step(None, ctx, agent, "", "", "задача", [], ["boom"], [], emit))
    assert res.text == "итог без инструмента"
    assert "инструмент недоступен (ValueError)" in seen[-1]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8710/api/auth/me", "https://evil.example.com/", "file:///etc/passwd",
                                 "https://user:pass@example.com/"])
def test_http_fetch_blocks_ssrf_and_foreign_hosts(url, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FETCH_ALLOWLIST", "example.com,127.0.0.1")
    from orchestra.settings import get_settings
    get_settings.cache_clear()
    with pytest.raises(ToolError):
        asyncio.run(HttpFetchTool().run({"url": url}, ToolContext("u", None, "a")))
    get_settings.cache_clear()
