"""Интеграции: чтение с правами и грифом, запись — только предложение и выполнение человеком/по визе,
входящие вебхуки с подписью, секреты не возвращаются."""
import hashlib
import hmac
import json
import time

from tests.conftest import OWNER_PASSWORD, Api, wait_run
from tests.test_api_flows import PASS, add_fake_provider, add_user, ids


def bitrix(api: Api, key="bitrix") -> dict:
    r = api.post("/api/integrations", {"kind": "bitrix24", "name": "Битрикс24 (демо)", "key": key, "mode": "demo"})
    assert r.status_code == 200, r.text
    return r.json()


def test_connectors_become_agent_tools(owner: Api):
    b = bitrix(owner)
    keys = {t["key"]: t for t in b["tools"]}
    assert keys["bitrix.create_task"]["write"] and not keys["bitrix.find_user"]["write"]
    catalog = {t["key"] for t in owner.get("/api/agents/tools").json()}
    assert {"bitrix.create_task", "bitrix.find_user", "knowledge_search"} <= catalog
    kinds = {k["kind"] for k in owner.get("/api/integrations/kinds").json()}
    assert kinds == {"bitrix24", "onec", "email", "telegram", "webhook_out", "webhook_in"}


def test_read_now_write_needs_human_and_visa(owner: Api):
    b = bitrix(owner)
    found = owner.post(f"/api/integrations/{b['id']}/call", {"action": "find_user", "args": {"query": "Петров"}}).json()
    assert found["result"]["users"][0]["last_name"] == "Петрова"
    # владелец: запись выполняется сразу — но через предложение и аудит
    done = owner.post(f"/api/integrations/{b['id']}/call", {"action": "create_task", "args": {
        "title": "Проверка", "responsible_id": "12", "deadline": "2026-11-05"}}).json()["action"]
    assert done["status"] == "executed" and done["result"]["task_id"]
    units, roles = ids(owner)
    add_user(owner, "boss", [(units["Компания"], roles["head"])])        # видит интеграции, запись — по визе
    add_user(owner, "intern", [(units["Компания"], roles["trainee"])])
    owner.login("boss", PASS)
    pending = owner.post(f"/api/integrations/{b['id']}/call", {"action": "create_task", "args": {
        "title": "Задача руководителя", "responsible_id": "12"}}).json()["action"]
    assert pending["status"] == "pending_approval"
    assert owner.get("/api/approvals/inbox").json() == []                # своё не визирует
    owner.login("owner", OWNER_PASSWORD)
    visa = owner.get("/api/approvals/inbox").json()[0]
    assert visa["kind"] == "integration_action" and visa["payload"]["args"]["title"] == "Задача руководителя"
    assert owner.post(f"/api/approvals/{visa['id']}/decide", {"approve": True}).status_code == 200
    owner.login("boss", PASS)
    mine = {a["id"]: a for a in owner.get("/api/integrations/actions/list").json()}
    assert mine[pending["id"]]["status"] == "executed"
    owner.login("intern", PASS)
    r = owner.post(f"/api/integrations/{b['id']}/call", {"action": "create_task", "args": {"title": "x", "responsible_id": "1"}})
    assert r.status_code == 403                          # стажёру — ни видеть, ни писать


def test_confidential_1c_data_stays_out_of_lower_classification(owner: Api):
    r = owner.post("/api/integrations", {"kind": "onec", "name": "1С:Бухгалтерия", "key": "onec", "mode": "demo",
                                         "settings": {"base_url": "http://192.0.2.10/buh/odata/standard.odata", "username": "ai"}})
    assert r.status_code == 200, r.text
    one = r.json()
    assert one["data_classification"] == "confidential" and one["trust_tier"] == "local"
    low = owner.post(f"/api/integrations/{one['id']}/call", {"action": "find_counterparty", "args": {"query": "Демо"}})
    assert low.status_code == 422 and "гриф" in low.json()["detail"]
    ok = owner.post(f"/api/integrations/{one['id']}/call", {"action": "find_counterparty", "args": {"query": "Демо"},
                                                            "classification": "confidential"})
    assert ok.json()["result"]["counterparties"][0]["ИНН"] == "7700000001"
    bad = owner.post(f"/api/integrations/{one['id']}/call", {"action": "list", "args": {"entity": "Catalog_Пользователи"},
                                                             "classification": "confidential"})
    assert bad.status_code == 422 and "не входит в разрешённые" in bad.json()["detail"]


