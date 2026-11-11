import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, patch, post, upload, type Integration, type PTask, type ProtocolDetail, type ProtocolRow, type RefField } from '../api';
import { Icon } from '../icons';
import { CLASS_OPTS, Card, Classification, Empty, Field, Modal, OptSelect, Page, UnitSelect, errText, useCan, useMe, useToast, when } from '../ui';

const P_STATUS: Record<string, [string, string]> = {
  processing: ['Разбирается', 'pill--accent pill--busy'], ready: ['Разобран', 'pill--good'], partial: ['Разобран частично', 'pill--warn'],
  error: ['Ошибка', 'pill--danger'], interrupted: ['Прерван', 'pill--warn'],
};
const T_STATE: Record<string, [string, string]> = {
  draft: ['Черновик', ''], confirmed: ['Подтверждена', 'pill--info'], approval: ['Ждёт визы на отправку', 'pill--warn'],
  failed: ['Не отправлена', 'pill--danger'], sent: ['В Битрикс24', 'pill--good'],
};
// Статусы полей: откуда значение
const F_STATUS: Record<string, [string, string]> = {
  fact: ['факт', 'pill--good'], exact: ['дословно', 'pill--good'], derived: ['вычислено правилом', 'pill--info'],
  assumption: ['предположение AI', 'pill--violet'], ai: ['формулировка AI', 'pill--violet'], edited: ['исправлено', 'pill--accent'],
  fuzzy: ['неточно', 'pill--warn'], none: ['нет', ''],
};
const fstatus = (s: string): [string, string] => F_STATUS[s] ?? ['не определено', 'pill--danger'];
const PRIORITY: Record<string, string> = { high: 'Высокий', medium: 'Средний', low: 'Низкий' };

function FS({ s }: { s: string }) {
  const [t, c] = fstatus(s);
  return <span className={`pill pill--plain ${c}`} style={{ height: 20, fontSize: 10.5 }}>{t}</span>;
}

// ── список и новый протокол ─────────────────────────────────────────────────────
export function ProtocolsList() {
  const can = useCan();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['protocols'], queryFn: () => get<ProtocolRow[]>('/api/protocols'), refetchInterval: 5000 });
  const [creating, setCreating] = useState(false);
  return (
    <Page title="Протоколы совещаний" crumbs="Протокол → задачи → проверка человеком → Битрикс24. Готовый процесс поверх оркестрации"
      actions={can('runs.start') && <button className="btn btn--primary btn--sm" onClick={() => setCreating(true)}><Icon name="plus" />Новый протокол</button>}>
      <div className="banner banner--accent"><Icon name="clipboard" size={16} />
        <span>Модель только понимает текст. Люди, проекты, сроки и цитаты проверяются правилами по справочнику — модель справочник не видит,
          поэтому ничего не выдумывает. Всё, чего нет в тексте, помечено «не определено» и блокирует отправку.</span></div>
      <Card pad={false}>
        {q.data?.length ? (
          <table className="table">
            <thead><tr><th>Протокол</th><th>Дата совещания</th><th>Задачи</th><th>Статус</th><th>Загружен</th></tr></thead>
            <tbody>{q.data.map((p) => {
              const [t, c] = P_STATUS[p.status] ?? [p.status, ''];
              const total = Object.values(p.counts).reduce((s, n) => s + n, 0);
              return (
                <tr key={p.id} className="click" onClick={() => nav(`/protocols/${p.id}`)}>
                  <td>{p.title}{p.error && <div className="small" style={{ color: 'var(--danger)' }}>{p.error.slice(0, 120)}</div>}</td>
                  <td className="mono small">{p.meeting_date ? p.meeting_date.split('-').reverse().join('.') : '—'}</td>
                  <td className="small">{total ? `${total} · в Битрикс24 ${p.counts.sent ?? 0}` : '—'}</td>
                  <td><span className={`pill ${c}`}>{t}</span></td>
                  <td className="muted small">{when(p.created_at)}</td>
                </tr>
              );
            })}</tbody>
          </table>
        ) : <Empty title="Протоколов пока нет" text="Вставьте текст, загрузите Word/PDF или попробуйте пример" />}
      </Card>
      {creating && <NewProtocol onClose={() => setCreating(false)} onDone={(id) => nav(`/protocols/${id}`)} />}
    </Page>
  );
}

