"""База знаний: разбор текста, ранжирование, права и гриф — для людей и для агентов."""
import io

from orchestra.knowledge.index import Bm25Index, _Chunk
from orchestra.knowledge.text import chunks, terms
from tests.conftest import Api
from tests.test_api_flows import PASS, add_fake_provider, add_user, ids

GOST = ("ГОСТ Р 21.101-2020. Основные требования к проектной и рабочей документации.\n\n"
        "Основная надпись выполняется по форме 3 для чертежей. Ведомость ссылочных и прилагаемых документов "
        "помещают на листе общих данных.")


def test_terms_keep_standard_numbers_and_stem():
    t = terms("Требования ГОСТ Р 21.101 к основным надписям чертежей")
    assert "21.101" in t and "гост" in t
    assert set(terms("надписи")) & set(terms("надпись")) & set(terms("надписям"))   # формы слова находят друг друга


def test_chunks_respect_size():
    text = "\n\n".join(f"Абзац {i}. " + "слово " * 40 for i in range(30))
    parts = chunks(text, size=900)
    assert len(parts) > 3 and all(len(p) <= 1500 for p in parts)


def test_bm25_filters_before_ranking():
    idx = Bm25Index()
    idx.items = [_Chunk("a", "ГОСТ", "основная надпись", 2, "u1", "internal"),
                 _Chunk("b", "Договор", "основная надпись договора", 3, "u2", "confidential")]
    for i, c in enumerate(idx.items):
        for t in terms(c.text):
            idx.postings[t].append((i, 1))
    assert [h.doc_id for h in idx.search("основная надпись", None, "secret")] == ["a", "b"]
    assert [h.doc_id for h in idx.search("основная надпись", None, "internal")] == ["a"]       # гриф
    assert [h.doc_id for h in idx.search("основная надпись", {"u2"}, "secret")] == ["b"]      # подразделение


def test_docs_respect_units_and_classification(owner: Api):
    units, roles = ids(owner)
    tender, arch = units["Тендеры и договоры"], units["Архитектура"]
    assert owner.post("/api/knowledge", {"title": "ГОСТ Р 21.101", "body": GOST, "kind": "standard",
                                         "unit_id": units["Компания"]}).status_code == 200
    assert owner.post("/api/knowledge", {"title": "Договор с заказчиком", "body": "Неустойка 0,1% за день просрочки",
                                         "unit_id": tender, "classification": "confidential"}).status_code == 200
    assert owner.post("/api/knowledge", {"title": "Регламент тендеров", "body": "Заявку визирует ГИП",
                                         "unit_id": tender}).status_code == 200
    add_user(owner, "arch1", [(arch, roles["employee"])])
    owner.login("arch1", PASS)
    titles = {d["title"] for d in owner.get("/api/knowledge").json()}
    assert titles == {"ГОСТ Р 21.101"}                          # чужой отдел и конфиденциальное не видны
    assert owner.get("/api/knowledge/search?q=основная надпись").json()[0]["title"] == "ГОСТ Р 21.101"
    assert owner.get("/api/knowledge/search?q=неустойка просрочки").json() == []
    r = owner.post("/api/knowledge", {"title": "x", "body": "y", "unit_id": arch})
    assert r.status_code == 403                                 # сотрудник не добавляет документы


def test_reference_is_attached_before_the_model_is_called(owner: Api):
    """Справка из базы подкладывается в поручение заранее — модель не обязана сама решить искать."""
    from tests.conftest import FakeProvider
    units, _ = ids(owner)
    add_fake_provider(owner)
    owner.post("/api/knowledge", {"title": "Регламент выпуска", "body": "Срок нормоконтроля — 5 рабочих дней.",
                                  "unit_id": units["Компания"]})
    agent = next(a for a in owner.get("/api/agents").json() if a["slug"] == "normcontrol")
    owner.post(f"/api/agents/{agent['id']}/try", {"input": "Какой срок нормоконтроля?", "body": "Ты нормоконтролёр"})
    first = FakeProvider.calls[-1].messages[0].content
    assert "СПРАВКА ИЗ БАЗЫ ЗНАНИЙ" in first and "5 рабочих дней" in first and "Регламент выпуска" in first


def test_upload_docx_with_table_and_reject_unknown(owner: Api):
    import docx
    d = docx.Document()
    d.add_paragraph("Регламент выпуска документации")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Нормоконтроль", "5 рабочих дней"
    buf = io.BytesIO()
    d.save(buf)
    h = {"X-CSRF-Token": owner.c.cookies.get("orchestra_csrf")}
    r = owner.c.post("/api/knowledge/upload", headers=h, data={"kind": "regulation"},
                     files={"file": ("Регламент.docx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = owner.get(f"/api/knowledge/{r.json()['id']}").json()["body"]
    assert "Нормоконтроль | 5 рабочих дней" in body
    bad = owner.c.post("/api/knowledge/upload", headers=h, files={"file": ("макрос.xlsm", b"PK\x03\x04", "x")})
    assert bad.status_code == 422 and "не поддерживается" in bad.json()["detail"]


def test_agent_reads_only_what_step_classification_allows(owner: Api):
    units, _ = ids(owner)
    add_fake_provider(owner)
    owner.post("/api/knowledge", {"title": "ГОСТ Р 21.101", "body": GOST, "unit_id": units["Компания"]})
    owner.post("/api/knowledge", {"title": "Секретный регламент штампов", "body": "Основная надпись: особый штамп",
                                  "unit_id": units["Компания"], "classification": "confidential"})
    agent = next(a for a in owner.get("/api/agents").json() if a["slug"] == "normcontrol")
    r = owner.post(f"/api/agents/{agent['id']}/try", {"input": "найди в базе основная надпись", "body": "Ты нормоконтролёр"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tools"] == ["knowledge_search", "knowledge_search"]   # справка до вызова + поиск самой моделью
    assert "ГОСТ Р 21.101" in out["text"]
    assert "Секретный" not in out["text"]                      # шаг «для служебного» — конфиденциальное не выдаётся
    r = owner.post(f"/api/agents/{agent['id']}/try", {"input": "найди в базе основная надпись", "body": "Ты нормоконтролёр",
                                                      "classification": "confidential"})
    assert "Секретный регламент штампов" in r.json()["text"]