def test_email_and_telegram_allowlists(owner: Api):
    mail = owner.post("/api/integrations", {"kind": "email", "name": "Почта", "key": "mail", "mode": "demo",
                                            "settings": {"host": "smtp.yandex.ru", "username": "ai@example.com",
                                                         "from_address": "ai@example.com"}}).json()
    r = owner.post(f"/api/integrations/{mail['id']}/call", {"action": "send", "args": {
        "to": "client@other.ru", "subject": "Сроки", "body": "Текст"}})
    assert r.status_code == 200 and r.json()["action"]["status"] == "failed"
    assert "не в списке разрешённых" in r.json()["action"]["error"]
    tg = owner.post("/api/integrations", {"kind": "telegram", "name": "Бот", "key": "tg", "mode": "demo",
                                          "secret": "123:abc", "settings": {"chats": ["-1001"]}}).json()
    r = owner.post(f"/api/integrations/{tg['id']}/call", {"action": "send_message", "args": {"text": "hi", "chat_id": "-999"}})
    assert r.json()["action"]["status"] == "failed"
    assert owner.post("/api/integrations", {"kind": "email", "name": "x", "key": "x1", "settings": {"evil": 1}}).status_code == 422


def test_secrets_are_write_only(owner: Api):
    owner.post("/api/integrations", {"kind": "bitrix24", "name": "Боевой", "key": "b24",
                                     "secret": "https://example.bitrix24.ru/rest/1/supersecretcode123/"})
    listed = owner.get("/api/integrations").text
    assert "supersecretcode" not in listed and "••••" in listed
    assert "supersecretcode" not in owner.get("/api/audit").text
    r = owner.post("/api/integrations", {"kind": "bitrix24", "name": "x", "key": "b25", "trust_tier": "cloud_ru",
                                         "secret": "http://example.bitrix24.ru/rest/1/x/"})
    assert r.status_code == 422                          # облако — только https


def test_agent_only_proposes_writes(owner: Api):
    add_fake_provider(owner)
    bitrix(owner)
    agent = next(a for a in owner.get("/api/agents").json() if a["slug"] == "architect")
    owner.patch(f"/api/agents/{agent['id']}", {"tools": ["knowledge_search", "bitrix.create_task"]})
    run = owner.post("/api/runs", {"request": "создай задачу в битрикс на визуализации", "agent_id": agent["id"]}).json()
    done = wait_run(owner, run["id"])
    assert done["status"] == "succeeded", done
    acts = owner.get(f"/api/integrations/actions/list?run_id={run['id']}").json()
    assert len(acts) == 1 and acts[0]["status"] == "proposed"        # агент сам ничего не создал
    assert "НЕ выполнено" in done["steps"][0]["output"]
    executed = owner.post(f"/api/integrations/actions/{acts[0]['id']}/execute").json()
    assert executed["status"] == "executed" and executed["result"]["task_id"]


def test_inbound_webhook_needs_signature(owner: Api):
    add_fake_provider(owner)
    hook = owner.post("/api/integrations", {"kind": "webhook_in", "name": "Сайт: заявка", "key": "site",
                                            "settings": {"instruction": "Оцени заявку с сайта"}}).json()
    secret, path = hook["secret_once"], hook["hook_path"]
    assert secret and owner.get("/api/integrations").json()[0].get("secret_once") is None
    body = json.dumps({"name": "Иван", "message": "Нужен проект благоустройства двора"}, ensure_ascii=False).encode()
    bad = owner.c.post(path, content=body, headers={"X-Orchestra-Signature": "sha256=00", "Content-Type": "application/json"})
    assert bad.status_code == 401
    # подпись без метки времени (как раньше) больше не принимается: её можно было бы повторить
    old = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert owner.c.post(path, content=body, headers={"X-Orchestra-Signature": old}).status_code == 401
    stale = str(int(time.time()) - 600)
    sig_stale = "sha256=" + hmac.new(secret.encode(), f"{stale}.".encode() + body, hashlib.sha256).hexdigest()
    assert owner.c.post(path, content=body, headers={"X-Orchestra-Timestamp": stale, "X-Orchestra-Signature": sig_stale}).status_code == 401
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"X-Orchestra-Timestamp": ts, "X-Orchestra-Signature": sig, "Content-Type": "application/json"}
    ok = owner.c.post(path, content=body, headers=headers)
    assert ok.status_code == 200, ok.text
    assert owner.c.post(path, content=body, headers=headers).status_code == 409          # перехваченный запрос не повторить
    run = wait_run(owner, ok.json()["run_id"])
    assert run["status"] == "succeeded" and "<событие>" in run["request"]
    assert owner.c.post("/api/hooks/nope", content=body).status_code == 404
