"""BM25 по фрагментам в памяти процесса. Пересобирается, когда меняется набор документов.

Фильтры по правам и грифу применяются ДО ранжирования: в выдачу не попадает то, что человеку
или шагу задачи видеть нельзя. На росте объёмов интерфейс сохраняется, реализация меняется
на PostgreSQL FTS / pgvector.
"""
import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.knowledge.models import KnowledgeChunk, KnowledgeDoc
from orchestra.knowledge.text import terms
from orchestra.providers.egress import rank

K1, B = 1.5, 0.75


@dataclass
class Hit:
    doc_id: str
    title: str
    chunk: str
    score: float
    classification: str
    unit_id: str | None


@dataclass
class _Chunk:
    doc_id: str
    title: str
    text: str
    length: int
    unit_id: str | None
    classification: str


class Bm25Index:
    def __init__(self) -> None:
        self.signature: tuple | None = None
        self.items: list[_Chunk] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.avg = 1.0

    async def ensure(self, db: AsyncSession) -> None:
        sig = tuple((await db.execute(select(func.count(KnowledgeDoc.id), func.max(KnowledgeDoc.updated_at)))).one())
        if sig == self.signature:
            return
        rows = (await db.execute(
            select(KnowledgeChunk.doc_id, KnowledgeChunk.text, KnowledgeChunk.terms, KnowledgeDoc.title,
                   KnowledgeDoc.unit_id, KnowledgeDoc.classification)
            .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id))).all()
        items, postings = [], defaultdict(list)
        for i, r in enumerate(rows):
            tf = Counter(r.terms.split())
            items.append(_Chunk(r.doc_id, r.title, r.text, sum(tf.values()) or 1, r.unit_id, r.classification))
            for t, n in tf.items():
                postings[t].append((i, n))
        self.items, self.postings, self.signature = items, postings, sig
        self.avg = (sum(c.length for c in items) / len(items)) if items else 1.0

    def search(self, query: str, units: set[str] | None, max_class: str, k: int = 6, per_doc: int = 2) -> list[Hit]:
        """units=None — без ограничения по подразделениям (владелец). Документ без подразделения — общий."""
        q = set(terms(query))
        if not q or not self.items:
            return []
        n = len(self.items)
        ceiling = rank(max_class)
        scores: dict[int, float] = defaultdict(float)
        for t in q:
            plist = self.postings.get(t)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist:
                c = self.items[i]
                if rank(c.classification) > ceiling:
                    continue
                if units is not None and c.unit_id is not None and c.unit_id not in units:
                    continue
                scores[i] += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * c.length / self.avg))
        out, per = [], Counter()
        for i, s in sorted(scores.items(), key=lambda x: -x[1]):
            c = self.items[i]
            if per[c.doc_id] >= per_doc:
                continue
            per[c.doc_id] += 1
            out.append(Hit(c.doc_id, c.title, c.text, round(s, 3), c.classification, c.unit_id))
            if len(out) >= k:
                break
        return out


index = Bm25Index()
