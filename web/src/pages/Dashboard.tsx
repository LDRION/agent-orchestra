import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { get, post, type Agent, type Overview, type Run, type UsageToday } from '../api';
import { Icon } from '../icons';
import { CLASS_OPTS, Card, Classification, Empty, OptSelect, Page, Status, UnitSelect, errText, useCan, useMe, useToast, when } from '../ui';

const EXAMPLES = [
  'Разбери протокол совещания: решения, поручения, ответственные, сроки',
  'Проверь пояснительную записку на соответствие ГОСТ Р 21.101',
  'Подготовь вопросы заказчику по ТЗ на офисный центр',
  'Набросай 3 варианта планировки open-space на 600 м²',
];

export function Dashboard() {
  const me = useMe();
  const can = useCan();
  const nav = useNavigate();
  const ov = useQuery({ queryKey: ['overview'], queryFn: () => get<Overview>('/api/system/overview'), refetchInterval: 20_000 });
  const runs = useQuery({ queryKey: ['runs', 'mine'], queryFn: () => get<Run[]>('/api/runs?scope=mine&limit=6'), enabled: can('runs.view') });
  const o = ov.data;
  const active = (o?.runs.running ?? 0) + (o?.runs.planning ?? 0) + (o?.runs.queued ?? 0);
  const first = me.user.display_name.split(' ')[0];
  const max = Math.max(1, ...(o?.usage ?? []).map((u) => u.calls));

  return (
    <Page title="Обзор" crumbs={`Здравствуйте, ${first}`}>
      <div className="hero">
        <span className="cross cross--tl" /><span className="cross cross--br" />
        <Link to="/agents" className="hero__cell"><div className="hero__num">{o?.agents ?? '—'}</div><div className="hero__label">агентов в команде</div></Link>
        <Link to="/runs" className={`hero__cell ${active ? 'hero__cell--hot' : ''}`}><div className="hero__num">{active}</div><div className="hero__label">задач в работе</div></Link>
        <Link to="/approvals" className={`hero__cell ${o?.approvals ? 'hero__cell--hot' : ''}`}><div className="hero__num">{o?.approvals ?? 0}</div><div className="hero__label">ждут вашей визы</div></Link>
        <Link to="/models" className="hero__cell"><div className="hero__num">{o?.providers ?? 0}</div><div className="hero__label">моделей подключено</div></Link>
      </div>

      {!!o?.approvals && (
        <Link to="/approvals" className="band">
          <span className="band__num">{o.approvals}</span>
          <span className="band__text">{o.approvals === 1 ? 'запрос ждёт' : 'запросов ждут'} вашего решения</span>
          <span className="spacer" /><Icon name="arrow" size={20} />
        </Link>
      )}
      {o && o.providers === 0 && (
        <div className="banner banner--accent">
          <Icon name="cpu" size={16} />
          <span>Модели ещё не подключены. {can('providers.manage') ? <Link to="/models" className="link">Подключите локальную Ollama или облачную модель</Link> : 'Попросите администратора ИИ подключить модель.'}</span>
        </div>
      )}

      {can('runs.start', ['allow', 'approval']) && <Composer />}

      <div className="grid2">
        <Card title="Мои последние задачи" actions={<Link to="/runs" className="btn btn--ghost btn--sm">Все задачи</Link>} pad={false}>
          {runs.data?.length ? (
            <table className="table"><tbody>
              {runs.data.map((r) => (
                <tr key={r.id} className="click" onClick={() => nav(`/runs/${r.id}`)}>
                  <td>{r.title}<div className="muted small">{when(r.created_at)}</div></td>
                  <td style={{ textAlign: 'right' }}><Status s={r.status} /></td>
                </tr>
              ))}
            </tbody></table>
          ) : <Empty title="Задач пока нет" text="Поставьте первую задачу команде выше" />}
        </Card>
        <Card title="Вызовы моделей" sub="за 14 дней">
          {o?.usage.length ? (
            <div className="bars">
              {o.usage.map((u) => (
                <div key={u.day} className="bars__b" style={{ height: `${(u.calls / max) * 100}%` }}>
                  <span>{u.calls} · {u.day.slice(5)}</span>
                </div>
              ))}
            </div>
          ) : <Empty title="Пока тихо" text={can('system.usage') ? 'Статистика появится после первых задач' : 'Статистика доступна администраторам'} />}
        </Card>
      </div>
    </Page>
  );
}

function Composer() {
  const nav = useNavigate();
  const toast = useToast();
  const me = useMe();
  const agents = useQuery({ queryKey: ['agents'], queryFn: () => get<Agent[]>('/api/agents') });
  const [text, setText] = useState('');
  const [cls, setCls] = useState('internal');
  const [unit, setUnit] = useState<string | null>(me.memberships[0]?.unit_id ?? null);
  const [agent, setAgent] = useState('');
  const [busy, setBusy] = useState(false);
  const approval = me.permissions['runs.start'] === 'approval';
  const usage = useQuery({ queryKey: ['usage-today'], queryFn: () => get<UsageToday>('/api/providers/usage/today'), refetchInterval: 30_000 });
  const lim = usage.data?.me;

  const go = async () => {
    setBusy(true);
    try {
      const r = await post<Run>('/api/runs', { request: text, classification: cls, unit_id: unit, agent_id: agent || null });
      nav(`/runs/${r.id}`);
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  return (
    <section className="card composer">
      <div className="row row--between">
        <h2>Задача команде</h2>
        <div className="row">
          {!!lim?.limit && <span className={`chip mono ${lim.tokens >= lim.limit ? 'pill--danger' : ''}`} title="Ваш дневной лимит токенов">
            сегодня {lim.tokens.toLocaleString('ru-RU')} / {lim.limit.toLocaleString('ru-RU')}</span>}
          <Classification c={cls} />
        </div>
      </div>
      <textarea className="textarea" placeholder="Что нужно сделать? Опишите задачу как коллеге: контекст, что на входе, что хотите получить."
        value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && text.trim().length > 2) go(); }} />
      <div className="examples">{EXAMPLES.map((x) => <button key={x} className="ex" onClick={() => setText(x)}>{x}</button>)}</div>
      <div className="grid3">
        <label className="field"><span>Кому</span>
          <select className="select" value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">Команде — оркестратор распределит</option>
            {(agents.data ?? []).filter((a) => a.enabled && a.role !== 'orchestrator').map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className="field"><span>Гриф данных</span><OptSelect value={cls} options={CLASS_OPTS} onChange={setCls} /></label>
        <label className="field"><span>Подразделение</span><UnitSelect value={unit} onChange={setUnit} /></label>
      </div>
      <div className="row">
        <span className="muted small">{approval ? 'Ваши задачи исполняются после визы руководителя.' : 'Ctrl+Enter — отправить.'} Гриф определяет, в какие модели можно отправлять данные.</span>
        <span className="spacer" />
        <button className="btn btn--primary" disabled={busy || text.trim().length < 3} onClick={go}><Icon name="send" />Поставить задачу</button>
      </div>
    </section>
  );
}
