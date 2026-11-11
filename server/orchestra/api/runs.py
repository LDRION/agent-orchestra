import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.agents.models import Agent
from orchestra.api.schemas import Strict, iso
from orchestra.approvals.models import Approval
from orchestra.audit import service as audit
from orchestra.core.events import bus
from orchestra.core.ratelimit import run_limiter
from orchestra.db.base import utcnow
from orchestra.db.session import get_db, sessionmaker
from orchestra.iam.deps import Principal, current, deny
from orchestra.orchestration.engine import engine
from orchestra.iam.models import OrgUnit, User, UserSession
from orchestra.iam.service import aware
from orchestra.orchestration.models import ACTIVE, FINAL, Run, RunEvent, RunStep
from orchestra.system import settings_store

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunIn(Strict):
    request: str = Field(min_length=3, max_length=40000)
    unit_id: str | None = None
    classification: Literal["public", "internal", "confidential", "secret"] | None = None
    agent_id: str | None = None                        # задан — напрямую агенту, без плана


def run_out(r: Run, steps: list[RunStep] | None = None) -> dict:
    out = {"id": r.id, "title": r.title, "request": r.request, "mode": r.mode, "origin": r.origin, "status": r.status, "stage": r.stage,
           "classification": r.classification, "unit_id": r.unit_id, "initiator_id": r.initiator_id,
           "approach": r.approach, "result": r.result, "error": r.error, "approval_id": r.approval_id,
           "preapproved": r.preapproved, "created_at": iso(r.created_at), "started_at": iso(r.started_at),
           "finished_at": iso(r.finished_at), "tokens_in": r.tokens_in, "tokens_out": r.tokens_out}
    if steps is not None:
        out["steps"] = [{"id": s.id, "idx": s.idx, "agent_id": s.agent_id, "agent_name": s.agent_name, "task": s.task,
                         "depends_on": s.depends_on, "status": s.status, "attempts": s.attempts, "output": s.output,
                         "error": s.error, "provider": s.provider_name, "model": s.model,
                         "prompt_version": s.prompt_version, "tools_used": s.tools_used,
                         "started_at": iso(s.started_at), "finished_at": iso(s.finished_at),
                         "tokens_in": s.tokens_in, "tokens_out": s.tokens_out} for s in steps]
    return out


def can_see(p: Principal, r: Run) -> bool:
    return r.initiator_id == p.user.id or p.access.can("runs.view_all", r.unit_id)


async def get_run(db: AsyncSession, p: Principal, run_id: str) -> Run:
    r = await db.get(Run, run_id)
    if r is None or not can_see(p, r):
        raise HTTPException(404, "Задача не найдена")
    return r


@router.get("")
async def list_runs(scope: Literal["mine", "all"] = "mine", limit: int = 100, p: Principal = Depends(current),
                    db: AsyncSession = Depends(get_db)):
    if not p.access.can("runs.view"):
        raise HTTPException(403, "Нет доступа к задачам")
    q = select(Run).order_by(Run.created_at.desc()).limit(max(1, min(limit, 500)))
    if scope == "mine":
        q = q.where(Run.initiator_id == p.user.id)
    else:
        units = p.access.units_with("runs.view_all")
        if units is not None:
            q = q.where((Run.initiator_id == p.user.id) | Run.unit_id.in_(units))
    return [run_out(r) for r in (await db.execute(q)).scalars()]


