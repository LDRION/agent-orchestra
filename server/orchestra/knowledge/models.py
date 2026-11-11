from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orchestra.db.base import Base, IdMixin, TimestampMixin


class KnowledgeDoc(Base, IdMixin, TimestampMixin):
    """Документ базы знаний. Видимость — право knowledge.view в его подразделении; гриф — как у задач."""
    __tablename__ = "knowledge_docs"

    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="other")      # regulation | standard | project | template | other
    source: Mapped[str] = mapped_column(String(300), default="")
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True, index=True)
    classification: Mapped[str] = mapped_column(String(20), default="internal")
    body: Mapped[str] = mapped_column(Text)
    chars: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class KnowledgeChunk(Base, IdMixin):
    """Фрагмент документа для поиска. terms — основы слов через пробел (индекс строится из них)."""
    __tablename__ = "knowledge_chunks"

    doc_id: Mapped[str] = mapped_column(ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text)
