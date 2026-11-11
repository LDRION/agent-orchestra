import { useEffect, useState } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, get, post, type Me, type Overview } from './api';
import { Icon, Logo } from './icons';
import { MeCtx, Modal, Field, errText, initials, useMe, useToast } from './ui';
import { Login, Setup } from './pages/Auth';
import { Dashboard } from './pages/Dashboard';
import { Runs, RunView } from './pages/Runs';
import { Agents, AgentEditor } from './pages/Agents';
import { Models } from './pages/Models';
import { Access } from './pages/Access';
import { Approvals } from './pages/Approvals';
import { Audit } from './pages/Audit';
import { Settings } from './pages/Settings';
import { Knowledge } from './pages/Knowledge';
import { Directory, ProtocolReview, ProtocolsList } from './pages/Protocols';
import { Integrations } from './pages/Integrations';
import { ForceMfa, ReauthGate } from './pages/Security';
import { SecurityCenter } from './pages/SecurityCenter';

export function App() {
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ['auth-state'], queryFn: () => get<{ setup_required: boolean }>('/api/auth/state') });
  const me = useQuery({
    queryKey: ['me'], queryFn: () => get<Me>('/api/auth/me'), enabled: state.data?.setup_required === false,
    retry: false,
  });

  useEffect(() => {
    const h = () => qc.setQueryData(['me'], null);
    window.addEventListener('orchestra:unauthorized', h);
    return () => window.removeEventListener('orchestra:unauthorized', h);
  }, [qc]);

  if (state.isLoading || (state.data?.setup_required === false && me.isLoading)) return <div className="auth" />;
  if (state.data?.setup_required) return <Setup />;
  if (!me.data || (me.error instanceof ApiError && me.error.status === 401)) return <Login />;
  return (
    <MeCtx.Provider value={me.data}>
      <Shell />
      <ReauthGate />
      {me.data.user.must_change_password ? <ForcePassword />
        : me.data.mfa.required && !me.data.mfa.enabled && <ForceMfa />}
    </MeCtx.Provider>
  );
}

type Section = { key: string; title: string; icon: string; to: string; show: boolean; items: { to: string; title: string; icon: string; show?: boolean; badge?: number }[] };

