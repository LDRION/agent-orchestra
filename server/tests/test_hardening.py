"""Защита снаружи и внутри: каждый маршрут за сессией и CSRF, эскалация прав, подтверждение паролем,
временные роли и запросы доступа, периметр (Host, сети), SSRF, центр безопасности, логи без секретов."""
import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import OWNER_PASSWORD, Api
from tests.test_api_flows import PASS, add_user, ids

# Без входа доступны только они: состояние, первичная настройка, вход, проверка живости, подписанные вебхуки
PUBLIC = {("GET", "/api/auth/state"), ("POST", "/api/auth/setup"), ("POST", "/api/auth/login"),
          ("POST", "/api/auth/login/2fa"), ("GET", "/api/system/health"), ("POST", "/api/hooks/{integration_id}"),
          ("GET", "/{path:path}")}


def api_routes(client: TestClient):
    for path, ops in client.app.openapi()["paths"].items():
        for m in ops:
            yield m.upper(), path


def fill(path: str) -> str:
    return "/".join("x" if part.startswith("{") else part for part in path.split("/"))


def test_every_route_needs_session_and_csrf(owner: Api):
    routes = sorted(set(api_routes(owner.c)))
    assert len(routes) > 80
    owner.c.cookies.clear()
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        r = owner.c.request(method, fill(path))
        assert r.status_code == 401, f"{method} {path} без входа → {r.status_code}"
    owner.login("owner", OWNER_PASSWORD)
    for method, path in routes:
        if method in ("GET",) or (method, path) in PUBLIC:
            continue
        r = owner.c.request(method, fill(path), json={})          # сессия есть, CSRF-заголовка нет
        assert r.status_code == 403, f"{method} {path} без CSRF → {r.status_code}"


def test_user_without_roles_sees_nothing_sensitive(owner: Api):
    add_user(owner, "nobody", [])
    owner.login("nobody", PASS)
    for url in ("/api/iam/users", "/api/iam/roles", "/api/audit", "/api/providers", "/api/integrations",
                "/api/knowledge", "/api/system/settings", "/api/security/overview", "/api/security/sessions",
                "/api/runs", "/api/agents"):
        assert owner.get(url).status_code == 403, url
    assert owner.get("/api/auth/me").status_code == 200


def test_cannot_manage_people_with_more_rights(owner: Api):
    units, roles = ids(owner)
    company = units["Компания"]
    add_user(owner, "sec", [(company, roles["security"])])
    admin = add_user(owner, "aiadmin", [(company, roles["admin_ai"])])
    worker = add_user(owner, "worker", [(units["Архитектура"], roles["employee"])])
    me = owner.get("/api/auth/me").json()["user"]["id"]
    owner.login("sec", PASS)
    # у администратора ИИ есть права на модели, которых у «Безопасности» нет — захватить его учётку нельзя
    assert owner.post(f"/api/iam/users/{admin}/password", {"password": "New-Pass-2026-x!"}).status_code == 403
    assert owner.post(f"/api/iam/users/{admin}/2fa/reset").status_code == 403
    assert owner.post(f"/api/iam/users/{me}/password", {"password": "New-Pass-2026-x!"}).status_code == 403
    assert owner.post(f"/api/iam/users/{worker}/password", {"password": "New-Pass-2026-x!"}).status_code == 200
    # свои роли — только руками другого администратора
    sec = owner.get("/api/auth/me").json()["user"]["id"]
    r = owner.put(f"/api/iam/users/{sec}/memberships", [{"unit_id": company, "role_id": roles["admin_ai"]}])
    assert r.status_code == 403
    audit = owner.get("/api/audit?action=iam.user.manage&outcome=denied").json()
    assert {"aiadmin", "owner"} <= {e["target"] for e in audit}


def test_dangerous_actions_need_fresh_password(owner: Api):
    from orchestra.settings import get_settings
    units, roles = ids(owner)
    worker = add_user(owner, "worker", [(units["Архитектура"], roles["employee"])])
    get_settings().reauth_minutes = 0                        # окно подтверждения истекло
    r = owner.post(f"/api/iam/users/{worker}/password", {"password": "New-Pass-2026-x!"})
    assert r.status_code == 403 and r.headers.get("X-Orchestra-Reauth") == "1"
    assert owner.post("/api/auth/reauth", {"password": "wrong-pass-123"}).status_code == 403
    assert owner.post("/api/auth/reauth", {"password": OWNER_PASSWORD}).json()["ok"]
    get_settings().reauth_minutes = 10
    assert owner.post(f"/api/iam/users/{worker}/password", {"password": "New-Pass-2026-x!"}).status_code == 200
    # перебор пароля внутри чужой сессии: после серии ошибок сессия закрывается
    for _ in range(6):
        owner.post("/api/auth/reauth", {"password": "wrong-pass-123"})
    assert owner.post("/api/auth/reauth", {"password": "wrong-pass-123"}).status_code == 401
    assert owner.get("/api/auth/me").status_code == 401


