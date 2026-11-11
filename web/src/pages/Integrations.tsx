import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, patch, post, type Integration, type IntegrationActionRow, type IntegrationKind, type IntField } from '../api';
import { Icon } from '../icons';
import { CLASS_OPTS, CLASSES, Card, Classification, Empty, Field, Modal, OptSelect, Page, TIERS, Tier, errText, useCan, useToast, when } from '../ui';

const STATUS: Record<string, [string, string]> = {
  proposed: ['Предложено', 'pill--accent'], pending_approval: ['Ждёт визы', 'pill--warn'], executing: ['Выполняется', 'pill--busy'],
  executed: ['Выполнено', 'pill--good'],
  failed: ['Ошибка', 'pill--danger'], rejected: ['Отклонено', ''],
};
const TIER_OPTS = Object.entries(TIERS).map(([id, [title]]) => ({ id, title }));
const useKinds = () => useQuery({ queryKey: ['integration-kinds'], queryFn: () => get<IntegrationKind[]>('/api/integrations/kinds'), staleTime: Infinity });

export function Integrations() {
  const can = useCan();
  const q = useQuery({ queryKey: ['integrations'], queryFn: () => get<Integration[]>('/api/integrations') });
  const kinds = useKinds();
  const [picker, setPicker] = useState(false);
  const [form, setForm] = useState<{ kind: IntegrationKind; row?: Integration } | null>(null);
  const [open, setOpen] = useState<Integration | null>(null);
  const manage = can('integrations.manage');
  const kindOf = (k: string) => kinds.data?.find((x) => x.kind === k);
  return (
    <Page title="Интеграции" crumbs="Битрикс24, 1С, почта, Telegram, вебхуки — действия становятся инструментами агентов"
      actions={manage && <button className="btn btn--primary btn--sm" onClick={() => setPicker(true)}><Icon name="plus" />Подключить</button>}>
      <div className="banner banner--accent">
        <Icon name="shield" size={16} />
        <span><strong>Чтение</strong> агенты выполняют сразу — с правами поставившего задачу и только если гриф данных системы не выше грифа задачи.
          <strong> Запись</strong> (задача в Битрикс24, документ в 1С, письмо) агент только предлагает: выполняет человек кнопкой или по визе.</span>
      </div>
      {q.data?.length ? (
        <div className="agents">
          {q.data.map((r) => {
            const k = kindOf(r.kind);
            return (
              <div key={r.id} className={`agent ${r.enabled ? '' : 'agent--off'}`} onClick={() => setOpen(r)}>
                <div className="agent__top">
                  <span className="agent__mark"><Icon name={k?.icon ?? 'plug'} /></span>
                  <div style={{ minWidth: 0 }}>
                    <div className="agent__name">{r.name}</div>
                    <div className="muted small">{k?.title ?? r.kind} · <span className="mono">{r.key}</span></div>
                  </div>
                </div>
                <div className="row">
                  {r.mode === 'demo' ? <span className="pill pill--violet">демо</span> : <span className="pill pill--good">боевой</span>}
                  <Tier t={r.trust_tier} /><Classification c={r.data_classification} />
                </div>
                {r.last_check_at && (
                  <div className={`small ${r.last_check_ok ? 'muted' : ''}`} style={{ color: r.last_check_ok ? undefined : 'var(--danger)' }}>
                    {r.last_check_ok ? '✓' : '✕'} {r.last_check_message} · {when(r.last_check_at)}
                  </div>
                )}
                <div className="muted small">{r.tools.length ? `инструменты: ${r.tools.map((t) => t.key).join(', ')}` : r.hook_path ? `принимает: POST ${r.hook_path}` : ''}</div>
              </div>
            );
          })}
        </div>
      ) : <Empty title="Систем пока нет" text="Начните с Битрикс24 в демо-режиме — так можно показать весь путь без портала" />}
      <MyActions />
      {picker && kinds.data && (
        <Modal title="Что подключаем" wide onClose={() => setPicker(false)}>
          <div className="agents">
            {kinds.data.map((k) => (
              <button key={k.kind} className="agent" style={{ textAlign: 'left' }} onClick={() => { setPicker(false); setForm({ kind: k }); }}>
                <div className="agent__top"><span className="agent__mark"><Icon name={k.icon} /></span><div className="agent__name">{k.title}</div></div>
                <div className="agent__desc">{k.description}</div>
                <div className="row"><Tier t={k.default_trust} />{k.demo && <span className="pill pill--violet">есть демо</span>}
                  <span className="chip">{k.actions.length ? `${k.actions.length} действий` : 'запуск задач'}</span></div>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {form && <IntegrationForm kind={form.kind} row={form.row} onClose={() => setForm(null)} />}
      {open && kindOf(open.kind) && (
        <IntegrationView row={open} kind={kindOf(open.kind)!} manage={manage} onClose={() => setOpen(null)}
          onEdit={() => { setForm({ kind: kindOf(open.kind)!, row: open }); setOpen(null); }} />
      )}
    </Page>
  );
}

function slug(kind: string): string {
  return `${kind.replace(/[^a-z0-9]/g, '').slice(0, 12) || 'int'}_${Math.random().toString(36).slice(2, 6)}`;
}

function IntegrationForm({ kind, row, onClose }: { kind: IntegrationKind; row?: Integration; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const defaults = Object.fromEntries(kind.fields.map((f) => [f.key, row?.settings[f.key] ?? f.default ?? '']));
  const [f, setF] = useState({
    name: row?.name ?? kind.title, key: row?.key ?? ({ bitrix24: 'bitrix', onec: 'onec', email: 'mail', telegram: 'tg',
      webhook_out: 'hook_out', webhook_in: 'hook_in' } as Record<string, string>)[kind.kind] ?? slug(kind.kind),
    mode: row?.mode ?? (kind.demo ? 'demo' : 'live'), trust_tier: row?.trust_tier ?? kind.default_trust,
    data_classification: row?.data_classification ?? kind.default_classification, secret: '', enabled: row?.enabled ?? true,
  });
  const [settings, setSettings] = useState<Record<string, unknown>>(defaults);
  const [once, setOnce] = useState<{ secret: string; path: string } | null>(null);
  const save = async () => {
    const body: Record<string, unknown> = { name: f.name, mode: f.mode, trust_tier: f.trust_tier, data_classification: f.data_classification,
      enabled: f.enabled, settings };
    if (f.secret) body.secret = f.secret;
    try {
      if (row) await patch(`/api/integrations/${row.id}`, body);
      else {
        const created = await post<Integration>('/api/integrations', { ...body, kind: kind.kind, key: f.key });
        if (created.secret_once) { setOnce({ secret: created.secret_once, path: created.hook_path ?? '' }); qc.invalidateQueries({ queryKey: ['integrations'] }); return; }
      }
      qc.invalidateQueries({ queryKey: ['integrations'] });
      qc.invalidateQueries({ queryKey: ['tools'] });
      toast('Сохранено');
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  if (once) {
    return (
      <Modal title="Входящий вебхук готов" onClose={onClose} foot={<button className="btn btn--primary" onClick={onClose}>Я сохранил секрет</button>}>
        <p>Настройте отправителя. <strong>Секрет показывается один раз.</strong></p>
        <Field label="Адрес"><input className="input mono" readOnly value={`${window.location.origin}${once.path}`} /></Field>
        <Field label="Секрет"><input className="input mono" readOnly value={once.secret} /></Field>
        <p className="muted small">Подпись: заголовок <span className="mono">X-Orchestra-Timestamp: unix-время</span> и <span className="mono">X-Orchestra-Signature: sha256=HMAC_SHA256(секрет, «время.тело»)</span>; событие старше 5 минут или повтор — отклоняются. Режим «token»:
          <span className="mono"> Authorization: Bearer секрет</span> или поле <span className="mono">auth[application_token]</span> (исходящий вебхук Битрикс24).</p>
      </Modal>
    );
  }
  return (
    <Modal title={row ? `Интеграция: ${row.name}` : `Подключить: ${kind.title}`} wide onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" onClick={save}>Сохранить</button></>}>
      <p className="muted small">{kind.description}</p>
      <div className="grid3">
        <Field label="Название"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Ключ инструментов" hint={`агентам: ${f.key}.${kind.actions[0]?.key ?? '…'}`}>
          <input className="input mono" disabled={!!row} value={f.key} onChange={(e) => setF({ ...f, key: e.target.value.toLowerCase() })} />
        </Field>
        <Field label="Режим">
          <select className="select" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value as 'live' | 'demo' })}>
            <option value="live">Боевой</option>{kind.demo && <option value="demo">Демо — без настоящей системы</option>}
          </select>
        </Field>
      </div>
      <div className="grid2">
        <Field label="Куда уходят данные при записи" hint="Политика выхода данных: какой гриф можно отправлять"><OptSelect value={f.trust_tier} options={TIER_OPTS} onChange={(v) => setF({ ...f, trust_tier: v })} /></Field>
        <Field label="Гриф данных системы" hint={`Агенту отдаются только в задачах с грифом от «${CLASSES[f.data_classification]}»`}>
          <OptSelect value={f.data_classification} options={CLASS_OPTS} onChange={(v) => setF({ ...f, data_classification: v })} />
        </Field>
      </div>
      {kind.secret && (
        <Field label={kind.secret.title} hint={row?.has_secret ? `Сейчас: ${row.secret_hint}. Пусто — не менять.` : kind.secret.hint || 'Хранится зашифрованным, обратно не показывается'}>
          <input className="input mono" type="password" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />
        </Field>
      )}
      {kind.fields.map((fl) => <SettingField key={fl.key} f={fl} value={settings[fl.key]} onChange={(v) => setSettings({ ...settings, [fl.key]: v })} />)}
      <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />Включена</label>
    </Modal>
  );
}

function SettingField({ f, value, onChange }: { f: IntField; value: unknown; onChange: (v: unknown) => void }) {
  const label = f.title + (f.required ? ' *' : '');
  if (f.kind === 'list') {
    return <Field label={label} hint={f.hint || 'по одному в строке'}>
      <textarea className="textarea" style={{ minHeight: 70 }} value={Array.isArray(value) ? value.join('\n') : String(value ?? '')}
        onChange={(e) => onChange(e.target.value.split('\n'))} /></Field>;
  }
  if (f.kind === 'select') {
    return <Field label={label} hint={f.hint}><select className="select" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
      {f.options.map((o) => <option key={o} value={o}>{CLASSES[o] ?? o}</option>)}</select></Field>;
  }
  if (f.kind === 'textarea') {
    return <Field label={label} hint={f.hint}><textarea className="textarea" style={{ minHeight: 70 }} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} /></Field>;
  }
  return <Field label={label} hint={f.hint}><input className="input" type={f.kind === 'number' ? 'number' : 'text'} value={String(value ?? '')}
    onChange={(e) => onChange(f.kind === 'number' ? Number(e.target.value) : e.target.value)} /></Field>;
}

function IntegrationView({ row, kind, manage, onClose, onEdit }:
  { row: Integration; kind: IntegrationKind; manage: boolean; onClose: () => void; onEdit: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [check, setCheck] = useState<{ ok: boolean; message: string } | null>(null);
  const [act, setAct] = useState(kind.actions[0]?.key ?? '');
  const [args, setArgs] = useState<Record<string, string>>({});
  const [cls, setCls] = useState(row.data_classification);
  const [result, setResult] = useState<unknown>(null);
  const spec = kind.actions.find((a) => a.key === act);
  const run = async () => {
    const parsed: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(args)) {
      if (!v) continue;
      parsed[k] = v.trim().startsWith('{') ? (() => { try { return JSON.parse(v); } catch { return v; } })() : v;
    }
    try { setResult(await post(`/api/integrations/${row.id}/call`, { action: act, args: parsed, classification: cls })); qc.invalidateQueries({ queryKey: ['my-actions'] }); }
    catch (e) { toast(errText(e), 'error'); }
  };
  const remove = async () => {
    if (!confirm(`Отключить и удалить «${row.name}»? Инструменты пропадут у агентов.`)) return;
    try { await del(`/api/integrations/${row.id}`); qc.invalidateQueries({ queryKey: ['integrations'] }); onClose(); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title={row.name} wide onClose={onClose}
      foot={manage && <><button className="btn btn--danger" onClick={remove}><Icon name="trash" />Удалить</button><span className="spacer" />
        <button className="btn btn--line" onClick={async () => { try { setCheck(await post(`/api/integrations/${row.id}/check`)); qc.invalidateQueries({ queryKey: ['integrations'] }); } catch (e) { toast(errText(e), 'error'); } }}>
          <Icon name="refresh" />Проверить связь</button>
        <button className="btn btn--primary" onClick={onEdit}><Icon name="edit" />Изменить</button></>}>
      <div className="row">{row.mode === 'demo' ? <span className="pill pill--violet">демо-режим</span> : <span className="pill pill--good">боевой</span>}
        <Tier t={row.trust_tier} /><Classification c={row.data_classification} />{row.has_secret && <span className="chip mono"><Icon name="key" />{row.secret_hint}</span>}</div>
      {check && <div className={`banner ${check.ok ? 'banner--ok' : 'banner--danger'}`}>{check.message}</div>}
      {row.hook_path && <Field label="Адрес для внешней системы"><input className="input mono" readOnly value={`${window.location.origin}${row.hook_path}`} /></Field>}
      {!!kind.actions.length && (
        <Card title="Попробовать действие" sub="как агент, но от вашего имени">
          <div className="stack">
            <div className="grid2">
              <Field label="Действие">
                <select className="select" value={act} onChange={(e) => { setAct(e.target.value); setArgs({}); setResult(null); }}>
                  {kind.actions.map((a) => <option key={a.key} value={a.key}>{a.write ? '✎ ' : ''}{a.title}</option>)}
                </select>
              </Field>
              <Field label="Гриф" hint="чтение отдаёт данные только в пределах грифа"><OptSelect value={cls} options={CLASS_OPTS} onChange={setCls} /></Field>
            </div>
            {spec && <p className="muted small">{spec.description}{spec.write && ' Запись: сначала предложение, затем выполнение (сразу или по визе).'}</p>}
            {spec && Object.entries(spec.args).map(([k, hint]) => (
              <Field key={k} label={`${k}${spec.required.includes(k) ? ' *' : ''}`} hint={hint}>
                <input className="input" value={args[k] ?? ''} onChange={(e) => setArgs({ ...args, [k]: e.target.value })} />
              </Field>
            ))}
            <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" onClick={run}><Icon name="play" />Выполнить</button></div>
            {result !== null && <pre className="step__out mono" style={{ maxHeight: 300 }}>{JSON.stringify(result, null, 2)}</pre>}
          </div>
        </Card>
      )}
    </Modal>
  );
}

export function ActionsList({ items, onChange }: { items: IntegrationActionRow[]; onChange: () => void }) {
  const toast = useToast();
  const go = async (id: string, what: 'execute' | 'reject') => {
    try {
      const r = await post<IntegrationActionRow>(`/api/integrations/actions/${id}/${what}`);
      toast(r.status === 'executed' ? 'Выполнено' : r.status === 'pending_approval' ? 'Отправлено на визу' : r.status === 'failed' ? `Ошибка: ${r.error}` : 'Отклонено',
        r.status === 'failed' ? 'error' : 'ok');
      onChange();
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <table className="table"><tbody>
      {items.map((a) => {
        const [label, cls] = STATUS[a.status] ?? [a.status, ''];
        return (
          <tr key={a.id}>
            <td><strong>{a.integration}</strong> · {a.title}
              <div className="muted small mono" style={{ wordBreak: 'break-all' }}>{JSON.stringify(a.args).slice(0, 200)}</div>
              {a.error && <div className="small" style={{ color: 'var(--danger)' }}>{a.error}</div>}
              {a.status === 'executed' && Object.keys(a.result).length > 0 && <div className="small muted mono">→ {JSON.stringify(a.result).slice(0, 160)}</div>}
            </td>
            <td style={{ width: 120 }}><span className={`pill ${cls}`}>{label}</span></td>
            <td style={{ width: 210, textAlign: 'right' }}>
              {(a.status === 'proposed' || a.status === 'failed') && <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button className="btn btn--ghost btn--xs" onClick={() => go(a.id, 'reject')}>Отклонить</button>
                <button className="btn btn--primary btn--xs" onClick={() => go(a.id, 'execute')}>{a.status === 'failed' ? 'Повторить' : 'Выполнить'}</button>
              </div>}
              {a.run_id && <Link to={`/runs/${a.run_id}`} className="small">задача →</Link>}
            </td>
          </tr>
        );
      })}
    </tbody></table>
  );
}

function MyActions() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['my-actions'], queryFn: () => get<IntegrationActionRow[]>('/api/integrations/actions/list') });
  if (!q.data?.length) return null;
  return (
    <Card title="Мои действия во внешних системах" sub="предложенные агентами и вами" pad={false}>
      <ActionsList items={q.data} onChange={() => qc.invalidateQueries({ queryKey: ['my-actions'] })} />
    </Card>
  );
}
