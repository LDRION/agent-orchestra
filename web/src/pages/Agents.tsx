import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, patch, post, type Agent, type ChainRef, type PromptVersion, type ProvidersPayload, type Run, type Tool, type TryResult } from '../api';
import { Icon } from '../icons';
import {
  CLASS_OPTS, Card, Classification, Empty, Field, Modal, OptSelect, Page, ROLE_LABEL, Status, UnitSelect,
  errText, initials, orderTree, useCan, useToast, useUnits, when,
} from '../ui';

const PERM_HINT: Record<string, string> = { 'tools.read': 'чтение', 'tools.web': 'интернет', 'tools.write': 'изменения' };

export function Agents() {
  const can = useCan();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['agents'], queryFn: () => get<Agent[]>('/api/agents') });
  const { data: units = [] } = useUnits();
  const [create, setCreate] = useState(false);
  const [imp, setImp] = useState(false);

  const groups = useMemo(() => {
    const byUnit = new Map<string, Agent[]>();
    (q.data ?? []).forEach((a) => byUnit.set(a.unit_id ?? '', [...(byUnit.get(a.unit_id ?? '') ?? []), a]));
    const order = orderTree(units).map(([u]) => u);
    const out = order.filter((u) => byUnit.has(u.id)).map((u) => ({ unit: u.name, desc: u.description, agents: byUnit.get(u.id)! }));
    if (byUnit.has('')) out.push({ unit: 'Без отдела', desc: '', agents: byUnit.get('')! });
    const rank = { orchestrator: 0, lead: 1, specialist: 2 };
    out.forEach((g) => g.agents.sort((a, b) => rank[a.role] - rank[b.role] || a.name.localeCompare(b.name)));
    return out;
  }, [q.data, units]);

  return (
    <Page title="Агенты и отделы" crumbs={`${q.data?.length ?? 0} агентов · определения в agents/*.md`}
      actions={can('agents.edit') && <>
        <button className="btn btn--line btn--sm" onClick={() => setImp(true)}><Icon name="upload" />Импорт .md</button>
        <button className="btn btn--primary btn--sm" onClick={() => setCreate(true)}><Icon name="plus" />Агент</button>
      </>}>
      <div className="org">
        {groups.map((g) => (
          <section key={g.unit} className="org__dept">
            <div className="org__dept-head"><h2>{g.unit}</h2><span className="muted small">{g.desc}</span></div>
            <div className="agents">
              {g.agents.map((a) => (
                <Link key={a.id} to={`/agents/${a.id}`} className={`agent ${a.enabled ? '' : 'agent--off'}`}>
                  <div className="agent__top">
                    <span className={`agent__mark agent__mark--${a.role}`}>{initials(a.name)}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="agent__name">{a.name}</div>
                      <div className="muted small">{ROLE_LABEL[a.role]}{a.prompt_version ? ` · промт v${a.prompt_version}` : ''}</div>
                    </div>
                  </div>
                  <div className="agent__desc">{a.description}</div>
                  <div className="row">
                    <Classification c={a.classification} />
                    {a.tools.map((t) => <span key={t} className="chip"><Icon name="bolt" />{t}</span>)}
                    {!a.model_chain.length && <span className="chip">модели по умолчанию</span>}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
        {!q.isLoading && !groups.length && <Empty title="Агентов нет" text="Создайте агента или импортируйте манифест .md" />}
      </div>
      {create && <CreateAgent onClose={() => setCreate(false)} onDone={(id) => nav(`/agents/${id}`)} />}
      {imp && <ImportAgent onClose={() => setImp(false)} onDone={(id) => nav(`/agents/${id}`)} />}
    </Page>
  );
}

function CreateAgent({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ name: '', description: '', unit_id: null as string | null, prompt: '' });
  const save = async () => {
    try {
      const a = await post<Agent>('/api/agents', f);
      qc.invalidateQueries({ queryKey: ['agents'] });
      toast('Агент создан');
      onDone(a.id);
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title="Новый агент" onClose={onClose} wide
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={!f.name} onClick={save}>Создать</button></>}>
      <div className="grid2">
        <Field label="Имя"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Сметчик" /></Field>
        <Field label="Отдел"><UnitSelect value={f.unit_id} onChange={(v) => setF({ ...f, unit_id: v })} allowEmpty /></Field>
      </div>
      <Field label="Что делает" hint="Оркестратор выбирает агента по этому описанию — пишите конкретно">
        <input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field>
      <Field label="Системный промт"><textarea className="textarea textarea--code" value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} placeholder="Ты — …" /></Field>
    </Modal>
  );
}

function ImportAgent({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState('');
  const load = (file: File) => file.text().then(setText);
  const save = async () => {
    try {
      const a = await post<Agent>('/api/agents/import', { text });
      qc.invalidateQueries({ queryKey: ['agents'] });
      onDone(a.id);
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title="Импорт манифеста" onClose={onClose} wide
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={text.length < 10} onClick={save}>Импортировать</button></>}>
      <p className="muted small">Формат — markdown с YAML-заголовком (как в agents/*.md и AI Vision): name, description, department, reports_to, tools, classification + тело-промт.</p>
      <input type="file" accept=".md,text/markdown,text/plain" onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />
      <textarea className="textarea textarea--code" value={text} onChange={(e) => setText(e.target.value)} placeholder={'---\nname: …\ndescription: …\n---\nТы — …'} />
    </Modal>
  );
}

// ── Редактор агента ─────────────────────────────────────────────────────────────
export function AgentEditor() {
  const { id = '' } = useParams();
  const q = useQuery({ queryKey: ['agent', id], queryFn: () => get<Agent>(`/api/agents/${id}`) });
  const [tab, setTab] = useState<'profile' | 'prompt' | 'models'>('profile');
  const a = q.data;
  if (!a) return <Page title="Агент">{q.error ? <Empty title="Агент не найден" /> : <div />}</Page>;
  const tabs = [['profile', 'Профиль'], ['prompt', 'Промт и версии'], ['models', 'Модели']] as const;
  return (
    <Page title={a.name} crumbs={<><Link to="/agents">Команда</Link> · {ROLE_LABEL[a.role]} · {a.slug}</>}
      actions={<>
        {!a.enabled && <span className="pill">выключен</span>}
        <a className="btn btn--line btn--sm" href={`/api/agents/${a.id}/manifest`}><Icon name="download" />.md</a>
        <TryAgent agent={a} />
      </>}>
      <div className="tabs">{tabs.map(([k, t]) => <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{t}</button>)}</div>
      {tab === 'profile' && <Profile a={a} />}
      {tab === 'prompt' && <Prompts a={a} />}
      {tab === 'models' && <AgentModels a={a} />}
    </Page>
  );
}

function TryAgent({ agent }: { agent: Agent }) {
  const nav = useNavigate();
  const toast = useToast();
  const can = useCan();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  if (!can('runs.start', ['allow', 'approval']) || agent.role === 'orchestrator') return null;
  const go = async () => {
    try {
      const r = await post<Run>('/api/runs', { request: text, agent_id: agent.id });
      nav(`/runs/${r.id}`);
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <>
      <button className="btn btn--primary btn--sm" onClick={() => setOpen(true)}><Icon name="play" />Задача агенту</button>
      {open && (
        <Modal title={`Задача: ${agent.name}`} onClose={() => setOpen(false)}
          foot={<button className="btn btn--primary" disabled={text.length < 3} onClick={go}><Icon name="send" />Отправить</button>}>
          <textarea className="textarea" autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Поручение напрямую этому агенту, без плана оркестратора" />
        </Modal>
      )}
    </>
  );
}

function useSaveAgent(a: Agent) {
  const toast = useToast();
  const qc = useQueryClient();
  return async (data: Partial<Agent>) => {
    try {
      const r = await patch<{ pending_approval?: string }>(`/api/agents/${a.id}`, data);
      if (r.pending_approval) toast('Изменение отправлено на визу');
      else toast('Сохранено');
      qc.invalidateQueries({ queryKey: ['agent', a.id] });
      qc.invalidateQueries({ queryKey: ['agents'] });
    } catch (e) { toast(errText(e), 'error'); }
  };
}

function Profile({ a }: { a: Agent }) {
  const save = useSaveAgent(a);
  const toast = useToast();
  const nav = useNavigate();
  const qc = useQueryClient();
  const tools = useQuery({ queryKey: ['tools'], queryFn: () => get<Tool[]>('/api/agents/tools') });
  const agents = useQuery({ queryKey: ['agents'], queryFn: () => get<Agent[]>('/api/agents') });
  const [f, setF] = useState(a);
  useEffect(() => setF(a), [a]);
  const edit = a.can?.['agents.edit'];
  const readonly = edit === 'deny';
  const set = <K extends keyof Agent>(k: K, v: Agent[K]) => setF({ ...f, [k]: v });
  const remove = async () => {
    if (!confirm(`Удалить агента «${a.name}»? История задач сохранится.`)) return;
    try { await del(`/api/agents/${a.id}`); qc.invalidateQueries({ queryKey: ['agents'] }); nav('/agents'); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <div className="grid2" style={{ alignItems: 'start' }}>
      <Card title="Кто это">
        <div className="stack">
          {edit === 'approval' && <div className="banner banner--warn"><Icon name="clock" size={16} />Изменения применяются после визы администратора ИИ.</div>}
          <Field label="Имя"><input className="input" value={f.name} disabled={readonly} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="Что делает" hint="По этому описанию оркестратор выбирает агента">
            <textarea className="textarea" value={f.description} disabled={readonly} onChange={(e) => set('description', e.target.value)} style={{ minHeight: 70 }} />
          </Field>
          <div className="grid2">
            <Field label="Отдел"><UnitSelect value={f.unit_id} onChange={(v) => set('unit_id', v)} allowEmpty /></Field>
            <Field label="Отчитывается">
              <select className="select" value={f.reports_to_id ?? ''} disabled={readonly} onChange={(e) => set('reports_to_id', e.target.value || null)}>
                <option value="">—</option>
                {(agents.data ?? []).filter((x) => x.id !== a.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            </Field>
          </div>
          <div className="grid2">
            <Field label="Роль">
              <select className="select" value={f.role} disabled={readonly} onChange={(e) => set('role', e.target.value as Agent['role'])}>
                {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Гриф данных агента" hint="Задача с этим агентом получает гриф не ниже"><OptSelect value={f.classification} options={CLASS_OPTS} onChange={(v) => set('classification', v)} /></Field>
          </div>
          <label className="check"><input type="checkbox" checked={f.enabled} disabled={readonly} onChange={(e) => set('enabled', e.target.checked)} />Агент работает</label>
        </div>
      </Card>
      <div className="stack">
        <Card title="Инструменты" sub="права проверяются у того, кто ставит задачу">
          <div className="stack">
            {(tools.data ?? []).map((t) => (
              <label key={t.key} className="check" style={{ alignItems: 'flex-start' }}>
                <input type="checkbox" disabled={readonly} checked={f.tools.includes(t.key)}
                  onChange={(e) => set('tools', e.target.checked ? [...f.tools, t.key] : f.tools.filter((x) => x !== t.key))} />
                <span><strong>{t.title}</strong> <span className="chip">{PERM_HINT[t.permission] ?? t.permission}</span><br /><span className="muted small">{t.description}</span></span>
              </label>
            ))}
          </div>
        </Card>
        <Card title="Мышление">
          <div className="grid2">
            <Field label="Глубина" hint="Для Claude и моделей с рассуждением">
              <select className="select" value={f.effort} disabled={readonly} onChange={(e) => set('effort', e.target.value)}>
                <option value="low">Быстро</option><option value="medium">Обычно</option><option value="high">Глубоко</option>
              </select>
            </Field>
            <Field label="Длина ответа, токенов"><input className="input" type="number" min={256} max={64000} value={f.max_tokens} disabled={readonly} onChange={(e) => set('max_tokens', Number(e.target.value))} /></Field>
          </div>
        </Card>
        {!readonly && (
          <div className="row">
            {a.can?.['agents.delete'] === 'allow' && <button className="btn btn--danger btn--sm" onClick={remove}><Icon name="trash" />Удалить</button>}
            <span className="spacer" />
            <button className="btn btn--primary" onClick={() => save({
              name: f.name, description: f.description, unit_id: f.unit_id, reports_to_id: f.reports_to_id, role: f.role,
              classification: f.classification, enabled: f.enabled, tools: f.tools, effort: f.effort, max_tokens: f.max_tokens,
            })}>{edit === 'approval' ? 'Отправить на визу' : 'Сохранить'}</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Prompts({ a }: { a: Agent }) {
  const toast = useToast();
  const qc = useQueryClient();
  const canView = a.can?.['prompts.view'] === 'allow';
  const q = useQuery({ queryKey: ['prompts', a.id], queryFn: () => get<PromptVersion[]>(`/api/agents/${a.id}/prompts`), enabled: canView });
  const active = q.data?.find((v) => v.status === 'active');
  const [body, setBody] = useState('');
  const [note, setNote] = useState('');
  const [view, setView] = useState<PromptVersion | null>(null);
  const activeId = active?.id;
  const activeBody = active?.body;
  useEffect(() => { if (activeBody !== undefined) setBody(activeBody); }, [activeId, activeBody]);
  if (!canView) return <Empty title="Промт скрыт" text="Нужно право «Читать системные промты»" />;
  const pub = a.can?.['prompts.publish'];
  const draft = async (publish: boolean) => {
    try {
      const v = await post<{ id: string; version: number }>(`/api/agents/${a.id}/prompts`, { body, note });
      if (publish) await publishV(v.id);
      else toast(`Черновик v${v.version} сохранён`);
      setNote('');
      qc.invalidateQueries({ queryKey: ['prompts', a.id] });
    } catch (e) { toast(errText(e), 'error'); }
  };
  const publishV = async (pid: string) => {
    try {
      const r = await post<{ pending_approval?: string; version?: number }>(`/api/agents/${a.id}/prompts/${pid}/publish`);
      toast(r.pending_approval ? 'Версия отправлена на визу' : `Опубликована v${r.version}`);
      qc.invalidateQueries({ queryKey: ['prompts', a.id] });
      qc.invalidateQueries({ queryKey: ['agent', a.id] });
    } catch (e) { toast(errText(e), 'error'); }
  };
  const changed = active ? body !== active.body : body.length > 0;
  return (
    <div className="grid2" style={{ gridTemplateColumns: 'minmax(0,1.6fr) minmax(0,1fr)', alignItems: 'start' }}>
      <Card title="Системный промт" sub={active ? `работает v${active.version}` : 'нет активной версии'}>
        <div className="stack">
          <textarea className="textarea textarea--code" style={{ minHeight: 420 }} value={body} onChange={(e) => setBody(e.target.value)}
            disabled={a.can?.['prompts.edit'] !== 'allow'} />
          {a.can?.['prompts.edit'] === 'allow' && (
            <>
              <Field label="Что поменяли и зачем" hint="Попадёт в историю версий и журнал аудита">
                <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Добавил формат таблицы замечаний" />
              </Field>
              <div className="row">
                <span className="spacer" />
                <button className="btn btn--line" disabled={!changed} onClick={() => draft(false)}>Сохранить черновик</button>
                {pub !== 'deny' && <button className="btn btn--primary" disabled={!changed} onClick={() => draft(true)}>{pub === 'approval' ? 'На визу' : 'Опубликовать'}</button>}
              </div>
            </>
          )}
        </div>
      </Card>
      <Card title="Версии" pad={false}>
        <table className="table"><tbody>
          {(q.data ?? []).map((v) => (
            <tr key={v.id} className="click" onClick={() => setView(v)}>
              <td className="mono">v{v.version}</td>
              <td><div>{v.note || <span className="muted">без комментария</span>}</div><div className="muted small">{when(v.created_at)}</div></td>
              <td style={{ textAlign: 'right' }}><Status s={v.status === 'pending' ? 'awaiting_approval' : v.status} /></td>
            </tr>
          ))}
        </tbody></table>
      </Card>
      {a.can?.['prompts.edit'] === 'allow' && <div style={{ gridColumn: '1 / -1' }}><Playground a={a} draft={body} activeId={active?.id} changed={changed} /></div>}
      {view && (
        <Modal title={`Версия ${view.version}`} wide onClose={() => setView(null)}
          foot={<>
            <button className="btn btn--ghost" onClick={() => { setBody(view.body); setView(null); }}>Взять в редактор</button>
            {(view.status === 'draft' || view.status === 'archived') && pub !== 'deny' &&
              <button className="btn btn--primary" onClick={() => { publishV(view.id); setView(null); }}>{pub === 'approval' ? 'На визу' : 'Опубликовать'}</button>}
          </>}>
          <div className="row"><Status s={view.status} /><span className="muted small">{view.note}</span></div>
          <pre className="step__out mono" style={{ maxHeight: 460 }}>{view.body}</pre>
        </Modal>
      )}
    </div>
  );
}

/** Песочница: черновик промта отвечает на пример — без публикации; можно сравнить с работающей версией. */
function Playground({ a, draft, activeId, changed }: { a: Agent; draft: string; activeId?: string; changed: boolean }) {
  const toast = useToast();
  const [input, setInput] = useState('');
  const [cls, setCls] = useState(a.classification);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ draft?: TryResult; active?: TryResult }>({});
  const run = async (compare: boolean) => {
    setBusy(true);
    setRes({});
    const call = (extra: Record<string, unknown>) => post<TryResult>(`/api/agents/${a.id}/try`, { input, classification: cls, ...extra });
    try {
      const [d, act] = await Promise.all([call({ body: draft }), compare && activeId ? call({ prompt_id: activeId }) : Promise.resolve(undefined)]);
      setRes({ draft: d, active: act });
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  const Out = ({ r, title }: { r: TryResult; title: string }) => (
    <div className="stack" style={{ gap: 6, minWidth: 0 }}>
      <strong>{title}</strong>
      <div className="row" style={{ gap: 6 }}>
        <span className="chip"><Icon name="cpu" />{r.provider} · {r.model}</span><span className="chip mono">{(r.ms / 1000).toFixed(1)} с</span>
        <span className="chip mono">{r.tokens_in}→{r.tokens_out}</span>{r.tools.map((t, i) => <span key={i} className="chip"><Icon name="bolt" />{t}</span>)}</div>
      <div className="step__out pre" style={{ marginTop: 0, maxHeight: 480 }}>{r.text}</div>
    </div>
  );
  return (
    <Card title={<span className="row"><Icon name="flask" />Песочница</span>} sub="проверка до публикации: задача не создаётся, расход учитывается как тестовый">
      <div className="stack">
        <textarea className="textarea" style={{ minHeight: 90 }} value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="Пример поручения, как его дал бы сотрудник. Например: «Проверь пояснительную записку: …»" />
        <div className="row">
          <label className="field" style={{ width: 300 }}><span>Гриф примера</span><OptSelect value={cls} options={CLASS_OPTS} onChange={setCls} /></label>
          <span className="spacer" />
          <button className="btn btn--line" disabled={busy || !input.trim()} onClick={() => run(false)}><Icon name="play" />{changed ? 'Проверить черновик' : 'Проверить'}</button>
          {changed && activeId && <button className="btn btn--primary" disabled={busy || !input.trim()} onClick={() => run(true)}>Сравнить с работающей</button>}
        </div>
        {busy && <div className="muted small">Модель думает… локальные модели отвечают 10–90 секунд.</div>}
        {res.draft && (res.active
          ? <div className="grid2" style={{ alignItems: 'start' }}><Out r={res.draft} title="Черновик" /><Out r={res.active} title="Работающая версия" /></div>
          : <Out r={res.draft} title={changed ? 'Черновик' : 'Текущий текст'} />)}
      </div>
    </Card>
  );
}

function AgentModels({ a }: { a: Agent }) {
  const save = useSaveAgent(a);
  const [chain, setChain] = useState<ChainRef[]>(a.model_chain);
  useEffect(() => setChain(a.model_chain), [a]);
  return (
    <Card title="Цепочка моделей" sub="первая доступная по грифу и правам; при сбое — следующая">
      <div className="stack">
        <ChainEditor value={chain} onChange={setChain} />
        <p className="muted small">Пусто — агент работает на моделях по умолчанию (раздел «Модели → Маршруты»). Конфиденциальные агенты держите на локальных моделях.</p>
        <div className="row"><span className="spacer" /><button className="btn btn--primary" onClick={() => save({ model_chain: chain })}>Сохранить</button></div>
      </div>
    </Card>
  );
}

export function ChainEditor({ value, onChange, disabled }: { value: ChainRef[]; onChange: (v: ChainRef[]) => void; disabled?: boolean }) {
  const q = useQuery({ queryKey: ['providers'], queryFn: () => get<ProvidersPayload>('/api/providers') });
  const providers = q.data?.providers ?? [];
  const move = (i: number, d: number) => {
    const x = [...value];
    [x[i], x[i + d]] = [x[i + d], x[i]];
    onChange(x);
  };
  return (
    <div className="chain">
      {value.map((ref, i) => {
        const p = providers.find((x) => x.id === ref.provider_id);
        return (
          <div key={i} className="chain__item">
            <span className="chain__n">{i + 1}</span>
            <select className="select" value={ref.provider_id} disabled={disabled} onChange={(e) => onChange(value.map((r, j) => (j === i ? { ...r, provider_id: e.target.value } : r)))}>
              {!p && <option value={ref.provider_id}>удалена</option>}
              {providers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <input className="input" value={ref.model} disabled={disabled} placeholder={p?.default_model || 'модель по умолчанию'}
              onChange={(e) => onChange(value.map((r, j) => (j === i ? { ...r, model: e.target.value } : r)))} />
            <div className="row" style={{ flexWrap: 'nowrap', gap: 2 }}>
              <button className="btn btn--ghost btn--icon btn--xs" disabled={disabled || i === 0} onClick={() => move(i, -1)} aria-label="Выше"><Icon name="up" /></button>
              <button className="btn btn--ghost btn--icon btn--xs" disabled={disabled || i === value.length - 1} onClick={() => move(i, 1)} aria-label="Ниже"><Icon name="down" /></button>
              <button className="btn btn--ghost btn--icon btn--xs" disabled={disabled} onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Убрать"><Icon name="x" /></button>
            </div>
          </div>
        );
      })}
      {!disabled && (
        <div><button className="btn btn--line btn--sm" disabled={!providers.length}
          onClick={() => onChange([...value, { provider_id: providers[0].id, model: '' }])}><Icon name="plus" />Модель</button></div>
      )}
    </div>
  );
}