@router.post("")
async def create_run(body: RunIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    return await _create(body, p, db)


async def _create(body: RunIn, p: Principal, db: AsyncSession, origin: str = "") -> dict:
    if not run_limiter.hit(p.user.id):
        raise HTTPException(429, "Слишком много задач подряд — подождите минуту")
    if body.unit_id and await db.get(OrgUnit, body.unit_id) is None:
        raise HTTPException(422, "Подразделение не найдено")
    unit_id = body.unit_id or (p.access.memberships[0][0] if p.access.memberships else None)
    d = p.access.decide("runs.start", unit_id)
    if d.effect == "deny":
        raise await deny(p, d, "runs.start", body.request[:80])
    agent = None
    if body.agent_id:
        agent = await db.get(Agent, body.agent_id)
        if agent is None or not agent.enabled or not p.access.can("agents.view", agent.unit_id):
            raise HTTPException(404, "Агент не найден")
    classification = body.classification or await settings_store.get(db, "default_classification")
    title = " ".join(body.request.split())[:120]
    r = Run(title=title, request=body.request, mode="direct" if agent else "team",
            direct_agent_id=agent.id if agent else None, initiator_id=p.user.id, unit_id=unit_id,
            classification=classification, origin=origin)
    db.add(r)
    await db.commit()
    await audit.record("runs.start", actor=p.user, ip=p.ip, target_type="run", target_id=r.id, target_name=title,
                       data={"mode": r.mode, "classification": classification, "agent": agent.name if agent else None})
    engine.start(r.id)
    return run_out(r, [])


@router.get("/{run_id}")
async def run_detail(run_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    r = await get_run(db, p, run_id)
    steps = list((await db.execute(select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.idx))).scalars())
    out = run_out(r, steps)
    if r.approval_id:
        a = await db.get(Approval, r.approval_id)
        if a:
            out["approval"] = {"id": a.id, "status": a.status, "reasons": a.reasons, "note": a.decision_note}
    return out


async def session_alive(session_id: str) -> bool:
    async with sessionmaker()() as s:
        sess = await s.get(UserSession, session_id)
        user = await s.get(User, sess.user_id) if sess else None
        return bool(sess and sess.revoked_at is None and aware(sess.expires_at) > utcnow() and user and user.is_active)


@router.get("/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0, p: Principal = Depends(current),
                 db: AsyncSession = Depends(get_db)):
    """SSE: сначала история (после after), затем живые события. Подписка — до чтения истории, без дыр."""
    r = await get_run(db, p, run_id)
    session_id = p.session.id

    async def stream():
        async with bus.subscribe(f"run:{r.id}") as q:
            last = after
            async with sessionmaker()() as s:            # своя сессия: зависимость закрывается раньше потока
                rows = (await s.execute(select(RunEvent).where(RunEvent.run_id == r.id, RunEvent.id > after)
                                        .order_by(RunEvent.id))).scalars().all()
                status = (await s.get(Run, r.id)).status
            for ev in rows:
                last = ev.id
                yield f"id: {ev.id}\ndata: {json.dumps({'id': ev.id, 'ts': iso(ev.ts), 'type': ev.type, 'data': ev.data}, ensure_ascii=False)}\n\n"
            if status in FINAL:                          # задача завершена — история отдана, поток закрываем
                return
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    if not await session_alive(session_id):     # выход, отзыв сессии, отключение — поток закрывается
                        return
                    yield ": ping\n\n"
                    continue
                if ev["id"] <= last:
                    continue
                last = ev["id"]
                yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev["type"] == "status" and ev["data"].get("status") in FINAL:
                    return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    r = await get_run(db, p, run_id)
    if r.initiator_id != p.user.id and not p.access.can("runs.cancel_any", r.unit_id):
        raise await deny(p, p.access.decide("runs.cancel_any", r.unit_id), "runs.cancel", r.title)
    if r.status == "awaiting_approval":
        a = await db.get(Approval, r.approval_id) if r.approval_id else None
        if a and a.status == "pending":
            a.status = "cancelled"
        r.status, r.error, r.finished_at = "cancelled", "отменено до визы", utcnow()
        await db.commit()
    elif r.status in ACTIVE:
        if not engine.cancel(r.id):
            r.status, r.finished_at = "cancelled", utcnow()
            await db.commit()
    else:
        raise HTTPException(409, "Задача уже завершена")
    await audit.record("runs.cancel", actor=p.user, ip=p.ip, target_type="run", target_id=r.id, target_name=r.title)
    return {"ok": True}


@router.post("/{run_id}/retry")
async def retry(run_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    r = await get_run(db, p, run_id)
    body = RunIn(request=r.request, unit_id=r.unit_id, classification=r.classification, agent_id=r.direct_agent_id)
    return await _create(body, p, db, origin=r.origin)          # недоверенное происхождение сохраняется