function Shell() {
  const me = useMe();
  const can = (p: string, a: string[] = ['allow']) => a.includes(me.permissions[p] ?? 'deny');
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ['overview'], queryFn: () => get<Overview>('/api/system/overview'), refetchInterval: 20_000 });
  const pending = ov.data?.approvals ?? 0;

  const sections: Section[] = [
    { key: 'pulse', title: 'Пульт', icon: 'pulse', to: '/', show: true, items: [
      { to: '/', title: 'Обзор', icon: 'pulse' },
      { to: '/runs', title: 'Задачи', icon: 'list', show: can('runs.view') },
    ] },
    { key: 'agents', title: 'Команда', icon: 'team', to: '/agents', show: can('agents.view'), items: [
      { to: '/agents', title: 'Агенты и отделы', icon: 'team' },
    ] },
    { key: 'protocols', title: 'Процессы', icon: 'clipboard', to: '/protocols', show: can('runs.view'), items: [
      { to: '/protocols', title: 'Протоколы совещаний', icon: 'clipboard' },
      { to: '/directory', title: 'Справочник', icon: 'contacts' },
    ] },
    { key: 'knowledge', title: 'Знания', icon: 'book', to: '/knowledge', show: can('knowledge.view'), items: [
      { to: '/knowledge', title: 'База знаний', icon: 'book' },
    ] },
    { key: 'models', title: 'Модели', icon: 'cpu', to: '/models', show: can('providers.view'), items: [
      { to: '/models', title: 'Подключения', icon: 'cpu' },
      { to: '/models/routing', title: 'Маршруты и данные', icon: 'shield' },
      { to: '/models/usage', title: 'Расход', icon: 'bolt', show: can('system.usage') },
    ] },
    { key: 'integrations', title: 'Интеграции', icon: 'plug', to: '/integrations', show: can('integrations.view'), items: [
      { to: '/integrations', title: 'Подключённые системы', icon: 'plug' },
    ] },
    { key: 'access', title: 'Доступы', icon: 'key', to: '/access', show: can('iam.view'), items: [
      { to: '/access', title: 'Матрица прав', icon: 'grid' },
      { to: '/access/people', title: 'Люди', icon: 'users' },
      { to: '/access/org', title: 'Оргструктура', icon: 'tree' },
      { to: '/access/check', title: 'Проверка доступа', icon: 'search' },
    ] },
    { key: 'approvals', title: 'Одобрения', icon: 'hand', to: '/approvals', show: true, items: [
      { to: '/approvals', title: 'Входящие', icon: 'hand', badge: pending },
      { to: '/approvals/mine', title: 'Мои запросы', icon: 'send' },
      { to: '/approvals/history', title: 'Мои решения', icon: 'check' },
    ] },
    { key: 'security', title: 'Защита', icon: 'shield', to: '/security', show: can('security.view'), items: [
      { to: '/security', title: 'Центр безопасности', icon: 'shield' },
    ] },
    { key: 'audit', title: 'Журнал', icon: 'journal', to: '/audit', show: can('audit.view'), items: [
      { to: '/audit', title: 'Журнал аудита', icon: 'journal' },
    ] },
  ];
  const visible = sections.filter((s) => s.show);
  const current = visible.find((s) => s.to !== '/' && (loc.pathname.startsWith(s.to) || s.items.some((i) => loc.pathname.startsWith(i.to) && i.to !== '/')))
    ?? (loc.pathname.startsWith('/settings') ? null : visible[0]);

  const logout = async () => {
    try { await post('/api/auth/logout'); } finally { qc.clear(); nav('/'); window.location.reload(); }
  };
  const roleLine = me.user.is_owner ? 'Владелец платформы' : me.memberships.map((m) => m.role).filter((v, i, a) => a.indexOf(v) === i).join(', ') || me.user.position;

  return (
    <div className="shell">
      <nav className="rail" aria-label="Разделы">
        <span className="rail__logo" title="Оркестр · AI"><Logo /></span>
        {visible.map((s) => (
          <NavLink key={s.key} to={s.to} end={s.to === '/'} title={s.title}
            className={() => `rail__btn ${current?.key === s.key ? 'active' : ''}`}>
            <Icon name={s.icon} />
            {s.key === 'approvals' && pending > 0 && <span className="rail__dot" />}
          </NavLink>
        ))}
        <span className="rail__spacer" />
        <NavLink to="/settings" title="Настройки" className={({ isActive }) => `rail__btn ${isActive ? 'active' : ''}`}><Icon name="gear" /></NavLink>
        <button className="rail__btn" title="Выйти" onClick={logout}><Icon name="logout" /></button>
      </nav>
      <aside className="side">
        <div className="side__head">
          <div className="side__title">{current?.title ?? 'Настройки'}</div>
          <div className="side__sub">Оркестр · AI</div>
        </div>
        <div className="side__nav">
          {(current?.items ?? [{ to: '/settings', title: 'Профиль и платформа', icon: 'gear' }])
            .filter((i) => i.show !== false)
            .map((i) => (
              <NavLink key={i.to} to={i.to} end className={({ isActive }) => `nav-i ${isActive ? 'active' : ''}`}>
                <Icon name={i.icon} />{i.title}
                {!!i.badge && <span className="cnt">{i.badge}</span>}
              </NavLink>
            ))}
        </div>
        <div className="side__user">
          <span className="avatar">{initials(me.user.display_name)}</span>
          <div style={{ minWidth: 0 }}>
            <div className="side__user-name">{me.user.display_name}</div>
            <div className="side__user-role" title={roleLine}>{roleLine}</div>
          </div>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunView />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:id" element={<AgentEditor />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/protocols" element={<ProtocolsList />} />
          <Route path="/protocols/:id" element={<ProtocolReview />} />
          <Route path="/directory" element={<Directory />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/models/*" element={<Models />} />
          <Route path="/access/*" element={<Access />} />
          <Route path="/approvals/*" element={<Approvals />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/security" element={<SecurityCenter />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function ForcePassword() {
  const toast = useToast();
  const qc = useQueryClient();
  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await post('/api/auth/password', { current: cur, new: next });
      toast('Пароль изменён');
      qc.invalidateQueries({ queryKey: ['me'] });
    } catch (e) { toast(errText(e), 'error'); } finally { setBusy(false); }
  };
  return (
    <Modal title="Задайте свой пароль" onClose={() => {}}
      foot={<button className="btn btn--primary" disabled={busy || next.length < 10} onClick={save}>Сохранить</button>}>
      <p className="muted">Пароль выдан администратором — смените его перед началом работы. Не короче 10 символов.</p>
      <Field label="Текущий пароль"><input className="input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Field>
      <Field label="Новый пароль"><input className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></Field>
    </Modal>
  );
}
