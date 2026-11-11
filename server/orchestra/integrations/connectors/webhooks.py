"""Вебхуки: исходящий (событие в вашу систему — n8n, Make, внутренний сервис) и входящий
(внешняя система ставит задачу агентам). Тело подписывается HMAC-SHA256.

Подпись входящего (режим hmac): X-Orchestra-Timestamp: <unix-время>, X-Orchestra-Signature: sha256=HMAC(секрет, "<время>." + тело).
Событие старше 5 минут или уже принятое — отклоняется: перехваченный запрос нельзя повторить."""
import hashlib
import hmac
import json
import time

import httpx

from orchestra.integrations.base import Action, Connector, Field, IntegrationError, http
from orchestra.providers.egress import CLASSIFICATIONS


MAX_SKEW = 300


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def sign_event(secret: str, ts: int | str, body: bytes) -> str:
    return sign(secret, f"{ts}.".encode("ascii") + body)




class WebhookOut(Connector):
    kind = "webhook_out"
    title = "Исходящий вебхук"
    description = "Событие в вашу систему: n8n, Make, сервис компании. Подпись X-Orchestra-Signature (HMAC-SHA256)"
    icon = "webhook"
    secret = Field("signing_secret", "Секрет подписи", "password", hint="получатель проверяет подпись этим секретом")
    fields = [Field("url", "Адрес получателя", "url", required=True)]
    actions = [Action("send", "Отправить событие", "Отправить событие во внешнюю систему компании.", write=True,
                      args={"event": "название события", "data": "содержимое (текст или JSON)"}, required=("event",))]
    default_trust = "private"
    default_classification = "internal"
    demo = True

    def _body(self, event: str, data) -> bytes:
        return json.dumps({"event": str(event)[:120], "data": data, "source": "agent-orchestra", "sent_at": int(time.time())},
                          ensure_ascii=False).encode("utf-8")

    async def _post(self, body: bytes) -> int:
        url = str(self.settings.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise IntegrationError("не задан адрес получателя")
        headers = {"Content-Type": "application/json"}
        if self.secret_value:
            headers["X-Orchestra-Signature"] = sign(self.secret_value, body)
        try:
            resp = await http().post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"получатель недоступен ({type(exc).__name__})") from exc
        if resp.status_code >= 300:
            raise IntegrationError(f"получатель ответил {resp.status_code}")
        return resp.status_code

    async def live_check(self) -> str:
        return f"получатель ответил {await self._post(self._body('ping', None))}"

    async def live_run(self, action: str, args: dict) -> dict:
        return {"status": await self._post(self._body(args["event"], args.get("data")))}

    async def demo_run(self, action: str, args: dict) -> dict:
        return {"status": 200, "bytes": len(self._body(args["event"], args.get("data"))), "demo": True}


class WebhookIn(Connector):
    kind = "webhook_in"
    title = "Входящий вебхук"
    description = "Внешняя система (1С, Битрикс24, сайт, почтовый робот) ставит задачу агентам"
    icon = "webhook"
    secret = Field("signing_secret", "Секрет", "password", hint="пусто — сгенерируем; HMAC-подпись или токен")
    fields = [
        Field("auth", "Проверка отправителя", "select", default="hmac", options=("hmac", "token"),
              hint="hmac — X-Orchestra-Timestamp + X-Orchestra-Signature: sha256=HMAC(секрет, «время.тело»); "
                   "token — Authorization: Bearer … или auth[application_token] (Битрикс24; укажите сети отправителя)"),
        Field("agent", "Кому задача", hint="slug агента; пусто — команде через оркестратора"),
        Field("instruction", "Что сделать с событием", "textarea", required=True,
              default="Разбери событие и подготовь предложения по дальнейшим действиям."),
        Field("classification", "Гриф задач", "select", default="internal", options=tuple(CLASSIFICATIONS)),
    ]
    actions = []
    default_trust = "private"
    default_classification = "internal"

    async def live_check(self) -> str:
        return f"принимает события на /api/hooks/{self.id}"

    async def live_run(self, action: str, args: dict) -> dict:
        raise IntegrationError("у входящего вебхука нет действий для агентов")

    def verify(self, body: bytes, headers: dict, form: dict) -> bool:
        secret = self.secret_value or ""
        if not secret:
            return False
        if self.settings.get("auth") == "token":
            bearer = headers.get("authorization", "").removeprefix("Bearer ").strip()
            token = bearer or str(form.get("auth[application_token]") or "")
            return bool(token) and hmac.compare_digest(token, secret)
        ts = headers.get("x-orchestra-timestamp", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > MAX_SKEW:
            return False
        return hmac.compare_digest(headers.get("x-orchestra-signature", ""), sign_event(secret, ts, body))

    def fingerprint(self, body: bytes, headers: dict) -> str:
        """Повтор = то же тело (и та же подпись в режиме hmac) в пределах окна."""
        return hashlib.sha256(self.id.encode() + headers.get("x-orchestra-signature", "").encode() + body).hexdigest()
