"""Задача из ответа модели → проверенная структура: факты, предположения, пробелы.

Справочник передаётся параметром (Directory из БД)."""
import hashlib
import json
import re
from datetime import date

from . import dates
from .directory import Directory, employee_label
from .quotes import locate
from .text import norm

PRIORITY_MARKERS = ("срочн", "критичн", "в первую очередь", "приоритет", "горит", "немедленн", "asap", "важно")
PRIORITY_ALIASES = {"high": "high", "высокий": "high", "medium": "medium", "средний": "medium",
                    "normal": "medium", "low": "low", "низкий": "low"}
PRIORITY_RU = {"high": "высокий", "medium": "средний", "low": "низкий"}
FACT_LIKE = {"fact", "derived", "edited", "assumption"}
ESSENTIAL = ("title", "project", "assignee", "deadline", "priority")
# Сбой модели: иероглиф или «�» вместо слога в русском тексте
GARBLED = re.compile(r"[　-鿿가-힯�]")


def _s(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in ("", "null", "none", "не указан", "не указано", "-") else value


def _priority(raw: dict, quote_text: str) -> dict:
    value = PRIORITY_ALIASES.get(norm(_s(raw.get("priority")) or ""), "medium")
    reason = _s(raw.get("priority_reason")) or ""
    explicit = _s(raw.get("priority_basis")) == "explicit"
    marker = next((m for m in PRIORITY_MARKERS if m in norm(quote_text)), None)
    if explicit and marker:
        return {"value": value, "status": "fact", "reason": reason or f"в протоколе: «{marker}…»"}
    return {"value": value, "status": "assumption", "reason": reason or "оценка AI по смыслу пункта"}


def _risk(raw: dict, refs: dict[str, str]) -> dict:
    value = _s(raw.get("risk"))
    sources, dropped = [], []
    for ref in raw.get("risk_sources") or []:
        ref = str(ref).strip("[] ")
        (sources if ref in refs else dropped).append(ref)
    return {"value": value, "status": "assumption" if value else "missing",
            "reason": _s(raw.get("risk_reason")) or "",
            "sources": [{"ref": r, "title": refs[r]} for r in sources],
            "dropped_sources": dropped}


def _quote(value: str, text: str) -> dict:
    """Почти точная цитата (модель исказила символы) заменяется фрагментом протокола — он и есть источник."""
    q = locate(value, text)
    if q["status"] == "fuzzy" and q["score"] >= 0.9:
        return {"value": q["match"], **locate(q["match"], text), "ai_value": value, "corrected": True}
    return {"value": value, **q}


def normalize_task(raw: dict, text: str, meeting: str | None, refs: dict[str, str], d: Directory) -> dict:
    q = _quote(_s(raw.get("quote")) or "", text)
    quote_for_markers = q["match"] or q["value"]
    task = {
        "kind": "task",
        "item": _s(raw.get("item")),
        "title": {"value": _s(raw.get("title")) or "", "status": "ai"},
        "project": d.match_project(_s(raw.get("project")), _s(raw.get("project_basis"))),
        "assignee": d.match_employee(_s(raw.get("assignee"))),
        "deadline": dates.parse_deadline(_s(raw.get("deadline_text")), meeting, _s(raw.get("deadline_date"))),
        "priority": _priority(raw, quote_for_markers),
        "risk": _risk(raw, refs),
        "quote": q,
        "ambiguities": [a for a in (raw.get("ambiguities") or []) if _s(a)],
        "edited": [],
    }
    _project_from_line(task, q, text, d)
    return validate(task, text, meeting)


def _project_from_line(task: dict, q: dict, text: str, d: Directory) -> None:
    """Если проект назван в той же строке протокола, что и цитата, — это факт, а не догадка."""
    if q["start"] is None or task["project"]["status"] not in ("assumption", "missing"):
        return
    start = text.rfind("\n", 0, q["start"]) + 1
    end = text.find("\n", q["end"])
    in_line = d.match_project(text[start:len(text) if end == -1 else end], "explicit")
    if in_line["status"] != "fact":
        return
    if task["project"]["status"] == "missing" or in_line["value"] == task["project"]["value"]:
        task["project"].update(value=in_line["value"], label=in_line["label"], status="fact",
                               mention=task["project"]["mention"] or in_line["label"],
                               note="проект назван в этом пункте протокола")


def normalize_decision(raw: dict, text: str, d: Directory) -> dict:
    return {"kind": "decision", "item": _s(raw.get("item")), "text": _s(raw.get("text")) or "",
            "project": d.match_project(_s(raw.get("project")), _s(raw.get("project_basis"))),
            "quote": _quote(_s(raw.get("quote")) or "", text)}


def normalize_skip(raw: dict, text: str) -> dict:
    return {"kind": "skip", "item": _s(raw.get("item")), "reason": _s(raw.get("reason")) or "",
            "quote": _quote(_s(raw.get("quote")) or "", text)}


def normalize_items(items: list[dict], text: str, meeting: str | None, refs: dict[str, str], d: Directory) -> dict:
    out = {"tasks": [], "decisions": [], "skipped": [], "unknown": []}
    for raw in items:
        kind = raw.get("type")
        if kind == "task":
            out["tasks"].append(normalize_task(raw, text, meeting, refs, d))
        elif kind == "decision":
            out["decisions"].append(normalize_decision(raw, text, d))
        elif kind == "skip":
            out["skipped"].append(normalize_skip(raw, text))
        elif kind not in ("end", "meta"):
            out["unknown"].append(raw)
    return out


# ── Проверка ───────────────────────────────────────────────────────────────────

def _issue(field: str, level: str, code: str, message: str) -> dict:
    return {"field": field, "level": level, "code": code, "message": message}


def essential_hash(task: dict) -> str:
    """Существенные поля: их изменение после подтверждения требует нового подтверждения."""
    payload = [task["title"]["value"].strip(), task["project"]["value"], task["assignee"]["value"],
               task["deadline"]["value"], task["priority"]["value"], task["quote"]["value"].strip()]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def _ref_issues(field: str, ref: dict, what: str) -> list[dict]:
    st, mention = ref["status"], ref.get("mention")
    if ref["value"]:
        if st == "assumption":
            return [_issue(field, "warn", f"{field}_assumption", f"{what}: {ref['note']} — проверьте")]
        return []
    messages = {
        "missing": f"{what} не указан в протоколе — выберите из справочника",
        "not_found": f"«{mention}» нет в справочнике — выберите {what.lower()}",
        "ambiguous": f"{what}: {ref['note']}",
        "department": f"{what}: {ref['note']}",
        "external": f"«{mention}» — {ref['note']}",
    }
    return [_issue(field, "block", f"{field}_{st}", messages.get(st, f"{what} не определён"))]


def validate(task: dict, text: str, meeting: str | None) -> dict:
    """Пересчитывает цитату, список проблем, готовность к подтверждению и отпечаток."""
    q = locate(task["quote"]["value"], text)
    task["quote"].update(q)
    issues: list[dict] = []

    if not task["title"]["value"].strip():
        issues.append(_issue("title", "block", "title_empty", "Нет формулировки задачи"))
    elif GARBLED.search(task["title"]["value"]):
        issues.append(_issue("title", "block", "title_garbled", "В формулировке посторонние символы — исправьте текст"))
    issues += _ref_issues("project", task["project"], "Проект")
    issues += _ref_issues("assignee", task["assignee"], "Ответственный")

    dl = task["deadline"]
    if not dl["value"]:
        if dl["status"] == "conflict":
            issues.append(_issue("deadline", "block", "deadline_conflict", f"Сроки противоречат: {dl['note']}"))
        elif dl["status"] == "missing":
            issues.append(_issue("deadline", "block", "deadline_missing", "Срок не указан — укажите дату"))
        else:
            issues.append(_issue("deadline", "block", "deadline_unclear", f"Срок нужно уточнить: {dl['note']}"))
    else:
        if meeting and date.fromisoformat(dl["value"]) < date.fromisoformat(meeting):
            issues.append(_issue("deadline", "block", "deadline_past", "Срок раньше даты совещания"))
        if dates.is_weekend(dl["value"]):
            issues.append(_issue("deadline", "warn", "deadline_weekend",
                                 f"Срок выпадает на выходной: {dates.fmt(date.fromisoformat(dl['value']))}"))
        if dl.get("ai_mismatch") and dl["status"] != "edited":
            issues.append(_issue("deadline", "info", "deadline_ai_mismatch",
                                 f"AI посчитал {dl['ai_mismatch']}, правило — {dl['value']}; взято правило"))

    if q["status"] == "not_found":
        issues.append(_issue("quote", "block", "quote_not_found",
                             "Цитата не найдена в протоколе — проверьте, есть ли такая задача"))
    elif q["status"] == "fuzzy":
        issues.append(_issue("quote", "warn", "quote_fuzzy", f"Цитата совпадает неточно ({int(q['score'] * 100)}%)"))

    pr = task["priority"]
    if pr["status"] == "assumption":
        issues.append(_issue("priority", "info", "priority_assumption",
                             f"Приоритет «{PRIORITY_RU[pr['value']]}» — предположение AI: {pr['reason']}"))
    if task["risk"].get("dropped_sources"):
        issues.append(_issue("risk", "warn", "risk_bad_source",
                             "AI сослался на источник, которого не было в контексте — ссылка убрана"))
    for amb in task.get("ambiguities", []):
        issues.append(_issue("ambiguity", "warn", "ai_ambiguity", f"AI отметил неясность: {amb}"))

    task["issues"] = issues
    task["can_confirm"] = not any(i["level"] == "block" for i in issues)
    task["essential_hash"] = essential_hash(task)
    return task


def rematch(task: dict, text: str, meeting: str | None, d: Directory) -> dict:
    """Ненайденные упоминания сверяются со справочником ещё раз (его могли пополнить)."""
    for field, fn in (("assignee", d.match_employee), ("project", d.match_project)):
        ref = task[field]
        if ref["status"] in ("not_found", "ambiguous") and ref.get("mention"):
            fresh = fn(ref["mention"], "explicit") if field == "project" else fn(ref["mention"])
            if fresh["status"] != ref["status"] or fresh.get("candidates") != ref.get("candidates"):
                task[field] = fresh
    return validate(task, text, meeting)


# ── Правки человека ────────────────────────────────────────────────────────────

class EditError(ValueError):
    pass


def apply_edit(task: dict, changes: dict, text: str, meeting: str | None, d: Directory) -> dict:
    edited = set(task.get("edited", []))
    for field, value in changes.items():
        if field == "title":
            task["title"] = {"value": str(value or "").strip(), "status": "edited"}
        elif field == "project":
            if value and value not in d.projects:
                raise EditError(f"неизвестный проект {value}")
            p = d.projects.get(value)
            task["project"].update(value=value or None, label=p["name"] if p else None,
                                   status="edited" if p else "missing",
                                   note="выбрано пользователем" if p else "проект не выбран")
        elif field == "assignee":
            if value and value not in d.employees:
                raise EditError(f"неизвестный сотрудник {value}")
            e = d.employees.get(value)
            task["assignee"].update(value=value or None, label=employee_label(e) if e else None,
                                    status="edited" if e else "missing",
                                    note="выбран пользователем" if e else "ответственный не выбран")
        elif field == "deadline":
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise EditError("срок должен быть датой ГГГГ-ММ-ДД") from exc
            task["deadline"].update(value=value or None, status="edited" if value else "missing",
                                    note="указано пользователем" if value else "срок не указан")
            task["deadline"].pop("ai_mismatch", None)
        elif field == "priority":
            if value not in PRIORITY_RU:
                raise EditError("приоритет: high, medium или low")
            task["priority"] = {"value": value, "status": "edited", "reason": "указано пользователем"}
        elif field == "risk":
            task["risk"].update(value=_s(value), status="edited" if _s(value) else "missing",
                                reason="указано пользователем", sources=[], dropped_sources=[])
        elif field == "quote":
            task["quote"]["value"] = str(value or "").strip().strip("«»\"")
            task["quote"].pop("corrected", None)
        else:
            raise EditError(f"поле {field} нельзя менять")
        edited.add(field)
    task["edited"] = sorted(edited)
    return validate(task, text, meeting)
