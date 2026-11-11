import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { post } from '../api';
import { Field, errText } from '../ui';

function Art() {
  return (
    <div className="auth__art">
      <div className="auth__word">ОРКЕСТР</div>
      <div className="lead" style={{ marginTop: 28, maxWidth: 520 }}>Каждая задача — <em>продуманное</em> решение команды агентов</div>
      <div className="auth__num">AI</div>
      <div className="auth__cap">Оркестрация ИИ-агентов · модели · доступы · аудит</div>
      <div className="auth__band" />
    </div>
  );
}

export function Login() {
  const qc = useQueryClient();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState('');
  const [code, setCode] = useState('');
  const done = async () => {
    await qc.invalidateQueries({ queryKey: ['me'] });
    await qc.refetchQueries({ queryKey: ['me'] });
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const r = await post<{ ok: boolean; need_2fa?: boolean; challenge?: string }>('/api/auth/login', { login, password });
      if (r.need_2fa && r.challenge) { setChallenge(r.challenge); setPassword(''); }
      else await done();
    } catch (err) { setError(errText(err)); } finally { setBusy(false); }
  };
  const second = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await post('/api/auth/login/2fa', { challenge, code });
      await done();
    } catch (err) {
      setError(errText(err));
      if (/истёк/.test(errText(err))) setChallenge('');
    } finally { setBusy(false); setCode(''); }
  };
  if (challenge) {
    return (
      <div className="auth">
        <Art />
        <form className="auth__form" onSubmit={second}>
          <h1>Второй фактор</h1>
          <p className="muted">Введите 6 цифр из приложения-аутентификатора или один из кодов восстановления.</p>
          <Field label="Код">
            <input className="input mono" style={{ fontSize: 20, letterSpacing: '.2em', height: 48 }} value={code} autoFocus
              inputMode="numeric" autoComplete="one-time-code" onChange={(e) => setCode(e.target.value)} />
          </Field>
          {error && <div className="banner banner--danger">{error}</div>}
          <button className="btn btn--primary btn--block" disabled={busy || code.trim().length < 6}>Подтвердить</button>
          <button type="button" className="btn btn--ghost btn--block" onClick={() => { setChallenge(''); setError(''); }}>Назад</button>
        </form>
      </div>
    );
  }
  return (
    <div className="auth">
      <Art />
      <form className="auth__form" onSubmit={submit}>
        <h1>Вход</h1>
        <Field label="Логин"><input className="input" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus /></Field>
        <Field label="Пароль"><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
        {error && <div className="banner banner--danger">{error}</div>}
        <button className="btn btn--primary btn--block" disabled={busy || !login || !password}>Войти</button>
        <p className="muted small">Нет доступа — обратитесь к администратору платформы. Все входы записываются в журнал.</p>
      </form>
    </div>
  );
}

export function Setup() {
  const qc = useQueryClient();
  // Токен — во фрагменте (#token=…): он не уходит на сервер и в журналы. Сразу убираем из адресной строки.
  const [fromUrl] = useState(() => {
    const t = new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '';
    if (t) window.history.replaceState(null, '', window.location.pathname);
    return t;
  });
  const [form, setForm] = useState({ token: fromUrl, login: '', display_name: '', password: '', confirm: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.password !== form.confirm) { setError('Пароли не совпадают'); return; }
    setBusy(true);
    setError('');
    try {
      await post('/api/auth/setup', { token: form.token, login: form.login, display_name: form.display_name, password: form.password });
      window.history.replaceState(null, '', '/');
      await qc.invalidateQueries();
    } catch (err) { setError(errText(err)); } finally { setBusy(false); }
  };
  return (
    <div className="auth">
      <Art />
      <form className="auth__form" onSubmit={submit}>
        <h1>Первый запуск</h1>
        <p className="muted">Создайте учётную запись владельца платформы. Токен настройки — в консоли сервера (лаунчер подставляет его сам).</p>
        {!fromUrl && <Field label="Токен настройки"><input className="input mono" value={form.token} onChange={set('token')} /></Field>}
        <div className="grid2">
          <Field label="Логин"><input className="input" value={form.login} onChange={set('login')} autoComplete="username" /></Field>
          <Field label="Имя"><input className="input" value={form.display_name} onChange={set('display_name')} /></Field>
        </div>
        <Field label="Пароль" hint="Не короче 10 символов; длинная фраза надёжнее «сложного» пароля">
          <input className="input" type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
        </Field>
        <Field label="Ещё раз"><input className="input" type="password" value={form.confirm} onChange={set('confirm')} autoComplete="new-password" /></Field>
        {error && <div className="banner banner--danger">{error}</div>}
        <button className="btn btn--primary btn--block" disabled={busy}>Создать и войти</button>
      </form>
    </div>
  );
}
