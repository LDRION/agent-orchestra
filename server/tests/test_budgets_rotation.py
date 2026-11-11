"""Дневные лимиты токенов и ротация мастер-ключа."""
import asyncio
import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import SETUP_TOKEN, OWNER_PASSWORD, Api, wait_run
from tests.test_api_flows import add_fake_provider


def test_personal_daily_limit_stops_runs(owner: Api):
    add_fake_provider(owner)
    assert owner.put("/api/system/settings/user_daily_tokens", 10).status_code == 200
    r = wait_run(owner, owner.post("/api/runs", {"request": "Сделай сводку"}).json()["id"])
    assert r["status"] == "failed" and "лимит" in r["error"]                 # план съел 30 токенов — дальше нельзя
    today = owner.get("/api/providers/usage/today").json()
    assert today["me"]["tokens"] >= 10 and today["me"]["limit"] == 10


def test_provider_limit_falls_back_to_next_model(owner: Api):
    first = add_fake_provider(owner, chain=False)
    second = owner.post("/api/providers", {"name": "Запасная", "kind": "fake", "trust_tier": "local"}).json()["id"]
    owner.patch(f"/api/providers/{first}", {"settings": {"daily_tokens": 1}})
    owner.put("/api/system/settings/default_chain", [{"provider_id": first, "model": ""}, {"provider_id": second, "model": ""}])
    r = wait_run(owner, owner.post("/api/runs", {"request": "Сделай сводку"}).json()["id"])
    assert r["status"] == "succeeded"
    assert {s["provider"] for s in r["steps"]} == {"Запасная"}               # первая исчерпала лимит на плане


def test_rotate_master_key_reencrypts_everything(env):
    from orchestra.app import create_app
    from orchestra.core import crypto, rotate
    from orchestra.providers.service import secret_purpose

    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"token": SETUP_TOKEN, "login": "owner", "display_name": "В", "password": OWNER_PASSWORD})
        api = Api(c)
        pid = api.post("/api/providers", {"name": "Claude", "kind": "anthropic", "trust_tier": "cloud_foreign",
                                          "base_url": "https://api.anthropic.com", "api_key": "sk-ant-rotate-me-123456"}).json()["id"]
        tg = api.post("/api/integrations", {"kind": "telegram", "name": "Бот", "key": "bot", "mode": "demo",
                                            "secret": "123456789:telegram-token-rotate", "settings": {"chats": ["1"]}}).json()
        assert api.get("/api/audit/verify").json()["ok"]
    old_key = (env / "master.key").read_bytes()
    print(asyncio.run(rotate.run()))
    assert (env / "master.key").read_bytes() != old_key and not (env / "master.key.new").exists()
    raw = sqlite3.connect(env / "orchestra.db").execute("select secret_enc from providers where id = ?", (pid,)).fetchone()[0]
    crypto.set_vault(None)                                   # новый ключ читается из файла
    assert crypto.get_vault().decrypt(raw, secret_purpose(pid)) == "sk-ant-rotate-me-123456"
    from orchestra.audit import anchor
    from orchestra.integrations.service import secret_purpose as integration_purpose
    raw_tg = sqlite3.connect(env / "orchestra.db").execute("select secret_enc from integrations where id = ?", (tg["id"],)).fetchone()[0]
    assert crypto.get_vault().decrypt(raw_tg, integration_purpose(tg["id"])) == "123456789:telegram-token-rotate"
    assert anchor.genuine(anchor.read())                     # метка журнала — под новым ключом
    old = crypto.Vault(crypto._read_key_file(env / "master.key"))
    assert old.decrypt(raw, secret_purpose(pid))             # (самопроверка: текущий файл = новый ключ)
