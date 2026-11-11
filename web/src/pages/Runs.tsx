import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post, type IntegrationActionRow, type Run, type RunEvent } from '../api';
import { ActionsList } from './Integrations';
import { Icon } from '../icons';
import { Card, Classification, Empty, Page, Status, errText, useCan, useMe, useToast, when } from '../ui';

const FINAL = ['succeeded', 'failed', 'cancelled', 'rejected', 'interrupted'];

export function Runs() {
  const can = useCan();
  const nav = useNavigate();
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const q = useQuery({ queryKey: ['runs', scope], queryFn: () => get<Run[]>(`/api/runs?scope=${scope}&limit=200`), refetchInterval: 8000 });
  return (
    <Page title="Задачи" actions={can('runs.view_all') && (
      <div className="seg">
        <button className={scope === 'mine' ? 'active' : ''} onClick={() => setScope('mine')}>Мои</button>
        <button className={scope === 'all' ? 'active' : ''} onClick={() => setScope('all')}>Подразделения</button>
      </div>
    )}>
      <Card pad={false}>
        {q.data?.length ? (
          <table className="table">
            <thead><tr><th>Задача</th><th>Как</th><th>Гриф</th><th>Статус</th><th>Создана</th><th className="num">Токены</th></tr></thead>
            <tbody>
              {q.data.map((r) => (
                <tr key={r.id} className="click" onClick={() => nav(`/runs/${r.id}`)}>
                  <td style={{ maxWidth: 520 }}>{r.title}{r.error && <div className="small" style={{ color: 'var(--danger)' }}>{r.error.slice(0, 140)}</div>}</td>
                  <td className="muted small">{r.mode === 'team' ? 'Команда' : 'Агент'}</td>
                  <td><Classification c={r.classification} /></td>
                  <td><Status s={r.status} /></td>
                  <td className="muted small">{when(r.created_at)}</td>
                  <td className="num">{(r.tokens_in + r.tokens_out).toLocaleString('ru-RU')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="Задач нет" text="Поставьте задачу на «Пульте»" />}
      </Card>
    </Page>
  );
}

export function RunView() {
  const { id = '' } = useParams();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const me = useMe();
  const can = useCan();
  const q = useQuery({ queryKey: ['run', id], queryFn: () => get<Run>(`/api/runs/${id}`) });
  const [log, setLog] = useState<RunEvent[]>([]);
  const run = q.data;
  const live = run && !FINAL.includes(run.status);

  useEffect(() => {
    if (!id) return;
    setLog([]);
    const es = new EventSource(`/api/runs/${id}/events`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as RunEvent;
      setLog((x) => (x.some((e) => e.id === ev.id) ? x : [...x, ev]));
      if (ev.type !== 'log' && ev.type !== 'tool') qc.invalidateQueries({ queryKey: ['run', id] });
      if (ev.type === 'status' && FINAL.includes(String(ev.data.status))) {
        qc.invalidateQueries({ queryKey: ['runs'] });
        qc.invalidateQueries({ queryKey: ['overview'] });
      }
    };
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) es.close(); };
    return () => es.close();
  }, [id, qc]);

  if (!run) return <Page title="Задача">{q.error ? <Empty title="Нет доступа или задача не найдена" /> : <div />}</Page>;
  const mine = run.initiator_id === me.user.id;

  const act = async (what: 'cancel' | 'retry') => {
    try {
      const r = await post<Run>(`/api/runs/${id}/${what}`);
      if (what === 'retry') nav(`/runs/${r.id}`);
      else qc.invalidateQueries({ queryKey: ['run', id] });
    } catch (e) { toast(errText(e), 'error'); }
  };

  return (
    <Page title={run.title} crumbs={<><Link to="/runs">Задачи</Link> · {when(run.created_at)}</>}
      actions={<>
        <Status s={run.status} />
        {live && (mine || can('runs.cancel_any')) && <button className="btn btn--line btn--sm" onClick={() => act('cancel')}><Icon name="stop" />Остановить</button>}
        {!live && mine && <button className="btn btn--line btn--sm" onClick={() => act('retry')}><Icon name="refresh" />Повторить</button>}
      </>}>
      {run.status === 'awaiting_approval' && (
        <div className="banner banner--warn">
          <Icon name="clock" size={16} />
          <div>
            <strong>{run.stage === 'plan' ? 'План ждёт визы' : 'Задача ждёт визы'}</strong>
            {run.approval?.reasons?.length ? <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{run.approval.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
            <div className="small" style={{ marginTop: 6 }}>Визирует руководитель с правом одобрения. Свои запросы одобрить нельзя.</div>
          </div>
        </div>
      )}
      {run.error && <div className={`banner ${run.status === 'succeeded' ? 'banner--warn' : 'banner--danger'}`}><Icon name="ban" size={16} /><span>{run.error}</span></div>}

      <div className="grid2" style={{ alignItems: 'start' }}>
        <Card title="Запрос" actions={<Classification c={run.classification} />}>
          <p className="pre">{run.request}</p>
          {run.approach && <p className="muted small" style={{ marginTop: 12 }}><strong>Подход:</strong> {run.approach}</p>}
          {!!run.preapproved.length && <div className="row" style={{ marginTop: 12 }}>{run.preapproved.map((p) => <span key={p} className="chip"><Icon name="check" />виза: {p}</span>)}</div>}
        </Card>
        <Card title="Ход работы" sub={live ? 'вживую' : `${log.length} событий`}>
          <div className="log">
            {log.length ? log.map((e) => <div key={e.id}><span className="log__t">{when(e.ts)}</span>{describe(e)}</div>) : <span className="muted">Событий пока нет</span>}
          </div>
        </Card>
      </div>

      {!!run.steps?.length && (
        <section className="steps">
          {run.steps.map((s) => (
            <div key={s.id} className={`step step--${s.status}`}>
              <div className="step__n">{s.idx + 1}</div>
              <div style={{ minWidth: 0 }}>
                <div className="row">
                  <span className="step__who">{s.agent_name}</span>
                  {!!s.depends_on.length && <span className="muted small">после шага {s.depends_on.map((d) => d + 1).join(', ')}</span>}
                  <span className="spacer" /><Status s={s.status} />
                </div>
                <div className="step__task">{s.task}</div>
                {s.output && <div className="step__out pre">{s.output}</div>}
                {s.error && <div className="banner banner--danger" style={{ marginTop: 8 }}>{s.error}</div>}
                {(s.model || s.prompt_version) && (
                  <div className="step__meta">
                    {s.model && <span className="chip"><Icon name="cpu" />{s.provider} · {s.model}</span>}
                    {s.prompt_version && <span className="chip">промт v{s.prompt_version}</span>}
                    {s.tools_used.map((t, i) => <span key={i} className="chip"><Icon name="bolt" />{t}</span>)}
                    {!!(s.tokens_in + s.tokens_out) && <span className="chip mono">{s.tokens_in}→{s.tokens_out}</span>}
                    {s.attempts > 1 && <span className="chip">попыток: {s.attempts}</span>}
                  </div>
                )}
              </div>
            </div>
          ))}
        </section>
      )}

      <RunActions runId={run.id} live={!!live} />

      {run.result && (
        <Card title="Итог" actions={<button className="btn btn--ghost btn--sm" onClick={() => { navigator.clipboard.writeText(run.result); toast('Скопировано'); }}>Копировать</button>}>
          <div className="pre" style={{ fontSize: 14.5, lineHeight: 1.65 }}>{run.result}</div>
        </Card>
      )}
    </Page>
  );
}

function RunActions({ runId, live }: { runId: string; live: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['run-actions', runId], queryFn: () => get<IntegrationActionRow[]>(`/api/integrations/actions/list?run_id=${runId}`),
    refetchInterval: live ? 3000 : false });
  if (!q.data?.length) return null;
  return (
    <Card title="Предложено агентами" sub="записи во внешние системы — выполняются только после вашего решения" pad={false}>
      <ActionsList items={q.data} onChange={() => qc.invalidateQueries({ queryKey: ['run-actions', runId] })} />
    </Card>
  );
}

function describe(e: RunEvent): string {
  const d = e.data as Record<string, string | number | string[]>;
  switch (e.type) {
    case 'status': return `статус: ${d.status}${d.error ? ` — ${d.error}` : ''}${d.approved_by ? ` (виза: ${d.approved_by})` : ''}`;
    case 'plan': return `план готов: ${(d.steps as unknown as unknown[]).length} шаг(ов)`;
    case 'step': return `шаг ${Number(d.idx) + 1}${d.agent ? ` · ${d.agent}` : ''}: ${d.status}${d.error ? ` — ${String(d.error).slice(0, 160)}` : ''}`;
    case 'tool': return `шаг ${Number(d.idx) + 1} · ${d.agent} → инструмент ${d.tool}`;
    case 'approval': return `нужна виза: ${(d.reasons as string[]).join('; ')}`;
    case 'result': return 'итог готов';
    case 'log': return `${d.idx !== undefined ? `шаг ${Number(d.idx) + 1}: ` : ''}${d.text}`;
    default: return e.type;
  }
}
