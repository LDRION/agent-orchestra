import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, del, get, onReauth, post, type SessionRow } from '../api';
import { Icon } from '../icons';
import { Card, Field, Modal, errText, useMe, useToast, when } from '../ui';

/** Подключение второго фактора: QR → проверочный код → коды восстановления (показываются один раз). */
export function MfaSetup({ onDone, autoStart }: { onDone: () => void; autoStart?: boolean }) {
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const start = useCallback(async () => {
    setBusy(true);
    try { setSetup(await post('/api/auth/2fa/setup')); } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  }, [toast]);
  const started = useRef(false);
  useEffect(() => {
    if (autoStart && !started.current) { started.current = true; start(); }
  }, [autoStart, start]);
  const enable = async () => {
    setBusy(true);
    try { setCodes((await post<{ recovery_codes: string[] }>('/api/auth/2fa/enable', { code })).recovery_codes); }
    catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); setCode(''); }
  };
  const download = () => {
    const blob = new Blob([`Оркестр · AI — коды восстановления (каждый один раз)\n\n${codes!.join('\n')}\n`], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'orchestra-recovery-codes.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  if (codes) {
    return (
      <div className="stack">
        <div className="banner banner--ok"><Icon name="check" size={16} />Второй фактор включён. Остальные сессии завершены.</div>
        <p>Коды восстановления — на случай потери телефона. Каждый срабатывает один раз. <strong>Больше они не покажутся</strong>: сохраните их вне компьютера.</p>
        <pre className="step__out mono" style={{ columns: 2, fontSize: 14 }}>{codes.join('\n')}</pre>
        <div className="row">
          <button className="btn btn--line btn--sm" onClick={download}><Icon name="download" />Скачать .txt</button>
          <span className="spacer" />
          <button className="btn btn--primary" onClick={onDone}>Коды сохранены</button>
        </div>
      </div>
    );
  }
  if (!setup) {
    if (autoStart) return <p className="muted">Готовлю QR-код…</p>;
    return (
      <div className="stack">
        <p>Кроме пароля при входе понадобится код из приложения на телефоне: Яндекс Ключ, Google Authenticator, 1Password и т. п.
          Украденного пароля станет недостаточно.</p>
        <div><button className="btn btn--primary" disabled={busy} onClick={start}><Icon name="shield" />Подключить</button></div>
      </div>
    );
  }
  return (
    <div className="grid2" style={{ gridTemplateColumns: '220px 1fr', alignItems: 'start' }}>
      <img alt="QR-код для приложения" width={210} height={210} style={{ border: '1px solid var(--line)', borderRadius: 12 }}
        src={`data:image/svg+xml;utf8,${encodeURIComponent(setup.qr_svg)}`} />
      <div className="stack">
        <p>1. Отсканируйте QR-код в приложении-аутентификаторе.</p>
        <p className="muted small">Не сканируется — введите ключ вручную: <span className="mono" style={{ wordBreak: 'break-all' }}>{setup.secret}</span></p>
        <Field label="2. Код из приложения">
          <input className="input mono" inputMode="numeric" autoComplete="one-time-code" value={code} autoFocus
            onChange={(e) => setCode(e.target.value)} style={{ fontSize: 18, letterSpacing: '.2em' }} />
        </Field>
        <div><button className="btn btn--primary" disabled={busy || code.replace(/\D/g, '').length !== 6} onClick={enable}>Включить</button></div>
      </div>
    </div>
  );
}

/** Подтверждение паролем перед опасным действием: всплывает само, после ввода запрос повторяется. */
export function ReauthGate() {
  const me = useMe();
  const [resolve, setResolve] = useState<((ok: boolean) => void) | null>(null);
  const [form, setForm] = useState({ password: '', code: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onReauth(() => new Promise<boolean>((res) => {
      setForm({ password: '', code: '' });
      setError('');
      setResolve(() => res);
    }));
    return () => onReauth(null);
  }, []);
  if (!resolve) return null;
  const close = (ok: boolean) => { resolve(ok); setResolve(null); };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try { await post('/api/auth/reauth', form); close(true); }
    catch (err) {
      setError(errText(err));
      if (err instanceof ApiError && err.status === 401) close(false);
    } finally { setBusy(false); }
  };
  return (
    <Modal title="Подтвердите, что это вы" onClose={() => close(false)}
      foot={<>
        <button className="btn btn--ghost" onClick={() => close(false)}>Отмена</button>
        <button className="btn btn--primary" type="submit" form="reauth-form"
          disabled={busy || !form.password || (me.mfa.enabled && form.code.replace(/\D/g, '').length < 6)}>Подтвердить</button>
      </>}>
      <form id="reauth-form" className="stack" onSubmit={submit}>
        <div className="banner banner--accent"><Icon name="lock" size={16} />
          <span>Действие меняет доступы, ключи или политику. Введите пароль — следующие несколько минут подтверждать не придётся.</span></div>
        <Field label="Пароль"><input className="input" type="password" autoFocus autoComplete="current-password" value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
        {me.mfa.enabled && (
          <Field label="Код из приложения"><input className="input mono" inputMode="numeric" autoComplete="one-time-code" value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })} /></Field>
        )}
        {error && <div className="banner banner--danger">{error}</div>}
      </form>
    </Modal>
  );
}