def test_temporary_role_expires(owner: Api, env):
    units, roles = ids(owner)
    arch = units["Архитектура"]
    worker = add_user(owner, "worker", [(arch, roles["employee"])])
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    bad = owner.put(f"/api/iam/users/{worker}/memberships", [{"unit_id": arch, "role_id": roles["head"], "expires_at": past}])
    assert bad.status_code == 422
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    r = owner.put(f"/api/iam/users/{worker}/memberships", [{"unit_id": arch, "role_id": roles["employee"]},
                                                           {"unit_id": arch, "role_id": roles["head"], "expires_at": soon}])
    assert r.status_code == 200 and any(m["expires_at"] for m in r.json()["memberships"])
    owner.login("worker", PASS)
    assert owner.get("/api/auth/me").json()["permissions"]["approvals.decide"] == "allow"
    db = sqlite3.connect(env / "orchestra.db")
    db.execute("update memberships set expires_at = '2020-01-01 00:00:00.000000' where expires_at is not null")
    db.commit()
    assert owner.get("/api/auth/me").json()["permissions"]["approvals.decide"] == "deny"


def test_access_request_is_granted_only_by_who_can_grant(owner: Api):
    units, roles = ids(owner)
    arch, company = units["Архитектура"], units["Компания"]
    add_user(owner, "worker", [(arch, roles["employee"])])
    add_user(owner, "sec", [(company, roles["security"])])
    owner.login("worker", PASS)
    opts = owner.get("/api/iam/request-options").json()
    assert any(r["id"] == roles["head"] for r in opts["roles"])
    r = owner.post("/api/iam/access-requests", {"unit_id": arch, "role_id": roles["head"], "days": 7,
                                                "reason": "замещаю руководителя в отпуске"})
    assert r.status_code == 200, r.text
    approval = r.json()["approval_id"]
    assert owner.post("/api/iam/access-requests", {"unit_id": arch, "role_id": roles["head"], "days": 7,
                                                   "reason": "ещё раз то же самое"}).status_code == 422
    assert owner.post(f"/api/approvals/{approval}/decide", {"approve": True}).status_code == 403    # свой — нельзя
    owner.login("sec", PASS)                     # «Безопасность» назначает роли, но прав руководителя у неё нет
    assert not any(a["id"] == approval for a in owner.get("/api/approvals/inbox").json())
    assert owner.post(f"/api/approvals/{approval}/decide", {"approve": True}).status_code == 403
    owner.login("owner", OWNER_PASSWORD)
    assert any(a["id"] == approval for a in owner.get("/api/approvals/inbox").json())
    assert owner.post(f"/api/approvals/{approval}/decide", {"approve": True}).status_code == 200
    owner.login("worker", PASS)
    me = owner.get("/api/auth/me").json()
    assert me["permissions"]["approvals.decide"] == "allow"


def test_unknown_host_is_rejected(client: TestClient):
    assert client.get("/api/auth/state").status_code == 200
    assert client.get("/api/auth/state", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/api/auth/state", headers={"Host": "127.0.0.1:8710"}).status_code == 200


def test_network_allowlist(env, monkeypatch):
    from orchestra.app import create_app
    from orchestra.settings import get_settings
    monkeypatch.setenv("ORCHESTRA_ALLOWED_NETWORKS", "10.0.0.0/8")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:                      # клиент тестов — не из сети компании
        assert c.get("/api/auth/state").status_code == 403
    get_settings.cache_clear()


def test_web_tools_off_for_confidential_and_webhook_runs():
    from orchestra.orchestration.engine import tool_usable
    tool = SimpleNamespace(spec=SimpleNamespace(permission="tools.web"))
    access = SimpleNamespace(decide=lambda perm, unit: SimpleNamespace(allowed=True, effect="allow"))
    assert tool_usable(tool, access, None, classification="internal")
    assert not tool_usable(tool, access, None, classification="confidential")
    assert not tool_usable(tool, access, None, classification="internal", origin="webhook")


def test_ssrf_guards(owner: Api):
    from orchestra.core import netguard
    for host in ("169.254.169.254", "metadata.google.internal", "0.0.0.0", "[fe80::1]"):
        with pytest.raises(netguard.NetError):
            asyncio.run(netguard.check_company_target(host))
    asyncio.run(netguard.check_company_target("10.0.0.5"))          # сервер 1С в сети компании — можно
    with pytest.raises(netguard.NetError):
        asyncio.run(netguard.public_ip("127.0.0.1"))
    r = owner.post("/api/providers", {"name": "x", "kind": "fake", "trust_tier": "private",
                                      "base_url": "http://169.254.169.254/latest", "default_model": "m"})
    assert r.status_code == 422


def test_security_center(owner: Api):
    units, roles = ids(owner)
    add_user(owner, "worker", [(units["Архитектура"], roles["employee"])])
    o = owner.get("/api/security/overview").json()
    assert {p["key"] for p in o["posture"]} >= {"mfa_policy", "owners", "alerts"}
    sessions = owner.get("/api/security/sessions").json()
    assert any(s["current"] for s in sessions)
    owner.login("worker", PASS)
    assert owner.get("/api/security/overview").status_code == 403
    worker_session = next(s for s in owner.get("/api/auth/sessions").json() if s["current"])["id"]
    owner.login("owner", OWNER_PASSWORD)
    assert owner.c.delete(f"/api/security/sessions/{worker_session}",
                          headers={"X-CSRF-Token": owner.c.cookies.get("orchestra_csrf")}).status_code == 200
    assert not any(s["id"] == worker_session for s in owner.get("/api/security/sessions").json())


def test_secrets_never_reach_logs(caplog):
    from orchestra.core.redact import SecretFilter
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: POST %s", (
        "https://api.telegram.org/bot1234567890:AAH-abcdefghijklmnopqrstuvwxyz012345/sendMessage",), None)
    SecretFilter().filter(rec)
    assert "AAH-abc" not in rec.getMessage()


