"""Сквозные сценарии через HTTP: вход, CSRF, эскалация, задачи, визы, промты, ключи, аудит."""
import json
import sqlite3

from tests.conftest import OWNER_PASSWORD, SETUP_TOKEN, Api, wait_run

PASS = "Proekt-Bim-2026!"


def ids(api: Api):
    units = {u["name"]: u["id"] for u in api.get("/api/iam/units").json()}
    roles = {r["key"]: r["id"] for r in api.get("/api/iam/roles").json()}
    return units, roles


def add_user(api: Api, login: str, memberships: list[tuple[str, str]]) -> str:
    r = api.post("/api/iam/users", {"login": login, "display_name": login.title(), "password": PASS,
                                    "memberships": [{"unit_id": u, "role_id": ro} for u, ro in memberships]})
    assert r.status_code == 200, r.text
    # временный пароль сотрудник меняет сам (сервер иначе не пустит дальше входа); в тестах — сразу и обратно на PASS
    saved = dict(api.c.cookies)
    api.login(login, PASS)
    for cur, new in ((PASS, PASS + "-tmp"), (PASS + "-tmp", PASS)):
        assert api.post("/api/auth/password", {"current": cur, "new": new}).status_code == 200
    api.c.cookies.clear()
    api.c.cookies.update(saved)
    return r.json()["id"]


