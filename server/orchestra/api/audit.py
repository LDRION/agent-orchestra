from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.api.schemas import iso
from orchestra.audit import service as svc
from orchestra.audit.models import AuditEvent
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, require

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def events(action: str = "", actor_id: str = "", outcome: str = "", before: int = 0, limit: int = 100,
                 p: Principal = Depends(require("audit.view")), db: AsyncSession = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(max(1, min(limit, 500)))
    if action:
        q = q.where(AuditEvent.action.startswith(action))
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    if outcome:
        q = q.where(AuditEvent.outcome == outcome)
    if before:
        q = q.where(AuditEvent.seq < before)
    return [{"seq": e.seq, "ts": iso(e.ts), "actor_id": e.actor_id, "actor": e.actor_name, "ip": e.ip,
             "action": e.action, "target_type": e.target_type, "target_id": e.target_id, "target": e.target_name,
             "outcome": e.outcome, "data": e.data, "hash": e.hash[:12]} for e in (await db.execute(q)).scalars()]


@router.get("/verify")
async def verify(p: Principal = Depends(require("audit.view"))):
    return await svc.verify()