export function ForceMfa() {
  const qc = useQueryClient();
  return (
    <Modal title="Включите двухфакторный вход" onClose={() => {}} wide>
      <div className="banner banner--warn"><Icon name="shield" size={16} />Политика безопасности требует второй фактор для вашей роли. До подключения остальные разделы недоступны.</div>
      <MfaSetup onDone={() => qc.invalidateQueries()} />
    </Modal>
  );
}

export function LoginSecurity() {
  const me = useMe();
  const toast = useToast();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => get<SessionRow[]>('/api/auth/sessions') });
  const [setup, setSetup] = useState(false);
  const [off, setOff] = useState(false);
  const [form, setForm] = useState({ password: '', code: '' });
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast(ok); qc.invalidateQueries({ queryKey: ['sessions'] }); qc.invalidateQueries({ queryKey: ['me'] }); }
    catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Card title="Безопасность входа">
      <div className="stack">
        <div className="row">
          <Icon name="shield" />
          <strong>Двухфакторный вход</strong>
          {me.mfa.enabled ? <span className="pill pill--good">включён</span> : <span className="pill pill--warn">выключен</span>}
          {me.mfa.enabled && <span className="muted small">кодов восстановления осталось: {me.mfa.recovery_left}</span>}
          <span className="spacer" />
          {!me.mfa.enabled && !setup && <button className="btn btn--primary btn--sm" onClick={() => setSetup(true)}>Подключить</button>}
          {me.mfa.enabled && !me.mfa.required && <button className="btn btn--ghost btn--sm" onClick={() => setOff(true)}>Отключить</button>}
          {me.mfa.enabled && me.mfa.required && <span className="muted small">обязателен для вашей роли</span>}
        </div>
        {setup && !me.mfa.enabled && <MfaSetup autoStart onDone={() => { setSetup(false); qc.invalidateQueries({ queryKey: ['me'] }); }} />}
        <div className="row" style={{ marginTop: 6 }}>
          <strong>Активные сессии</strong><span className="spacer" />
          <button className="btn btn--line btn--xs" onClick={() => act(() => post('/api/auth/sessions/revoke-others'), 'Остальные сессии завершены')}>Завершить все, кроме этой</button>
        </div>
        <table className="table"><tbody>
          {(sessions.data ?? []).map((s) => (
            <tr key={s.id}>
              <td><div>{browser(s.user_agent)}{s.current && <span className="pill pill--accent" style={{ marginLeft: 8 }}>эта</span>}</div>
                <div className="muted small mono">{s.ip}</div></td>
              <td className="muted small">вход {when(s.created_at)}<br />активность {when(s.last_seen_at)}</td>
              <td style={{ textAlign: 'right' }}>{!s.current && <button className="btn btn--ghost btn--xs" onClick={() => act(() => del(`/api/auth/sessions/${s.id}`), 'Сессия завершена')}>Завершить</button>}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
      {off && (
        <Modal title="Отключить второй фактор" onClose={() => setOff(false)}
          foot={<button className="btn btn--danger" disabled={!form.password || form.code.length < 6}
            onClick={() => act(() => post('/api/auth/2fa/disable', form), 'Второй фактор отключён').then(() => setOff(false))}>Отключить</button>}>
          <Field label="Пароль"><input className="input" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="Код из приложения"><input className="input mono" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></Field>
        </Modal>
      )}
    </Card>
  );
}

function browser(ua: string): string {
  if (/Edg\//.test(ua)) return 'Microsoft Edge';
  if (/YaBrowser/.test(ua)) return 'Яндекс Браузер';
  if (/Chrome\//.test(ua)) return 'Chrome';
  if (/Firefox\//.test(ua)) return 'Firefox';
  if (/Safari\//.test(ua)) return 'Safari';
  if (/python|httpx/i.test(ua)) return 'Программа';
  return ua ? ua.slice(0, 40) : 'Неизвестно';
}
