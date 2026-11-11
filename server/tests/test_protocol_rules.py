"""Правила процесса «Протоколы»."""
import json
from pathlib import Path

import pytest

import orchestra.integrations
from orchestra.processes.protocols.dates import parse_deadline
from orchestra.processes.protocols.directory import Directory
from orchestra.processes.protocols.quotes import locate
from orchestra.processes.protocols.tasks import apply_edit, normalize_task, rematch

DEMO = Path(orchestra.integrations.__file__).parent / "demo" / "bitrix_directory.json"
D = Directory.from_json(json.loads(DEMO.read_text("utf-8")))
match_employee, match_project = D.match_employee, D.match_project

MEETING = "2026-10-08"  # четверг


@pytest.mark.parametrize("text,expected", [
    ("завтра", "2026-10-09"),
    ("послезавтра", "2026-10-10"),
    ("до пятницы", "2026-10-09"),
    ("до среды", "2026-10-14"),
    ("к следующей пятнице", "2026-10-16"),
    ("через неделю", "2026-10-15"),
    ("через две недели", "2026-10-22"),
    ("в течение 5 рабочих дней", "2026-10-15"),
    ("до конца месяца", "2026-10-31"),
    ("до конца недели", "2026-10-09"),
    ("до конца следующей недели", "2026-10-16"),
])
def test_relative_dates_from_meeting(text, expected):
    r = parse_deadline(text, MEETING)
    assert r["status"] == "derived" and r["value"] == expected


@pytest.mark.parametrize("text,expected", [
    ("к 14.10", "2026-10-14"),
    ("до 30.10.2026", "2026-10-30"),
    ("до 25 октября", "2026-10-25"),
])
def test_absolute_dates(text, expected):
    r = parse_deadline(text, MEETING)
    assert r["status"] == "fact" and r["value"] == expected


@pytest.mark.parametrize("text", ["на следующей неделе", "как можно скорее", "срочно",
                                  "к следующей планёрке", "после согласования с заказчиком"])
def test_vague_needs_clarification(text):
    r = parse_deadline(text, MEETING, "2026-10-15")
    assert r["status"] == "needs_clarification" and r["value"] is None


def test_relative_without_meeting_date_is_not_guessed():
    r = parse_deadline("завтра", None, "2026-10-12")
    assert r["status"] == "needs_clarification" and r["value"] is None and r["suggestion"] is None


@pytest.mark.parametrize("text,meeting", [
    ("на следующей неделе", None),      # без даты совещания AI отсчитывал бы от «сегодня»
    ("", MEETING),                       # срока в тексте нет — только «не определено»
    ("как можно скорее", MEETING),       # опоры для даты нет
    ("срочно", MEETING),
])
def test_no_invented_date_hints(text, meeting):
    r = parse_deadline(text, meeting, "2026-10-15")
    assert r["value"] is None and r["suggestion"] is None


def test_range_phrase_with_meeting_date_keeps_hint():
    r = parse_deadline("на следующей неделе", MEETING, "2026-10-15")
    assert r["status"] == "needs_clarification" and r["suggestion"] == "2026-10-15"


def test_conflicting_deadlines():
    r = parse_deadline("до 15 октября, но просили успеть до 13-го", MEETING)
    assert r["status"] == "conflict" and r["value"] is None


def test_deadline_before_meeting_is_conflict():
    assert parse_deadline("до 01.10.2026", MEETING)["status"] == "conflict"


def test_rule_beats_ai_date():
    r = parse_deadline("до пятницы", MEETING, "2026-10-16")
    assert r["value"] == "2026-10-09" and r["ai_mismatch"] == "2026-10-16"


@pytest.mark.parametrize("mention,status,value", [
    ("Петровой М.А.", "fact", "E-02"),
    ("Морозову", "fact", "E-05"),
    ("Кузнецову Андрею", "fact", "E-03"),
    ("Кузнецову", "ambiguous", None),
    ("ландшафтному отделу", "department", None),
    ("подрядчик", "external", None),
    ("Маша", "not_found", None),
    ("Иванов и Морозов", "ambiguous", None),
    (None, "missing", None),
])
def test_employee_matching(mention, status, value):
    r = match_employee(mention)
    assert r["status"] == status and r["value"] == value


def test_department_offers_head_first():
    r = match_employee("ландшафтному отделу")
    assert r["candidates"][0]["id"] == "E-04"


@pytest.mark.parametrize("mention,basis,status,value", [
    ("по Северной Звезде", "explicit", "fact", "P-101"),
    ("Сквер на Лесной", "explicit", "fact", "P-103"),
    ("Парк «Речной берег»", "context", "assumption", "P-102"),
    ("ЖК «Зелёная долина»", "explicit", "not_found", None),
])
def test_project_matching(mention, basis, status, value):
    r = match_project(mention, basis)
    assert r["status"] == status and r["value"] == value


TEXT = "1. Петровой М.А. подготовить откорректированный дендроплан до пятницы.\n2. Решили не переносить сдачу."


