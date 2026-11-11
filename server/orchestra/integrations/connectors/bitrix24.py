"""Битрикс24 через входящий вебхук REST: https://<портал>.bitrix24.ru/rest/<id>/<код>/

Права вебхука: task, user, department, sonet_group, crm. Демо-режим — справочник и счётчик задач
в демо-режиме, без сети.
"""
import itertools
import json
from datetime import date
from pathlib import Path

import httpx

from orchestra.integrations.base import Action, Connector, Field, IntegrationError, http

DEMO = json.loads((Path(__file__).resolve().parent.parent / "demo" / "bitrix_directory.json").read_text("utf-8"))
PRIORITY = {"high": 2, "medium": 1, "low": 0}
_demo_ids = itertools.count(4100)


class Bitrix24(Connector):
    kind = "bitrix24"
    title = "Битрикс24"
    description = "Задачи, сотрудники, отделы, проекты (группы) и CRM — через входящий вебхук REST"
    icon = "bitrix"
    secret = Field("webhook", "Входящий вебхук", "url", required=True,
                   hint="https://<портал>.bitrix24.ru/rest/<id>/<код>/ — права: task, user, department, sonet_group, crm")
    fields = [Field("tag", "Тег создаваемых задач", default="Оркестр AI")]
    actions = [
        Action("find_user", "Найти сотрудника", "Найти сотрудника портала по фамилии, имени или e-mail.",
               args={"query": "фамилия, имя или e-mail"}, required=("query",)),
        Action("list_tasks", "Задачи сотрудника", "Незавершённые задачи сотрудника: название, срок, статус.",
               args={"responsible_id": "id сотрудника в Битрикс24"}, required=("responsible_id",)),
        Action("create_task", "Создать задачу", "Поставить задачу сотруднику в Битрикс24.", write=True,
               args={"title": "название", "description": "описание", "responsible_id": "id ответственного",
                     "deadline": "срок ГГГГ-ММ-ДД", "group_id": "id проекта (группы)", "priority": "high | medium | low"},
               required=("title", "responsible_id")),
        Action("add_comment", "Комментарий к задаче", "Написать комментарий в задачу Битрикс24.", write=True,
               args={"task_id": "id задачи", "text": "текст комментария"}, required=("task_id", "text")),
        Action("create_lead", "Лид в CRM", "Завести лид в CRM (новый запрос заказчика).", write=True,
               args={"title": "суть запроса", "name": "контактное лицо", "phone": "телефон", "email": "e-mail",
                     "comment": "подробности"}, required=("title",)),
        Action("list_users", "Сотрудники портала", "Список активных сотрудников (для справочника).", args={}),
        Action("list_departments", "Отделы портала", "Структура отделов (для справочника).", args={}),
        Action("list_groups", "Проекты (группы)", "Рабочие группы и проекты портала (для справочника).", args={}),
    ]
    default_trust = "cloud_ru"
    default_classification = "internal"
    demo = True

    def _base(self) -> str:
        url = self.need_secret().strip().rstrip("/")
        if "/rest/" not in url:
            raise IntegrationError("адрес вебхука должен содержать /rest/<id>/<код>")
        return url

    async def _call(self, method: str, params: dict | None = None) -> dict:
        try:
            resp = await http().post(f"{self._base()}/{method}.json", json=params or {})
        except httpx.TimeoutException as exc:
            raise IntegrationError("Битрикс24 не ответил за 20 с") from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Битрикс24 недоступен ({type(exc).__name__})") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise IntegrationError(f"Битрикс24 {resp.status_code}: ответ не JSON") from exc
        if resp.status_code >= 400 or "error" in data:
            raise IntegrationError(f"Битрикс24: {data.get('error_description') or data.get('error') or resp.status_code}")
        return data

    async def _list(self, method: str, params: dict | None = None, limit: int = 500) -> list:
        out, start = [], 0
        while len(out) < limit:
            data = await self._call(method, {**(params or {}), "start": start})
            res = data.get("result") or []
            res = res.get("tasks", []) if isinstance(res, dict) else res
            out.extend(res)
            if "next" not in data:
                break
            start = data["next"]
        return out[:limit]

    async def live_check(self) -> str:
        me = (await self._call("profile")).get("result") or {}
        return f"подключено от имени {me.get('NAME', '')} {me.get('LAST_NAME', '')}".strip()

    @staticmethod
    def task_fields(args: dict, tag: str) -> dict:
        fields = {"TITLE": args["title"], "DESCRIPTION": args.get("description") or "",
                  "RESPONSIBLE_ID": str(args["responsible_id"]), "TAGS": [tag] if tag else []}
        if args.get("deadline"):
            try:
                fields["DEADLINE"] = f"{date.fromisoformat(str(args['deadline'])).isoformat()}T19:00:00+03:00"
            except ValueError as exc:
                raise IntegrationError("срок — дата ГГГГ-ММ-ДД") from exc
        if args.get("group_id"):
            fields["GROUP_ID"] = str(args["group_id"])
        fields["PRIORITY"] = PRIORITY.get(str(args.get("priority") or "medium"), 1)
        return fields

    async def live_run(self, action: str, args: dict) -> dict:
        if action == "find_user":
            users = await self._list("user.search", {"FILTER": {"FIND": args["query"], "ACTIVE": True}}, limit=20)
            return {"users": [_user(u) for u in users]}
        if action == "list_tasks":
            tasks = await self._list("tasks.task.list", {"filter": {"RESPONSIBLE_ID": args["responsible_id"], "!STATUS": 5},
                                                         "select": ["ID", "TITLE", "DEADLINE", "STATUS"]}, limit=30)
            return {"tasks": [{"id": t.get("id"), "title": t.get("title"), "deadline": t.get("deadline"),
                               "status": t.get("status")} for t in tasks]}
        if action == "create_task":
            data = await self._call("tasks.task.add", {"fields": self.task_fields(args, self.settings.get("tag"))})
            task_id = ((data.get("result") or {}).get("task") or {}).get("id")
            if not task_id:
                raise IntegrationError("Битрикс24 не вернул id задачи")
            return {"task_id": str(task_id)}
        if action == "add_comment":
            data = await self._call("task.commentitem.add", {"TASKID": args["task_id"], "FIELDS": {"POST_MESSAGE": args["text"]}})
            return {"comment_id": str(data.get("result"))}
        if action == "create_lead":
            fields = {"TITLE": args["title"], "NAME": args.get("name") or "", "COMMENTS": args.get("comment") or ""}
            if args.get("phone"):
                fields["PHONE"] = [{"VALUE": args["phone"], "VALUE_TYPE": "WORK"}]
            if args.get("email"):
                fields["EMAIL"] = [{"VALUE": args["email"], "VALUE_TYPE": "WORK"}]
            data = await self._call("crm.lead.add", {"fields": fields})
            return {"lead_id": str(data.get("result"))}
        if action == "list_users":
            return {"users": [_user(u) for u in await self._list("user.get", {"FILTER": {"ACTIVE": True}})]}
        if action == "list_departments":
            deps = await self._list("department.get")
            return {"departments": [{"id": str(d["ID"]), "name": d.get("NAME", ""), "head": str(d.get("UF_HEAD") or ""),
                                     "aliases": []} for d in deps]}
        if action == "list_groups":
            groups = await self._list("sonet_group.get", {"FILTER": {"ACTIVE": "Y"}})
            return {"groups": [{"id": str(g["ID"]), "name": g.get("NAME", ""), "description": g.get("DESCRIPTION") or "",
                                "aliases": []} for g in groups]}
        raise IntegrationError(f"неизвестное действие {action}")

    async def demo_run(self, action: str, args: dict) -> dict:
        emps = DEMO["employees"]
        if action == "find_user":
            q = str(args["query"]).lower()
            return {"users": [_demo_user(e) for e in emps if q in f"{e['last_name']} {e['first_name']}".lower()]}
        if action == "list_tasks":
            return {"tasks": []}
        if action == "create_task":
            self.task_fields(args, self.settings.get("tag"))          # та же проверка полей, что в боевом режиме
            return {"task_id": str(next(_demo_ids)), "demo": True}
        if action == "add_comment":
            return {"comment_id": str(next(_demo_ids)), "demo": True}
        if action == "create_lead":
            return {"lead_id": str(next(_demo_ids)), "demo": True}
        if action == "list_users":
            return {"users": [_demo_user(e) for e in emps]}
        if action == "list_departments":
            by_id = {e["id"]: e for e in emps}
            return {"departments": [{"id": d["id"], "name": d["name"], "aliases": d["aliases"],
                                     "head": str(by_id[d["head"]]["bitrix_user_id"]) if d.get("head") in by_id else ""}
                                    for d in DEMO["departments"]]}
        if action == "list_groups":
            return {"groups": [{"id": str(p["bitrix_group_id"]), "name": p["name"], "description": p.get("stage", ""),
                                "aliases": p["aliases"]} for p in DEMO["projects"]]}
        raise IntegrationError(f"неизвестное действие {action}")


def _user(u: dict) -> dict:
    deps = u.get("UF_DEPARTMENT") or []
    return {"id": str(u.get("ID")), "last_name": u.get("LAST_NAME") or "", "first_name": u.get("NAME") or "",
            "middle_name": u.get("SECOND_NAME") or "", "position": u.get("WORK_POSITION") or "",
            "email": u.get("EMAIL") or "", "department": str(deps[0]) if deps else ""}


def _demo_user(e: dict) -> dict:
    return {"id": str(e["bitrix_user_id"]), "last_name": e["last_name"], "first_name": e["first_name"],
            "middle_name": e.get("middle_name", ""), "position": e.get("role", ""), "email": "",
            "department": e.get("department") or ""}
