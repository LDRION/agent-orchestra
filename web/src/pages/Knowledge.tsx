import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, post, upload, type KnowledgeDoc, type KnowledgeHit } from '../api';
import { Icon } from '../icons';
import {
  CLASS_OPTS, Card, Classification, Empty, Field, Modal, OptSelect, Page, UnitSelect, errText, useCan, useMe, useToast,
  useUnits, when,
} from '../ui';

const KINDS: Record<string, string> = {
  regulation: 'Регламент', standard: 'Норматив (СП, ГОСТ)', project: 'Проектный документ', template: 'Шаблон', other: 'Другое',
};

export function Knowledge() {
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const { data: units = [] } = useUnits();
  const docs = useQuery({ queryKey: ['knowledge'], queryFn: () => get<KnowledgeDoc[]>('/api/knowledge') });
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const hits = useQuery({ queryKey: ['knowledge-search', query], enabled: query.length > 1,
    queryFn: () => get<KnowledgeHit[]>(`/api/knowledge/search?q=${encodeURIComponent(query)}`) });
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const unitName = Object.fromEntries(units.map((u) => [u.id, u.name]));
  const total = (docs.data ?? []).reduce((s, d) => s + d.chars, 0);

  const remove = async (d: KnowledgeDoc) => {
    if (!confirm(`Удалить «${d.title}» из базы знаний?`)) return;
    try { await del(`/api/knowledge/${d.id}`); qc.invalidateQueries({ queryKey: ['knowledge'] }); toast('Удалено'); }
    catch (e) { toast(errText(e), 'error'); }
  };

  return (
    <Page title="База знаний" crumbs={`${docs.data?.length ?? 0} документов · ${Math.round(total / 1000)} тыс. знаков`}
      actions={can('knowledge.edit') && <button className="btn btn--primary btn--sm" onClick={() => setAdding(true)}><Icon name="plus" />Документ</button>}>
      <div className="banner banner--accent">
        <Icon name="book" size={16} />
        <span>Агенты с инструментом «База знаний» находят здесь регламенты и нормативы и ссылаются на них. Они видят только то, что видит
          поставивший задачу, и только документы с грифом не выше грифа задачи.</span>
      </div>
      <form className="row" style={{ flexWrap: 'nowrap' }} onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
        <input className="input" style={{ height: 42, fontSize: 15 }} placeholder="Найти: «основная надпись», «срок нормоконтроля», «21.101»…"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn btn--dark" style={{ height: 42 }}><Icon name="search" />Найти</button>
      </form>
      {query && (
        <Card title={`Найдено по запросу «${query}»`} actions={<button className="btn btn--ghost btn--xs" onClick={() => { setQuery(''); setQ(''); }}>Сбросить</button>}>
          {hits.data?.length ? (
            <div className="stack">
              {hits.data.map((h, i) => (
                <div key={i} className="stack" style={{ gap: 4 }}>
                  <div className="row"><button className="link-btn" onClick={() => setOpen(h.doc_id)}>{h.title}</button>
                    <Classification c={h.classification} /><span className="muted small mono">{h.score}</span></div>
                  <div className="step__out" style={{ marginTop: 0, maxHeight: 160 }}>{highlight(h.chunk.slice(0, 700), query)}</div>
                </div>
              ))}
            </div>
          ) : <Empty title={hits.isLoading ? 'Ищу…' : 'Ничего не найдено'} text="Поиск понимает формы слов и номера нормативов" />}
        </Card>
      )}
      <Card pad={false}>
        {docs.data?.length ? (
          <table className="table">
            <thead><tr><th>Документ</th><th>Тип</th><th>Подразделение</th><th>Гриф</th><th className="num">Знаков</th><th>Добавлен</th><th /></tr></thead>
            <tbody>
              {docs.data.map((d) => (
                <tr key={d.id} className="click" onClick={() => setOpen(d.id)}>
                  <td>{d.title}{d.source && <div className="muted small">{d.source}</div>}</td>
                  <td className="small">{KINDS[d.kind] ?? d.kind}</td>
                  <td className="small">{d.unit_id ? unitName[d.unit_id] : 'Вся компания'}</td>
                  <td><Classification c={d.classification} /></td>
                  <td className="num">{d.chars.toLocaleString('ru-RU')}</td>
                  <td className="muted small">{d.author}<br />{when(d.created_at)}</td>
                  <td onClick={(e) => e.stopPropagation()}>{can('knowledge.edit') && <button className="btn btn--ghost btn--icon btn--xs" onClick={() => remove(d)} aria-label="Удалить"><Icon name="trash" /></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="База пуста" text="Добавьте регламенты компании, выдержки из СП и ГОСТ, шаблоны писем и ТЗ" />}
      </Card>
      {adding && <AddDoc onClose={() => setAdding(false)} />}
      {open && <DocView id={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function highlight(text: string, query: string): ReactNode {
  const words = query.toLowerCase().split(/\s+/).filter((w) => w.length > 2).map((w) => w.slice(0, Math.max(3, w.length - 2)));
  if (!words.length) return text;
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i} style={{ background: 'var(--accent-soft)', color: 'inherit' }}>{part}</mark> : part));
}

function AddDoc({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const me = useMe();
  const [mode, setMode] = useState<'file' | 'text'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState<{ title: string; body: string; kind: string; classification: string; unit_id: string | null }>(
    { title: '', body: '', kind: 'regulation', classification: 'internal', unit_id: me.memberships[0]?.unit_id ?? null });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      if (mode === 'file' && file) {
        const form = new FormData();
        form.append('file', file);
        form.append('title', f.title);
        form.append('kind', f.kind);
        form.append('classification', f.classification);
        form.append('unit_id', f.unit_id ?? '');
        await upload('/api/knowledge/upload', form);
      } else {
        await post('/api/knowledge', { title: f.title, body: f.body, kind: f.kind, classification: f.classification, unit_id: f.unit_id });
      }
      qc.invalidateQueries({ queryKey: ['knowledge'] });
      toast('Документ добавлен');
      onClose();
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  const ready = mode === 'file' ? !!file : f.title.length > 0 && f.body.length > 0;
  return (
    <Modal title="Документ в базу знаний" wide onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={!ready || busy} onClick={save}>{busy ? 'Разбираю…' : 'Добавить'}</button></>}>
      <div className="seg" style={{ justifySelf: 'start' }}>
        <button className={mode === 'file' ? 'active' : ''} onClick={() => setMode('file')}>Файл</button>
        <button className={mode === 'text' ? 'active' : ''} onClick={() => setMode('text')}>Текст</button>
      </div>
      {mode === 'file' ? (
        <Field label="Файл" hint="Word (.docx), PDF с текстовым слоем, .txt, .md, .csv — до 15 МБ. Сканы без распознавания не подойдут.">
          <input type="file" accept=".docx,.pdf,.txt,.md,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
      ) : (
        <Field label="Текст"><textarea className="textarea" style={{ minHeight: 220 }} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></Field>
      )}
      <Field label="Название" hint={mode === 'file' ? 'Пусто — по имени файла' : undefined}>
        <input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Регламент выпуска документации" />
      </Field>
      <div className="grid3">
        <Field label="Тип"><OptSelect value={f.kind} options={Object.entries(KINDS).map(([id, title]) => ({ id, title }))} onChange={(v) => setF({ ...f, kind: v })} /></Field>
        <Field label="Кто видит" hint="Подразделение, вложенные и вышестоящие"><UnitSelect value={f.unit_id} onChange={(v) => setF({ ...f, unit_id: v })} /></Field>
        <Field label="Гриф"><OptSelect value={f.classification} options={CLASS_OPTS} onChange={(v) => setF({ ...f, classification: v })} /></Field>
      </div>
    </Modal>
  );
}

function DocView({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['knowledge-doc', id], queryFn: () => get<KnowledgeDoc>(`/api/knowledge/${id}`) });
  const d = q.data;
  return (
    <Modal title={d?.title ?? 'Документ'} wide onClose={onClose}>
      {d && <>
        <div className="row"><Classification c={d.classification} /><span className="chip">{KINDS[d.kind] ?? d.kind}</span>
          {d.source && <span className="muted small">{d.source}</span>}<span className="spacer" /><span className="muted small">{d.chars.toLocaleString('ru-RU')} знаков</span></div>
        <div className="step__out pre" style={{ maxHeight: '60vh', marginTop: 0 }}>{d.body}</div>
      </>}
    </Modal>
  );
}
