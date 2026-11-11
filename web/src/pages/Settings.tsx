import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post, put, type SettingsPayload } from '../api';
import { CLASS_OPTS, Card, Field, OptSelect, Page, errText, useCan, useMe, useToast } from '../ui';
import { LoginSecurity } from './Security';

const MFA_OPTS = [
  { id: 'none', title: 'По желанию' },
  { id: 'privileged', title: 'Обязателен для управляющих доступами и моделями' },
  { id: 'all', title: 'Обязателен для всех' },
];

export function Settings() {
  const me = useMe();
  const can = useCan();
  const toast = useToast();
  const info = useQuery({ queryKey: ['info'], queryFn: () => get<{ version: string; mode: string; database: string }>('/api/system/info') });
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' });
  const changePw = async () => {
    if (pw.next !== pw.confirm) { toast('Пароли не совпадают', 'error'); return; }
    try { await post('/api/auth/password', { current: pw.current, new: pw.next }); toast('Пароль изменён. Остальные сессии завершены.'); setPw({ current: '', next: '', confirm: '' }); }
    catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Page title="Настройки">
      <div className="grid2" style={{ alignItems: 'start' }}>
        <Card title="Профиль">
          <dl className="kv">
            <dt>Имя</dt><dd>{me.user.display_name}</dd>
            <dt>Логин</dt><dd className="mono">{me.user.login}</dd>
            <dt>Должность</dt><dd>{me.user.position || '—'}</dd>
            <dt>Роли</dt><dd>{me.user.is_owner ? 'Владелец платформы' : ''}{me.memberships.map((m) => <div key={m.unit_id + m.role_id}>{m.role} · {m.unit}</div>)}</dd>
          </dl>
        </Card>
        <Card title="Смена пароля">
          <div className="stack">
            <Field label="Текущий"><input className="input" type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" /></Field>
            <div className="grid2">
              <Field label="Новый"><input className="input" type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" /></Field>
              <Field label="Ещё раз"><input className="input" type="password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} autoComplete="new-password" /></Field>
            </div>
            <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" disabled={pw.next.length < 10} onClick={changePw}>Сменить</button></div>
          </div>
        </Card>
      </div>
      <LoginSecurity />
      {(can('system.settings') || can('providers.view')) && <Platform />}
      <Card title="О системе">
        <dl className="kv">
          <dt>Версия</dt><dd className="mono">{info.data?.version}</dd>
          <dt>Режим</dt><dd>{info.data?.mode === 'desktop' ? 'Лаунчер на этом компьютере' : 'Сервер компании'}</dd>
          <dt>База данных</dt><dd>{info.data?.database}</dd>
        </dl>
      </Card>
    </Page>
  );
}

function Platform() {
  const toast = useToast();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ['settings'], queryFn: () => get<SettingsPayload>('/api/system/settings') });
  const [f, setF] = useState({ company_name: '', default_classification: 'internal', max_parallel_steps: 3, user_daily_tokens: 0, require_2fa: 'none' });
  useEffect(() => {
    if (!s.data) return;
    const v = s.data.values;
    setF({ company_name: v.company_name, default_classification: v.default_classification, max_parallel_steps: v.max_parallel_steps,
      user_daily_tokens: v.user_daily_tokens, require_2fa: v.require_2fa });
  }, [s.data]);
  if (!s.data) return null;
  const ed = s.data.editable;
  const save = async () => {
    try {
      for (const [k, v] of Object.entries(f)) if (ed[k]) await put(`/api/system/settings/${k}`, v);
      toast('Сохранено');
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['me'] });                 // политика 2FA могла коснуться и меня
    } catch (e) { toast(errText(e), 'error'); }
  };
  const any = ed.company_name || ed.default_classification || ed.max_parallel_steps || ed.require_2fa;
  return (
    <Card title="Платформа">
      <div className="stack">
        <div className="grid3">
          <Field label="Компания" hint="Подставляется в промт оркестратора"><input className="input" disabled={!ed.company_name} value={f.company_name} onChange={(e) => setF({ ...f, company_name: e.target.value })} /></Field>
          <Field label="Гриф задач по умолчанию"><OptSelect value={f.default_classification} options={CLASS_OPTS} onChange={(v) => setF({ ...f, default_classification: v })} /></Field>
          <Field label="Шагов параллельно" hint="1–8; ограничивает нагрузку на модели"><input className="input" type="number" min={1} max={8} disabled={!ed.max_parallel_steps} value={f.max_parallel_steps} onChange={(e) => setF({ ...f, max_parallel_steps: Number(e.target.value) })} /></Field>
        </div>
        <div className="grid2">
          <Field label="Двухфакторный вход" hint="Управляющие — у кого есть права на людей, роли, модели или настройки, и владельцы">
            <select className="select" disabled={!ed.require_2fa} value={f.require_2fa} onChange={(e) => setF({ ...f, require_2fa: e.target.value })}>
              {MFA_OPTS.map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}
            </select>
          </Field>
          <Field label="Дневной лимит токенов на человека" hint="0 — без лимита. Сутки — по Москве. Лимиты моделей — в карточке модели">
            <input className="input" type="number" min={0} step={10000} disabled={!ed.user_daily_tokens} value={f.user_daily_tokens}
              onChange={(e) => setF({ ...f, user_daily_tokens: Math.max(0, Number(e.target.value) || 0) })} />
          </Field>
        </div>
        {any && <div className="row"><span className="spacer" /><button className="btn btn--primary btn--sm" onClick={save}>Сохранить</button></div>}
      </div>
    </Card>
  );
}
