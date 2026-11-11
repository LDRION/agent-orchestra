"""Справочник сотрудников, отделов и проектов + сопоставление упоминаний из протокола.

Логика сопоставления
та же, но справочник — объект из БД платформы (таблицы dir_*), а не глобальный JSON.
Формат записей:
  сотрудник: id, last_name, first_name, middle_name, role, department, external_id (id в Bitrix24)
  отдел:     id, name, aliases, head
  проект:    id, name, aliases, stage, external_id (id группы Bitrix24)
"""
from .text import norm, stems, tokens

# Слова, которые не отличают один проект от другого
GENERIC = {"жк", "парк", "сквер", "на", "улиц", "общественн", "пространств", "жил", "комплекс", "проект", "по"}
EXTERNAL = ("подрядчик", "генподряд", "субподряд", "заказчик", "поставщик", "питомник", "экспертиз", "сетев")
SURNAME_SUFFIXES = {"", "а", "у", "ом", "ым", "е", "ой", "ою", "ы", "ей", "ую", "ых"}
FIRST_SUFFIXES = {"", "а", "я", "у", "ю", "е", "и", "ой", "ей", "ем", "ом", "ью", "й", "ы"}
# Имена с беглой гласной
FIRST_FORMS = {"павел": {"павел", "павла", "павлу", "павлом", "павле"}}


def employee_label(e: dict) -> str:
    first = f" {e['first_name'][0]}." if e.get("first_name") else ""
    middle = f" {e['middle_name'][0]}." if e.get("middle_name") else ""
    return f"{e['last_name']}{first}{middle}"


def employee_brief(e: dict) -> dict:
    return {"id": e["id"], "label": employee_label(e), "hint": e.get("role") or ""}


def project_brief(p: dict) -> dict:
    return {"id": p["id"], "label": p["name"], "hint": p.get("stage") or ""}


def _surname_stem(last: str) -> str:
    s = norm(last)
    return s[:-1] if s.endswith(("а", "я")) else s


def _is_surname_form(token: str, last: str) -> bool:
    base = _surname_stem(last)
    return token.startswith(base) and token[len(base):] in SURNAME_SUFFIXES


def _is_first_form(token: str, first: str) -> bool:
    f = norm(first)
    if f in FIRST_FORMS:
        return token in FIRST_FORMS[f]
    base = f[:-1] if f[-1] in "аяйьо" else f
    return token.startswith(base) and token[len(base):] in FIRST_SUFFIXES


def _result(status: str, mention: str | None, value=None, label=None, candidates=None, note=""):
    return {"status": status, "value": value, "label": label, "mention": mention,
            "candidates": candidates or [], "note": note}


