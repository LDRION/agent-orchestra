"""1С:Предприятие через стандартный интерфейс OData (публикация базы на веб-сервере):
http://сервер/база/odata/standard.odata — Бухгалтерия, УТ, ERP, УНФ.

Агенты видят только объекты из списка «Разрешённые объекты». Данные 1С по умолчанию —
«конфиденциально»: попадают только в задачи с таким грифом, а значит — только в локальные модели.
"""
import re

import httpx

from orchestra.integrations.base import Action, Connector, Field, IntegrationError, http

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ENTITY = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_]{2,120}$")
DEFAULT_ENTITIES = ["Catalog_Контрагенты", "Catalog_Номенклатура", "Catalog_ДоговорыКонтрагентов",
                    "Document_СчетНаОплатуПокупателю", "Document_ЗаказКлиента"]

DEMO_COUNTERPARTIES = [
    {"Ref_Key": "6f1c1c1a-0000-4000-8000-000000000001", "Description": "ООО «Демо-Девелопмент»", "ИНН": "7700000001", "КПП": "770001001"},
    {"Ref_Key": "6f1c1c1a-0000-4000-8000-000000000002", "Description": "АО «Пример Строй»", "ИНН": "7700000002", "КПП": "770001001"},
    {"Ref_Key": "6f1c1c1a-0000-4000-8000-000000000003", "Description": "ИП Тестов Т. Т.", "ИНН": "770000000003", "КПП": ""},
]
DEMO_INVOICES = [
    {"Ref_Key": "7a2d2d2b-0000-4000-8000-000000000011", "Number": "ДЕМО-0042", "Date": "2026-10-01T00:00:00",
     "Контрагент_Key": DEMO_COUNTERPARTIES[0]["Ref_Key"], "СуммаДокумента": 1250000, "Комментарий": "Аванс 30% по договору ПД"},
]


def quote(value: str) -> str:
    """Строка для $filter: апостроф удваивается — иначе подмена условия."""
    return "'" + str(value).replace("'", "''") + "'"


class OneC(Connector):
    kind = "onec"
    title = "1С:Предприятие"
    description = "Контрагенты, договоры, счета и заказы через стандартный OData-интерфейс 1С"
    icon = "onec"
    secret = Field("password", "Пароль пользователя 1С", "password")
    fields = [
        Field("base_url", "Адрес OData", "url", required=True, hint="http://сервер/база/odata/standard.odata"),
        Field("username", "Пользователь 1С", required=True, hint="отдельный пользователь с правами только на нужные объекты"),
        Field("entities", "Разрешённые объекты", "list", default=DEFAULT_ENTITIES,
              hint="Catalog_… и Document_… — агенты видят только их"),
    ]
    actions = [
        Action("find_counterparty", "Найти контрагента", "Найти контрагента в 1С по ИНН или части названия.",
               args={"query": "ИНН или название"}, required=("query",)),
        Action("list", "Выборка из 1С", "Записи разрешённого справочника или документа; можно с фильтром OData.",
               args={"entity": "объект, например Document_СчетНаОплатуПокупателю",
                     "filter": "условие OData, например Контрагент_Key eq guid'…'", "top": "сколько записей (до 50)"},
               required=("entity",)),
        Action("get", "Запись по ключу", "Одна запись справочника или документа по Ref_Key.",
               args={"entity": "объект", "key": "Ref_Key (GUID)"}, required=("entity", "key")),
        Action("create", "Создать запись", "Создать запись справочника или документа (черновиком).", write=True,
               args={"entity": "объект", "fields": "поля записи (JSON-объект)"}, required=("entity", "fields")),
    ]
    default_trust = "local"
    default_classification = "confidential"
    demo = True

    def _entity(self, name: str) -> str:
        name = str(name).strip()
        if not ENTITY.match(name):
            raise IntegrationError("недопустимое имя объекта 1С")
        if name not in (self.settings.get("entities") or []):
            raise IntegrationError(f"объект «{name}» не входит в разрешённые для агентов")
        return name

    def _url(self) -> str:
        base = str(self.settings.get("base_url") or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise IntegrationError("не задан адрес OData")
        return base

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = await http().get(f"{self._url()}/{path}", params={"$format": "json", **(params or {})},
                                    auth=(str(self.settings.get("username") or ""), self.secret_value or ""))
        except httpx.TimeoutException as exc:
            raise IntegrationError("1С не ответила за 20 с") from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"1С недоступна ({type(exc).__name__})") from exc
        if resp.status_code == 401:
            raise IntegrationError("1С: неверный пользователь или пароль")
        if resp.status_code >= 400:
            raise IntegrationError(f"1С {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def live_check(self) -> str:
        data = await self._get("")
        return f"подключено: объектов в публикации — {len(data.get('value') or [])}"

    async def live_run(self, action: str, args: dict) -> dict:
        if action == "find_counterparty":
            q = str(args["query"]).strip()[:120]
            flt = f"ИНН eq {quote(q)}" if q.isdigit() else f"substringof({quote(q)}, Description)"
            data = await self._get("Catalog_Контрагенты", {"$filter": flt, "$top": 10,
                                                           "$select": "Ref_Key,Description,ИНН,КПП"})
            return {"counterparties": data.get("value", [])}
        if action == "list":
            flt = str(args.get("filter") or "").strip()
            if len(flt) > 300 or "\n" in flt:
                raise IntegrationError("фильтр — одна строка до 300 символов")
            top = max(1, min(int(args.get("top") or 20), 50))
            params = {"$top": top}
            if flt:
                params["$filter"] = flt
            data = await self._get(self._entity(args["entity"]), params)
            return {"items": data.get("value", [])}
        if action == "get":
            key = str(args["key"]).strip()
            if not GUID.match(key):
                raise IntegrationError("ключ — GUID вида 00000000-0000-0000-0000-000000000000")
            return {"item": await self._get(f"{self._entity(args['entity'])}(guid'{key}')")}
        if action == "create":
            fields = args["fields"]
            if not isinstance(fields, dict) or len(fields) > 60:
                raise IntegrationError("fields — объект с полями записи")
            try:
                resp = await http().post(f"{self._url()}/{self._entity(args['entity'])}", params={"$format": "json"},
                                         json=fields, auth=(str(self.settings.get("username") or ""), self.secret_value or ""))
            except httpx.HTTPError as exc:
                raise IntegrationError(f"1С недоступна ({type(exc).__name__})") from exc
            if resp.status_code >= 400:
                raise IntegrationError(f"1С {resp.status_code}: {resp.text[:200]}")
            return {"created": resp.json().get("Ref_Key")}
        raise IntegrationError(f"неизвестное действие {action}")

    async def demo_run(self, action: str, args: dict) -> dict:
        if action == "find_counterparty":
            q = str(args["query"]).lower()
            return {"counterparties": [c for c in DEMO_COUNTERPARTIES if q in c["ИНН"] or q in c["Description"].lower()],
                    "demo": True}
        if action == "list":
            entity = self._entity(args["entity"])
            rows = DEMO_COUNTERPARTIES if entity == "Catalog_Контрагенты" else \
                DEMO_INVOICES if entity == "Document_СчетНаОплатуПокупателю" else []
            return {"items": rows, "demo": True}
        if action == "get":
            key = str(args["key"])
            rows = [r for r in DEMO_COUNTERPARTIES + DEMO_INVOICES if r["Ref_Key"] == key]
            return {"item": rows[0] if rows else None, "demo": True}
        if action == "create":
            self._entity(args["entity"])
            return {"created": "00000000-0000-4000-8000-0000000000ff", "demo": True}
        raise IntegrationError(f"неизвестное действие {action}")
