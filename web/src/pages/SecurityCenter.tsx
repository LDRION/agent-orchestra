import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, post, put, type SecEvent, type SecSession, type SecurityOverview } from '../api';
import { Icon } from '../icons';
import { Card, Empty, Field, Page, errText, useCan, useToast, when } from '../ui';

const KIND_TITLE: Record<string, string> = { telegram: 'Telegram', email: 'Почта', webhook_out: 'Вебхук' };

export function SecurityCenter() {
  const ov = useQuery({ queryKey: ['security'], queryFn: () => get<SecurityOverview>('/api/security/overview'), refetchInterval: 30_000 });
  const o = ov.data;
  const weak = o?.posture.filter((p) => !p.ok).length ?? 0;
  return (
    <Page title="Центр безопасности" crumbs="Что защищено, кто входит, что подозрительно — за последние сутки">
      <div className="hero">
        <span className="cross cross--tl" /><span className="cross cross--br" />
        <div className="hero__cell"><div className="hero__num">{o?.day.logins ?? '—'}</div><div className="hero__label">входов</div></div>
        <div className={`hero__cell ${o?.day.failed_logins ? 'hero__cell--hot' : ''}`}><div className="hero__num">{o?.day.failed_logins ?? '—'}</div><div className="hero__label">неудачных попыток</div></div>
        <div className="hero__cell"><div className="hero__num">{o?.day.denied ?? '—'}</div><div className="hero__label">отказов в доступе</div></div>
        <div className={`hero__cell ${o?.day.alerts ? 'hero__cell--hot' : ''}`}><div className="hero__num">{o?.day.alerts ?? '—'}</div><div className="hero__label">тревог</div></div>
      </div>
      <div className="grid2" style={{ alignItems: 'start' }}>
        <Card title="Состояние защиты" sub={weak ? `стоит поправить: ${weak}` : 'всё в порядке'} pad={false}>
          <table className="table"><tbody>
            {(o?.posture ?? []).map((p) => (
              <tr key={p.key}>
                <td style={{ width: 28 }}>{p.ok ? <span className="ok-mark"><Icon name="check" size={14} /></span> : <span className="warn-mark"><Icon name="clock" size={14} /></span>}</td>
                <td><div>{p.title}</div><div className="muted small">{p.hint}</div></td>
              </tr>
            ))}
          </tbody></table>
        </Card>
        <div className="stack">
          {o && <Channel o={o} />}
          <AuditIntegrity seq={o?.chain.seq} hash={o?.chain.hash} />
        </div>
      </div>
      <div className="grid2" style={{ alignItems: 'start' }}>
        <Events title="Тревоги" rows={o?.alerts ?? []} empty="Тревог не было" text={(e) => String(e.data.text ?? e.target)} />
        <Events title="Неудачные попытки" rows={o?.failed ?? []} empty="Неудачных попыток нет"
          text={(e) => `${ACTION[e.action] ?? e.action}${e.target ? ` · ${e.target}` : ''}${e.data.reason ? ` — ${e.data.reason}` : ''}`} />
      </div>
      <Events title="Изменения прав, ключей и настроек" rows={o?.changes ?? []} empty="Изменений не было"
        text={(e) => `${CHANGE[e.action] ?? e.action}${e.target ? ` · ${e.target}` : ''}`} />
      <Sessions canManage={!!o?.can_manage} />
    </Page>
  );
}

