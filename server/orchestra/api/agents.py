import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.agents import manifest
from orchestra.agents import service as svc
from orchestra.agents.models import Agent, PromptVersion
from orchestra.api.schemas import Strict, iso
from orchestra.approvals import service as approvals
from orchestra.audit import service as audit
from orchestra.db.session import get_db
from orchestra.iam.deps import Principal, current, deny, require
from orchestra.iam.models import OrgUnit
from orchestra.providers.models import Provider
from orchestra.tools.registry import catalog as tools_catalog

router = APIRouter(prefix="/api/agents", tags=["agents"])


class ChainRef(Strict):
    provider_id: str
    model: str = Field(default="", max_length=200)


class AgentIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    role: Literal["orchestrator", "lead", "specialist", "service"] = "specialist"
    unit_id: str | None = None
    reports_to_id: str | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    tools: list[str] = Field(default_factory=list, max_length=30)
    model_chain: list[ChainRef] = Field(default_factory=list, max_length=6)
    classification: Literal["public", "internal", "confidential", "secret"] = "internal"
    effort: Literal["low", "medium", "high"] = "medium"
    max_tokens: int = Field(default=4096, ge=256, le=64000)
    enabled: bool = True
    prompt: str = Field(default="", max_length=60000)


class AgentPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    role: Literal["orchestrator", "lead", "specialist", "service"] | None = None
    unit_id: str | None = None
    reports_to_id: str | None = None
    capabilities: list[str] | None = Field(default=None, max_length=20)
    tools: list[str] | None = Field(default=None, max_length=30)
    model_chain: list[ChainRef] | None = Field(default=None, max_length=6)
    classification: Literal["public", "internal", "confidential", "secret"] | None = None
    effort: Literal["low", "medium", "high"] | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=64000)
    enabled: bool | None = None


class PromptIn(Strict):
    body: str = Field(min_length=1, max_length=60000)
    note: str = Field(default="", max_length=500)


class ImportIn(Strict):
    text: str = Field(min_length=10, max_length=80000)


def agent_out(a: Agent, pv: PromptVersion | None, show_prompt: bool) -> dict:
    return {"id": a.id, "slug": a.slug, "name": a.name, "description": a.description, "role": a.role,
            "unit_id": a.unit_id, "reports_to_id": a.reports_to_id, "capabilities": a.capabilities,
            "tools": a.tools, "model_chain": a.model_chain, "classification": a.classification,
            "effort": a.effort, "max_tokens": a.max_tokens, "enabled": a.enabled,
            "prompt_version": pv.version if pv else None,
            "prompt": pv.body if (pv and show_prompt) else None, "updated_at": iso(a.updated_at)}


async def get_agent(db: AsyncSession, agent_id: str) -> Agent:
    a = await db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(404, "Агент не найден")
    return a


async def validate_refs(db: AsyncSession, data: dict, self_id: str | None = None) -> None:
    if data.get("unit_id") and await db.get(OrgUnit, data["unit_id"]) is None:
        raise HTTPException(422, "Подразделение не найдено")
    if data.get("reports_to_id"):
        if data["reports_to_id"] == self_id or await db.get(Agent, data["reports_to_id"]) is None:
            raise HTTPException(422, "Руководитель указан неверно")
    for ref in data.get("model_chain") or []:
        if await db.get(Provider, ref["provider_id"]) is None:
            raise HTTPException(422, "Модель из цепочки не найдена")


@router.get("/tools")
async def tools(p: Principal = Depends(current)):
    return tools_catalog()


@router.get("")
async def list_agents(p: Principal = Depends(require("agents.view")), db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(Agent).order_by(Agent.name))).scalars().all()
    pvs = {pv.id: pv for pv in (await db.execute(select(PromptVersion).where(PromptVersion.status == "active"))).scalars()}
    return [agent_out(a, pvs.get(a.active_prompt_id), False) for a in agents if p.access.can("agents.view", a.unit_id)]