def test_external_write_happens_once_when_two_approve_at_once(owner: Api, monkeypatch):
    from sqlalchemy import select

    from orchestra.approvals import service as approvals
    from orchestra.db.session import sessionmaker
    from orchestra.iam.authz import load_access
    from orchestra.iam.models import User
    from orchestra.integrations.connectors.bitrix24 import Bitrix24
    from tests.test_integrations import bitrix
    calls = []
    real = Bitrix24.demo_run

    async def counted(self, action, args):
        calls.append(action)
        await asyncio.sleep(0.2)
        return await real(self, action, args)
    monkeypatch.setattr(Bitrix24, "demo_run", counted)
    units, roles = ids(owner)
    arch = units["Архитектура"]
    add_user(owner, "head1", [(arch, roles["head"])])
    add_user(owner, "head2", [(arch, roles["head"])])
    b = bitrix(owner)
    owner.login("head1", PASS)
    r = owner.post(f"/api/integrations/{b['id']}/call", {"action": "create_task", "args": {
        "title": "Визуализации", "responsible_id": "12", "deadline": "2026-11-05"}})
    assert r.json()["action"]["status"] == "pending_approval", r.text
    owner.login("owner", OWNER_PASSWORD)
    aid = owner.get("/api/approvals/inbox").json()[0]["id"]

    async def both():
        async def one(login):
            async with sessionmaker()() as s:
                u = (await s.execute(select(User).where(User.login == login))).scalar()
                try:
                    await approvals.decide(s, await load_access(s, u), aid, True)
                    return "ok"
                except Exception as exc:                 # noqa: BLE001 — проигравший получает отказ или «занято»
                    return type(exc).__name__
        return await asyncio.gather(one("owner"), one("head2"))
    outcome = owner.c.portal.call(both)
    assert outcome.count("ok") == 1, outcome
    assert calls == ["create_task"]                      # запись в Битрикс24 — ровно одна


def test_outbound_clients_check_address_at_connect_time():
    import httpx

    from orchestra.core import netguard

    async def go(public_only: bool, url: str) -> str:
        async with httpx.AsyncClient(transport=netguard.transport(public_only=public_only)) as c:
            try:
                await c.get(url)
            except httpx.ConnectError as exc:
                return str(exc)
        return "ok"
    assert "запрещён" in asyncio.run(go(True, "http://127.0.0.1:9/"))           # агенту — только интернет
    assert "запрещён" in asyncio.run(go(False, "http://169.254.169.254/latest"))  # моделям — не метаданные облака
    assert "запрещён" in asyncio.run(go(True, "http://localhost:9/"))


def test_office_behind_nat_is_not_locked_out_by_successful_logins(owner: Api):
    for _ in range(40):                                  # весь офис утром с одного адреса
        owner.login("owner", OWNER_PASSWORD)
    from orchestra.core.ratelimit import ip_key
    assert ip_key("2001:db8:1:2:3:4:5:6") == ip_key("2001:db8:1:2:ffff::1") == "2001:db8:1:2::/64"
    assert ip_key("10.1.2.3") == "10.1.2.3"


