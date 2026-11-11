from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.audit import service as audit
from orchestra.core.ratelimit import upload_limiter
from orchestra.db.base import utcnow
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, current, deny, require
from orchestra.integrations import service as integrations
from orchestra.integrations.base import IntegrationError
from orchestra.integrations.connectors.bitrix24 import Bitrix24
from orchestra.integrations.models import Integration
from orchestra.knowledge import ingest
from orchestra.processes.protocols import meta as protocol_meta
from orchestra.processes.protocols import tasks as rules
from orchestra.processes.protocols.models import DirDepartment, DirPerson, DirProject, Protocol, ProtocolTask
from orchestra.processes.protocols.service import decorate, engine, load_directory, recheck
from orchestra.providers.egress import rank

router = APIRouter(prefix="/api/protocols", tags=["protocols"])
dir_router = APIRouter(prefix="/api/directory", tags=["directory"])
SAMPLE = Path(rules.__file__).parent / "samples" / "planerka-2026-10-08.txt"
Cls = Literal["public", "internal", "confidential", "secret"]


class ProtocolIn(Strict):
    text: str = Field(min_length=20, max_length=200_000)
    title: str = Field(default="", max_length=300)
    meeting_date: str | None = None
    unit_id: str | None = None
    classification: Cls = "internal"
    source: str = Field(default="", max_length=300)

    @field_validator("meeting_date")
    @classmethod
    def _date(cls, v):
        if v:
            date.fromisoformat(v)
        return v or None


class EditIn(Strict):
    changes: dict


class IdsIn(Strict):
    task_ids: list[str] = Field(max_length=200)


class SendIn(IdsIn):
    integration_id: str
    preview: bool = False


def task_out(t: ProtocolTask) -> dict:
    return {"id": t.id, "idx": t.idx, **t.data, "state": t.state, "deleted": t.deleted, "external_id": t.external_id,
            "send_error": t.send_error, "confirmed": t.state in ("confirmed", "approval", "failed", "sent"),
            "stale": t.state == "confirmed" and t.confirmed_hash != t.data.get("essential_hash")}


def proto_out(p: Protocol, counts: dict | None = None) -> dict:
    return {"id": p.id, "title": p.title, "meeting_date": p.meeting_date, "status": p.status, "error": p.error,
            "classification": p.classification, "unit_id": p.unit_id, "source": p.source, "created_by": p.created_by,
            "created_at": iso(p.created_at), "progress": (p.meta or {}).get("progress"), "counts": counts or {}}


def visible(p: Principal, proto: Protocol) -> bool:
    return proto.created_by == p.user.id or p.access.can("runs.view_all", proto.unit_id)


async def get_protocol(db: AsyncSession, p: Principal, pid: str) -> Protocol:
    proto = await db.get(Protocol, pid)
    if proto is None or not visible(p, proto):
        raise HTTPException(404, "Протокол не найден")
    return proto


async def get_task(db: AsyncSession, proto: Protocol, tid: str) -> ProtocolTask:
    t = await db.get(ProtocolTask, tid)
    if t is None or t.protocol_id != proto.id:
        raise HTTPException(404, "Задача не найдена")
    return t


# ── протоколы ──────────────────────────────────────────────────────────────────
@router.get("/sample")
async def sample(p: Principal = Depends(current)):
    text = SAMPLE.read_text("utf-8")
    return {"text": text, "meeting_date": "2026-10-08", "title": "Еженедельная планёрка (пример)"}


@router.post("/extract")
async def extract(file: UploadFile = File(...), p: Principal = Depends(require("runs.start"))):
    """Файл протокола → текст + дата и название из шапки (для формы; протокол не создаётся)."""
    if not upload_limiter.hit(p.user.id):
        raise HTTPException(429, "Слишком много файлов подряд — подождите минуту")
    name = (file.filename or "протокол").replace("\\", "/").rsplit("/", 1)[-1][:200]
    try:
        text = await ingest.extract(name, await file.read(ingest.MAX_FILE + 1))
    except ingest.IngestError as exc:
        raise HTTPException(422, f"{name}: {exc}") from exc
    found = protocol_meta.detect(text)
    return {"text": text, "source": name, "meeting_date": found["meeting_date"], "date_source": found["date_source"],
            "title": found["title"] or name.rsplit(".", 1)[0]}


