"""Инструмент агентов «Поиск в базе знаний»."""
from orchestra.db.session import sessionmaker
from orchestra.knowledge import service
from orchestra.tools.base import Tool, ToolContext, ToolError, ToolSpec


class KnowledgeSearchTool(Tool):
    spec = ToolSpec("knowledge_search", "База знаний",
                    "Найти в базе знаний компании регламенты, нормативы, шаблоны и проектные документы. "
                    "Возвращает фрагменты с названием документа — ссылайся на него в ответе.",
                    "tools.read", {"query": "что ищем — ключевые слова или вопрос"})

    async def run(self, args: dict, ctx: ToolContext) -> str:
        query = str(args.get("query", "")).strip()[:500]
        if not query:
            raise ToolError("пустой запрос")
        if ctx.access is None or not ctx.access.can("knowledge.view"):
            raise ToolError("у поставившего задачу нет доступа к базе знаний")
        async with sessionmaker()() as db:
            hits = await service.search(db, ctx.access, query, ctx.classification, k=5)
        if not hits:
            return "ничего не найдено (или документы с более строгим грифом, чем у задачи)"
        return "\n\n".join(f"<документ название=\"{h.title}\">\n{h.chunk[:1500]}\n</документ>" for h in hits)
