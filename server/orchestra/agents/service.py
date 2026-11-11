"""Агенты: импорт/экспорт манифестов, версии промтов, публикация (сразу или через визу)."""
import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.agents import manifest
from orchestra.agents.models import Agent, PromptVersion
from orchestra.approvals import service as approvals
from orchestra.audit import service as audit
from orchestra.db.base import utcnow
from orchestra.iam.models import OrgUnit, User
from orchestra.providers.egress import CLASSIFICATIONS
from orchestra.settings import get_settings
from orchestra.tools.registry import TOOLS

log = logging.getLogger("orchestra.agents")
EFFORTS = ("low", "medium", "high")


def sanitize(spec: dict) -> dict:
    """Общая чистка входа из формы и из манифеста: только известные инструменты, грифы, уровни."""
    spec = dict(spec)
    spec["tools"] = [t for t in spec.get("tools", []) if t in TOOLS]
    if spec.get("classification") not in CLASSIFICATIONS:
        spec["classification"] = "internal"
    if spec.get("effort") not in EFFORTS:
        spec["effort"] = "medium"
    spec["max_tokens"] = max(256, min(int(spec.get("max_tokens") or 4096), 64000))
    if spec.get("role") not in manifest.ROLES:
        spec["role"] = "specialist"
    return spec


async def next_version(db: AsyncSession, agent_id: str) -> int:
    return ((await db.execute(select(func.max(PromptVersion.version)).where(PromptVersion.agent_id == agent_id)))
            .scalar() or 0) + 1


async def activate(db: AsyncSession, agent: Agent, pv: PromptVersion, user: User | None) -> None:
    await db.execute(update(PromptVersion).where(PromptVersion.agent_id == agent.id, PromptVersion.status == "active")
                     .values(status="archived"))
    pv.status, pv.published_at, pv.published_by = "active", utcnow(), user.id if user else None
    agent.active_prompt_id = pv.id


async def seed_agents(db: AsyncSession) -> int:
    """Агенты из agents/*.md (определения в git). Новые манифесты досеваются и в работающие установки,
    но удалённого администратором агента не возрождаем: посеянные slug — в system_settings."""
    from orchestra.system import settings_store
    existing = {a.slug: a for a in (await db.execute(select(Agent))).scalars()}
    seeded = await settings_store.get(db, "seeded_agents")
    if seeded is None:                                   # установка до появления списка: всё, что есть, — посеяно
        seeded = list(existing)
    units = {u.name.lower(): u.id for u in (await db.execute(select(OrgUnit))).scalars()}
    specs = []
    for path in sorted(get_settings().agents_dir.glob("*.md")):
        try:
            specs.append(sanitize(manifest.parse(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            log.exception("манифест %s не прочитан", path.name)
    fresh = [s for s in specs if s["slug"] not in existing and s["slug"] not in seeded]
    await settings_store.put(db, "seeded_agents", sorted(set(seeded) | {s["slug"] for s in specs}), None)
    if not fresh:
        await db.commit()
        return 0
    by_slug: dict[str, Agent] = dict(existing)
    specs = fresh
    for spec in specs:
        a = Agent(slug=spec["slug"], name=spec["name"], description=spec["description"], role=spec["role"],
                  unit_id=units.get(spec["department"].lower()), capabilities=spec["capabilities"],
                  tools=spec["tools"], classification=spec["classification"], effort=spec["effort"],
                  max_tokens=spec["max_tokens"])
        db.add(a)
        await db.flush()
        pv = PromptVersion(agent_id=a.id, version=1, body=spec["prompt"], note="из манифеста agents/")
        db.add(pv)
        await db.flush()
        await activate(db, a, pv, None)
        by_slug[a.slug] = a
    for spec in specs:                                   # руководителей — вторым проходом, когда все созданы
        boss = by_slug.get(manifest.slugify(spec["reports_to"])) if spec["reports_to"] else None
        if boss:
            by_slug[spec["slug"]].reports_to_id = boss.id
    await db.commit()
    return len(specs)


async def export_manifest(db: AsyncSession, agent: Agent) -> str:
    unit = await db.get(OrgUnit, agent.unit_id) if agent.unit_id else None
    boss = await db.get(Agent, agent.reports_to_id) if agent.reports_to_id else None
    pv = await db.get(PromptVersion, agent.active_prompt_id) if agent.active_prompt_id else None
    return manifest.render({
        "name": agent.name, "slug": agent.slug, "description": agent.description, "role": agent.role,
        "department": unit.name if unit else "", "reports_to": boss.slug if boss else "",
        "capabilities": agent.capabilities, "tools": agent.tools, "classification": agent.classification,
        "effort": agent.effort, "max_tokens": agent.max_tokens, "prompt": pv.body if pv else "",
    })


@approvals.on("prompt_publish")
async def _on_prompt_publish(db: AsyncSession, a, approver: User, approved: bool):
    pv = await db.get(PromptVersion, a.target_id)
    if pv is None or pv.status != "pending":
        return None
    agent = await db.get(Agent, pv.agent_id)
    if approved and agent is not None:
        await activate(db, agent, pv, approver)

        async def after():
            await audit.record("prompt.published", actor=approver, target_type="agent", target_id=agent.id,
                               target_name=f"{agent.name} v{pv.version}", data={"via": "approval"})
        return after
    pv.status = "draft"
    return None


@approvals.on("agent_update")
async def _on_agent_update(db: AsyncSession, a, approver: User, approved: bool):
    agent = await db.get(Agent, a.target_id)
    if agent is None or not approved:
        return None
    for k, v in (a.payload.get("changes") or {}).items():
        if hasattr(agent, k) and k not in ("id", "slug", "active_prompt_id", "created_by"):
            setattr(agent, k, v)
    return None
