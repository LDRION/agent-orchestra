"""База знаний: добавление, удаление, поиск с учётом прав человека и грифа шага."""
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.iam.authz import AccessContext
from orchestra.knowledge.index import Hit, index
from orchestra.knowledge.models import KnowledgeChunk, KnowledgeDoc
from orchestra.knowledge.text import chunks, terms

MAX_CHARS = 2_000_000
KINDS = {"regulation": "Регламент", "standard": "Норматив (СП, ГОСТ)", "project": "Проектный документ",
         "template": "Шаблон", "other": "Другое"}


async def add(db: AsyncSession, *, title: str, body: str, kind: str, unit_id: str | None, classification: str,
              source: str, tags: list[str], user_id: str) -> KnowledgeDoc:
    body = body[:MAX_CHARS]
    doc = KnowledgeDoc(title=title, body=body, kind=kind if kind in KINDS else "other", unit_id=unit_id,
                       classification=classification, source=source[:300], tags=tags[:20], chars=len(body),
                       created_by=user_id)
    db.add(doc)
    await db.flush()
    head = " ".join(terms(title))                      # заголовок (дважды) усиливает каждый фрагмент
    for i, text in enumerate(chunks(body)):
        db.add(KnowledgeChunk(doc_id=doc.id, idx=i, text=text, terms=f"{head} {head} {' '.join(terms(text))}"))
    return doc


async def remove(db: AsyncSession, doc: KnowledgeDoc) -> None:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
    await db.delete(doc)


def visible_units(access: AccessContext) -> set[str] | None:
    """Где человек читает документы: подразделения с правом (и вложенные) + все вышестоящие.
    Регламент компании (корень) видят все отделы; документы соседнего отдела — нет."""
    allowed = access.units_with("knowledge.view")
    if allowed is None:
        return None
    out = set(allowed)
    for u in allowed:
        out.update(access.tree.chain(u))
    return out


def can_see(access: AccessContext, doc: KnowledgeDoc) -> bool:
    units = visible_units(access)
    if units is None:
        return True
    if doc.unit_id is None:
        return access.can("knowledge.view")
    return doc.unit_id in units


async def search(db: AsyncSession, access: AccessContext, query: str, max_class: str, k: int = 6) -> list[Hit]:
    if not access.can("knowledge.view"):
        return []
    await index.ensure(db)
    return index.search(query, visible_units(access), max_class, k)
