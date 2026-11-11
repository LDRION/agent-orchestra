from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.core.ratelimit import upload_limiter
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, deny, require
from orchestra.iam.models import OrgUnit, User
from orchestra.knowledge import ingest, service
from orchestra.knowledge.models import KnowledgeDoc
from orchestra.providers.egress import CLASSIFICATIONS, rank

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
Kind = Literal["regulation", "standard", "project", "template", "other"]
Cls = Literal["public", "internal", "confidential", "secret"]


class DocIn(Strict):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=service.MAX_CHARS)
    kind: Kind = "other"
    unit_id: str | None = None
    classification: Cls = "internal"
    source: str = Field(default="", max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=20)


def human_ceiling(p: Principal) -> str:
    """Человек читает конфиденциальное, только если ему можно работать с такими данными."""
    return "secret" if p.access.can("data.confidential") else "internal"


def visible(p: Principal, d: KnowledgeDoc) -> bool:
    return service.can_see(p.access, d) and rank(d.classification) <= rank(human_ceiling(p))


def out(d: KnowledgeDoc, authors: dict[str, str] | None = None, body: bool = False) -> dict:
    o = {"id": d.id, "title": d.title, "kind": d.kind, "source": d.source, "unit_id": d.unit_id,
         "classification": d.classification, "chars": d.chars, "tags": d.tags, "created_at": iso(d.created_at),
         "author": (authors or {}).get(d.created_by or "", "")}
    if body:
        o["body"] = d.body
    return o


async def check_write(p: Principal, db: AsyncSession, unit_id: str | None, classification: str, title: str) -> None:
    if unit_id and await db.get(OrgUnit, unit_id) is None:
        raise HTTPException(422, "Подразделение не найдено")
    d = p.access.decide("knowledge.edit", unit_id)
    if not d.allowed:
        raise await deny(p, d, "knowledge.create", title)
    if rank(classification) >= rank("confidential") and not p.access.can("data.confidential", unit_id):
        raise await deny(p, p.access.decide("data.confidential", unit_id), "knowledge.create", title)


@router.get("")
async def docs(p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc()))).scalars().all()
    authors = dict((await db.execute(select(User.id, User.display_name))).all())
    return [out(d, authors) for d in rows if visible(p, d)]


@router.get("/search")
async def search(q: str, p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    hits = await service.search(db, p.access, q[:500], human_ceiling(p), k=10)
    return [{"doc_id": h.doc_id, "title": h.title, "chunk": h.chunk, "score": h.score,
             "classification": h.classification} for h in hits]


@router.get("/{doc_id}")
async def doc(doc_id: str, p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or not visible(p, d):
        raise HTTPException(404, "Документ не найден")
    return out(d, body=True)


@router.post("")
async def create(body: DocIn, p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    await check_write(p, db, body.unit_id, body.classification, body.title)
    d = await service.add(db, title=body.title, body=body.body, kind=body.kind, unit_id=body.unit_id,
                          classification=body.classification, source=body.source, tags=body.tags, user_id=p.user.id)
    await db.commit()
    await audit.record("knowledge.create", actor=p.user, ip=p.ip, target_type="doc", target_id=d.id, target_name=d.title,
                       data={"classification": d.classification, "chars": d.chars})
    return out(d)


@router.post("/upload")
async def upload(file: UploadFile = File(...), title: str = Form(""), kind: Kind = Form("other"),
                 unit_id: str = Form(""), classification: Cls = Form("internal"),
                 p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    if not upload_limiter.hit(p.user.id):
        raise HTTPException(429, "Слишком много файлов подряд — подождите минуту")
    name = (file.filename or "документ").replace("\\", "/").rsplit("/", 1)[-1][:200]
    title = (title.strip() or name.rsplit(".", 1)[0])[:300]
    if classification not in CLASSIFICATIONS:
        raise HTTPException(422, "Неизвестный гриф")
    await check_write(p, db, unit_id or None, classification, title)
    data = await file.read(ingest.MAX_FILE + 1)
    try:
        text = await ingest.extract(name, data)
    except ingest.IngestError as exc:
        raise HTTPException(422, f"{name}: {exc}") from exc
    d = await service.add(db, title=title, body=text, kind=kind, unit_id=unit_id or None,
                          classification=classification, source=name, tags=[], user_id=p.user.id)
    await db.commit()
    await audit.record("knowledge.create", actor=p.user, ip=p.ip, target_type="doc", target_id=d.id, target_name=d.title,
                       data={"file": name, "bytes": len(data), "chars": d.chars, "classification": classification})
    return out(d)


@router.delete("/{doc_id}")
async def remove(doc_id: str, p: Principal = Depends(require("knowledge.view")), db: AsyncSession = Depends(get_db)):
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or not visible(p, d):
        raise HTTPException(404, "Документ не найден")
    dec = p.access.decide("knowledge.edit", d.unit_id)
    if not dec.allowed:
        raise await deny(p, dec, "knowledge.delete", d.title)
    await service.remove(db, d)
    await db.commit()
    await audit.record("knowledge.delete", actor=p.user, ip=p.ip, target_type="doc", target_id=doc_id, target_name=d.title)
    return {"ok": True}