def test_temporary_password_must_be_changed_before_anything_else(owner: Api):
    units, roles = ids(owner)
    r = owner.post("/api/iam/users", {"login": "fresh", "display_name": "Новенький", "password": PASS,
                                      "memberships": [{"unit_id": units["Архитектура"], "role_id": roles["employee"]}]})
    assert r.status_code == 200
    owner.login("fresh", PASS)
    assert owner.get("/api/auth/me").json()["user"]["must_change_password"]
    blocked = owner.get("/api/runs")
    assert blocked.status_code == 403 and "временный пароль" in blocked.json()["detail"]
    assert owner.post("/api/auth/password", {"current": PASS, "new": "Svoj-Parol-2026!"}).status_code == 200
    assert owner.get("/api/runs").status_code == 200


def test_errors_and_logs_carry_no_secrets():
    from orchestra.core.redact import SecretFilter, safe_error
    from orchestra.integrations.base import IntegrationError
    from orchestra.providers.base import ProviderError
    hook = "https://portal.example.com/rest/12/a1b2c3d4e5f6g7h8/tasks.task.add.json"
    assert "a1b2c3d4e5" not in str(IntegrationError(f"ответ 401 для {hook}"))           # Битрикс24 «в коробке»
    assert "s3cret" not in ProviderError("auth", "https://ai:s3cret@llm.example.com/v1 отклонил").message
    assert "AIzaXYZ" not in safe_error(Exception("GET https://x.example/v1?key=AIzaXYZ123 → 403"))
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", "/api/knowledge/search?q=договор+с+Газпромом", "1.1", 200), None)
    SecretFilter().filter(rec)
    assert "Газпром" not in rec.getMessage() and "/api/knowledge/search" in rec.getMessage()


def test_api_docs_off_by_default(client: TestClient):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_console_recovery_returns_owner_access(owner: Api, env):
    from orchestra.iam import recovery
    owner.c.cookies.clear()
    out = asyncio.run(recovery.run("owner", reset_2fa=True))
    temp = out.split(": ", 1)[1].split()[0]
    owner.login("owner", temp)
    me = owner.get("/api/auth/me").json()
    assert me["user"]["must_change_password"] and not me["mfa"]["enabled"]
    rows = sqlite3.connect(env / "orchestra.db").execute("select ip from audit_events where action = 'iam.recovery'").fetchall()
    assert rows == [("консоль сервера",)]


def test_live_stream_notices_revoked_session(owner: Api):
    from orchestra.api.runs import session_alive
    sid = next(s for s in owner.get("/api/auth/sessions").json() if s["current"])["id"]
    assert owner.c.portal.call(session_alive, sid)
    owner.post("/api/auth/logout")
    assert not owner.c.portal.call(session_alive, sid)


def test_smtp_host_cannot_point_at_cloud_metadata(owner: Api):
    r = owner.post("/api/integrations", {"kind": "email", "name": "Почта", "key": "mail", "mode": "demo",
                                         "settings": {"host": "169.254.169.254", "username": "ai", "from_address": "ai@example.com"}})
    assert r.status_code == 422


def test_encrypted_backup_and_restore_keep_audit_consistent(env, tmp_path_factory):
    from orchestra.app import create_app
    from orchestra.audit import anchor
    from orchestra.audit.service import verify
    from orchestra.core import backup
    from orchestra.db.session import init_engine
    from tests.conftest import SETUP_TOKEN
    copy = tmp_path_factory.mktemp("copies") / "orchestra.orchbak"
    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"token": SETUP_TOKEN, "login": "owner", "display_name": "В", "password": OWNER_PASSWORD})
        print(backup.create(copy, "Ochen-dlinnyj-parol"))
        Api(c).post("/api/iam/units", {"name": "Создан после копии"})
    raw = copy.read_bytes()
    assert raw.startswith(backup.MAGIC) and b"SQLite format 3" not in raw and b"owner" not in raw   # внутри — шифротекст
    with pytest.raises(SystemExit):
        backup.restore(copy, "neverno-neverno")
    backup.restore(copy, "Ochen-dlinnyj-parol")
    names = [r[0] for r in sqlite3.connect(env / "orchestra.db").execute("select name from org_units")]
    assert "Создан после копии" not in names
    init_engine()
    assert not asyncio.run(verify())["ok"]                    # без переподписи откат базы виден
    asyncio.run(anchor.reanchor("тест"))
    assert asyncio.run(verify())["ok"]


def test_weak_passwords_are_refused():
    from orchestra.core.passwords import password_problem
    for weak in ("1234567890123", "qwertyuiop123", "йцукенгшщз2026", "Orchestra2026!!", "ivanova-2026-x"):
        assert password_problem(weak, 10, "ivanova", "Иванова Ivanova") is not None, weak
    assert password_problem("Kirpich-Fasad-Ataka-7", 10, "ivanova", "Иванова А.") is None


def test_security_center_shows_privilege_changes(owner: Api):
    units, roles = ids(owner)
    add_user(owner, "worker", [(units["Архитектура"], roles["employee"])])
    actions = {e["action"] for e in owner.get("/api/security/overview").json()["changes"]}
    assert "iam.user.create" in actions
