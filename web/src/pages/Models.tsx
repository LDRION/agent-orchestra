import { useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, patch, post, put, type ChainRef, type Preset, type Provider, type ProvidersPayload, type SettingsPayload, type UsageToday } from '../api';
import { Icon } from '../icons';
import { CLASSES, Card, Empty, Field, Modal, OptSelect, Page, Tier, errText, useCan, useToast } from '../ui';
import { ChainEditor } from './Agents';

export function Models() {
  return (
    <Routes>
      <Route index element={<Connections />} />
      <Route path="routing" element={<Routing />} />
      <Route path="usage" element={<Usage />} />
    </Routes>
  );
}

const useProviders = () => useQuery({ queryKey: ['providers'], queryFn: () => get<ProvidersPayload>('/api/providers') });

function Connections() {
  const can = useCan();
  const q = useProviders();
  const [adding, setAdding] = useState<Preset | null>(null);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [picker, setPicker] = useState(false);
  const manage = can('providers.manage');
  return (
    <Page title="Подключения моделей" crumbs="Локальные модели и API — в одном шлюзе"
      actions={manage && <button className="btn btn--primary btn--sm" onClick={() => setPicker(true)}><Icon name="plus" />Подключить</button>}>
      <div className="banner banner--accent">
        <Icon name="shield" size={16} />
        <span>Ключи хранятся зашифрованными (AES-256-GCM) и никогда не возвращаются в интерфейс. Уровень доверия определяет, какие данные можно отправлять в модель.</span>
      </div>
      {q.data?.providers.length ? (
        <div className="agents">
          {q.data.providers.map((p) => <ProviderCard key={p.id} p={p} manage={manage} onEdit={() => setEditing(p)} />)}
        </div>
      ) : <Empty title="Моделей нет" text="Начните с локальной Ollama — данные не покидают компьютер" />}
      {picker && q.data && (
        <Modal title="Что подключаем" wide onClose={() => setPicker(false)}>
          <div className="agents">
            {q.data.presets.map((pr) => (
              <button key={pr.id} className="agent" style={{ textAlign: 'left', border: '1px solid var(--line)' }} onClick={() => { setPicker(false); setAdding(pr); }}>
                <div className="agent__name">{pr.title}</div>
                <div className="row"><Tier t={pr.trust} />{pr.key && <span className="chip"><Icon name="key" />ключ</span>}</div>
                <div className="muted small mono">{pr.base_url}</div>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {adding && <ProviderForm preset={adding} onClose={() => setAdding(null)} />}
      {editing && <ProviderForm provider={editing} onClose={() => setEditing(null)} />}
    </Page>
  );
}

function ProviderCard({ p, manage, onEdit }: { p: Provider; manage: boolean; onEdit: () => void }) {
  const toast = useToast();
  const [test, setTest] = useState<{ ok: boolean; text?: string; error?: string; latency_ms?: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[] | null>(null);
  const run = async () => {
    setBusy(true);
    try { setTest(await post(`/api/providers/${p.id}/test`)); } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  const list = async () => {
    try {
      const r = await get<{ ok: boolean; error?: string; models: { id: string; size: string }[] }>(`/api/providers/${p.id}/models`);
      if (!r.ok) toast(r.error ?? 'не удалось', 'error');
      setModels(r.models.map((m) => `${m.id}${m.size ? ` · ${m.size}` : ''}`));
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <div className={`agent ${p.enabled ? '' : 'agent--off'}`} style={{ cursor: 'default' }}>
      <div className="agent__top">
        <span className="agent__mark"><Icon name="cpu" size={16} /></span>
        <div style={{ minWidth: 0 }}>
          <div className="agent__name">{p.name}</div>
          <div className="muted small">{p.kind}{p.default_model ? ` · ${p.default_model}` : ''}</div>
        </div>
      </div>
      <div className="row">
        <Tier t={p.trust_tier} />
        {p.has_key ? <span className="chip mono"><Icon name="key" />{p.key_hint}</span> : null}
        {p.redact_pii && p.trust_tier.startsWith('cloud') && <span className="chip">маскирует ПДн</span>}
        {!p.enabled && <span className="pill">выключена</span>}
      </div>
      <div className="muted small mono" style={{ wordBreak: 'break-all' }}>{p.base_url || 'адрес по умолчанию'}</div>
      {test && <div className={`banner ${test.ok ? 'banner--ok' : 'banner--danger'}`}>{test.ok ? `Отвечает за ${test.latency_ms} мс: «${test.text}»` : test.error}</div>}
      {models && <div className="small mono" style={{ maxHeight: 140, overflow: 'auto' }}>{models.length ? models.map((m) => <div key={m}>{m}</div>) : 'список пуст'}</div>}
      <div className="row">
        {manage && <button className="btn btn--line btn--xs" disabled={busy} onClick={run}><Icon name="play" />{busy ? 'Проверяю…' : 'Проверить'}</button>}
        <button className="btn btn--ghost btn--xs" onClick={list}>Модели</button>
        <span className="spacer" />
        {manage && <button className="btn btn--ghost btn--xs" onClick={onEdit}><Icon name="edit" />Изменить</button>}
      </div>
    </div>
  );
}

function withoutLimit(s: Record<string, unknown>): Record<string, unknown> {
  const copy = { ...s };
  delete copy.daily_tokens;                        // лимит — отдельным полем формы
  return copy;
}

function ProviderForm({ preset, provider, onClose }: { preset?: Preset; provider?: Provider; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useProviders();
  const [f, setF] = useState({
    name: provider?.name ?? preset?.title ?? '', kind: provider?.kind ?? preset?.kind ?? 'ollama',
    base_url: provider?.base_url ?? preset?.base_url ?? '', trust_tier: provider?.trust_tier ?? preset?.trust ?? 'local',
    default_model: provider?.default_model ?? preset?.model ?? '', api_key: '', redact_pii: provider?.redact_pii ?? true,
    enabled: provider?.enabled ?? true, settings: JSON.stringify(withoutLimit(provider?.settings ?? preset?.settings ?? {}), null, 1),
    daily_tokens: Number((provider?.settings ?? {}).daily_tokens ?? 0),
  });
  const set = (k: keyof typeof f, v: string | boolean | number) => setF({ ...f, [k]: v });
  const save = async () => {
    let settings: Record<string, unknown>;
    try { settings = JSON.parse(f.settings || '{}'); } catch { toast('Настройки — неверный JSON', 'error'); return; }
    if (f.daily_tokens > 0) settings.daily_tokens = f.daily_tokens;
    else delete settings.daily_tokens;
    const body: Record<string, unknown> = { name: f.name, base_url: f.base_url, trust_tier: f.trust_tier, default_model: f.default_model,
      redact_pii: f.redact_pii, enabled: f.enabled, settings };
    if (f.api_key) body.api_key = f.api_key;
    try {
      if (provider) await patch(`/api/providers/${provider.id}`, body);
      else await post('/api/providers', { ...body, kind: f.kind });
      qc.invalidateQueries({ queryKey: ['providers'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
      toast('Сохранено');
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  const remove = async () => {
    if (!provider || !confirm(`Удалить «${provider.name}»? Агенты с ней перейдут на следующую модель цепочки.`)) return;
    try { await del(`/api/providers/${provider.id}`); qc.invalidateQueries({ queryKey: ['providers'] }); onClose(); } catch (e) { toast(errText(e), 'error'); }
  };
  const needsKey = preset ? preset.key : provider?.has_key || f.kind === 'anthropic' || f.kind === 'siberi';
  return (
    <Modal title={provider ? `Модель: ${provider.name}` : `Подключить: ${preset?.title}`} wide onClose={onClose}
      foot={<>
        {provider && <button className="btn btn--danger" onClick={remove}><Icon name="trash" />Удалить</button>}
        <span className="spacer" />
        <button className="btn btn--ghost" onClick={onClose}>Отмена</button>
        <button className="btn btn--primary" disabled={!f.name} onClick={save}>Сохранить</button>
      </>}>
      <div className="grid2">
        <Field label="Название"><input className="input" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Уровень доверия" hint="Определяет, какой гриф данных сюда можно отправлять">
          <OptSelect value={f.trust_tier} options={q.data?.tiers ?? []} onChange={(v) => set('trust_tier', v)} />
        </Field>
      </div>
      <div className="grid2">
        <Field label="Адрес API"><input className="input mono" value={f.base_url} onChange={(e) => set('base_url', e.target.value)} /></Field>
        <Field label="Модель по умолчанию"><input className="input mono" value={f.default_model} onChange={(e) => set('default_model', e.target.value)} /></Field>
      </div>
      {(needsKey || provider) && (
        <Field label="Ключ API" hint={provider?.has_key ? `Сейчас: ${provider.key_hint}. Оставьте пустым, чтобы не менять.` : 'Хранится зашифрованным, обратно не показывается'}>
          <input className="input mono" type="password" autoComplete="off" value={f.api_key} onChange={(e) => set('api_key', e.target.value)} />
        </Field>
      )}
      <Field label="Лимит токенов в сутки" hint="0 — без лимита. Исчерпан — задачи переходят на следующую модель цепочки">
        <input className="input" type="number" min={0} step={10000} value={f.daily_tokens} onChange={(e) => set('daily_tokens', Math.max(0, Number(e.target.value) || 0))} style={{ maxWidth: 240 }} />
      </Field>
      <div className="row">
        <label className="check"><input type="checkbox" checked={f.redact_pii} onChange={(e) => set('redact_pii', e.target.checked)} />Маскировать персональные данные перед отправкой в облако</label>
        <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set('enabled', e.target.checked)} />Включена</label>
      </div>
      <details>
        <summary className="muted small" style={{ cursor: 'pointer' }}>Тонкие настройки (JSON)</summary>
        <textarea className="textarea textarea--code" style={{ minHeight: 120, marginTop: 8 }} value={f.settings} onChange={(e) => set('settings', e.target.value)} />
      </details>
    </Modal>
  );
}

const useSettings = () => useQuery({ queryKey: ['settings'], queryFn: () => get<SettingsPayload>('/api/system/settings') });

function Routing() {
  const toast = useToast();
  const qc = useQueryClient();
  const s = useSettings();
  const [chain, setChain] = useState<ChainRef[]>([]);
  const [planner, setPlanner] = useState<ChainRef[]>([]);
  const [policy, setPolicy] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!s.data) return;
    setChain(s.data.values.default_chain);
    setPlanner(s.data.values.planner_chain);
    setPolicy(s.data.values.egress_policy);
  }, [s.data]);
  if (!s.data) return <Page title="Маршруты и данные"><div /></Page>;
  const ed = s.data.editable;
  const save = async (key: string, value: unknown) => {
    try { await put(`/api/system/settings/${key}`, value); toast('Сохранено'); qc.invalidateQueries({ queryKey: ['settings'] }); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Page title="Маршруты и данные">
      <div className="grid2" style={{ alignItems: 'start' }}>
        <Card title="Модели по умолчанию" sub="для агентов без своей цепочки">
          <div className="stack">
            <ChainEditor value={chain} onChange={setChain} disabled={!ed.default_chain} />
            {ed.default_chain && <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" onClick={() => save('default_chain', chain)}>Сохранить</button></div>}
          </div>
        </Card>
        <Card title="Модели оркестратора" sub="планирование и сводка; пусто — как по умолчанию">
          <div className="stack">
            <ChainEditor value={planner} onChange={setPlanner} disabled={!ed.planner_chain} />
            {ed.planner_chain && <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" onClick={() => save('planner_chain', planner)}>Сохранить</button></div>}
          </div>
        </Card>
      </div>
      <Card title="Политика выхода данных" sub="самый строгий гриф, который можно отправить в модель каждого уровня">
        <div className="stack">
          <table className="table">
            <thead><tr><th>Где работает модель</th><th>Предельный гриф</th><th>Что это значит</th></tr></thead>
            <tbody>
              {s.data.tiers.map((t) => (
                <tr key={t.id}>
                  <td><Tier t={t.id} /></td>
                  <td style={{ width: 300 }}><OptSelect value={policy[t.id] ?? 'public'} options={s.data!.classifications} onChange={(v) => setPolicy({ ...policy, [t.id]: v })} /></td>
                  <td className="muted small">До «{CLASSES[policy[t.id] ?? 'public']}» включительно; выше — модель пропускается, задача уходит в следующую модель цепочки или останавливается с объяснением.</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Гриф шага = максимум из грифа задачи и грифа агента. Внешние облака дополнительно требуют права «Отправлять данные во внешние облачные модели».</p>
          {ed.egress_policy && <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" onClick={() => save('egress_policy', policy)}>Сохранить политику</button></div>}
        </div>
      </Card>
    </Page>
  );
}

function Meter({ used, limit }: { used: number; limit: number }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  const color = pct >= 100 ? 'var(--danger)' : pct >= 80 ? 'var(--warn)' : 'var(--accent)';
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="row small"><span className="mono">{used.toLocaleString('ru-RU')}</span>
        <span className="muted">{limit ? `из ${limit.toLocaleString('ru-RU')}` : 'без лимита'}</span></div>
      {!!limit && <div style={{ height: 6, borderRadius: 3, background: 'var(--soft)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: 3, background: color }} /></div>}
    </div>
  );
}

function Usage() {
  const q = useQuery({ queryKey: ['usage'], queryFn: () => get<{ provider: string; model: string; calls: number; tokens_in: number; tokens_out: number; avg_ms: number; errors: number }[]>('/api/providers/usage/summary?days=30') });
  const today = useQuery({ queryKey: ['usage-today'], queryFn: () => get<UsageToday>('/api/providers/usage/today'), refetchInterval: 15_000 });
  return (
    <Page title="Расход моделей" crumbs="сегодня — по Москве; история — за 30 дней">
      <div className="grid2" style={{ alignItems: 'start' }}>
        <Card title="Сегодня по моделям" pad={false}>
          <table className="table"><tbody>
            {(today.data?.providers ?? []).map((p) => (
              <tr key={p.id}><td style={{ width: '45%' }}>{p.name}</td><td><Meter used={p.tokens} limit={p.limit} /></td></tr>
            ))}
          </tbody></table>
        </Card>
        <Card title="Сегодня по людям" pad={false}>
          {today.data?.users?.length ? (
            <table className="table"><tbody>
              {today.data.users.map((u) => <tr key={u.name}><td>{u.name}</td><td className="num">{u.tokens.toLocaleString('ru-RU')}</td></tr>)}
            </tbody></table>
          ) : <Empty title="Сегодня вызовов не было" />}
        </Card>
      </div>
      <Card pad={false} title="За 30 дней">
        {q.data?.length ? (
          <table className="table">
            <thead><tr><th>Провайдер</th><th>Модель</th><th className="num">Вызовов</th><th className="num">Токены вход</th><th className="num">Токены выход</th><th className="num">Средне, мс</th><th className="num">Ошибок</th></tr></thead>
            <tbody>
              {q.data.map((r) => (
                <tr key={r.provider + r.model}>
                  <td>{r.provider}</td><td className="mono">{r.model}</td><td className="num">{r.calls}</td>
                  <td className="num">{r.tokens_in.toLocaleString('ru-RU')}</td><td className="num">{r.tokens_out.toLocaleString('ru-RU')}</td>
                  <td className="num">{r.avg_ms}</td><td className="num">{r.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="Вызовов пока не было" />}
      </Card>
    </Page>
  );
}
