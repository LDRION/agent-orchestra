"""Второй фактор, сессии, политика обязательного 2FA."""
from orchestra.core import totp
from tests.conftest import OWNER_PASSWORD, Api
from tests.test_api_flows import PASS, add_user, ids

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # ASCII «12345678901234567890» из RFC 6238


def test_rfc6238_vector_and_replay():
    assert totp.code_at(RFC_SECRET, 1) == "287082"      # T=59 → 94287082 (последние 6 цифр)
    assert totp.verify(RFC_SECRET, "287 082", last_step=0, now=59) == 1
    assert totp.verify(RFC_SECRET, "287082", last_step=1, now=59) is None     # повтор того же кода
    assert totp.verify(RFC_SECRET, "000000", last_step=0, now=59) is None


def enable_2fa(api: Api) -> tuple[str, list[str]]:
    s = api.post("/api/auth/2fa/setup").json()
    assert s["qr_svg"].startswith("<svg") and s["uri"].startswith("otpauth://totp/")
    assert api.post("/api/auth/2fa/enable", {"code": "000000"}).status_code == 422
    r = api.post("/api/auth/2fa/enable", {"code": totp.code_at(s["secret"], totp.current_step())})
    assert r.status_code == 200
    return s["secret"], r.json()["recovery_codes"]


def login_first(api: Api, login: str, password: str) -> dict:
    api.c.cookies.clear()
    return api.c.post("/api/auth/login", json={"login": login, "password": password}).json()


def test_two_step_login_replay_and_recovery(owner: Api):
    secret, codes = enable_2fa(owner)
    first = login_first(owner, "owner", OWNER_PASSWORD)
    assert first["need_2fa"] and "orchestra_session" not in owner.c.cookies       # пароля мало — сессии нет
    assert owner.c.post("/api/auth/login/2fa", json={"challenge": first["challenge"], "code": "123456"}).status_code == 401
    # код текущего шага уже потрачен при включении (защита от повтора) — берём следующий
    code = totp.code_at(secret, totp.current_step() + 1)
    ok = owner.c.post("/api/auth/login/2fa", json={"challenge": first["challenge"], "code": code})
    assert ok.status_code == 200 and owner.get("/api/auth/me").status_code == 200
    again = login_first(owner, "owner", OWNER_PASSWORD)
    assert owner.c.post("/api/auth/login/2fa", json={"challenge": again["challenge"], "code": code}).status_code == 401
    assert owner.c.post("/api/auth/login/2fa", json={"challenge": again["challenge"], "code": codes[0]}).status_code == 200
    me = owner.get("/api/auth/me").json()
    assert me["mfa"]["recovery_left"] == len(codes) - 1
    again = login_first(owner, "owner", OWNER_PASSWORD)                      # код восстановления — одноразовый
    assert owner.c.post("/api/auth/login/2fa", json={"challenge": again["challenge"], "code": codes[0]}).status_code == 401
    assert owner.c.post("/api/auth/login/2fa", json={"challenge": "v1:подделка", "code": codes[1]}).status_code == 401


def test_policy_forces_2fa_for_privileged(owner: Api):
    units, roles = ids(owner)
    add_user(owner, "sec", [(units["Компания"], roles["security"])])
    add_user(owner, "emp", [(units["Компания"], roles["employee"])])
    assert owner.put("/api/system/settings/require_2fa", "privileged").status_code == 200
    # у владельца 2FA нет — после смены политики дальше /api/auth/ не пустит, пока не включит
    assert owner.get("/api/agents").status_code == 403
    enable_2fa(owner)
    assert owner.get("/api/agents").status_code == 200
    owner.login("sec", PASS)
    assert owner.get("/api/iam/users").status_code == 403 and "двухфакторный" in owner.get("/api/iam/users").json()["detail"]
    assert owner.get("/api/auth/me").json()["mfa"] == {"enabled": False, "required": True, "recovery_left": 0}
    owner.login("emp", PASS)
    assert owner.get("/api/agents").status_code == 200                        # обычному сотруднику — не требуется


def test_sessions_and_admin_reset(owner: Api):
    units, roles = ids(owner)
    uid = add_user(owner, "emp", [(units["Компания"], roles["employee"])])
    owner.login("emp", PASS)
    enable_2fa(owner)
    sessions = owner.get("/api/auth/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"]
    owner.c.cookies.clear()
    first = owner.c.post("/api/auth/login", json={"login": "owner", "password": OWNER_PASSWORD}).json()
    assert first["ok"]
    assert owner.post(f"/api/iam/users/{uid}/2fa/reset").status_code == 200
    users = {u["login"]: u for u in owner.get("/api/iam/users").json()}
    assert users["emp"]["mfa"] is False
    assert "iam.user.2fa_reset" in owner.get("/api/audit?action=iam.user").text