@router.get("/{agent_id}")
async def agent_detail(agent_id: str, p: Principal = Depends(require("agents.view")), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    if not p.access.can("agents.view", a.unit_id):
        raise HTTPException(404, "Агент не найден")
    pv = await db.get(PromptVersion, a.active_prompt_id) if a.active_prompt_id else None
    out = agent_out(a, pv, p.access.can("prompts.view", a.unit_id))
    out["can"] = {k: p.access.decide(k, a.unit_id).effect for k in
                  ("agents.edit", "agents.delete", "prompts.view", "prompts.edit", "prompts.publish")}
    return out


@router.post("")
async def create_agent(body: AgentIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    d = p.access.decide("agents.edit", body.unit_id)
    if not d.allowed:
        raise await deny(p, d, "agents.create", body.name)
    data = svc.sanitize(body.model_dump())
    await validate_refs(db, data)
    base = slug = manifest.slugify(body.name)
    n = 1
    while (await db.execute(select(Agent.id).where(Agent.slug == slug))).scalar():
        n += 1
        slug = f"{base}-{n}"
    prompt = data.pop("prompt")
    a = Agent(slug=slug, created_by=p.user.id, **data)
    db.add(a)
    await db.flush()
    pv = PromptVersion(agent_id=a.id, version=1, body=prompt or f"Ты — {a.name}. {a.description}", note="создание",
                       author_id=p.user.id)
    db.add(pv)
    await db.flush()
    await svc.activate(db, a, pv, p.user)
    await db.commit()
    await audit.record("agents.create", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id, target_name=a.name)
    return agent_out(a, pv, True)


@router.patch("/{agent_id}")
async def patch_agent(agent_id: str, body: AgentPatch, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    data = body.model_dump(exclude_unset=True)
    merged = svc.sanitize({**svc_fields(a), **data})
    for k in SANITIZED:
        if k in data:
            data[k] = merged[k]
    await validate_refs(db, data, a.id)
    # смена подразделения — право нужно и там, и там
    d = p.access.decide("agents.edit", a.unit_id)
    if "unit_id" in data and d.allowed:
        d = p.access.decide("agents.edit", data["unit_id"])
    if d.needs_approval:
        req = await approvals.request(db, kind="agent_update", title=f"Изменить агента «{a.name}»", requester=p.user,
                                      unit_id=a.unit_id, permissions=["agents.edit"], reasons=[d.reason],
                                      approver_role_ids=d.approver_role_ids, payload={"changes": data},
                                      summary=", ".join(data), target_type="agent", target_id=a.id)
        await db.commit()
        return {"pending_approval": req.id}
    if not d.allowed:
        raise await deny(p, d, "agents.update", a.name)
    for k, v in data.items():
        setattr(a, k, v)
    await db.commit()
    await audit.record("agents.update", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id,
                       target_name=a.name, data=data)
    pv = await db.get(PromptVersion, a.active_prompt_id) if a.active_prompt_id else None
    return agent_out(a, pv, p.access.can("prompts.view", a.unit_id))


SANITIZED = ("tools", "classification", "effort", "max_tokens", "role")


def svc_fields(a: Agent) -> dict:
    return {k: getattr(a, k) for k in SANITIZED}


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    d = p.access.decide("agents.delete", a.unit_id)
    if not d.allowed:
        raise await deny(p, d, "agents.delete", a.name)
    await db.delete(a)
    await db.commit()
    await audit.record("agents.delete", actor=p.user, ip=p.ip, target_type="agent", target_id=agent_id, target_name=a.name)
    return {"ok": True}


@router.get("/{agent_id}/prompts")
async def prompts(agent_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    d = p.access.decide("prompts.view", a.unit_id)
    if not d.allowed:
        raise await deny(p, d, "prompts.view", a.name)
    rows = (await db.execute(select(PromptVersion).where(PromptVersion.agent_id == a.id)
                             .order_by(PromptVersion.version.desc()))).scalars()
    return [{"id": v.id, "version": v.version, "body": v.body, "note": v.note, "status": v.status,
             "author_id": v.author_id, "created_at": iso(v.created_at), "published_at": iso(v.published_at)}
            for v in rows]


@router.post("/{agent_id}/prompts")
async def new_prompt(agent_id: str, body: PromptIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    d = p.access.decide("prompts.edit", a.unit_id)
    if not d.allowed:
        raise await deny(p, d, "prompts.edit", a.name)
    pv = PromptVersion(agent_id=a.id, version=await svc.next_version(db, a.id), body=body.body, note=body.note,
                       author_id=p.user.id)
    db.add(pv)
    await db.commit()
    await audit.record("prompt.draft", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id,
                       target_name=f"{a.name} v{pv.version}", data={"note": body.note})
    return {"id": pv.id, "version": pv.version, "status": pv.status}


@router.post("/{agent_id}/prompts/{prompt_id}/publish")
async def publish(agent_id: str, prompt_id: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    pv = await db.get(PromptVersion, prompt_id)
    if pv is None or pv.agent_id != a.id:
        raise HTTPException(404, "Версия не найдена")
    if pv.status not in ("draft", "archived"):
        raise HTTPException(409, "Эта версия уже опубликована или ждёт визы")
    d = p.access.decide("prompts.publish", a.unit_id)
    if d.needs_approval:
        pv.status = "pending"
        req = await approvals.request(db, kind="prompt_publish", title=f"Промт «{a.name}» v{pv.version}",
                                      requester=p.user, unit_id=a.unit_id, permissions=["prompts.publish"],
                                      reasons=[d.reason], approver_role_ids=d.approver_role_ids,
                                      summary=pv.note or "без комментария", payload={"body": pv.body[:20000]},
                                      target_type="prompt", target_id=pv.id)
        await db.commit()
        return {"pending_approval": req.id}
    if not d.allowed:
        raise await deny(p, d, "prompts.publish", a.name)
    await svc.activate(db, a, pv, p.user)
    await db.commit()
    await audit.record("prompt.published", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id,
                       target_name=f"{a.name} v{pv.version}")
    return {"ok": True, "version": pv.version}


class TryIn(Strict):
    input: str = Field(min_length=1, max_length=20000)
    body: str | None = Field(default=None, max_length=60000)          # текст из редактора (ещё не сохранён)
    prompt_id: str | None = None                                       # или конкретная версия
    classification: Literal["public", "internal", "confidential", "secret"] | None = None


@router.post("/{agent_id}/try")
async def try_prompt(agent_id: str, body: TryIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Песочница: агент с выбранным промтом отвечает на пример. Ничего не публикуется, задача не создаётся."""
    from orchestra.orchestration import executor
    from orchestra.orchestration.engine import agent_chain, tool_usable
    from orchestra.providers import egress
    from orchestra.providers import service as models
    from orchestra.system import settings_store
    from orchestra.tools.registry import TOOLS

    a = await get_agent(db, agent_id)
    d = p.access.decide("prompts.edit", a.unit_id)
    if not d.allowed:
        raise await deny(p, d, "prompt.try", a.name)
    if body.prompt_id:
        pv = await db.get(PromptVersion, body.prompt_id)
        if pv is None or pv.agent_id != a.id:
            raise HTTPException(404, "Версия не найдена")
        prompt, label = pv.body, f"v{pv.version}"
    else:
        prompt, label = body.body or "", "черновик"
    cls = egress.max_rank([body.classification or await settings_store.get(db, "default_classification"),
                           a.classification])
    if egress.rank(cls) >= egress.rank("confidential") and not p.access.can("data.confidential", a.unit_id):
        raise await deny(p, p.access.decide("data.confidential", a.unit_id), "prompt.try", a.name)
    tools = [t for t in a.tools if t in TOOLS and tool_usable(TOOLS[t], p.access, a.unit_id, classification=cls)]
    ctx = models.CallContext(access=p.access, classification=cls, purpose="test", unit_id=a.unit_id, agent_id=a.id)
    used: list[str] = []

    async def emit(kind: str, data: dict) -> None:
        if kind == "tool":
            used.append(str(data.get("tool")))

    t0 = time.monotonic()
    try:
        res = await asyncio.wait_for(executor.run_step(
            db, ctx, a, prompt, executor.place_card(a, p.access.tree.names.get(a.unit_id or "", ""), "", []),
            body.input, [], tools, await agent_chain(db, a), emit), timeout=240)
    except models.RoutingError as exc:
        raise HTTPException(422, f"Модель недоступна: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Модель не ответила за 4 минуты") from exc
    await audit.record("prompt.try", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id,
                       target_name=f"{a.name} · {label}", data={"chars": len(prompt), "model": res.model})
    return {"text": res.text, "provider": res.provider, "model": res.model, "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out, "ms": int((time.monotonic() - t0) * 1000), "tools": used, "label": label,
            "classification": cls}


@router.get("/{agent_id}/manifest", response_class=PlainTextResponse)
async def export(agent_id: str, p: Principal = Depends(require("prompts.view")), db: AsyncSession = Depends(get_db)):
    a = await get_agent(db, agent_id)
    if not (p.access.can("agents.view", a.unit_id) and p.access.can("prompts.view", a.unit_id)):
        raise HTTPException(404, "Агент не найден")
    await audit.record("agents.export", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id, target_name=a.name)
    return PlainTextResponse(await svc.export_manifest(db, a), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{a.slug}.md"'})


@router.post("/import")
async def import_manifest(body: ImportIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    spec = svc.sanitize(manifest.parse(body.text))
    unit = (await db.execute(select(OrgUnit).where(OrgUnit.name == spec["department"]))).scalar() if spec["department"] else None
    d = p.access.decide("agents.edit", unit.id if unit else None)
    if not d.allowed:
        raise await deny(p, d, "agents.import", spec["name"])
    if (await db.execute(select(Agent).where(Agent.slug == spec["slug"]))).scalar():
        raise HTTPException(409, f"Агент «{spec['slug']}» уже есть — правьте его в редакторе")
    boss = (await db.execute(select(Agent).where(Agent.slug == spec["reports_to"]))).scalar() if spec["reports_to"] else None
    a = Agent(slug=spec["slug"], name=spec["name"], description=spec["description"], role=spec["role"],
              unit_id=unit.id if unit else None, reports_to_id=boss.id if boss else None,
              capabilities=spec["capabilities"], tools=spec["tools"], classification=spec["classification"],
              effort=spec["effort"], max_tokens=spec["max_tokens"], created_by=p.user.id)
    db.add(a)
    await db.flush()
    pv = PromptVersion(agent_id=a.id, version=1, body=spec["prompt"], note="импорт манифеста", author_id=p.user.id)
    db.add(pv)
    await db.flush()
    await svc.activate(db, a, pv, p.user)
    await db.commit()
    await audit.record("agents.import", actor=p.user, ip=p.ip, target_type="agent", target_id=a.id, target_name=a.name)
    return agent_out(a, pv, True)