function NewProtocol({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const toast = useToast();
  const me = useMe();
  const [f, setF] = useState<{ text: string; title: string; meeting_date: string; source: string; classification: string; unit_id: string | null }>(
    { text: '', title: '', meeting_date: '', source: '', classification: 'internal', unit_id: me.memberships[0]?.unit_id ?? null });
  const [hint, setHint] = useState('');
  const [busy, setBusy] = useState(false);
  const fromFile = async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await upload<{ text: string; title: string; meeting_date: string | null; date_source: string | null; source: string }>('/api/protocols/extract', form);
      setF({ ...f, text: r.text, title: r.title, meeting_date: r.meeting_date ?? '', source: r.source });
      setHint(r.meeting_date ? `Дата найдена в шапке: «${r.date_source}»` : 'Дата совещания в шапке не найдена — укажите её, иначе «до пятницы» не превратится в дату');
    } catch (e) { toast(errText(e), 'error'); }
  };
  const sample = async () => {
    const s = await get<{ text: string; meeting_date: string; title: string }>('/api/protocols/sample');
    setF({ ...f, text: s.text, title: s.title, meeting_date: s.meeting_date, source: 'пример' });
    setHint('17 пунктов с ловушками: однофамильцы, отделы вместо людей, «на следующей неделе», противоречивые сроки');
  };
  const go = async () => {
    setBusy(true);
    try {
      const r = await post<ProtocolRow>('/api/protocols', { ...f, meeting_date: f.meeting_date || null });
      onDone(r.id);
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  return (
    <Modal title="Новый протокол" wide onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={busy || f.text.length < 20} onClick={go}><Icon name="play" />Разобрать</button></>}>
      <div className="row">
        <label className="btn btn--line btn--sm" style={{ cursor: 'pointer' }}><Icon name="upload" />Файл (Word, PDF, текст)
          <input type="file" accept=".docx,.pdf,.txt,.md" hidden onChange={(e) => e.target.files?.[0] && fromFile(e.target.files[0])} /></label>
        <button className="btn btn--ghost btn--sm" onClick={sample}><Icon name="spark" />Пример протокола</button>
        {f.source && <span className="muted small">{f.source}</span>}
      </div>
      {hint && <div className="banner banner--accent small">{hint}</div>}
      <textarea className="textarea" style={{ minHeight: 240 }} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Текст протокола совещания" />
      <div className="grid2">
        <Field label="Название"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="из шапки, если пусто" /></Field>
        <Field label="Дата совещания" hint="От неё считаются «завтра», «до пятницы», «через неделю»"><input className="input" type="date" value={f.meeting_date} onChange={(e) => setF({ ...f, meeting_date: e.target.value })} /></Field>
        <Field label="Гриф"><OptSelect value={f.classification} options={CLASS_OPTS} onChange={(v) => setF({ ...f, classification: v })} /></Field>
        <Field label="Подразделение"><UnitSelect value={f.unit_id} onChange={(v) => setF({ ...f, unit_id: v })} /></Field>
      </div>
    </Modal>
  );
}

