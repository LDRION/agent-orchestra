from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import Strict, iso
from orchestra.approvals import service as svc
from orchestra.approvals.models import Approval
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, current, fresh
from orchestra.iam.models import User
from orchestra.iam.permissions import BY_KEY

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecideIn(Strict):
    approve: bool
    note: str = Field(default="", max_length=2000)


async def out(db: AsyncSession, a: Approval) -> dict:
    req = await db.get(User, a.requester_id)
    dec = await db.get(User, a.decided_by) if a.decided_by else None
    return {"id": a.id, "kind": a.kind, "title": a.title, "summary": a.summary, "reasons": a.reasons,
            "permissions": [{"key": k, "title": BY_KEY[k].title if k in BY_KEY else k} for k in a.permissions],
            "payload": a.payload, "target_type": a.target_type, "target_id": a.target_id, "unit_id": a.unit_id,
            "requester": req.display_name if req else "—", "requester_id": a.requester_id, "status": a.status,
            "decided_by": dec.display_name if dec else None, "decided_at": iso(a.decided_at),
            "decision_note": a.decision_note, "created_at": iso(a.created_at), "expires_at": iso(a.expires_at)}


@router.get("/inbox")
async def inbox(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    return [await out(db, a) for a in await svc.inbox(db, p.access)]


@router.get("/mine")
async def mine(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Approval).where(Approval.requester_id == p.user.id)
                             .order_by(Approval.created_at.desc()).limit(100))).scalars()
    return [await out(db, a) for a in rows]


@router.get("/history")
async def history(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Approval).where(Approval.decided_by == p.user.id)
                             .order_by(Approval.decided_at.desc()).limit(100))).scalars()
    return [await out(db, a) for a in rows]


@router.post("/{approval_id}/decide")
async def decide(approval_id: str, body: DecideIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    pending = await db.get(Approval, approval_id)
    if pending is not None and pending.kind == "access_request" and body.approve:
        fresh(p)                                         # выдача роли — как назначение в «Доступах»
    try:
        a = await svc.decide(db, p.access, approval_id, body.approve, body.note, ip=p.ip)
    except svc.ApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    return await out(db, a)
