"""Почта (SMTP) и Telegram: отправка — только по белым спискам получателей."""
import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import httpx

from orchestra.integrations.base import Action, Connector, Field, IntegrationError, http

EMAIL = re.compile(r"^[\w.+-]+@([\w-]+\.)+[\w-]{2,}$")


class Email(Connector):
    kind = "email"
    title = "Почта (SMTP)"
    description = "Письма заказчикам и коллегам — только на разрешённые домены"
    icon = "mail"
    secret = Field("password", "Пароль (пароль приложения)", "password")
    fields = [
        Field("host", "SMTP-сервер", required=True, hint="smtp.yandex.ru, smtp.mail.ru, почтовый сервер компании"),
        Field("port", "Порт", "number", default=465),
        Field("security", "Шифрование", "select", default="ssl", options=("ssl", "starttls")),
        Field("username", "Логин", required=True),
        Field("from_address", "Отправитель", required=True, hint="ai@example.com"),
        Field("from_name", "Имя отправителя", default="Оркестр · AI"),
        Field("allowed_domains", "Разрешённые домены получателей", "list", required=True, default=["example.com"],
              hint="письма на другие домены не уйдут"),
        Field("signature", "Подпись", "textarea", default="—\nОтправлено ИИ-ассистентом «Оркестр» после подтверждения сотрудником."),
    ]
    actions = [Action("send", "Отправить письмо", "Отправить письмо (только на разрешённые домены).", write=True,
                      args={"to": "адреса через запятую", "subject": "тема", "body": "текст письма"},
                      required=("to", "subject", "body"))]
    default_trust = "cloud_ru"
    default_classification = "internal"
    demo = True

    def _recipients(self, raw: str) -> list[str]:
        allowed = {d.lower().lstrip("@") for d in self.settings.get("allowed_domains") or []}
        out = []
        for addr in [a.strip() for a in str(raw).replace(";", ",").split(",") if a.strip()]:
            if not EMAIL.match(addr):
                raise IntegrationError(f"неверный адрес: {addr}")
            if addr.rsplit("@", 1)[1].lower() not in allowed:
                raise IntegrationError(f"домен {addr.rsplit('@', 1)[1]} не в списке разрешённых")
            out.append(addr)
        if not out or len(out) > 10:
            raise IntegrationError("от 1 до 10 получателей")
        return out

    def _message(self, args: dict) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((str(self.settings.get("from_name") or ""), str(self.settings["from_address"])))
        msg["To"] = ", ".join(self._recipients(args["to"]))
        subject = str(args["subject"]).replace("\n", " ").strip()[:200]
        msg["Subject"] = subject
        msg.set_content(f"{args['body']}\n\n{self.settings.get('signature') or ''}".strip())
        return msg

    def _smtp(self) -> smtplib.SMTP:
        host, port = str(self.settings.get("host") or ""), int(self.settings.get("port") or 465)
        ctx = ssl.create_default_context()
        if self.settings.get("security") == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.starttls(context=ctx)
        s.login(str(self.settings.get("username") or ""), self.need_secret())
        return s

    async def live_check(self) -> str:
        def probe():
            with self._smtp():
                return f"вход на {self.settings.get('host')} выполнен"
        try:
            return await asyncio.to_thread(probe)
        except (OSError, smtplib.SMTPException) as exc:
            raise IntegrationError(f"SMTP: {type(exc).__name__}: {str(exc)[:160]}") from exc

    async def live_run(self, action: str, args: dict) -> dict:
        msg = self._message(args)

        def send():
            with self._smtp() as s:
                s.send_message(msg)
        try:
            await asyncio.to_thread(send)
        except (OSError, smtplib.SMTPException) as exc:
            raise IntegrationError(f"SMTP: {type(exc).__name__}: {str(exc)[:160]}") from exc
        return {"sent_to": msg["To"], "subject": msg["Subject"]}

    async def demo_run(self, action: str, args: dict) -> dict:
        msg = self._message(args)                       # те же проверки адресов, что в боевом режиме
        return {"sent_to": msg["To"], "subject": msg["Subject"], "demo": True}


class Telegram(Connector):
    kind = "telegram"
    title = "Telegram-бот"
    description = "Уведомления и сводки в разрешённые чаты"
    icon = "send"
    secret = Field("token", "Токен бота от @BotFather", "password", required=True)
    fields = [Field("chats", "Разрешённые чаты (chat_id)", "list", required=True,
                    hint="id чатов, куда боту можно писать; первый — по умолчанию")]
    actions = [Action("send_message", "Сообщение в чат", "Отправить сообщение в разрешённый чат Telegram.", write=True,
                      args={"text": "текст", "chat_id": "id чата (по умолчанию — первый разрешённый)"},
                      required=("text",))]
    default_trust = "cloud_foreign"
    default_classification = "internal"
    demo = True

    def _chat(self, chat_id) -> str:
        chats = [str(c).strip() for c in self.settings.get("chats") or [] if str(c).strip()]
        if not chats:
            raise IntegrationError("не задан ни один разрешённый чат")
        chat = str(chat_id).strip() if chat_id else chats[0]
        if chat not in chats:
            raise IntegrationError("чат не в списке разрешённых")
        return chat

    async def _api(self, method: str, payload: dict | None = None) -> dict:
        try:
            resp = await http().post(f"https://api.telegram.org/bot{self.need_secret()}/{method}", json=payload or {})
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Telegram недоступен ({type(exc).__name__})") from exc
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            raise IntegrationError(f"Telegram: {data.get('description') or resp.status_code}")
        return data["result"]

    async def live_check(self) -> str:
        me = await self._api("getMe")
        return f"бот @{me.get('username')}"

    async def live_run(self, action: str, args: dict) -> dict:
        res = await self._api("sendMessage", {"chat_id": self._chat(args.get("chat_id")), "text": str(args["text"])[:4000],
                                              "disable_web_page_preview": True})
        return {"message_id": res.get("message_id")}

    async def demo_run(self, action: str, args: dict) -> dict:
        return {"message_id": 1, "chat_id": self._chat(args.get("chat_id")), "demo": True}
