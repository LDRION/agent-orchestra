"""Реестр коннекторов. Сторонний пакет добавляет свой через entry point:

    [project.entry-points."orchestra.integrations"]
    amocrm = "my_pkg.amocrm:AmoCRM"
"""
import logging
from importlib.metadata import entry_points

from orchestra.integrations.base import Connector
from orchestra.integrations.connectors.bitrix24 import Bitrix24
from orchestra.integrations.connectors.messaging import Email, Telegram
from orchestra.integrations.connectors.onec import OneC
from orchestra.integrations.connectors.webhooks import WebhookIn, WebhookOut

log = logging.getLogger("orchestra.integrations")

CONNECTORS: dict[str, type[Connector]] = {c.kind: c for c in (Bitrix24, OneC, Email, Telegram, WebhookOut, WebhookIn)}


def load_plugins() -> None:
    for ep in entry_points(group="orchestra.integrations"):
        try:
            cls = ep.load()
            if issubclass(cls, Connector) and cls.kind not in CONNECTORS:
                CONNECTORS[cls.kind] = cls
        except Exception:
            log.exception("не удалось загрузить коннектор-плагин %s", ep.name)


def meta() -> list[dict]:
    def field(f):
        return {"key": f.key, "title": f.title, "kind": f.kind, "hint": f.hint, "required": f.required,
                "default": f.default, "options": list(f.options)}
    return [{"kind": c.kind, "title": c.title, "description": c.description, "icon": c.icon,
             "secret": field(c.secret) if c.secret else None, "fields": [field(f) for f in c.fields],
             "actions": [{"key": a.key, "title": a.title, "description": a.description, "write": a.write,
                          "args": a.args, "required": list(a.required)} for a in c.actions],
             "default_trust": c.default_trust, "default_classification": c.default_classification, "demo": c.demo}
            for c in CONNECTORS.values()]