def test_quote_exact_and_fuzzy_and_missing():
    assert locate("подготовить откорректированный дендроплан", TEXT)["status"] == "exact"
    assert locate("Петровой подготовить откорректированный дендроплан до пятницы", TEXT)["status"] == "fuzzy"
    assert locate("Иванову согласовать смету с заказчиком", TEXT)["status"] == "not_found"


def _raw(**kw):
    base = {"type": "task", "item": "1", "quote": "Петровой М.А. подготовить откорректированный дендроплан до пятницы",
            "title": "Подготовить откорректированный дендроплан", "project": "ЖК «Северная Звезда»",
            "project_basis": "explicit", "assignee": "Петрова М.А.", "deadline_text": "до пятницы",
            "deadline_date": "2026-10-09", "priority": "high", "priority_basis": "inferred",
            "priority_reason": "замечания заказчика"}
    return {**base, **kw}


def test_complete_task_can_be_confirmed():
    t = normalize_task(_raw(), TEXT, MEETING, {}, D)
    assert t["can_confirm"], t["issues"]
    assert t["priority"]["status"] == "assumption"


def test_missing_assignee_blocks():
    t = normalize_task(_raw(assignee=None), TEXT, MEETING, {}, D)
    assert not t["can_confirm"]
    assert any(i["code"] == "assignee_missing" for i in t["issues"])


def test_invented_quote_blocks():
    t = normalize_task(_raw(quote="Иванову согласовать смету"), TEXT, MEETING, {}, D)
    assert any(i["code"] == "quote_not_found" for i in t["issues"]) and not t["can_confirm"]


def test_garbled_quote_replaced_by_protocol_text():
    # Модель вставила иероглиф вместо слога — показываем дословный фрагмент протокола
    t = normalize_task(_raw(quote="Петровой М.А. подготовить откорректированный де開роплан до пятницы"), TEXT, MEETING, {}, D)
    assert t["quote"]["status"] == "exact" and t["quote"]["corrected"]
    assert "開" not in t["quote"]["value"] and "дендроплан" in t["quote"]["value"]
    assert not any(i["code"].startswith("quote") for i in t["issues"])


def test_fuzzy_quote_span_is_tight():
    q = locate("Петровой подготовить откорректированный дендроплан до пятницы", TEXT)
    assert q["status"] == "fuzzy" and q["match"].startswith("Петровой") and q["match"].rstrip(".").endswith("пятницы")


def test_garbled_title_blocks():
    t = normalize_task(_raw(title="Подготовить де開роплан"), TEXT, MEETING, {}, D)
    assert any(i["code"] == "title_garbled" for i in t["issues"]) and not t["can_confirm"]


def test_essential_edit_changes_hash_but_risk_does_not():
    t = normalize_task(_raw(), TEXT, MEETING, {}, D)
    h = t["essential_hash"]
    t = apply_edit(t, {"risk": "может не успеть"}, TEXT, MEETING, D)
    assert t["essential_hash"] == h
    t = apply_edit(t, {"deadline": "2026-10-12"}, TEXT, MEETING, D)
    assert t["essential_hash"] != h and t["deadline"]["status"] == "edited"


def test_project_named_in_item_line_is_fact():
    text = "1. ЖК «Северная Звезда». Петровой подготовить дендроплан до пятницы.\n2. Прочее."
    raw = _raw(quote="Петровой подготовить дендроплан до пятницы", project="Северная Звезда", project_basis="context")
    assert normalize_task(raw, text, MEETING, {}, D)["project"]["status"] == "fact"
    missing = normalize_task({**raw, "project": None, "project_basis": None}, text, MEETING, {}, D)
    assert missing["project"]["value"] == "P-101"


def test_fixing_quote_unblocks_task_and_changes_hash():
    t = normalize_task(_raw(quote="Иванову согласовать смету"), TEXT, MEETING, {}, D)
    h = t["essential_hash"]
    t = apply_edit(t, {"quote": "«подготовить откорректированный дендроплан»"}, TEXT, MEETING, D)
    assert t["quote"]["status"] == "exact" and t["can_confirm"] and t["essential_hash"] != h


def test_unknown_risk_source_is_dropped():
    t = normalize_task(_raw(risk="срыв", risk_sources=["K1", "K9"]), TEXT, MEETING, {"K1": "Регламент"}, D)
    assert [s["ref"] for s in t["risk"]["sources"]] == ["K1"]
    assert any(i["code"] == "risk_bad_source" for i in t["issues"])


def test_added_employee_is_matched_and_rematched():
    t = normalize_task(_raw(assignee="Сидоров А.В.", project="Квартал «Береговой»"), TEXT, MEETING, {}, D)
    assert t["assignee"]["status"] == "not_found" and t["project"]["status"] == "not_found"
    fresh = Directory([*D.employees.values(), {"id": "x1", "last_name": "Сидоров", "first_name": "Антон",
                                               "middle_name": "Викторович", "role": "Инженер"}],
                      [*D.projects.values(), {"id": "p1", "name": "Квартал «Береговой»", "aliases": []}],
                      list(D.departments.values()))
    assert fresh.match_employee("Сидорову А.В.")["value"] == "x1"
    t = rematch(t, TEXT, MEETING, fresh)
    assert t["assignee"]["value"] == "x1" and t["project"]["value"] == "p1" and t["can_confirm"]
