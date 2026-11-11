"""Процесс «Протоколы» целиком: справочник из Битрикс24 → разбор → проверка → отправка."""
import time

from tests.conftest import Api
from tests.test_api_flows import PASS, add_fake_provider, add_user, ids
from tests.test_integrations import bitrix


def wait_protocol(api: Api, pid: str, timeout=15.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = api.get(f"/api/protocols/{pid}").json()
        if p["status"] != "processing":
            return p
        time.sleep(0.1)
    raise AssertionError("протокол не разобрался")


def setup(owner: Api) -> tuple[dict, dict]:
    add_fake_provider(owner)
    b = bitrix(owner)
    stats = owner.post("/api/directory/import", {"integration_id": b["id"]}).json()
    assert stats["people"] == 12 and stats["projects"] == 5 and stats["departments"] == 7
    sample = owner.get("/api/protocols/sample").json()
    created = owner.post("/api/protocols", {"text": sample["text"], "meeting_date": sample["meeting_date"]}).json()
    return b, wait_protocol(owner, created["id"])


def test_full_protocol_flow(owner: Api):
    b, p = setup(owner)
    assert p["status"] == "ready", p
    assert p["meta"]["agent"] == "Разборщик протоколов" and p["meta"]["prompt_version"] == 1
    assert len(p["skipped"]) == 16 and len(p["tasks"]) == 1
    t = p["tasks"][0]
    assert t["assignee"]["status"] == "fact" and t["assignee"]["label"] == "Петрова М. А."
    assert t["deadline"]["status"] == "derived" and t["deadline"]["value"] == "2026-10-09"   # «до пятницы» от 08.10
    assert t["quote"]["status"] == "exact" and t["can_confirm"]
    base = f"/api/protocols/{p['id']}"

    # не подтверждена — не уходит
    r = owner.post(f"{base}/send", {"task_ids": [t["id"]], "integration_id": b["id"]}).json()
    assert r["results"][0]["error"] == "задача не подтверждена"
    assert owner.post(f"{base}/tasks/{t['id']}/confirm").json()["state"] == "confirmed"
    # правка существенного поля сбрасывает подтверждение
    edited = owner.patch(f"{base}/tasks/{t['id']}", {"changes": {"deadline": "2026-10-12"}}).json()
    assert edited["state"] == "draft" and edited["deadline"]["status"] == "edited"
    owner.post(f"{base}/confirm", {"task_ids": [t["id"]]})

    preview = owner.post(f"{base}/send", {"task_ids": [t["id"]], "integration_id": b["id"], "preview": True}).json()
    fields = preview["results"][0]["payload"]["fields"]
    assert fields["RESPONSIBLE_ID"] == "12" and fields["GROUP_ID"] == "12"
    assert fields["DEADLINE"] == "2026-10-12T19:00:00+03:00" and "Цитата:" in fields["DESCRIPTION"]
    sent = owner.post(f"{base}/send", {"task_ids": [t["id"]], "integration_id": b["id"]}).json()["results"][0]
    assert sent["ok"] and sent["status"] == "executed"
    after = owner.get(base).json()["tasks"][0]
    assert after["state"] == "sent" and after["external_id"]
    assert owner.patch(f"{base}/tasks/{t['id']}", {"changes": {"priority": "high"}}).status_code == 409

    # тот же протокол ещё раз — задача уже в работе: предупреждение о дубле
    sample = owner.get("/api/protocols/sample").json()
    again = wait_protocol(owner, owner.post("/api/protocols", {"text": sample["text"], "meeting_date": "2026-10-15"}).json()["id"])
    assert any(i["code"] == "duplicate" for i in again["tasks"][0]["issues"])


def test_employee_send_goes_through_visa(owner: Api):
    units, roles = ids(owner)
    add_user(owner, "emp", [(units["Компания"], roles["employee"])])
    add_user(owner, "boss", [(units["Компания"], roles["head"])])
    add_fake_provider(owner)
    b = bitrix(owner)
    owner.post("/api/directory/import", {"integration_id": b["id"]})
    owner.login("emp", PASS)
    sample = owner.get("/api/protocols/sample").json()
    p = wait_protocol(owner, owner.post("/api/protocols", {"text": sample["text"], "meeting_date": "2026-10-08"}).json()["id"])
    t = p["tasks"][0]
    owner.post(f"/api/protocols/{p['id']}/tasks/{t['id']}/confirm")
    r = owner.post(f"/api/protocols/{p['id']}/send", {"task_ids": [t["id"]], "integration_id": b["id"]}).json()["results"][0]
    assert r["status"] == "pending_approval"
    assert owner.get(f"/api/protocols/{p['id']}").json()["tasks"][0]["state"] == "approval"
    owner.login("boss", PASS)
    visa = owner.get("/api/approvals/inbox").json()[0]
    owner.post(f"/api/approvals/{visa['id']}/decide", {"approve": True})
    owner.login("emp", PASS)
    assert owner.get(f"/api/protocols/{p['id']}").json()["tasks"][0]["state"] == "sent"


def test_unknown_people_block_until_directory_has_them(owner: Api):
    add_fake_provider(owner)
    sample = owner.get("/api/protocols/sample").json()
    p = wait_protocol(owner, owner.post("/api/protocols", {"text": sample["text"], "meeting_date": "2026-10-08"}).json()["id"])
    t = p["tasks"][0]
    assert t["assignee"]["status"] == "not_found" and not t["can_confirm"]          # справочник пуст — не угадываем
    owner.post("/api/directory/items/people", {"last_name": "Петрова", "first_name": "Мария", "middle_name": "Андреевна"})
    owner.post("/api/directory/items/projects", {"name": "ЖК «Северная Звезда»", "aliases": ["Северная Звезда"]})
    owner.post(f"/api/protocols/{p['id']}/rematch")
    t = owner.get(f"/api/protocols/{p['id']}").json()["tasks"][0]
    assert t["assignee"]["status"] == "fact" and t["can_confirm"]