// ── проверка задач ──────────────────────────────────────────────────────────────
export function ProtocolReview() {
  const { id = '' } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['protocol', id], queryFn: () => get<ProtocolDetail>(`/api/protocols/${id}`),
    refetchInterval: (query) => (query.state.data?.status === 'processing' ? 1200 : false) });
  const [sender, setSender] = useState('');
  const [preview, setPreview] = useState<{ task_id: string; ok: boolean; error?: string; payload?: unknown }[] | null>(null);
  const [showText, setShowText] = useState(false);
  const p = q.data;
  useEffect(() => { if (p?.senders.length && !sender) setSender(p.senders[0].id); }, [p, sender]);
  if (!p) return <Page title="Протокол">{q.error ? <Empty title="Протокол не найден" /> : <div />}</Page>;
  const refresh = () => qc.invalidateQueries({ queryKey: ['protocol', id] });
  const live = p.tasks.filter((t) => !t.deleted);
  const confirmed = live.filter((t) => t.state === 'confirmed' || t.state === 'failed');
  const ready = live.filter((t) => t.state === 'draft' && t.can_confirm);
  const [st, sc] = P_STATUS[p.status] ?? [p.status, ''];

  const confirmReady = async () => {
    try {
      const r = await post<{ results: { problem: string | null }[] }>(`/api/protocols/${id}/confirm`, { task_ids: ready.map((t) => t.id) });
      toast(`Подтверждено: ${r.results.filter((x) => !x.problem).length}`);
      refresh();
    } catch (e) { toast(errText(e), 'error'); }
  };
  const send = async (previewOnly: boolean) => {
    if (!sender) { toast('Подключите Битрикс24 в разделе «Интеграции» (можно в демо-режиме)', 'error'); return; }
    try {
      const r = await post<{ mode: string; results: { task_id: string; ok: boolean; error?: string; status?: string; payload?: unknown }[] }>(
        `/api/protocols/${id}/send`, { task_ids: confirmed.map((t) => t.id), integration_id: sender, preview: previewOnly });
      if (previewOnly) setPreview(r.results);
      else {
        const sent = r.results.filter((x) => x.status === 'executed').length;
        const visa = r.results.filter((x) => x.status === 'pending_approval').length;
        const bad = r.results.filter((x) => !x.ok);
        toast(`В Битрикс24: ${sent}${visa ? `, на визе: ${visa}` : ''}${bad.length ? `, не ушло: ${bad.length}` : ''}${r.mode === 'demo' ? ' (демо)' : ''}`, bad.length ? 'error' : 'ok');
        refresh();
      }
    } catch (e) { toast(errText(e), 'error'); }
  };
  const again = async () => {
    try {
      const r = await post<ProtocolRow>('/api/protocols', { text: p.text, title: p.title, meeting_date: p.meeting_date, classification: p.classification });
      nav(`/protocols/${r.id}`);
    } catch (e) { toast(errText(e), 'error'); }
  };

  return (
    <Page title={p.title} crumbs={<><Link to="/protocols">Протоколы</Link> · совещание {p.meeting_date ? p.meeting_date.split('-').reverse().join('.') : 'без даты'}</>}
      actions={<><span className={`pill ${sc}`}>{st}</span><Classification c={p.classification} />
        <button className="btn btn--ghost btn--sm" onClick={() => setShowText(true)}><Icon name="journal" />Текст</button></>}>
      {p.status === 'processing' && (
        <Card>
          <div className="row"><span className="pill pill--accent pill--busy">{p.progress?.stage ?? 'разбор'}</span>
            <span>Блоков разобрано: {p.progress?.done ?? 0} из {p.progress?.total ?? '…'}</span></div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--soft)', marginTop: 12 }}>
            <div style={{ height: '100%', borderRadius: 3, background: 'var(--accent)', transition: 'width .4s',
              width: `${p.progress?.total ? (100 * (p.progress.done ?? 0)) / p.progress.total : 5}%` }} /></div>
        </Card>
      )}
      {(p.status === 'error' || p.status === 'interrupted') && (
        <div className="banner banner--danger"><Icon name="ban" size={16} /><span>{p.error}</span><span className="spacer" />
          <button className="btn btn--line btn--sm" onClick={again}><Icon name="refresh" />Разобрать заново</button></div>
      )}
      {!!p.meta.failed?.length && <div className="banner banner--warn">Не разобраны: {p.meta.failed.map((f) => `${f.label} (${f.message})`).join('; ')}</div>}
      {p.status !== 'processing' && (
        <>
          <div className="hero" style={{ gridTemplateColumns: 'repeat(5, minmax(0,1fr))' }}>
            {[[live.length, 'задач'], [ready.length, 'готовы к подтверждению'], [live.filter((t) => !t.can_confirm).length, 'требуют внимания'],
              [confirmed.length, 'подтверждено'], [live.filter((t) => t.state === 'sent').length, 'в Битрикс24']].map(([n, l], i) => (
              <div key={i} className={`hero__cell ${i === 2 && Number(n) ? 'hero__cell--hot' : ''}`}><div className="hero__num" style={{ fontSize: 52 }}>{n}</div><div className="hero__label">{l}</div></div>
            ))}
          </div>
          {p.can_edit && (
            <section className="card"><div className="card__body row">
              <button className="btn btn--line btn--sm" disabled={!ready.length} onClick={confirmReady}><Icon name="check" />Подтвердить готовые ({ready.length})</button>
              <span className="spacer" />
              {p.senders.length ? (
                <select className="select" style={{ width: 240, height: 32 }} value={sender} onChange={(e) => setSender(e.target.value)}>
                  {p.senders.map((s) => <option key={s.id} value={s.id}>{s.name}{s.mode === 'demo' ? ' · демо' : ''}</option>)}
                </select>
              ) : <Link to="/integrations" className="small">Подключите Битрикс24 →</Link>}
              <button className="btn btn--ghost btn--sm" disabled={!confirmed.length || !sender} onClick={() => send(true)}>Предпросмотр</button>
              <button className="btn btn--primary btn--sm" disabled={!confirmed.length || !sender} onClick={() => send(false)}>
                <Icon name="send" />Отправить подтверждённые ({confirmed.length})</button>
            </div></section>
          )}
          <div className="stack">{p.tasks.map((t) => <TaskCard key={t.id} pid={id} t={t} p={p} onChange={refresh} />)}</div>
          {!!p.decisions.length && (
            <Card title={`Решения (${p.decisions.length})`} pad={false}><table className="table"><tbody>
              {p.decisions.map((d, i) => <tr key={i}><td className="mono small" style={{ width: 50 }}>п. {d.item ?? '—'}</td><td>{d.text}<div className="muted small">«{d.quote.value}»</div></td></tr>)}
            </tbody></table></Card>
          )}
          {!!p.skipped.length && (
            <details className="card"><summary className="card__head" style={{ cursor: 'pointer' }}><h2>Пункты без задач ({p.skipped.length})</h2></summary>
              <table className="table"><tbody>
                {p.skipped.map((s, i) => <tr key={i}><td className="mono small" style={{ width: 50 }}>п. {s.item ?? '—'}</td><td>{s.reason}<div className="muted small">«{s.quote.value}»</div></td></tr>)}
              </tbody></table></details>
          )}
          {p.meta.agent && <p className="muted small">Разобрал: {p.meta.agent} (промт v{p.meta.prompt_version}) · {p.meta.provider} · {p.meta.model} · {p.meta.seconds} с
            {p.meta.refs?.length ? ` · справка: ${p.meta.refs.map((r) => `[${r.ref}] ${r.title}`).join(', ')}` : ''}</p>}
        </>
      )}
      {preview && (
        <Modal title="Предпросмотр запросов в Битрикс24" wide onClose={() => setPreview(null)}>
          {preview.map((r) => (
            <div key={r.task_id} className="stack" style={{ gap: 4 }}>
              {r.ok ? <pre className="step__out mono" style={{ marginTop: 0 }}>{'POST tasks.task.add\n' + JSON.stringify(r.payload, null, 2)}</pre>
                : <div className="banner banner--danger">{r.error}</div>}
            </div>
          ))}
        </Modal>
      )}
      {showText && <Modal title="Текст протокола" wide onClose={() => setShowText(false)}><div className="step__out pre" style={{ maxHeight: '65vh', marginTop: 0 }}>{p.text}</div></Modal>}
    </Page>
  );
}

