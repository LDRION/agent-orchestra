import { useState } from 'react';
import { Link, Route, Routes } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post, type Approval, type RequestOptions } from '../api';
import { Icon } from '../icons';
import { Empty, Field, Modal, Page, Status, UnitSelect, errText, useToast, when } from '../ui';

const KIND: Record<string, string> = { run: 'Задача', prompt_publish: 'Промт', agent_update: 'Агент', integration_action: 'Запись', access_request: 'Доступ' };

export function Approvals() {
  return (
    <Routes>
      <Route index element={<List title="Входящие" url="/api/approvals/inbox" decide empty="Ничего не ждёт вашего решения" />} />
      <Route path="mine" element={<List title="Мои запросы" url="/api/approvals/mine" empty="Вы ничего не отправляли на визу" request />} />
      <Route path="history" element={<List title="Мои решения" url="/api/approvals/history" empty="Вы ещё ничего не визировали" />} />
    </Routes>
  );
}

function List({ title, url, decide, empty, request }: { title: string; url: string; decide?: boolean; empty: string; request?: boolean }) {
  const q = useQuery({ queryKey: ['approvals', url], queryFn: () => get<Approval[]>(url), refetchInterval: decide ? 10_000 : false });
  const [asking, setAsking] = useState(false);
  return (
    <Page title={title} crumbs={decide ? 'Свои запросы одобрить нельзя — это делает другой человек' : undefined}
      actions={request && <button className="btn btn--primary btn--sm" onClick={() => setAsking(true)}><Icon name="key" />Запросить доступ</button>}>
      {q.data?.length ? <div className="stack">{q.data.map((a) => <ApprovalCard key={a.id} a={a} decide={decide} />)}</div>
        : <Empty title={empty} />}
      {asking && <AccessRequest onClose={() => setAsking(false)} />}
    </Page>
  );
}

/** Запрос роли на срок: визирует тот, у кого есть все права этой роли и право назначать роли. */
function AccessRequest({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const opts = useQuery({ queryKey: ['request-options'], queryFn: () => get<RequestOptions>('/api/iam/request-options') });
  const [f, setF] = useState({ role_id: '', unit_id: '' as string | null, days: 7, reason: '' });
  const role = opts.data?.roles.find((r) => r.id === f.role_id);
  const send = async () => {
    try {
      await post('/api/iam/access-requests', { ...f, unit_id: f.unit_id });
      toast('Запрос отправлен — придёт решение');
      qc.invalidateQueries({ queryKey: ['approvals'] });
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title="Запросить доступ" onClose={onClose}
      foot={<button className="btn btn--primary" disabled={!f.role_id || !f.unit_id || f.reason.trim().length < 5} onClick={send}>Отправить на визу</button>}>
      <div className="stack">
        <Field label="Роль">
          <select className="select" value={f.role_id} onChange={(e) => setF({ ...f, role_id: e.target.value })}>
            <option value="">— выберите —</option>
            {(opts.data?.roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Field>
        {role?.description && <p className="muted small">{role.description}</p>}
        <div className="grid2">
          <Field label="Где"><UnitSelect value={f.unit_id} onChange={(v) => setF({ ...f, unit_id: v })} allowEmpty emptyLabel="— подразделение —" /></Field>
          <Field label="На сколько">
            <select className="select" value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })}>
              {[1, 3, 7, 14, 30, 90].filter((d) => d <= (opts.data?.max_days ?? 90)).map((d) => <option key={d} value={d}>{d} дн.</option>)}
            </select>
          </Field>
        </div>
        <Field label="Зачем" hint="увидит визирующий и журнал аудита">
          <textarea className="textarea" rows={3} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}
            placeholder="Замещаю руководителя отдела на время отпуска" />
        </Field>
        <p className="muted small">Роль выдаётся на срок и сама перестаёт действовать. Одобрить может только тот, у кого есть все права этой роли в подразделении.</p>
      </div>
    </Modal>
  );
}

function ApprovalCard({ a, decide }: { a: Approval; decide?: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async (approve: boolean) => {
    setBusy(true);
    try {
      await post(`/api/approvals/${a.id}/decide`, { approve, note });
      toast(approve ? 'Одобрено' : 'Отклонено');
      qc.invalidateQueries({ queryKey: ['approvals'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  return (
    <section className="card">
      <div className="card__head">
        <span className="pill pill--plain">{KIND[a.kind] ?? a.kind}</span>
        <h2>{a.title}</h2>
        <span className="spacer" />
        {!decide && <Status s={a.status} />}
        <span className="muted small">{when(a.created_at)}</span>
      </div>
      <div className="card__body stack">
        <div className="row"><span className="muted small">Просит:</span><strong>{a.requester}</strong>
          {a.permissions.map((p) => <span key={p.key} className="chip"><Icon name="key" />{p.title}</span>)}
        </div>
        {!!a.reasons.length && (
          <div className="banner banner--warn"><Icon name="clock" size={16} />
            <div><strong>Почему нужна виза</strong><ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul></div>
          </div>
        )}
        {a.summary && <div><div className="muted small">Запрос</div><div className="pre">{a.summary}</div></div>}
        {!!a.payload.steps?.length && (
          <div><div className="muted small">План</div>
            <ol style={{ margin: '4px 0 0', paddingLeft: 20 }}>{a.payload.steps.map((s, i) => <li key={i}><strong>{s.agent}</strong> — {s.task}</li>)}</ol>
          </div>
        )}
        {a.payload.body && <div><div className="muted small">Новая версия промта</div><pre className="step__out mono" style={{ maxHeight: 260 }}>{a.payload.body}</pre></div>}
        {a.payload.changes && <div><div className="muted small">Изменения</div><pre className="step__out mono">{JSON.stringify(a.payload.changes, null, 2)}</pre></div>}
        {a.target_type === 'run' && <Link to={`/runs/${a.target_id}`} className="small">Открыть задачу →</Link>}
        {a.decided_by && <div className="muted small">Решение: {a.decided_by}, {when(a.decided_at)}{a.decision_note ? ` — «${a.decision_note}»` : ''}</div>}
      </div>
      {decide && (
        <div className="card__foot">
          <input className="input" placeholder="Комментарий (виден просящему и в журнале)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn btn--danger" disabled={busy} onClick={() => go(false)}>Отклонить</button>
          <button className="btn btn--primary" disabled={busy} onClick={() => go(true)}><Icon name="check" />Одобрить</button>
        </div>
      )}
    </section>
  );
}