class Directory:
    def __init__(self, employees: list[dict], projects: list[dict], departments: list[dict]):
        self.employees = {e["id"]: e for e in employees}
        self.projects = {p["id"]: p for p in projects}
        self.departments = {d["id"]: d for d in departments}

    @classmethod
    def from_json(cls, data: dict) -> "Directory":
        """Формат выгрузки справочника (bitrix_user_id/bitrix_group_id → external_id)."""
        emps = [{**e, "external_id": str(e.get("bitrix_user_id") or e.get("external_id") or "")} for e in data["employees"]]
        projs = [{**p, "external_id": str(p.get("bitrix_group_id") or p.get("external_id") or "")} for p in data["projects"]]
        return cls(emps, projs, data.get("departments", []))

    def _looks_like_first_name(self, token: str) -> bool:
        return any(len(e.get("first_name", "")) > 1 and _is_first_form(token, e["first_name"])
                   for e in self.employees.values())

    def _person_fits(self, e: dict, rest: list[str]) -> bool:
        """Не противоречат ли имя/инициалы найденной фамилии."""
        initials = [t for t in rest if len(t) == 1]
        if initials and e.get("first_name") and initials[0] != norm(e["first_name"])[0]:
            return False
        if len(initials) > 1 and e.get("middle_name") and initials[1] != norm(e["middle_name"])[0]:
            return False
        for t in rest:
            if len(t) > 1 and len(e.get("first_name", "")) > 1 and self._looks_like_first_name(t) \
                    and not _is_first_form(t, e["first_name"]):
                return False
        return True

    def match_department(self, mention: str) -> dict | None:
        m = set(stems(mention))
        for d in self.departments.values():
            for alias in [d["name"], *d.get("aliases", [])]:
                a = set(stems(alias))
                if a and a <= m:
                    return d
        return None

    def match_employee(self, mention: str | None) -> dict:
        if not mention or not mention.strip():
            return _result("missing", None, note="в протоколе не указан")
        toks = tokens(mention)

        by_surname: dict[str, list[dict]] = {}
        for e in self.employees.values():
            if any(_is_surname_form(t, e["last_name"]) for t in toks):
                by_surname.setdefault(norm(e["last_name"]), []).append(e)

        if len(by_surname) > 1:
            people = [e for group in by_surname.values() for e in group]
            return _result("ambiguous", mention, candidates=[employee_brief(e) for e in people],
                           note="указано несколько человек — выберите одного ответственного")

        if by_surname:
            group = next(iter(by_surname.values()))
            surname = norm(group[0]["last_name"])
            rest = [t for t in toks if not _is_surname_form(t, surname)]
            fits = [e for e in group if self._person_fits(e, rest)]
            if len(fits) == 1:
                e = fits[0]
                return _result("fact", mention, e["id"], employee_label(e), note="найден в справочнике")
            pool = fits or group
            return _result("ambiguous", mention, candidates=[employee_brief(e) for e in pool],
                           note=f"в справочнике несколько сотрудников с фамилией {group[0]['last_name']}")

        dept = self.match_department(mention)
        if dept:
            members = sorted((e for e in self.employees.values() if e.get("department") == dept["id"]),
                             key=lambda e: e["id"] != dept.get("head"))
            head = self.employees.get(dept.get("head") or "")
            tail = f"; руководитель — {employee_label(head)}" if head else ""
            return _result("department", mention, candidates=[employee_brief(e) for e in members],
                           note=f"указан отдел «{dept['name']}», а не человек{tail}")

        by_first = [e for e in self.employees.values() if len(e.get("first_name", "")) > 1
                    and any(_is_first_form(t, e["first_name"]) for t in toks)]
        if by_first:
            return _result("ambiguous", mention, candidates=[employee_brief(e) for e in by_first],
                           note="указано только имя — подтвердите сотрудника")

        if any(t.startswith(EXTERNAL) for t in toks):
            return _result("external", mention, note="внешний участник — нужен сотрудник, который проконтролирует")
        return _result("not_found", mention, note="нет в справочнике сотрудников")

    def match_project(self, mention: str | None, basis: str | None = None) -> dict:
        if not mention or not mention.strip():
            return _result("missing", None, note="в протоколе не указан")
        m = set(stems(mention)) | set(tokens(mention))
        hits = []
        for p in self.projects.values():
            for alias in [p["name"], *p.get("aliases", [])]:
                a = set(stems(alias)) - GENERIC
                if a and a <= m:
                    hits.append(p)
                    break
        if len(hits) == 1:
            p = hits[0]
            if basis == "context":
                return _result("assumption", mention, p["id"], p["name"],
                               note="проект определён по контексту протокола, а не назван в пункте")
            return _result("fact", mention, p["id"], p["name"], note="найден в справочнике")
        if hits:
            return _result("ambiguous", mention, candidates=[project_brief(p) for p in hits],
                           note="подходят несколько проектов")
        return _result("not_found", mention, note="нет в справочнике проектов")

    def public(self) -> dict:
        return {"projects": list(self.projects.values()), "departments": list(self.departments.values()),
                "employees": [{**e, "label": employee_label(e)} for e in self.employees.values()]}