function TaskCard({ pid, t, p, onChange }: { pid: string; t: PTask; p: ProtocolDetail; onChange: () => void }) {
  const toast = useToast();
  const [title, setTitle] = useState(t.title.value);
  const [risk, setRisk] = useState(t.risk.value ?? '');
  useEffect(() => { setTitle(t.title.value); setRisk(t.risk.value ?? ''); }, [t.title.value, t.risk.value]);
  const locked = !p.can_edit || t.state === 'sent' || t.state === 'approval' || t.deleted;
  const edit = async (changes: Record<string, unknown>) => {
    try { await patch(`/api/protocols/${pid}/tasks/${t.id}`, { changes }); onChange(); } catch (e) { toast(errText(e), 'error'); }
  };
  const op = async (what: string) => {
    try { await post(`/api/protocols/${pid}/tasks/${t.id}/${what}`); onChange(); } catch (e) { toast(errText(e), 'error'); }
  };
  const [stl, stc] = T_STATE[t.state] ?? [t.state, ''];
  const blocks = t.issues.filter((i) => i.level === 'block');
  return (
    <section className="card" style={{ opacity: t.deleted ? 0.5 : 1, borderColor: blocks.length && !t.deleted ? '#e6c3bc' : undefined }}>
      <div className="card__head">
        <span className="step__n" style={{ width: 30, height: 30 }}>{t.item ?? t.idx + 1}</span>
        <input className="input" style={{ fontWeight: 600, border: 'none', boxShadow: 'none', padding: 0, background: 'transparent' }} disabled={locked}
          value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => title !== t.title.value && edit({ title })} />
        <FS s={t.title.status} />
        <span className={`pill ${stc}`}>{t.deleted ? 'удалена' : stl}{t.external_id ? ` #${t.external_id}` : ''}</span>
      </div>
      <div className="card__body stack">
        <div className="grid3">
          <RefSelect label="Проект" f={t.project} options={p.directory.projects.map((x) => ({ id: x.id, label: x.name }))} disabled={locked}
            onPick={(v) => edit({ project: v })} />
          <RefSelect label="Ответственный" f={t.assignee} options={p.directory.employees.map((x) => ({ id: x.id, label: x.label }))} disabled={locked}
            onPick={(v) => edit({ assignee: v })} />
          <Field label="Срок">
            <input className="input" type="date" disabled={locked} value={t.deadline.value ?? ''} onChange={(e) => edit({ deadline: e.target.value || null })} />
            <span className="row" style={{ gap: 6 }}><FS s={t.deadline.status} /><span className="muted small">{t.deadline.note}</span></span>
            {!t.deadline.value && t.deadline.suggestion && !locked &&
              <button className="ex" onClick={() => edit({ deadline: t.deadline.suggestion })}>Подставить {t.deadline.suggestion.split('-').reverse().join('.')}</button>}
          </Field>
          <Field label="Приоритет">
            <select className="select" disabled={locked} value={t.priority.value} onChange={(e) => edit({ priority: e.target.value })}>
              {Object.entries(PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="row" style={{ gap: 6 }}><FS s={t.priority.status} /><span className="muted small">{t.priority.reason}</span></span>
          </Field>
          <Field label="Риск" hint={t.risk.sources.length ? `основание: ${t.risk.sources.map((s) => s.title).join(', ')}` : undefined}>
            <input className="input" disabled={locked} value={risk} onChange={(e) => setRisk(e.target.value)} onBlur={() => risk !== (t.risk.value ?? '') && edit({ risk })} />
            <span><FS s={t.risk.status === 'missing' ? 'none' : t.risk.status} /></span>
          </Field>
          <Field label="Цитата из протокола">
            <div className="small" style={{ padding: '8px 10px', borderRadius: 8, background: 'var(--soft)' }}>«{t.quote.value}»</div>
            <span className="row" style={{ gap: 6 }}><FS s={t.quote.status} />{t.quote.corrected && <span className="muted small">модель исказила символы — взят текст протокола</span>}</span>
          </Field>
        </div>
        {!!t.issues.length && !t.deleted && (
          <div className="stack" style={{ gap: 4 }}>
            {t.issues.map((i, k) => (
              <div key={k} className="small row" style={{ gap: 6, color: i.level === 'block' ? 'var(--danger)' : i.level === 'warn' ? 'var(--warn)' : 'var(--ink-3)' }}>
                <Icon name={i.level === 'block' ? 'ban' : i.level === 'warn' ? 'clock' : 'spark'} size={13} />{i.message}
              </div>
            ))}
          </div>
        )}
        {t.send_error && <div className="banner banner--danger small">{t.send_error}</div>}
        {t.stale && <div className="banner banner--warn small">После подтверждения изменились существенные поля — подтвердите заново</div>}
        {p.can_edit && (
          <div className="row">
            {t.deleted ? <button className="btn btn--ghost btn--xs" onClick={() => op('restore')}>Восстановить</button> : <>
              {t.state === 'draft' && <button className="btn btn--primary btn--xs" disabled={!t.can_confirm} onClick={() => op('confirm')}><Icon name="check" />Подтвердить</button>}
              {(t.state === 'confirmed' || t.state === 'failed') && <button className="btn btn--ghost btn--xs" onClick={() => op('unconfirm')}>Снять подтверждение</button>}
              {!locked && <button className="btn btn--ghost btn--xs" onClick={() => op('delete')}><Icon name="trash" />Это не задача</button>}
            </>}
          </div>
        )}
      </div>
    </section>
  );
}

function RefSelect({ label, f, options, disabled, onPick }:
  { label: string; f: RefField; options: { id: string; label: string }[]; disabled: boolean; onPick: (v: string | null) => void }) {
  return (
    <Field label={label}>
      <select className="select" disabled={disabled} value={f.value ?? ''} onChange={(e) => onPick(e.target.value || null)}>
        <option value="">— {f.mention ? `«${f.mention}» — не выбрано` : 'не указан'} —</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      <span className="row" style={{ gap: 6 }}><FS s={f.status} /><span className="muted small">{f.note}</span></span>
      {!f.value && !!f.candidates.length && !disabled && (
        <div className="examples">{f.candidates.map((c) => <button key={c.id} className="ex" title={c.hint} onClick={() => onPick(c.id)}>{c.label}</button>)}</div>
      )}
    </Field>
  );
}

// ── справочник ──────────────────────────────────────────────────────────────────
type DirRow = Record<string, any>; // строки трёх таблиц справочника с разными полями
type DirRows = { people: DirRow[]; projects: DirRow[]; departments: DirRow[] };

export function Directory() {
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['directory'], queryFn: () => get<DirRows>('/api/directory') });
  const ints = useQuery({ queryKey: ['integrations'], queryFn: () => get<Integration[]>('/api/integrations'), enabled: can('integrations.view') });
  const [tab, setTab] = useState<'people' | 'projects' | 'departments'>('people');
  const [adding, setAdding] = useState(false);
  const bitrix = (ints.data ?? []).filter((i) => i.kind === 'bitrix24');
  const edit = can('directory.edit');
  const refresh = () => qc.invalidateQueries({ queryKey: ['directory'] });
  const importFrom = async (id: string) => {
    try {
      const r = await post<{ people: number; projects: number; departments: number; new: number }>('/api/directory/import', { integration_id: id });
      toast(`Загружено: сотрудников ${r.people}, проектов ${r.projects}, отделов ${r.departments} (новых ${r.new})`);
      refresh();
    } catch (e) { toast(errText(e), 'error'); }
  };
  const remove = async (id: string) => {
    if (!confirm('Удалить запись справочника?')) return;
    try { await del(`/api/directory/items/${tab}/${id}`); refresh(); } catch (e) { toast(errText(e), 'error'); }
  };
  const saveAliases = async (id: string, value: string) => {
    try { await patch(`/api/directory/items/${tab}/${id}`, { aliases: value.split(',').map((s) => s.trim()).filter(Boolean) }); refresh(); }
    catch (e) { toast(errText(e), 'error'); }
  };
  const d = q.data;
  const deps = Object.fromEntries((d?.departments ?? []).map((x) => [x.id, x.name]));
  const people = Object.fromEntries((d?.people ?? []).map((x) => [x.id, `${x.last_name} ${x.first_name?.[0] ?? ''}.`]));
  return (
    <Page title="Справочник" crumbs="Сотрудники, отделы и проекты для сопоставления упоминаний в протоколах"
      actions={edit && <>
        {bitrix.map((b) => <button key={b.id} className="btn btn--line btn--sm" onClick={() => importFrom(b.id)}><Icon name="download" />Загрузить из «{b.name}»</button>)}
        <button className="btn btn--primary btn--sm" onClick={() => setAdding(true)}><Icon name="plus" />Добавить</button>
      </>}>
      <div className="tabs">
        {([['people', 'Сотрудники'], ['projects', 'Проекты'], ['departments', 'Отделы']] as const).map(([k, t]) =>
          <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{t} ({d?.[k].length ?? 0})</button>)}
      </div>
      <Card pad={false}>
        {!d?.[tab].length ? <Empty title="Пусто" text={bitrix.length ? 'Загрузите из Битрикс24 кнопкой вверху' : 'Подключите Битрикс24 (раздел «Интеграции») или добавьте вручную'} /> : (
          <table className="table"><tbody>
            {d[tab].map((r) => (
              <tr key={r.id}>
                {tab === 'people' && <><td>{r.last_name} {r.first_name} {r.middle_name}<div className="muted small">{r.position}</div></td>
                  <td className="small">{deps[r.department_id] ?? '—'}</td></>}
                {tab !== 'people' && <td style={{ width: '35%' }}>{r.name}<div className="muted small">{tab === 'departments' && r.head_id ? `руководитель — ${people[r.head_id]}` : r.stage}</div></td>}
                {tab !== 'people' && <td>
                  <input className="input" disabled={!edit} defaultValue={(r.aliases ?? []).join(', ')} placeholder="как ещё называют: через запятую"
                    onBlur={(e) => e.target.value !== (r.aliases ?? []).join(', ') && saveAliases(r.id, e.target.value)} />
                </td>}
                <td className="mono small muted" style={{ width: 120 }}>{r.external_id ? `Б24 #${r.external_id}` : 'нет id Б24'}</td>
                <td style={{ width: 40 }}>{edit && <button className="btn btn--ghost btn--icon btn--xs" onClick={() => remove(r.id)} aria-label="Удалить"><Icon name="trash" /></button>}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </Card>
      {adding && <AddDirItem kind={tab} deps={d?.departments ?? []} onClose={() => setAdding(false)} onDone={refresh} />}
    </Page>
  );
}

function AddDirItem({ kind, deps, onClose, onDone }: { kind: string; deps: DirRow[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState<Record<string, string>>({});
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    const body: Record<string, unknown> = { ...f };
    if (kind !== 'people') body.aliases = (f.aliases ?? '').split(',').map((s) => s.trim()).filter(Boolean);
    if (kind === 'people' && !f.department_id) delete body.department_id;
    try { await post(`/api/directory/items/${kind}`, body); onDone(); onClose(); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title={kind === 'people' ? 'Сотрудник' : kind === 'projects' ? 'Проект' : 'Отдел'} onClose={onClose}
      foot={<button className="btn btn--primary" onClick={save}>Добавить</button>}>
      {kind === 'people' ? <>
        <div className="grid3">
          <Field label="Фамилия"><input className="input" onChange={set('last_name')} /></Field>
          <Field label="Имя"><input className="input" onChange={set('first_name')} /></Field>
          <Field label="Отчество"><input className="input" onChange={set('middle_name')} /></Field>
        </div>
        <Field label="Должность"><input className="input" onChange={set('position')} /></Field>
        <Field label="Отдел"><select className="select" onChange={set('department_id')}><option value="">—</option>{deps.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
      </> : <>
        <Field label="Название"><input className="input" onChange={set('name')} /></Field>
        <Field label="Как ещё называют" hint="через запятую: «Северная Звезда, СЗ»"><input className="input" onChange={set('aliases')} /></Field>
      </>}
      <Field label="id в Битрикс24" hint="нужен для отправки задач"><input className="input mono" onChange={set('external_id')} /></Field>
    </Modal>
  );
}
