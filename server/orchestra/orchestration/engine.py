"""Движок задач. Состояния: stage start → (виза) → план → stage plan → (виза по плану) → exec.

Ворота (gate) перед планом: право ставить задачи, гриф «конфиденциально», внешняя модель
для планирования. Ворота после плана: инструменты агентов и внешние модели шагов — визирующий
видит готовый план. Права «с одобрением», выданные визой, действуют только на эту задачу.

Исполнение: шаги — граф зависимостей; независимые идут параллельно (семафор), зависимый ждёт
предков и получает их результаты как данные. Сбой шага → повтор; не вышло → зависимые пропускаются.
"""
import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.agents.models import Agent, PromptVersion
from orchestra.approvals import service as approvals
from orchestra.audit import service as audit
from orchestra.core.events import bus
from orchestra.db.base import utcnow
from orchestra.db.session import sessionmaker
from orchestra.iam.authz import AccessContext, load_access, load_tree
from orchestra.iam.models import User
from orchestra.orchestration import executor, planner
from orchestra.orchestration.models import ACTIVE, FINAL, Run, RunEvent, RunStep
from orchestra.providers import egress
from orchestra.providers import service as models
from orchestra.providers.base import ChatRequest, Message
from orchestra.providers.models import Provider
from orchestra.settings import get_settings
from orchestra.system import settings_store
from orchestra.tools.registry import TOOLS

log = logging.getLogger("orchestra.engine")


async def emit(run_id: str, type_: str, data: dict) -> None:
    async with sessionmaker()() as s:
        ev = RunEvent(run_id=run_id, type=type_, data=data)
        s.add(ev)
        await s.commit()
        bus.publish(f"run:{run_id}", {"id": ev.id, "ts": ev.ts.isoformat(), "type": type_, "data": data})


async def agent_chain(db: AsyncSession, agent: Agent | None) -> list[dict]:
    if agent and agent.model_chain:
        return list(agent.model_chain)
    return await models.default_chain(db)


async def planner_agent(db: AsyncSession) -> Agent | None:
    return (await db.execute(select(Agent).where(Agent.role == "orchestrator", Agent.enabled.is_(True))
                             .limit(1))).scalar()


async def planner_chain(db: AsyncSession) -> list[dict]:
    orch = await planner_agent(db)
    if orch and orch.model_chain:
        return list(orch.model_chain)
    return await models.default_chain(db, "planner_chain")


async def active_prompt(db: AsyncSession, agent: Agent) -> tuple[str, int | None]:
    if not agent.active_prompt_id:
        return "", None
    pv = await db.get(PromptVersion, agent.active_prompt_id)
    return (pv.body, pv.version) if pv else ("", None)


def tool_usable(tool, access: AccessContext, unit_id: str | None, preapproved=(), *, classification: str = "internal",
                origin: str = "") -> bool:
    """Предлагающий инструмент (запись во внешнюю систему) доступен, если право не запрещено:
    исполнит человек. Остальные — только при allow или визе на задачу.

    Выход в интернет закрыт в задачах с грифом «конфиденциально» и выше и в задачах от входящих
    вебхуков: инструкция, внедрённая в документ или событие, могла бы унести данные в адресе запроса."""
    if tool.spec.permission == "tools.web" and (
            egress.rank(classification) >= egress.rank("confidential") or origin == "webhook"):
        return False
    d = access.decide(tool.spec.permission, unit_id)
    if getattr(tool, "deferred", False):
        return d.effect != "deny"
    return d.allowed or tool.spec.permission in preapproved


async def external_need(db: AsyncSession, access: AccessContext, run: Run, chain: list[dict],
                        classification: str | None = None) -> tuple[str, list[str]]:
    """'ok' — есть маршрут без визы; 'approval' — только через внешнее облако по визе; 'none' — маршрута нет."""
    policy = await settings_store.get(db, "egress_policy")
    cls = classification or run.classification
    base = models.CallContext(access=access, classification=cls, unit_id=run.unit_id,
                              external_preapproved="providers.external" in run.preapproved)
    pre = models.CallContext(access=access, classification=cls, unit_id=run.unit_id, external_preapproved=True)
    reasons = []
    for ref in chain:
        p = await db.get(Provider, ref.get("provider_id", ""))
        why = await models.check_route(db, base, p, policy)
        if why is None:
            return "ok", []
        reasons.append(why)
    for ref in chain:
        p = await db.get(Provider, ref.get("provider_id", ""))
        if await models.check_route(db, pre, p, policy) is None:
            return "approval", reasons
    return "none", reasons or ["у агента не настроены модели, и модели по умолчанию нет"]