def add_fake_provider(api: Api, tier="local", chain=True) -> str:
    r = api.post("/api/providers", {"name": f"Тест {tier}", "kind": "fake", "trust_tier": tier,
                                    "base_url": "https://203.0.113.10" if tier != "local" else "",
                                    "default_model": "fake-1"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    if chain:
        assert api.put("/api/system/settings/default_chain", [{"provider_id": pid, "model": ""}]).status_code == 200
    return pid


def test_setup_is_one_time_and_needs_token(client):
    bad = client.post("/api/auth/setup", json={"token": "wrong-token-000", "login": "x", "display_name": "X",
                                               "password": OWNER_PASSWORD})
    assert bad.status_code == 403
    ok = client.post("/api/auth/setup", json={"token": SETUP_TOKEN, "login": "owner", "display_name": "В",
                                              "password": OWNER_PASSWORD})
    assert ok.status_code == 200
    again = client.post("/api/auth/setup", json={"token": SETUP_TOKEN, "login": "x2", "display_name": "X",
                                                 "password": OWNER_PASSWORD})
    assert again.status_code == 409


def test_login_generic_error_and_lockout(owner, env):
    owner.c.cookies.clear()
    r = owner.c.post("/api/auth/login", json={"login": "nobody", "password": "whatever-123"})
    r2 = owner.c.post("/api/auth/login", json={"login": "owner", "password": "whatever-123"})
    assert r.status_code == r2.status_code == 401 and r.json() == r2.json()      # не выдаём, есть ли логин
    for _ in range(5):
        owner.c.post("/api/auth/login", json={"login": "owner", "password": "wrong-pass-123"})
    locked = owner.c.post("/api/auth/login", json={"login": "owner", "password": OWNER_PASSWORD})
    assert locked.status_code == 429 and "попыток" in locked.json()["detail"]
    # несуществующий логин после серии попыток отвечает так же — по ответу не понять, есть ли учётка
    for _ in range(8):
        owner.c.post("/api/auth/login", json={"login": "nobody", "password": "wrong-pass-123"})
    ghost = owner.c.post("/api/auth/login", json={"login": "nobody", "password": "wrong-pass-123"})
    assert ghost.status_code == 429 and ghost.json() == locked.json()
    rows = sqlite3.connect(env / "orchestra.db").execute("select data from audit_events where action = 'security.alert'").fetchall()
    kinds = [json.loads(r[0])["kind"] for r in rows]
    assert kinds.count("lockout") == 2                 # и настоящая блокировка, и перебор несуществующего логина


def test_csrf_origin_and_session_required(owner):
    assert owner.c.post("/api/iam/units", json={"name": "X"}).status_code == 403            # без CSRF
    r = owner.c.post("/api/iam/units", json={"name": "X"},
                     headers={"X-CSRF-Token": owner.c.cookies.get("orchestra_csrf"), "Origin": "https://evil.example"})
    assert r.status_code == 403                                                             # чужой Origin
    owner.c.cookies.clear()
    assert owner.c.get("/api/agents").status_code == 401


def test_extra_fields_rejected(owner):
    r = owner.post("/api/iam/units", {"name": "X", "is_admin": True})
    assert r.status_code == 422


def test_no_privilege_escalation(owner):
    units, roles = ids(owner)
    add_user(owner, "sec", [(units["Компания"], roles["security"])])
    add_user(owner, "ivan", [])
    sec = owner
    sec.login("sec", PASS)
    # выдать роли право, которого у самого нет
    r = sec.put(f"/api/iam/roles/{roles['security']}/grants",
                {"grants": [{"permission": "providers.manage", "effect": "allow"}]})
    assert r.status_code == 403
    # назначить человеку роль с правами шире своих
    users = {u["login"]: u["id"] for u in sec.get("/api/iam/users").json()}
    r = sec.put(f"/api/iam/users/{users['ivan']}/memberships", [{"unit_id": units["Компания"], "role_id": roles["admin_ai"]}])
    assert r.status_code == 403
    # а сужать и ставить запреты — можно
    r = sec.put(f"/api/iam/roles/{roles['auditor']}/grants",
                {"grants": [{"permission": "audit.view", "effect": "allow"}, {"permission": "runs.start", "effect": "deny"}]})
    assert r.status_code == 200
    # владельца не-владелец не трогает
    r = sec.patch(f"/api/iam/users/{users['owner']}", {"is_active": False})
    assert r.status_code == 403


def test_team_run_end_to_end(owner):
    add_fake_provider(owner)
    run = owner.post("/api/runs", {"request": "Проверь протокол совещания и составь письмо заказчику"}).json()
    done = wait_run(owner, run["id"])
    assert done["status"] == "succeeded", done
    assert len(done["steps"]) == 2 and all(s["status"] == "done" for s in done["steps"])
    assert done["steps"][1]["depends_on"] == [0]
    assert done["steps"][0]["prompt_version"] == 1                # видно, какой версией промта сделано
    assert done["result"].startswith("ИТОГ")
    events = owner.get(f"/api/runs/{run['id']}/events").text
    assert '"type": "plan"' in events and '"type": "result"' in events
    assert owner.get("/api/audit/verify").json()["ok"]


def test_external_model_needs_visa_and_four_eyes(owner):
    units, roles = ids(owner)
    arch = units["Архитектура"]
    add_fake_provider(owner, tier="cloud_foreign")
    add_user(owner, "emp", [(units["Компания"], roles["employee"])])
    add_user(owner, "boss", [(arch, roles["head"])])
    emp = owner
    emp.login("emp", PASS)
    run = emp.post("/api/runs", {"request": "Сделай концепцию планировки офиса", "unit_id": arch}).json()
    waiting = wait_run(emp, run["id"])
    assert waiting["status"] == "awaiting_approval"
    assert "providers.external" not in waiting["preapproved"]
    assert emp.get("/api/approvals/inbox").json() == []                       # свой запрос не видно во входящих
    r = emp.post(f"/api/approvals/{waiting['approval_id']}/decide", {"approve": True})
    assert r.status_code == 403
    emp.login("boss", PASS)
    inbox = emp.get("/api/approvals/inbox").json()
    assert len(inbox) == 1 and inbox[0]["permissions"][0]["key"] == "providers.external"
    assert emp.post(f"/api/approvals/{inbox[0]['id']}/decide", {"approve": True, "note": "ок"}).status_code == 200
    # второй этап: по готовому плану — инструмент «Чтение сайта» у редактора (сотруднику — по визе)
    plan_wait = wait_run(emp, run["id"], until=("awaiting_approval", "succeeded", "failed"))
    while plan_wait["status"] == "queued":
        plan_wait = wait_run(emp, run["id"], until=("awaiting_approval", "succeeded", "failed"))
    assert plan_wait["status"] == "awaiting_approval" and plan_wait["stage"] == "plan"
    second = emp.get("/api/approvals/inbox").json()
    assert [p["key"] for p in second[0]["permissions"]] == ["tools.web"]
    assert second[0]["payload"]["steps"], "визирующий видит план"
    emp.post(f"/api/approvals/{second[0]['id']}/decide", {"approve": True})
    done = wait_run(emp, run["id"], until=("succeeded", "failed"))
    assert done["status"] == "succeeded", done
    assert {"providers.external", "tools.web"} <= set(done["preapproved"])


def test_trainee_cannot_use_external_at_all(owner):
    units, roles = ids(owner)
    add_fake_provider(owner, tier="cloud_foreign")
    add_user(owner, "intern", [(units["Компания"], roles["trainee"])])
    owner.login("intern", PASS)
    run = owner.post("/api/runs", {"request": "Набросай план совещания"}).json()
    r = wait_run(owner, run["id"])
    # стажёру сама постановка — по визе; внешняя модель — запрещена: задача падает сразу с понятной причиной
    assert r["status"] == "failed" and "внешнее облако" in r["error"]


def test_confidential_data_never_reaches_foreign_cloud(owner):
    add_fake_provider(owner, tier="cloud_foreign")
    run = owner.post("/api/runs", {"request": "Разбери договор с заказчиком", "classification": "confidential"}).json()
    r = wait_run(owner, run["id"])
    assert r["status"] == "failed" and "Конфиденциально" in r["error"]


def test_confidential_agent_stopped_before_any_call(owner, monkeypatch):
    """Гриф агента (Аналитик ТЗ — «конфиденциально») поднимает гриф шага: в облако не уходит ни слова."""
    from tests.conftest import FakeProvider
    monkeypatch.setattr(FakeProvider, "prefer", ["tz-analyst"])
    add_fake_provider(owner, tier="cloud_foreign")
    run = owner.post("/api/runs", {"request": "Разбери ТЗ заказчика"}).json()
    r = wait_run(owner, run["id"])
    assert r["status"] == "failed" and "Аналитик ТЗ" in r["error"] and "Конфиденциально" in r["error"]
    assert all(s["status"] != "done" for s in r["steps"])
    step_calls = [c for c in FakeProvider.calls if "ПОРУЧЕНИЕ" in c.messages[-1].content]
    assert step_calls == []


def test_prompt_publish_goes_through_visa(owner):
    units, roles = ids(owner)
    add_user(owner, "head", [(units["Архитектура"], roles["head"])])
    agent = next(a for a in owner.get("/api/agents").json() if a["slug"] == "architect")
    owner.login("head", PASS)
    draft = owner.post(f"/api/agents/{agent['id']}/prompts", {"body": "Новая роль", "note": "короче"}).json()
    pub = owner.post(f"/api/agents/{agent['id']}/prompts/{draft['id']}/publish").json()
    assert "pending_approval" in pub
    owner.login("owner", OWNER_PASSWORD)
    assert owner.post(f"/api/approvals/{pub['pending_approval']}/decide", {"approve": True}).status_code == 200
    versions = owner.get(f"/api/agents/{agent['id']}/prompts").json()
    assert versions[0]["version"] == 2 and versions[0]["status"] == "active"
    assert versions[1]["status"] == "archived"


def test_provider_key_is_write_only(owner, env):
    r = owner.post("/api/providers", {"name": "Claude", "kind": "anthropic", "trust_tier": "cloud_foreign",
                                      "base_url": "https://api.anthropic.com", "api_key": "sk-ant-test-1234567890wxyz"})
    assert r.status_code == 200
    listed = owner.get("/api/providers").text
    assert "sk-ant-test" not in listed and "••••wxyz" in listed
    raw = sqlite3.connect(env / "orchestra.db").execute("select secret_enc from providers").fetchone()[0]
    assert raw.startswith("v1:") and "sk-ant" not in raw
    assert "sk-ant-test" not in owner.get("/api/audit").text
    assert owner.post("/api/providers", {"name": "x", "kind": "anthropic", "trust_tier": "cloud_foreign",
                                         "base_url": "http://api.anthropic.com"}).status_code == 422   # облако — только https


def test_audit_chain_detects_tampering(owner, env):
    owner.post("/api/iam/units", {"name": "Отдел"})
    assert owner.get("/api/audit/verify").json()["ok"]
    db = sqlite3.connect(env / "orchestra.db")
    try:                                                 # приложение и случайный скрипт править журнал не могут
        db.execute("update audit_events set actor_name = 'кто-то другой' where seq = 1")
        raise AssertionError("журнал аудита изменился")
    except sqlite3.DatabaseError as exc:
        assert "только добавление" in str(exc)
    db.execute("drop trigger audit_events_no_update")    # злоумышленник с доступом к файлу базы
    db.execute("update audit_events set actor_name = 'кто-то другой' where seq = 1")
    db.commit()
    v = owner.get("/api/audit/verify").json()
    assert not v["ok"] and v["broken_at"] == 1


def test_audit_detects_truncated_tail(owner, env):
    owner.post("/api/iam/units", {"name": "Отдел"})
    owner.post("/api/iam/units", {"name": "Ещё отдел"})
    assert owner.get("/api/audit/verify").json()["ok"]
    db = sqlite3.connect(env / "orchestra.db")
    db.execute("drop trigger audit_events_no_delete")
    last = db.execute("select max(seq) from audit_events").fetchone()[0]
    db.execute("delete from audit_events where seq >= ?", (last - 1,))     # «замести следы»: срезать хвост
    db.commit()
    v = owner.get("/api/audit/verify").json()
    assert not v["ok"] and "обрезан" in v["reason"]