@router.get("")
async def protocols(p: Principal = Depends(require("runs.view")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Protocol).order_by(Protocol.created_at.desc()).limit(200))).scalars().all()
    rows = [r for r in rows if visible(p, r)]
    counts: dict[str, dict] = {}
    if rows:
        for t in (await db.execute(select(ProtocolTask.protocol_id, ProtocolTask.state).where(
                ProtocolTask.protocol_id.in_([r.id for r in rows]), ProtocolTask.deleted.is_(False)))).all():
            c = counts.setdefault(t.protocol_id, {})
            c[t.state] = c.get(t.state, 0) + 1
    return [proto_out(r, counts.get(r.id)) for r in rows]


@router.post("")
async def create(body: ProtocolIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    unit = body.unit_id or (p.access.memberships[0][0] if p.access.memberships else None)
    d = p.access.decide("runs.start", unit)
    if not d.allowed:
        raise await deny(p, d, "protocol.create", body.title)
    if rank(body.classification) >= rank("confidential") and not p.access.can("data.confidential", unit):
        raise await deny(p, p.access.decide("data.confidential", unit), "protocol.create", body.title)
    found = protocol_meta.detect(body.text)
    proto = Protocol(title=(body.title or found["title"] or "Совещание")[:300], text=body.text, source=body.source,
                     meeting_date=body.meeting_date or found["meeting_date"], unit_id=unit,
                     classification=body.classification, created_by=p.user.id, meta={})
    db.add(proto)
    await db.commit()
    await audit.record("protocol.create", actor=p.user, ip=p.ip, target_type="protocol", target_id=proto.id,
                       target_name=proto.title, data={"chars": len(body.text), "classification": body.classification})
    engine.start(proto.id)
    return proto_out(proto)


@router.get("/{pid}")
async def detail(pid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await get_protocol(db, p, pid)
    rows = (await db.execute(select(ProtocolTask).where(ProtocolTask.protocol_id == pid)
                             .order_by(ProtocolTask.idx))).scalars().all()
    d = await load_directory(db)
    senders = [{"id": i.id, "name": i.name, "mode": i.mode} for i in (await db.execute(
        select(Integration).where(Integration.kind == "bitrix24", Integration.enabled.is_(True)))).scalars()]
    m = proto.meta or {}
    return {**proto_out(proto), "text": proto.text, "decisions": proto.decisions, "skipped": proto.skipped,
            "tasks": [task_out(t) for t in rows], "directory": d.public(), "senders": senders,
            "meta": {k: m.get(k) for k in ("prompt_version", "agent", "provider", "model", "refs", "failed", "chunks",
                                           "seconds", "progress")},
            "can_edit": proto.created_by == p.user.id or p.access.can("runs.view_all", proto.unit_id)}


async def editable(db: AsyncSession, p: Principal, pid: str) -> Protocol:
    proto = await get_protocol(db, p, pid)
    if not (proto.created_by == p.user.id or p.access.can("runs.view_all", proto.unit_id)):
        raise HTTPException(403, "Править может автор протокола или руководитель подразделения")
    if proto.status == "processing":
        raise HTTPException(409, "Протокол ещё разбирается")
    return proto


@router.patch("/{pid}/tasks/{tid}")
async def edit(pid: str, tid: str, body: EditIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await editable(db, p, pid)
    t = await get_task(db, proto, tid)
    if t.state in ("approval", "sent"):
        raise HTTPException(409, "Задача уже отправлена или ждёт визы на отправку")
    try:
        data = rules.apply_edit(dict(t.data), body.changes, proto.text, proto.meeting_date, await load_directory(db))
    except rules.EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    t.data = decorate(data)
    if t.state in ("confirmed", "failed") and t.confirmed_hash != data["essential_hash"]:
        t.state = "draft"                                # изменилось существенное — подтверждение сбрасывается
    await db.commit()
    await audit.record("protocol.task_edit", actor=p.user, ip=p.ip, target_type="protocol", target_id=pid,
                       target_name=data["title"]["value"][:200], data={"fields": sorted(body.changes)})
    return task_out(t)


async def _confirm(db: AsyncSession, proto: Protocol, t: ProtocolTask, user_id: str) -> str | None:
    if t.deleted or t.state in ("approval", "sent"):
        return "задача удалена или уже отправлена"
    data = decorate(rules.validate(dict(t.data), proto.text, proto.meeting_date))
    t.data = data
    if not data["can_confirm"]:
        return "; ".join(i["message"] for i in data["issues"] if i["level"] == "block")
    t.state, t.confirmed_hash, t.confirmed_by = "confirmed", data["essential_hash"], user_id
    return None


@router.post("/{pid}/tasks/{tid}/{op}")
async def task_op(pid: str, tid: str, op: Literal["confirm", "unconfirm", "delete", "restore"],
                  p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await editable(db, p, pid)
    t = await get_task(db, proto, tid)
    if op == "confirm":
        problem = await _confirm(db, proto, t, p.user.id)
        if problem:
            await db.commit()
            raise HTTPException(409, f"Нельзя подтвердить: {problem}")
    elif t.state in ("approval", "sent"):
        raise HTTPException(409, "Задача уже отправлена или ждёт визы на отправку")
    elif op == "unconfirm":
        t.state = "draft"
    else:
        t.deleted = op == "delete"
        if t.deleted:
            t.state = "draft"
    await db.commit()
    await audit.record(f"protocol.task_{op}", actor=p.user, ip=p.ip, target_type="protocol", target_id=pid,
                       target_name=t.data["title"]["value"][:200])
    return task_out(t)


@router.post("/{pid}/confirm")
async def confirm_many(pid: str, body: IdsIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await editable(db, p, pid)
    results = []
    for tid in body.task_ids:
        t = await get_task(db, proto, tid)
        results.append({"task_id": tid, "problem": await _confirm(db, proto, t, p.user.id)})
    await db.commit()
    await audit.record("protocol.confirm", actor=p.user, ip=p.ip, target_type="protocol", target_id=pid,
                       target_name=proto.title, data={"ok": sum(not r["problem"] for r in results), "всего": len(results)})
    return {"results": results}


@router.post("/{pid}/rematch")
async def rematch_all(pid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Справочник пополнили — ненайденные люди и проекты сверяются заново."""
    proto = await editable(db, p, pid)
    d = await load_directory(db)
    for t in (await db.execute(select(ProtocolTask).where(ProtocolTask.protocol_id == pid))).scalars():
        if t.state in ("draft",) and not t.deleted:
            t.data = decorate(rules.rematch(dict(t.data), proto.text, proto.meeting_date, d))
    await db.commit()
    return {"ok": True}


@router.post("/{pid}/send")
async def send(pid: str, body: SendIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await editable(db, p, pid)
    row = await db.get(Integration, body.integration_id)
    if row is None or row.kind != "bitrix24":
        raise HTTPException(422, "Выберите подключение Битрикс24 (раздел «Интеграции»)")
    d = await load_directory(db)
    results = []
    for tid in body.task_ids:
        t = await get_task(db, proto, tid)
        problem, args = recheck(t, proto, d)
        if problem:
            results.append({"task_id": tid, "ok": False, "error": problem})
            continue
        if body.preview:
            fields = Bitrix24.task_fields(args, (row.settings or {}).get("tag", "Оркестр AI"))
            results.append({"task_id": tid, "ok": True, "method": "tasks.task.add", "mode": row.mode,
                            "payload": {"fields": fields}})
            continue
        # захват задачи: два одновременных «Отправить» не создадут две задачи в Битрикс24
        claimed = await db.execute(update(ProtocolTask).where(ProtocolTask.id == t.id, ProtocolTask.state.in_(
            ("confirmed", "failed"))).values(state="sending").execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            results.append({"task_id": tid, "ok": False, "error": "задача уже отправляется или отправлена"})
            continue
        try:
            act = await integrations.propose(db, p.access, row, "create_task", args, classification=proto.classification,
                                             origin_type="protocol_task", origin_id=t.id)
            await db.commit()                            # предложение — до сетевого вызова
            act = await integrations.submit(db, p.access, act, ip=p.ip)
            await db.commit()
        except IntegrationError as exc:
            await db.rollback()
            await db.execute(update(ProtocolTask).where(ProtocolTask.id == t.id, ProtocolTask.state == "sending")
                             .values(state="failed", send_error=str(exc)[:500]))
            await db.commit()
            results.append({"task_id": tid, "ok": False, "error": str(exc)})
            continue
        await db.refresh(t)
        results.append({"task_id": tid, "ok": act.status in ("executed", "pending_approval"), "status": act.status,
                        "external_id": t.external_id, "error": act.error or None})
    return {"mode": row.mode, "results": results}


@router.delete("/{pid}")
async def remove(pid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    proto = await get_protocol(db, p, pid)
    if proto.created_by != p.user.id:
        raise HTTPException(403, "Удалить может только автор")
    await db.delete(proto)
    await db.commit()
    await audit.record("protocol.delete", actor=p.user, ip=p.ip, target_type="protocol", target_id=pid, target_name=proto.title)
    return {"ok": True}


# ── справочник ─────────────────────────────────────────────────────────────────
class PersonIn(Strict):
    last_name: str = Field(min_length=1, max_length=100)
    first_name: str = Field(default="", max_length=100)
    middle_name: str = Field(default="", max_length=100)
    position: str = Field(default="", max_length=200)
    department_id: str | None = None
    email: str = Field(default="", max_length=200)
    external_id: str = Field(default="", max_length=60)


class ProjectIn(Strict):
    name: str = Field(min_length=2, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    stage: str = Field(default="", max_length=200)
    external_id: str = Field(default="", max_length=60)


class DepartmentIn(Strict):
    name: str = Field(min_length=2, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    head_id: str | None = None
    external_id: str = Field(default="", max_length=60)


class ImportIn(Strict):
    integration_id: str


KINDS = {"people": (DirPerson, PersonIn), "projects": (DirProject, ProjectIn), "departments": (DirDepartment, DepartmentIn)}


def parse(schema, data: dict) -> dict:
    try:
        return schema(**data).model_dump()
    except ValidationError as exc:
        e = exc.errors()[0]
        raise HTTPException(422, f"Неверные данные: {'.'.join(map(str, e['loc']))} — {e['msg']}") from exc


def row_out(r) -> dict:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name not in ("created_at", "updated_at")}


@dir_router.get("")
async def directory(p: Principal = Depends(require("runs.view")), db: AsyncSession = Depends(get_db)):
    return {k: [row_out(r) for r in (await db.execute(select(model))).scalars()] for k, (model, _) in KINDS.items()}


@dir_router.post("/items/{kind}")
async def add(kind: Literal["people", "projects", "departments"], body: dict,
              p: Principal = Depends(require("directory.edit")), db: AsyncSession = Depends(get_db)):
    model, schema = KINDS[kind]
    data = parse(schema, body)
    if kind == "people":
        data["last_name"] = data["last_name"].strip().strip(".").capitalize()
    r = model(**data)
    db.add(r)
    await db.commit()
    await audit.record("directory.create", actor=p.user, ip=p.ip, target_type=kind, target_id=r.id,
                       target_name=str(data.get("name") or data.get("last_name")))
    return row_out(r)


@dir_router.patch("/items/{kind}/{item_id}")
async def edit_item(kind: Literal["people", "projects", "departments"], item_id: str, body: dict,
                    p: Principal = Depends(require("directory.edit")), db: AsyncSession = Depends(get_db)):
    model, schema = KINDS[kind]
    r = await db.get(model, item_id)
    if r is None:
        raise HTTPException(404, "Запись не найдена")
    data = parse(schema, {k: v for k, v in {**row_out(r), **body}.items() if k in schema.model_fields})
    for k, v in data.items():
        setattr(r, k, v)
    if kind == "people" and "active" in body:
        r.active = bool(body["active"])
    await db.commit()
    await audit.record("directory.update", actor=p.user, ip=p.ip, target_type=kind, target_id=r.id,
                       target_name=str(data.get("name") or data.get("last_name")), data={"fields": sorted(body)})
    return row_out(r)


@dir_router.delete("/items/{kind}/{item_id}")
async def remove_item(kind: Literal["people", "projects", "departments"], item_id: str,
                      p: Principal = Depends(require("directory.edit")), db: AsyncSession = Depends(get_db)):
    model, _ = KINDS[kind]
    r = await db.get(model, item_id)
    if r is None:
        raise HTTPException(404, "Запись не найдена")
    await db.delete(r)
    await db.commit()
    await audit.record("directory.delete", actor=p.user, ip=p.ip, target_type=kind, target_id=item_id)
    return {"ok": True}


@dir_router.post("/import")
async def import_bitrix(body: ImportIn, p: Principal = Depends(require("directory.edit")),
                        db: AsyncSession = Depends(get_db)):
    """Сотрудники, отделы и проекты — из Битрикс24 (сверка по external_id; локальные псевдонимы сохраняются)."""
    row = await db.get(Integration, body.integration_id)
    if row is None or row.kind != "bitrix24":
        raise HTTPException(422, "Нужно подключение Битрикс24")
    try:
        run = lambda action: integrations.execute(db, p.access, row, action, {},  # noqa: E731
                                                  classification=row.data_classification, ip=p.ip)
        deps, users, groups = (await run("list_departments"))["departments"], (await run("list_users"))["users"], \
            (await run("list_groups"))["groups"]
    except IntegrationError as exc:
        raise HTTPException(422, str(exc)) from exc
    stats = {"departments": 0, "people": 0, "projects": 0, "new": 0}

    async def upsert(model, ext: str, values: dict, merge_aliases: bool = False):
        r = (await db.execute(select(model).where(model.external_id == ext))).scalar() if ext else None
        if r is None:
            r = model(external_id=ext, **values)
            db.add(r)
            stats["new"] += 1
        else:
            if merge_aliases:
                values["aliases"] = sorted(set(r.aliases or []) | set(values.get("aliases") or []))
            for k, v in values.items():
                setattr(r, k, v)
        await db.flush()
        return r

    dep_map, person_map = {}, {}
    for dep in deps:
        r = await upsert(DirDepartment, str(dep["id"]), {"name": dep["name"], "aliases": dep.get("aliases") or []}, True)
        dep_map[str(dep["id"])] = r
        stats["departments"] += 1
    for u in users:
        dep = dep_map.get(str(u.get("department") or ""))
        r = await upsert(DirPerson, str(u["id"]), {"last_name": u["last_name"] or "—", "first_name": u["first_name"],
                                                   "middle_name": u["middle_name"], "position": u["position"],
                                                   "email": u["email"], "department_id": dep.id if dep else None,
                                                   "active": True})
        person_map[str(u["id"])] = r
        stats["people"] += 1
    for dep in deps:
        head = person_map.get(str(dep.get("head") or ""))
        dep_map[str(dep["id"])].head_id = head.id if head else None
    for g in groups:
        await upsert(DirProject, str(g["id"]), {"name": g["name"], "stage": g.get("description", "")[:200],
                                                 "aliases": g.get("aliases") or [], "active": True}, True)
        stats["projects"] += 1
    await db.commit()
    await audit.record("directory.import", actor=p.user, ip=p.ip, target_type="integration", target_id=row.id,
                       target_name=row.name, data={**stats, "mode": row.mode, "at": utcnow().isoformat()})
    return stats