function AuditIntegrity({ seq, hash }: { seq?: number; hash?: string }) {
  const can = useCan();
  const toast = useToast();
  const [res, setRes] = useState<{ ok: boolean; checked: number; reason: string; anchor: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try { setRes(await get('/api/audit/verify')); } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  return (
    <Card title="Журнал аудита" actions={can('audit.view') && <button className="btn btn--line btn--xs" disabled={busy} onClick={check}>Проверить целостность</button>}>
      <p className="muted small">Записи связаны цепочкой хешей, конец журнала заверен подписью вне базы: правка, удаление
        или обрезка задним числом видны при проверке.</p>
      <dl className="kv"><dt>Последняя запись</dt><dd className="mono">№ {seq} · {hash}…</dd></dl>
      {res && (res.ok
        ? <div className="banner banner--ok"><Icon name="check" size={16} />Цепочка цела: проверено записей — {res.checked}{res.anchor ? `, метка конца №${res.anchor} сходится` : ''}.</div>
        : <div className="banner banner--danger"><Icon name="ban" size={16} />{res.reason}</div>)}
    </Card>
  );
}

const CHANGE: Record<string, string> = {
  'iam.role.grants': 'Права роли', 'iam.role.create': 'Новая роль', 'iam.role.delete': 'Роль удалена',
  'iam.user.create': 'Новый сотрудник', 'iam.user.roles': 'Роли сотрудника', 'iam.user.owner': 'Владельцы платформы',
  'iam.user.password_reset': 'Сброс пароля', 'iam.user.2fa_reset': 'Сброс 2FA', 'iam.user.update': 'Учётная запись',
  'iam.recovery': 'Восстановление из консоли', 'iam.unit.update': 'Оргструктура', 'settings.update': 'Настройка',
  'providers.create': 'Модель подключена', 'providers.update': 'Модель изменена', 'providers.delete': 'Модель удалена',
  'integrations.create': 'Интеграция подключена', 'integrations.update': 'Интеграция изменена',
  'integrations.delete': 'Интеграция удалена', 'audit.reanchor': 'Метка журнала переподписана',
  'auth.2fa_disabled': '2FA отключён', 'security.session_revoked': 'Сессия завершена принудительно',
};

const ACTION: Record<string, string> = {
  'auth.login': 'Вход', 'auth.reauth': 'Подтверждение паролем', 'auth.password': 'Смена пароля', 'auth.2fa_disabled': 'Отключение 2FA',
};

function Events({ title, rows, empty, text }: { title: string; rows: SecEvent[]; empty: string; text: (e: SecEvent) => string }) {
  return (
    <Card title={title} pad={false}>
      {rows.length ? (
        <table className="table"><tbody>
          {rows.map((e) => (
            <tr key={e.seq}>
              <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{when(e.ts)}</td>
              <td>{text(e)}{e.actor && e.actor !== 'система' && <div className="muted small">{e.actor}</div>}</td>
              <td className="muted small mono">{e.ip}</td>
            </tr>
          ))}
        </tbody></table>
      ) : <Empty title={empty} />}
    </Card>
  );
}

function Channel({ o }: { o: SecurityOverview }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ integration_id: o.channel.integration_id ?? '', to: o.channel.to });
  useEffect(() => setF({ integration_id: o.channel.integration_id ?? '', to: o.channel.to }), [o.channel.integration_id, o.channel.to]);
  const kind = o.channel.options.find((x) => x.id === f.integration_id)?.kind;
  const save = async () => {
    try { await put('/api/security/channel', { integration_id: f.integration_id || null, to: f.to }); toast('Канал тревог сохранён'); qc.invalidateQueries({ queryKey: ['security'] }); }
    catch (e) { toast(errText(e), 'error'); }
  };
  const test = async () => {
    try { await post('/api/security/channel/test'); toast('Проверочная тревога отправлена'); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Card title="Куда слать тревоги" sub="блокировки, подбор паролей, смена владельцев, запросы из чужих сетей">
      <div className="stack">
        {!o.channel.options.length && <p className="muted small">Подключите Telegram-бота, почту или исходящий вебхук в «Интеграциях» — и выберите его здесь.</p>}
        <div className="grid2">
          <Field label="Канал">
            <select className="select" value={f.integration_id} disabled={!o.can_manage} onChange={(e) => setF({ ...f, integration_id: e.target.value })}>
              <option value="">— только журнал —</option>
              {o.channel.options.map((x) => <option key={x.id} value={x.id}>{x.name} · {KIND_TITLE[x.kind] ?? x.kind}</option>)}
            </select>
          </Field>
          {kind && kind !== 'webhook_out' && (
            <Field label={kind === 'email' ? 'Адрес' : 'Чат (пусто — первый разрешённый)'}>
              <input className="input" value={f.to} disabled={!o.can_manage} onChange={(e) => setF({ ...f, to: e.target.value })}
                placeholder={kind === 'email' ? 'security@example.com' : 'chat_id'} />
            </Field>
          )}
        </div>
        {o.can_manage && (
          <div className="row"><span className="spacer" />
            {o.channel.integration_id && <button className="btn btn--line btn--sm" onClick={test}>Проверить</button>}
            <button className="btn btn--primary btn--sm" onClick={save}>Сохранить</button>
          </div>
        )}
      </div>
    </Card>
  );
}

function Sessions({ canManage }: { canManage: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['security-sessions'], queryFn: () => get<SecSession[]>('/api/security/sessions'), refetchInterval: 30_000 });
  const [filter, setFilter] = useState('');
  const rows = (q.data ?? []).filter((s) => !filter || (s.user + s.login + s.ip).toLowerCase().includes(filter.toLowerCase()));
  const revoke = async (s: SecSession) => {
    try { await del(`/api/security/sessions/${s.id}`); toast(`Сессия «${s.user}» завершена`); qc.invalidateQueries({ queryKey: ['security-sessions'] }); }
    catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Card title={`Кто сейчас в системе · ${q.data?.length ?? 0}`} pad={false}
      actions={<input className="input" style={{ width: 200, height: 30 }} placeholder="Человек или адрес…" value={filter} onChange={(e) => setFilter(e.target.value)} />}>
      <table className="table">
        <thead><tr><th>Человек</th><th>Адрес</th><th>Вход</th><th>Активность</th><th /></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td><div>{s.user}{s.current && <span className="pill pill--accent" style={{ marginLeft: 8 }}>вы</span>}
                {!s.mfa && <span className="pill pill--warn" style={{ marginLeft: 8 }}>без 2FA</span>}</div>
                <div className="muted small">{s.login}</div></td>
              <td className="mono small">{s.ip}</td>
              <td className="muted small">{when(s.created_at)}</td>
              <td className="muted small">{when(s.last_seen_at)}</td>
              <td style={{ textAlign: 'right' }}>{canManage && !s.current && <button className="btn btn--ghost btn--xs" onClick={() => revoke(s)}>Завершить</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
