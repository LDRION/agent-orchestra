import logging
from importlib.metadata import entry_points

from orchestra.tools.base import Tool
from orchestra.tools.builtin import DateTimeTool, HttpFetchTool

log = logging.getLogger("orchestra.tools")

TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    TOOLS[tool.spec.key] = tool


def _builtin() -> None:
    from orchestra.knowledge.tool import KnowledgeSearchTool
    for t in (DateTimeTool(), HttpFetchTool(), KnowledgeSearchTool()):
        register(t)


_builtin()


def load_plugins() -> None:
    for ep in entry_points(group="orchestra.tools"):
        try:
            tool = ep.load()()
            if isinstance(tool, Tool) and tool.spec.key not in TOOLS:
                register(tool)
        except Exception:
            log.exception("не удалось загрузить инструмент-плагин %s", ep.name)


def catalog() -> list[dict]:
    return [{"key": t.spec.key, "title": t.spec.title, "description": t.spec.description,
             "permission": t.spec.permission, "args": t.spec.args} for t in TOOLS.values()]
