"""Каталог прав — в коде, а не в БД: права типизированы, их видно в ревью, нельзя опечататься.

Модель доступа:
  • Право (permission) — одно действие: «agents.edit».
  • Роль выдаёт право с эффектом: allow — можно; approval — можно после одобрения;
    deny — запрещено явно (перебивает любые allow от других ролей).
  • Роль назначается человеку В ПОДРАЗДЕЛЕНИИ и действует в нём и во всех вложенных.
  • Чего не выдали — запрещено (default deny).
"""
from dataclasses import dataclass

ALLOW, APPROVAL, DENY = "allow", "approval", "deny"
EFFECTS = (ALLOW, APPROVAL, DENY)


@dataclass(frozen=True)
class Permission:
    key: str
    group: str
    title: str
    hint: str = ""
    level: str = "view"          # view | edit | manage — для пресетов «смотреть / править / управлять»
    approvable: bool = True      # имеет ли смысл режим «с одобрением»
    risky: bool = False          # подсветка в матрице: выдавать осознанно


CATALOG: list[Permission] = [
    # Задачи команде агентов
    Permission("runs.view", "Задачи", "Видеть свои задачи и результаты", level="view", approvable=False),
    Permission("runs.view_all", "Задачи", "Видеть задачи всех в подразделении", level="manage", approvable=False),
    Permission("runs.start", "Задачи", "Ставить задачи агентам", "С одобрением — план исполняется после визы", level="edit"),
    Permission("runs.cancel_any", "Задачи", "Останавливать чужие задачи", level="manage", approvable=False),

    # Агенты и промты
    Permission("agents.view", "Агенты", "Видеть агентов и оргсхему", level="view", approvable=False),
    Permission("agents.edit", "Агенты", "Создавать и менять агентов", "Модели, инструменты, отдел, гриф", level="edit"),
    Permission("agents.delete", "Агенты", "Удалять агентов", level="manage", risky=True),
    Permission("prompts.view", "Промты", "Читать системные промты", level="view", approvable=False),
    Permission("prompts.edit", "Промты", "Писать черновики промтов", level="edit", approvable=False),
    Permission("prompts.publish", "Промты", "Публиковать версию промта", "Новая версия начинает работать у всех", level="manage"),

    # Модели и данные
    Permission("providers.view", "Модели", "Видеть подключённые модели", level="view", approvable=False),
    Permission("providers.manage", "Модели", "Подключать модели и менять ключи", level="manage", risky=True),
    Permission("providers.external", "Модели", "Отправлять данные во внешние облачные модели",
               "Локальные модели доступны всем, у кого есть право ставить задачи", level="edit", risky=True),
    Permission("data.confidential", "Модели", "Работать с конфиденциальными данными",
               "Гриф «Конфиденциально»: только локальные и частные модели", level="manage", risky=True),

    # База знаний
    Permission("knowledge.view", "Знания", "Искать и читать базу знаний",
               "Агенты ищут только в том, что видит поставивший задачу", level="view", approvable=False),
    Permission("knowledge.edit", "Знания", "Добавлять и удалять документы", level="edit", approvable=False),

    # Интеграции и процессы
    Permission("integrations.view", "Интеграции", "Видеть подключённые системы",
               "Битрикс24, 1С, почта, Telegram, вебхуки", level="view", approvable=False),
    Permission("integrations.manage", "Интеграции", "Подключать системы и менять ключи", level="manage",
               approvable=False, risky=True),
    Permission("directory.edit", "Интеграции", "Править справочник сотрудников и проектов",
               "Справочник процесса «Протоколы»: загрузка из Битрикс24, псевдонимы", level="edit", approvable=False),

    # Инструменты агентов
    Permission("tools.read", "Инструменты", "Инструменты чтения", "Дата, справочники, база знаний", level="view"),
    Permission("tools.web", "Инструменты", "Выход в интернет", "Только разрешённые сайты", level="edit"),
    Permission("tools.write", "Инструменты", "Изменения во внешних системах",
               "Битрикс24, 1С, почта, Telegram: агент предлагает — выполняет человек; «с визой» — каждое действие по визе",
               level="manage", risky=True),

    # Одобрения
    Permission("approvals.decide", "Одобрения", "Одобрять и отклонять запросы", "Свои запросы одобрить нельзя", level="manage", approvable=False),

    # Доступы
    Permission("iam.view", "Доступы", "Видеть людей, роли и права", level="view", approvable=False),
    Permission("iam.users", "Доступы", "Заводить людей и назначать роли", level="manage", risky=True),
    Permission("iam.roles", "Доступы", "Менять роли и матрицу прав", level="manage", risky=True),
    Permission("iam.org", "Доступы", "Менять оргструктуру", level="manage"),

    # Аудит и система
    Permission("audit.view", "Аудит", "Читать журнал аудита", level="view", approvable=False),
    Permission("security.view", "Аудит", "Видеть центр безопасности",
               "Входы всех сотрудников, неудачные попытки, тревоги, состояние защиты", level="view", approvable=False),
    Permission("security.manage", "Аудит", "Закрывать чужие сессии и настраивать тревоги",
               "Выход сотрудника на всех устройствах, канал оповещений", level="manage", approvable=False, risky=True),
    Permission("system.settings", "Система", "Менять настройки платформы", level="manage", risky=True),
    Permission("system.usage", "Система", "Видеть расход моделей", level="view", approvable=False),
]

BY_KEY = {p.key: p for p in CATALOG}
GROUPS = list(dict.fromkeys(p.group for p in CATALOG))


def is_known(key: str) -> bool:
    return key in BY_KEY