class Engine:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}

    # ── управление ─────────────────────────────────────────────────────────────
    def start(self, run_id: str) -> None:
        if run_id in self.tasks and not self.tasks[run_id].done():
            return
        self.tasks[run_id] = asyncio.create_task(self._guard(run_id), name=f"run:{run_id}")

    def cancel(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def recover(self) -> int:
        """После перезапуска: прерванные на ходу задачи помечаются честно — их можно повторить."""
        async with sessionmaker()() as db:
            rows = (await db.execute(select(Run).where(Run.status.in_(ACTIVE)))).scalars().all()
            for r in rows:
                r.status, r.error, r.finished_at = "interrupted", "прервано перезапуском сервера", utcnow()
            await db.execute(update(RunStep).where(RunStep.status == "running").values(status="cancelled"))
            await db.commit()
        for r in rows:
            await emit(r.id, "status", {"status": "interrupted", "error": "прервано перезапуском сервера"})
        return len(rows)

    async def _finish(self, run_id: str, status: str, error: str = "") -> None:
        async with sessionmaker()() as db:
            run = await db.get(Run, run_id)
            if run is None or run.status in FINAL:
                return
            run.status, run.error, run.finished_at = status, error[:4000], utcnow()
            # шедший шаг — сорван (или остановлен), ждавшие — пропущены
            await db.execute(update(RunStep).where(RunStep.run_id == run_id, RunStep.status == "running")
                             .values(status="cancelled" if status == "cancelled" else "failed", error=error[:4000]))
            await db.execute(update(RunStep).where(RunStep.run_id == run_id, RunStep.status == "pending")
                             .values(status="cancelled" if status == "cancelled" else "skipped"))
            await db.commit()
        await emit(run_id, "status", {"status": status, "error": error})

    async def _guard(self, run_id: str) -> None:
        try:
            await self._drive(run_id)
        except asyncio.CancelledError:
            await asyncio.shield(self._finish(run_id, "cancelled", "остановлено"))
        except Exception as exc:
            log.exception("задача %s упала", run_id)
            await self._finish(run_id, "failed", f"внутренняя ошибка: {type(exc).__name__}")
        finally:
            self.tasks.pop(run_id, None)

    # ── машина состояний ───────────────────────────────────────────────────────
    async def _drive(self, run_id: str) -> None:
        async with sessionmaker()() as db:
            run = await db.get(Run, run_id)
            if run is None or run.status in FINAL or run.status == "awaiting_approval":
                return
            user = await db.get(User, run.initiator_id)
            access = await load_access(db, user)
            if run.stage == "start":
                if not await self._gate(db, run, access, None):
                    return
                await self._plan(db, run, access)
                if run.status in FINAL:
                    return
            if run.stage == "plan":
                steps = await self._steps(db, run.id)
                if not await self._gate(db, run, access, steps):
                    return
                run.stage = "exec"
                await db.commit()
        await self._execute(run_id)

    async def _steps(self, db: AsyncSession, run_id: str) -> list[RunStep]:
        return list((await db.execute(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.idx))).scalars())

    async def _gate(self, db: AsyncSession, run: Run, access: AccessContext, steps: list[RunStep] | None) -> bool:
        """True — можно дальше. Иначе задача ждёт визы или завершена с понятной причиной."""
        need: list[tuple[str, str]] = []
        fatal: list[str] = []
        if steps is None:
            need.append(("runs.start", "постановка задачи"))
            if egress.rank(run.classification) >= egress.rank("confidential"):
                need.append(("data.confidential", f"гриф «{egress.CLASS_TITLES[run.classification]}»"))
            if run.mode == "team":
                state, why = await external_need(db, access, run, await planner_chain(db))
                if state == "approval":
                    need.append(("providers.external", "планирование во внешней облачной модели"))
                elif state == "none":
                    fatal.append("оркестратору недоступна ни одна модель: " + "; ".join(why))
        else:
            for st in steps:
                agent = await db.get(Agent, st.agent_id) if st.agent_id else None
                if agent is None:
                    fatal.append(f"агент шага {st.idx + 1} удалён")
                    continue
                step_class = egress.max_rank([run.classification, agent.classification])
                for tk in agent.tools:
                    # запись во внешние системы агент только предлагает — визируется каждое действие, не план;
                    # выход в интернет в конфиденциальной задаче / задаче от вебхука закрыт — визировать нечего
                    if tk in TOOLS and not getattr(TOOLS[tk], "deferred", False) and not (
                            TOOLS[tk].spec.permission == "tools.web"
                            and (egress.rank(step_class) >= egress.rank("confidential") or run.origin == "webhook")):
                        need.append((TOOLS[tk].spec.permission, f"{agent.name}: инструмент «{TOOLS[tk].spec.title}»"))
                state, why = await external_need(db, access, run, await agent_chain(db, agent),
                                                 egress.max_rank([run.classification, agent.classification]))
                if state == "approval":
                    need.append(("providers.external", f"{agent.name} работает во внешней облачной модели"))
                elif state == "none":
                    fatal.append(f"{agent.name}: нет доступной модели — " + "; ".join(why))

        missing, approvers, perms = [], set(), []
        for perm, reason in need:
            if perm in run.preapproved:
                continue
            d = access.decide(perm, run.unit_id)
            if d.allowed:
                continue
            if d.needs_approval:
                missing.append(reason)
                approvers |= set(d.approver_role_ids)
                if perm not in perms:
                    perms.append(perm)
            elif perm.startswith("tools."):
                await emit(run.id, "log", {"text": f"{reason} — отключён для этой задачи ({d.reason})"})
            else:
                fatal.append(f"{reason}: {d.reason}")
        if fatal:
            run.status, run.error, run.finished_at = "failed", "; ".join(fatal)[:4000], utcnow()
            await db.commit()
            await emit(run.id, "status", {"status": "failed", "error": run.error})
            return False
        if missing:
            title = f"Задача: {run.title}" if steps is None else f"План задачи: {run.title}"
            a = await approvals.request(
                db, kind="run", title=title, requester=access.user, unit_id=run.unit_id, permissions=perms,
                reasons=missing, approver_role_ids=sorted(approvers), summary=run.request[:2000],
                payload={"stage": run.stage, "steps": [{"agent": s.agent_name, "task": s.task} for s in steps or []]},
                target_type="run", target_id=run.id)
            run.status, run.approval_id = "awaiting_approval", a.id
            await db.commit()
            await emit(run.id, "approval", {"approval_id": a.id, "reasons": missing})
            await emit(run.id, "status", {"status": "awaiting_approval"})
            return False
        return True

    async def _plan(self, db: AsyncSession, run: Run, access: AccessContext) -> None:
        run.status = "planning"
        await db.commit()
        await emit(run.id, "status", {"status": "planning"})
        if run.mode == "direct":
            agent = await db.get(Agent, run.direct_agent_id) if run.direct_agent_id else None
            if agent is None or not agent.enabled:
                await self._fail(db, run, "агент не найден или выключен")
                return
            db.add(RunStep(run_id=run.id, idx=0, agent_id=agent.id, agent_name=agent.name, task=run.request))
            run.approach = f"Напрямую агенту «{agent.name}»"
        else:
            agents = [a for a in (await db.execute(select(Agent).where(Agent.enabled.is_(True),
                                                                       Agent.role.notin_(("orchestrator", "service")))
                                                   .order_by(Agent.name))).scalars()
                      if access.can("agents.view", a.unit_id)]
            if not agents:
                await self._fail(db, run, "нет доступных агентов")
                return
            tree = await load_tree(db)
            company = await settings_store.get(db, "company_name")
            orch = await planner_agent(db)
            extra, _ = await active_prompt(db, orch) if orch else ("", None)
            system = planner.PLAN_SYSTEM.format(company=company)
            if extra:
                system += f"\n\nУКАЗАНИЯ РУКОВОДСТВА ПО ПЛАНИРОВАНИЮ:\n{extra}"
            ctx = models.CallContext(access=access, classification=run.classification, purpose="plan",
                                     unit_id=run.unit_id, run_id=run.id, agent_id=orch.id if orch else None,
                                     external_preapproved="providers.external" in run.preapproved)
            s = get_settings()
            try:
                resp, _p = await models.complete(db, ctx, await planner_chain(db), ChatRequest(
                    system=system, messages=[Message("user", planner.plan_prompt(run.request, agents, tree.names))],
                    max_tokens=4096, json_mode=True, effort="medium"))
                approach, planned = planner.parse_plan(resp.text, agents, s.max_plan_steps)
            except models.RoutingError as exc:
                await self._fail(db, run, "планирование: " + str(exc))
                return
            except planner.PlanError as exc:
                await self._fail(db, run, f"план не получился: {exc}")
                return
            run.approach = approach
            for i, st in enumerate(planned):
                db.add(RunStep(run_id=run.id, idx=i, agent_id=st.agent.id, agent_name=st.agent.name, task=st.task,
                               depends_on=st.depends_on))
        run.stage = "plan"
        await db.commit()
        steps = await self._steps(db, run.id)
        await emit(run.id, "plan", {"approach": run.approach, "steps": [
            {"idx": x.idx, "agent": x.agent_name, "task": x.task, "depends_on": x.depends_on} for x in steps]})

    async def _fail(self, db: AsyncSession, run: Run, error: str) -> None:
        run.status, run.error, run.finished_at = "failed", error[:4000], utcnow()
        await db.commit()
        await emit(run.id, "status", {"status": "failed", "error": run.error})

    async def _execute(self, run_id: str) -> None:
        async with sessionmaker()() as db:
            run = await db.get(Run, run_id)
            run.status, run.started_at = "running", run.started_at or utcnow()
            await db.commit()
            steps = await self._steps(db, run_id)
            limit = int(await settings_store.get(db, "max_parallel_steps") or get_settings().max_parallel_steps)
        await emit(run_id, "status", {"status": "running"})

        sem = asyncio.Semaphore(max(1, min(limit, 8)))
        done = {s.idx: asyncio.Event() for s in steps}
        ok: dict[int, bool] = {}
        outputs: dict[int, tuple[str, str]] = {}

        async def worker(st: RunStep) -> None:
            try:
                for d in st.depends_on:
                    await done[d].wait()
                if not all(ok.get(d) for d in st.depends_on):
                    await self._mark(st.id, status="skipped", error="не выполнен шаг, от которого зависит")
                    await emit(run_id, "step", {"idx": st.idx, "status": "skipped"})
                    ok[st.idx] = False
                    return
                async with sem:
                    ok[st.idx] = await self._run_step(run_id, st.id, [outputs[d] for d in st.depends_on if d in outputs])
                if ok[st.idx]:
                    async with sessionmaker()() as s:
                        fresh = await s.get(RunStep, st.id)
                        outputs[st.idx] = (fresh.agent_name, fresh.output)
            finally:
                done[st.idx].set()

        await asyncio.gather(*(worker(s) for s in steps))
        await self._summarize(run_id, steps, ok, outputs)

    async def _mark(self, step_id: str, **values) -> None:
        async with sessionmaker()() as s:
            await s.execute(update(RunStep).where(RunStep.id == step_id).values(**values))
            await s.commit()

    async def _run_step(self, run_id: str, step_id: str, context: list[tuple[str, str]]) -> bool:
        retries = get_settings().step_retries
        async with sessionmaker()() as db:
            st = await db.get(RunStep, step_id)
            run = await db.get(Run, run_id)
            agent = await db.get(Agent, st.agent_id) if st.agent_id else None
            if agent is None:
                st.status, st.error = "failed", "агент удалён"
                await db.commit()
                await emit(run_id, "step", {"idx": st.idx, "status": "failed", "error": st.error})
                return False
            access = await load_access(db, await db.get(User, run.initiator_id))
            prompt, version = await active_prompt(db, agent)
            tree = await load_tree(db)
            boss = await db.get(Agent, agent.reports_to_id) if agent.reports_to_id else None
            siblings = [n for (n,) in (await db.execute(select(RunStep.agent_name).where(
                RunStep.run_id == run_id, RunStep.id != step_id))).all() if n != agent.name]
            extra = executor.place_card(agent, tree.names.get(agent.unit_id or "", ""), boss.name if boss else "",
                                        sorted(set(siblings)))
            step_class = egress.max_rank([run.classification, agent.classification])
            tool_keys = [t for t in agent.tools if t in TOOLS and tool_usable(
                TOOLS[t], access, run.unit_id, run.preapproved, classification=step_class, origin=run.origin)]
            chain = await agent_chain(db, agent)
            ctx = models.CallContext(access=access, classification=step_class,
                                     purpose="step", unit_id=run.unit_id, run_id=run_id, step_id=step_id,
                                     agent_id=agent.id, external_preapproved="providers.external" in run.preapproved)
            st.status, st.started_at, st.prompt_version = "running", utcnow(), version
            await db.commit()
            await emit(run_id, "step", {"idx": st.idx, "status": "running", "agent": agent.name})

            async def step_emit(type_: str, data: dict) -> None:
                await emit(run_id, type_, {"idx": st.idx, **data})

            last_error = ""
            for attempt in range(1, retries + 2):
                st.attempts = attempt
                await db.commit()                        # не держать запись открытой на время вызова модели
                try:
                    res = await executor.run_step(db, ctx, agent, prompt, extra, st.task, context, tool_keys, chain, step_emit)
                except models.RoutingError as exc:
                    last_error = str(exc)
                    if attempt <= retries:
                        await step_emit("log", {"text": f"сбой, повтор {attempt}: {last_error[:300]}"})
                        await asyncio.sleep(2 * attempt)
                        continue
                    break
                st.status, st.output, st.finished_at = "done", res.text, utcnow()
                st.provider_name, st.model, st.tools_used = res.provider, res.model, res.tools_used
                st.tokens_in, st.tokens_out = res.tokens_in, res.tokens_out
                await db.commit()
                await emit(run_id, "step", {"idx": st.idx, "status": "done", "agent": agent.name,
                                            "output": res.text, "model": f"{res.provider} · {res.model}"})
                return True
            st.status, st.error, st.finished_at = "failed", last_error[:4000], utcnow()
            await db.commit()
            await emit(run_id, "step", {"idx": st.idx, "status": "failed", "error": st.error})
            return False

    async def _summarize(self, run_id: str, steps: list[RunStep], ok: dict[int, bool],
                         outputs: dict[int, tuple[str, str]]) -> None:
        good = [i for i in sorted(ok) if ok[i]]
        if not good:
            async with sessionmaker()() as db:          # причина — из первого упавшего шага, а не «что-то пошло не так»
                errors = [e for (e,) in (await db.execute(select(RunStep.error).where(
                    RunStep.run_id == run_id, RunStep.status == "failed").order_by(RunStep.idx))).all() if e]
            await self._finish(run_id, "failed", "ни один шаг не выполнен" + (f": {errors[0]}" if errors else ""))
            return
        async with sessionmaker()() as db:
            run = await db.get(Run, run_id)
            if len(steps) == 1:
                result = outputs[good[0]][1]
            else:
                access = await load_access(db, await db.get(User, run.initiator_id))
                ctx = models.CallContext(access=access, classification=run.classification, purpose="summary",
                                         unit_id=run.unit_id, run_id=run_id,
                                         external_preapproved="providers.external" in run.preapproved)
                body = "\n\n".join(f"<результат шага {i + 1} агента=\"{outputs[i][0]}\">\n{outputs[i][1][:12000]}\n</результат>"
                                   for i in good)
                failed = [s for s in steps if not ok.get(s.idx)]
                if failed:
                    body += "\n\nНЕ ВЫПОЛНЕНЫ: " + "; ".join(f"шаг {s.idx + 1} ({s.agent_name})" for s in failed)
                try:
                    resp, _ = await models.complete(db, ctx, await planner_chain(db), ChatRequest(
                        system=planner.SUMMARY_SYSTEM,
                        messages=[Message("user", f"ЗАПРОС:\n{run.request}\n\nРЕЗУЛЬТАТЫ:\n{body}")], max_tokens=4096))
                    result = resp.text
                except models.RoutingError:
                    result = "\n\n".join(f"### {outputs[i][0]}\n{outputs[i][1]}" for i in good)
            sums = (await db.execute(select(RunStep.tokens_in, RunStep.tokens_out).where(RunStep.run_id == run_id))).all()
            run.tokens_in, run.tokens_out = sum(a for a, _ in sums), sum(b for _, b in sums)
            partial = len(good) < len(steps)
            run.result, run.status, run.finished_at = result, "succeeded", utcnow()
            run.error = "часть шагов не выполнена" if partial else ""
            await db.commit()
            initiator = await db.get(User, run.initiator_id)
        await emit(run_id, "result", {"text": result, "partial": partial})
        await emit(run_id, "status", {"status": "succeeded"})
        await audit.record("run.finished", actor=initiator, target_type="run", target_id=run_id,
                           target_name=run.title, data={"steps": len(steps), "ok": len(good)})


engine = Engine()


@approvals.on("run")
async def _on_run_approval(db: AsyncSession, a, approver: User, approved: bool):
    run = await db.get(Run, a.target_id)
    if run is None or run.status != "awaiting_approval":
        return None
    if not approved:
        run.status, run.error, run.finished_at = "rejected", f"отклонено: {a.decision_note or 'без комментария'}", utcnow()

        async def after_reject():
            await emit(run.id, "status", {"status": "rejected", "error": run.error})
        return after_reject
    run.preapproved = sorted(set(run.preapproved) | set(a.permissions))
    run.status = "queued"

    async def after_approve():
        await emit(run.id, "status", {"status": "queued", "approved_by": approver.display_name})
        engine.start(run.id)
    return after_approve
