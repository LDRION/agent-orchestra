import { useEffect, useMemo, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { del, get, patch, post, put, type Catalog, type Decision, type Effect, type Grant, type Role, type Unit, type User } from '../api';
import { Icon } from '../icons';
import {
  Card, EffectBadge, Empty, Field, Modal, Page, Popover, UnitSelect, errText, initials, orderTree,
  useCan, useMe, useToast, useUnits, when,
} from '../ui';

export function Access() {
  return (
    <Routes>
      <Route index element={<Matrix />} />
      <Route path="people" element={<People />} />
      <Route path="org" element={<Org />} />
      <Route path="check" element={<Check />} />
    </Routes>
  );
}

const useCatalog = () => useQuery({ queryKey: ['catalog'], queryFn: () => get<Catalog>('/api/iam/catalog'), staleTime: Infinity });
const useRoles = () => useQuery({ queryKey: ['roles'], queryFn: () => get<Role[]>('/api/iam/roles') });

type Cell = { effect: Effect; approver_role_id: string | null } | null;
type Draft = Record<string, Record<string, Cell>>;

function toDraft(roles: Role[]): Draft {
  const d: Draft = {};
  roles.forEach((r) => { d[r.id] = {}; r.grants.forEach((g) => { d[r.id][g.permission] = { effect: g.effect, approver_role_id: g.approver_role_id }; }); });
  return d;
}
const same = (a: Cell, b: Cell) => (a?.effect ?? null) === (b?.effect ?? null) && (a?.approver_role_id ?? null) === (b?.approver_role_id ?? null);

// ── Матрица прав ────────────────────────────────────────────────────────────────
function Matrix() {
  const toast = useToast();
  const qc = useQueryClient();
  const can = useCan();
  const cat = useCatalog();
  const roles = useRoles();
  const edit = can('iam.roles');
  const [draft, setDraft] = useState<Draft>({});
  const [base, setBase] = useState<Draft>({});
  const [open, setOpen] = useState<{ role: string; perm: string; x: number; y: number } | null>(null);
  const [filter, setFilter] = useState('');
  const [risky, setRisky] = useState(false);
  const [roleModal, setRoleModal] = useState<Role | 'new' | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { if (roles.data) { const d = toDraft(roles.data); setDraft(d); setBase(d); } }, [roles.data]);

  const dirty = useMemo(() => {
    const out: [string, string][] = [];
    Object.keys(draft).forEach((r) => {
      const keys = new Set([...Object.keys(draft[r] ?? {}), ...Object.keys(base[r] ?? {})]);
      keys.forEach((p) => { if (!same(draft[r]?.[p] ?? null, base[r]?.[p] ?? null)) out.push([r, p]); });
    });
    return out;
  }, [draft, base]);

  if (!cat.data || !roles.data) return <Page title="Матрица прав"><div /></Page>;
  const roleName = Object.fromEntries(roles.data.map((r) => [r.id, r.name]));
  const perms = cat.data.permissions.filter((p) =>
    (!risky || p.risky) && (!filter || (p.title + p.hint + p.key).toLowerCase().includes(filter.toLowerCase())));
  const groups = cat.data.groups.filter((g) => perms.some((p) => p.group === g));

  const setCell = (role: string, perm: string, cell: Cell) => setDraft({ ...draft, [role]: { ...draft[role], [perm]: cell } });
  const save = async () => {
    setSaving(true);
    const changedRoles = [...new Set(dirty.map(([r]) => r))];
    try {
      for (const r of changedRoles) {
        const grants: Grant[] = Object.entries(draft[r]).filter(([, c]) => c).map(([permission, c]) => ({ permission, effect: c!.effect, approver_role_id: c!.approver_role_id }));
        await put(`/api/iam/roles/${r}/grants`, { grants });
      }
      toast(`Сохранено: ${dirty.length} изменений в ${changedRoles.length} рол${changedRoles.length === 1 ? 'и' : 'ях'}`);
      qc.invalidateQueries({ queryKey: ['roles'] });
      qc.invalidateQueries({ queryKey: ['me'] });
    } catch (e) { toast(errText(e), 'error'); qc.invalidateQueries({ queryKey: ['roles'] }); } finally { setSaving(false); }
  };
  const openCell = open && cat.data.permissions.find((p) => p.key === open.perm);
  const cur = open ? draft[open.role]?.[open.perm] ?? null : null;

  return (
    <Page title="Матрица прав" crumbs="Кто что может: смотреть, править, где нужна виза"
      actions={<>
        <input className="input" style={{ width: 220, height: 32 }} placeholder="Найти право…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label className="check small"><input type="checkbox" checked={risky} onChange={(e) => setRisky(e.target.checked)} />Только рискованные</label>
        {edit && <button className="btn btn--primary btn--sm" onClick={() => setRoleModal('new')}><Icon name="plus" />Роль</button>}
      </>}>
      <div className="legend">
        <EffectBadge e="allow" /> действует сразу
        <EffectBadge e="approval" /> после визы
        <EffectBadge e="deny" /> запрет сильнее любого «можно»
        <span className="eff eff--none">—</span> не выдано = нельзя
        <span className="row" style={{ gap: 6 }}><span className="risk" />выдавать осознанно</span>
      </div>
      <div className="matrix-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th>Право</th>
              {roles.data.map((r) => (
                <th key={r.id}>
                  <div className="role-h" onClick={() => edit && setRoleModal(r)} title={r.description}>
                    <span className="dot" style={{ background: r.color }} />
                    <span className="role-h__name">{r.name}</span>
                    <span className="role-h__meta">{r.members} чел.</span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <MatrixGroup key={g} title={g} cols={roles.data.length}>
                {perms.filter((p) => p.group === g).map((p) => (
                  <tr key={p.key}>
                    <td>
                      <div className="perm">
                        <span className="perm__t">{p.risky && <span className="risk" title="Рискованное право" />}{p.title}</span>
                        {p.hint && <span className="perm__h">{p.hint}</span>}
                      </div>
                    </td>
                    {roles.data.map((r) => {
                      const c = draft[r.id]?.[p.key] ?? null;
                      const isDirty = !same(c, base[r.id]?.[p.key] ?? null);
                      return (
                        <td key={r.id} className={`cell ${isDirty ? 'cell--dirty' : ''}`}>
                          <button disabled={!edit} onClick={(e) => {
                            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                            setOpen({ role: r.id, perm: p.key, x: rect.left, y: rect.bottom });
                          }} title={c?.effect === 'approval' ? `Визирует: ${c.approver_role_id ? roleName[c.approver_role_id] : 'любой с правом виз'}` : undefined}>
                            <EffectBadge e={c?.effect ?? 'none'} />
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </MatrixGroup>
            ))}
          </tbody>
        </table>
      </div>
      {open && openCell && (
        <Popover onClose={() => setOpen(null)} style={{ position: 'fixed', left: Math.min(open.x, window.innerWidth - 260), top: Math.min(open.y + 4, window.innerHeight - 280) }}>
          <div className="muted small" style={{ padding: '6px 10px' }}>{roleName[open.role]} · {openCell.title}</div>
          {(['none', 'allow', 'approval', 'deny'] as const).filter((e) => e !== 'approval' || openCell.approvable).map((e) => (
            <button key={e} className={`popover__item ${(cur?.effect ?? 'none') === e ? 'active' : ''}`}
              onClick={() => { setCell(open.role, open.perm, e === 'none' ? null : { effect: e, approver_role_id: e === 'approval' ? cur?.approver_role_id ?? null : null }); if (e !== 'approval') setOpen(null); }}>
              <EffectBadge e={e} />
              <span className="muted small">{e === 'none' ? 'не выдано' : e === 'allow' ? 'сразу' : e === 'approval' ? 'после визы' : 'явный запрет'}</span>
            </button>
          ))}
          {cur?.effect === 'approval' && (
            <div style={{ padding: '6px 10px 8px' }}>
              <Field label="Кто визирует">
                <select className="select" value={cur.approver_role_id ?? ''} onChange={(e) => setCell(open.role, open.perm, { effect: 'approval', approver_role_id: e.target.value || null })}>
                  <option value="">Любой с правом виз в подразделении</option>
                  {roles.data.filter((r) => r.id !== open.role).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </Field>
            </div>
          )}
        </Popover>
      )}
      {dirty.length > 0 && (
        <div className="savebar">
          <Icon name="edit" />
          <span>Изменено прав: <strong>{dirty.length}</strong>. Каждое изменение попадёт в журнал аудита.</span>
          <span className="spacer" />
          <button className="btn btn--ghost btn--sm" style={{ color: '#fff' }} onClick={() => setDraft(base)}>Отменить</button>
          <button className="btn btn--primary btn--sm" disabled={saving} onClick={save}>Сохранить</button>
        </div>
      )}
      {roleModal && <RoleModal role={roleModal === 'new' ? null : roleModal} roles={roles.data} onClose={() => setRoleModal(null)} />}
    </Page>
  );
}

function MatrixGroup({ title, cols, children }: { title: string; cols: number; children: React.ReactNode }) {
  return <>
    <tr className="grp"><td colSpan={cols + 1}>{title}</td></tr>
    {children}
  </>;
}

function RoleModal({ role, roles, onClose }: { role: Role | null; roles: Role[]; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ name: role?.name ?? '', description: role?.description ?? '', color: role?.color ?? '#8c8c8c', copy_from: '' });
  const save = async () => {
    try {
      if (role) await patch(`/api/iam/roles/${role.id}`, { name: f.name, description: f.description, color: f.color });
      else await post('/api/iam/roles', { name: f.name, description: f.description, color: f.color, copy_from: f.copy_from || null });
      qc.invalidateQueries({ queryKey: ['roles'] });
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  const remove = async () => {
    if (!role || !confirm(`Удалить роль «${role.name}»?`)) return;
    try { await del(`/api/iam/roles/${role.id}`); qc.invalidateQueries({ queryKey: ['roles'] }); onClose(); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title={role ? `Роль: ${role.name}` : 'Новая роль'} onClose={onClose}
      foot={<>
        {role && !role.is_system && <button className="btn btn--danger" onClick={remove}><Icon name="trash" />Удалить</button>}
        <span className="spacer" />
        <button className="btn btn--ghost" onClick={onClose}>Отмена</button>
        <button className="btn btn--primary" disabled={!f.name} onClick={save}>Сохранить</button>
      </>}>
      <div className="grid2" style={{ gridTemplateColumns: '1fr 120px' }}>
        <Field label="Название"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Цвет"><input className="input" type="color" value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })} style={{ padding: 4 }} /></Field>
      </div>
      <Field label="Описание" hint="Для кого роль и чего она не даёт"><textarea className="textarea" style={{ minHeight: 70 }} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      {!role && (
        <Field label="Скопировать права из" hint="Копировать можно только права, которые есть у вас самих">
          <select className="select" value={f.copy_from} onChange={(e) => setF({ ...f, copy_from: e.target.value })}>
            <option value="">— пустая роль —</option>
            {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Field>
      )}
      {role?.is_system && <p className="muted small">Системную роль нельзя удалить, но её права можно менять в матрице.</p>}
    </Modal>
  );
}

// ── Люди ────────────────────────────────────────────────────────────────────────
type MS = { unit_id: string; role_id: string; expires_at?: string | null };
const TERMS = [0, 1, 7, 30, 90];
const until = (iso: string) => new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });

function People() {
  const can = useCan();
  const users = useQuery({ queryKey: ['users'], queryFn: () => get<User[]>('/api/iam/users') });
  const roles = useRoles();
  const { data: units = [] } = useUnits();
  const [open, setOpen] = useState<User | 'new' | null>(null);
  const [q, setQ] = useState('');
  const roleBy = Object.fromEntries((roles.data ?? []).map((r) => [r.id, r]));
  const unitBy = Object.fromEntries(units.map((u) => [u.id, u.name]));
  const list = (users.data ?? []).filter((u) => !q || (u.display_name + u.login + u.position).toLowerCase().includes(q.toLowerCase()));
  return (
    <Page title="Люди" crumbs={`${users.data?.length ?? 0} человек`}
      actions={<>
        <input className="input" style={{ width: 220, height: 32 }} placeholder="Поиск…" value={q} onChange={(e) => setQ(e.target.value)} />
        {can('iam.users') && <button className="btn btn--primary btn--sm" onClick={() => setOpen('new')}><Icon name="plus" />Сотрудник</button>}
      </>}>
      <Card pad={false}>
        <table className="table">
          <thead><tr><th>Человек</th><th>Роли · где действуют</th><th>Статус</th><th>Последний вход</th></tr></thead>
          <tbody>
            {list.map((u) => (
              <tr key={u.id} className="click" onClick={() => setOpen(u)}>
                <td>
                  <div className="row" style={{ flexWrap: 'nowrap' }}>
                    <span className="avatar">{initials(u.display_name)}</span>
                    <div><div style={{ fontWeight: 500 }}>{u.display_name}</div><div className="muted small">{u.login}{u.position ? ` · ${u.position}` : ''}</div></div>
                  </div>
                </td>
                <td>
                  <div className="row">
                    {u.memberships.map((m) => (
                      <span key={m.unit_id + m.role_id} className="chip"><span className="dot" style={{ background: roleBy[m.role_id]?.color }} />{roleBy[m.role_id]?.name} · {unitBy[m.unit_id]}
                        {m.expires_at && <span className="muted"> · до {until(m.expires_at)}</span>}</span>
                    ))}
                    {!u.memberships.length && <span className="muted small">ролей нет — ничего не может</span>}
                  </div>
                </td>
                <td><div className="row">
                  {u.is_owner && <span className="pill pill--accent">владелец</span>}
                  {u.mfa && <span className="pill pill--plain pill--info"><Icon name="shield" size={11} />2FA</span>}
                  {!u.is_active && <span className="pill pill--danger">отключён</span>}
                  {u.locked && <span className="pill pill--warn">заблокирован</span>}
                  {u.must_change_password && <span className="pill">временный пароль</span>}
                  {u.is_active && !u.locked && !u.is_owner && !u.must_change_password && <span className="pill pill--good">активен</span>}
                </div></td>
                <td className="muted small">{when(u.last_login_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {open === 'new' && <NewUser onClose={() => setOpen(null)} />}
      {open && open !== 'new' && <UserModal user={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function genPassword(): string {
  const words = ['Фасад', 'Пролёт', 'Ригель', 'Атриум', 'Эркер', 'Портал', 'Модуль', 'Терраса', 'Колонна', 'Кровля'];
  const a = new Uint32Array(3);
  crypto.getRandomValues(a);
  return `${words[a[0] % words.length]}-${words[a[1] % words.length]}-${1000 + (a[2] % 9000)}`;
}

function MembershipEditor({ value, onChange, disabled }: { value: MS[]; onChange: (v: MS[]) => void; disabled?: boolean }) {
  const roles = useRoles();
  const { data: units = [] } = useUnits();
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.map((m, i) => (
        <div key={i} className="grid2" style={{ gridTemplateColumns: '1fr 1fr 120px 32px', alignItems: 'center' }}>
          <select className="select" value={m.role_id} disabled={disabled} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, role_id: e.target.value } : x)))}>
            {(roles.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <UnitSelect value={m.unit_id} onChange={(v) => v && onChange(value.map((x, j) => (j === i ? { ...x, unit_id: v } : x)))} />
          <select className="select" value={m.expires_at ? 'keep' : '0'} disabled={disabled} title="Срок роли"
            onChange={(e) => {
              if (e.target.value === 'keep') return;
              const days = Number(e.target.value);
              const expires_at = days ? new Date(Date.now() + days * 86_400_000).toISOString() : null;
              onChange(value.map((x, j) => (j === i ? { ...x, expires_at } : x)));
            }}>
            {m.expires_at && <option value="keep">до {until(m.expires_at)}</option>}
            {TERMS.map((d) => <option key={d} value={String(d)}>{d ? `на ${d} дн.` : 'бессрочно'}</option>)}
          </select>
          <button className="btn btn--ghost btn--icon btn--sm" disabled={disabled} onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Убрать"><Icon name="x" /></button>
        </div>
      ))}
      {!disabled && (
        <div><button className="btn btn--line btn--sm" disabled={!roles.data?.length || !units.length}
          onClick={() => onChange([...value, { role_id: roles.data!.find((r) => r.key === 'employee')?.id ?? roles.data![0].id, unit_id: units.find((u) => !u.parent_id)?.id ?? units[0].id }])}>
          <Icon name="plus" />Роль в подразделении</button></div>
      )}
      <p className="muted small">Роль действует в выбранном подразделении и во всех вложенных. Роль в корневом подразделении — на всю компанию.
        Временная роль сама перестаёт действовать в срок — удобно для замещения и разовых задач.</p>
    </div>
  );
}

function NewUser({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ login: '', display_name: '', position: '', email: '', password: genPassword() });
  const [ms, setMs] = useState<MS[]>([]);
  const save = async () => {
    try {
      await post('/api/iam/users', { ...f, email: f.email || null, memberships: ms });
      qc.invalidateQueries({ queryKey: ['users'] });
      toast('Сотрудник заведён. Передайте временный пароль лично — при входе его попросят сменить.');
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title="Новый сотрудник" wide onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={!f.login || !f.display_name} onClick={save}>Завести</button></>}>
      <div className="grid2">
        <Field label="ФИО"><input className="input" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} placeholder="Иванова Мария Сергеевна" /></Field>
        <Field label="Логин"><input className="input" value={f.login} onChange={(e) => setF({ ...f, login: e.target.value })} placeholder="m.ivanova" /></Field>
        <Field label="Должность"><input className="input" value={f.position} onChange={(e) => setF({ ...f, position: e.target.value })} placeholder="Архитектор" /></Field>
        <Field label="E-mail"><input className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
      </div>
      <Field label="Временный пароль" hint="Смена обязательна при первом входе">
        <div className="row" style={{ flexWrap: 'nowrap' }}>
          <input className="input mono" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <button className="btn btn--line btn--sm" onClick={() => setF({ ...f, password: genPassword() })}><Icon name="refresh" /></button>
        </div>
      </Field>
      <Field label="Роли"><MembershipEditor value={ms} onChange={setMs} /></Field>
    </Modal>
  );
}

function UserModal({ user, onClose }: { user: User; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const can = useCan();
  const me = useMe();
  const [tab, setTab] = useState<'roles' | 'effective' | 'profile' | 'security'>('roles');
  const [ms, setMs] = useState<MS[]>(user.memberships);
  const [f, setF] = useState({ display_name: user.display_name, position: user.position, email: user.email ?? '' });
  const manage = can('iam.users') && (!user.is_owner || me.user.is_owner);
  const refresh = () => { qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['roles'] }); };
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast(ok); refresh(); } catch (e) { toast(errText(e), 'error'); }
  };
  const tabs = [['roles', 'Роли'], ['effective', 'Что может'], ['profile', 'Профиль'], ['security', 'Безопасность']] as const;
  return (
    <Modal title={<span className="row"><span className="avatar">{initials(user.display_name)}</span>{user.display_name}</span>} wide onClose={onClose}>
      <div className="tabs">{tabs.map(([k, t]) => <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{t}</button>)}</div>
      {tab === 'roles' && (
        <>
          <MembershipEditor value={ms} onChange={setMs} disabled={!manage} />
          {manage && <div className="row"><span className="spacer" />
            <button className="btn btn--primary" onClick={() => act(() => put(`/api/iam/users/${user.id}/memberships`, ms), 'Роли сохранены')}>Сохранить роли</button></div>}
        </>
      )}
      {tab === 'effective' && <Effective userId={user.id} />}
      {tab === 'profile' && (
        <>
          <div className="grid2">
            <Field label="ФИО"><input className="input" disabled={!manage} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
            <Field label="Должность"><input className="input" disabled={!manage} value={f.position} onChange={(e) => setF({ ...f, position: e.target.value })} /></Field>
            <Field label="E-mail"><input className="input" disabled={!manage} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label="Логин"><input className="input" disabled value={user.login} /></Field>
          </div>
          {manage && <div className="row"><span className="spacer" />
            <button className="btn btn--primary" onClick={() => act(() => patch(`/api/iam/users/${user.id}`, { ...f, email: f.email || null }), 'Сохранено')}>Сохранить</button></div>}
        </>
      )}
      {tab === 'security' && (
        <div className="stack">
          {manage && (
            <>
              <div className="row">
                <button className="btn btn--line" onClick={() => {
                  const p = genPassword();
                  act(() => post(`/api/iam/users/${user.id}/password`, { password: p }), `Новый временный пароль: ${p}`);
                }}><Icon name="key" />Сбросить пароль</button>
                <span className="muted small">Все сессии человека будут завершены</span>
              </div>
              <div className="row">
                <button className="btn btn--line" onClick={() => act(() => post(`/api/iam/users/${user.id}/logout`), 'Все сессии завершены')}><Icon name="logout" />Завершить все сессии</button>
                {user.mfa && user.id !== me.user.id &&
                  <button className="btn btn--line" onClick={() => confirm('Снять второй фактор? Человек настроит его заново при входе.') &&
                    act(() => post(`/api/iam/users/${user.id}/2fa/reset`), 'Второй фактор сброшен')}><Icon name="shield" />Сбросить 2FA (потерян телефон)</button>}
              </div>
              <div className="row">
                {user.is_active
                  ? <button className="btn btn--danger" disabled={user.id === me.user.id} onClick={() => act(() => patch(`/api/iam/users/${user.id}`, { is_active: false }), 'Доступ отключён')}><Icon name="ban" />Отключить доступ</button>
                  : <button className="btn btn--line" onClick={() => act(() => patch(`/api/iam/users/${user.id}`, { is_active: true }), 'Доступ включён')}>Включить доступ</button>}
              </div>
            </>
          )}
          {me.user.is_owner && (
            <div className="row">
              <button className="btn btn--line" onClick={() => act(() => post(`/api/iam/users/${user.id}/owner`, { is_owner: !user.is_owner }), 'Готово')}>
                <Icon name="shield" />{user.is_owner ? 'Снять права владельца' : 'Сделать владельцем'}</button>
              <span className="muted small">Владелец может всё; каждое его действие всё равно в аудите</span>
            </div>
          )}
          {!manage && !me.user.is_owner && <Empty title="Нет прав на управление учётными записями" />}
        </div>
      )}
    </Modal>
  );
}

function Effective({ userId }: { userId: string }) {
  const [unit, setUnit] = useState<string | null>(null);
  const cat = useCatalog();
  const roles = useRoles();
  const q = useQuery({ queryKey: ['effective', userId, unit], queryFn: () => get<Record<string, Decision>>(`/api/iam/users/${userId}/effective${unit ? `?unit_id=${unit}` : ''}`) });
  const roleName = Object.fromEntries((roles.data ?? []).map((r) => [r.id, r.name]));
  return (
    <div className="stack">
      <Field label="В подразделении" hint="Пусто — «где-нибудь в компании»"><UnitSelect value={unit} onChange={setUnit} allowEmpty emptyLabel="— везде —" /></Field>
      <div style={{ maxHeight: 420, overflow: 'auto' }}>
        <table className="table"><tbody>
          {(cat.data?.permissions ?? []).map((p) => {
            const d = q.data?.[p.key];
            return (
              <tr key={p.key}>
                <td style={{ width: '42%' }}>{p.title}<div className="muted small">{p.group}</div></td>
                <td style={{ width: 120 }}>{d && <EffectBadge e={d.effect === 'deny' && d.reason.startsWith('нет права') ? 'none' : d.effect} />}</td>
                <td className="muted small">{d?.reason}{d?.approver_role_ids.length ? ` · визирует: ${d.approver_role_ids.map((r) => roleName[r] ?? r).join(', ')}` : ''}</td>
              </tr>
            );
          })}
        </tbody></table>
      </div>
    </div>
  );
}

// ── Оргструктура ────────────────────────────────────────────────────────────────
function Org() {
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const { data: units = [] } = useUnits();
  const [modal, setModal] = useState<{ unit?: Unit; parent?: string } | null>(null);
  const edit = can('iam.org');
  const KIND: Record<string, string> = { company: 'компания', department: 'отдел', group: 'группа', project: 'проект' };
  const remove = async (u: Unit) => {
    if (!confirm(`Удалить «${u.name}»?`)) return;
    try { await del(`/api/iam/units/${u.id}`); qc.invalidateQueries({ queryKey: ['units'] }); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Page title="Оргструктура" crumbs="Роли наследуются сверху вниз: роль в отделе действует во всех его группах и проектах">
      <Card pad={false}>
        <div className="tree" style={{ padding: 8 }}>
          {orderTree(units).map(([u, depth]) => (
            <div key={u.id} className="tree__row" style={{ paddingLeft: 12 + depth * 26 }}>
              {depth > 0 && <span className="tree__line" />}
              <strong style={{ fontWeight: depth === 0 ? 700 : 500 }}>{u.name}</strong>
              <span className="tree__kind">{KIND[u.kind] ?? u.kind}</span>
              {u.members > 0 && <span className="chip"><Icon name="users" />{u.members}</span>}
              <span className="muted small">{u.description}</span>
              <span className="spacer" />
              {edit && <>
                <button className="btn btn--ghost btn--xs" onClick={() => setModal({ parent: u.id })}><Icon name="plus" />Вложить</button>
                <button className="btn btn--ghost btn--xs" onClick={() => setModal({ unit: u })}><Icon name="edit" /></button>
                {u.parent_id && <button className="btn btn--ghost btn--xs" onClick={() => remove(u)}><Icon name="trash" /></button>}
              </>}
            </div>
          ))}
        </div>
      </Card>
      {modal && <UnitModal unit={modal.unit} parent={modal.parent} onClose={() => setModal(null)} />}
    </Page>
  );
}

function UnitModal({ unit, parent, onClose }: { unit?: Unit; parent?: string; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ name: unit?.name ?? '', kind: unit?.kind ?? 'department', description: unit?.description ?? '', parent_id: unit?.parent_id ?? parent ?? null });
  const save = async () => {
    try {
      if (unit) await patch(`/api/iam/units/${unit.id}`, f);
      else await post('/api/iam/units', f);
      qc.invalidateQueries({ queryKey: ['units'] });
      onClose();
    } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Modal title={unit ? unit.name : 'Новое подразделение'} onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>Отмена</button><button className="btn btn--primary" disabled={!f.name} onClick={save}>Сохранить</button></>}>
      <div className="grid2">
        <Field label="Название"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Тип">
          <select className="select" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="department">Отдел</option><option value="group">Группа</option><option value="project">Проект</option><option value="company">Компания</option>
          </select>
        </Field>
      </div>
      {unit?.parent_id !== null && <Field label="Входит в"><UnitSelect value={f.parent_id} onChange={(v) => setF({ ...f, parent_id: v })} /></Field>}
      <Field label="Описание"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
    </Modal>
  );
}

// ── Проверка доступа ────────────────────────────────────────────────────────────
function Check() {
  const users = useQuery({ queryKey: ['users'], queryFn: () => get<User[]>('/api/iam/users') });
  const cat = useCatalog();
  const roles = useRoles();
  const [user, setUser] = useState('');
  const [perm, setPerm] = useState('');
  const [unit, setUnit] = useState<string | null>(null);
  const ok = !!user && !!perm;
  const q = useQuery({
    queryKey: ['check', user, perm, unit], enabled: ok,
    queryFn: () => get<Decision>(`/api/iam/check?user_id=${user}&permission=${perm}${unit ? `&unit_id=${unit}` : ''}`),
  });
  const roleName = Object.fromEntries((roles.data ?? []).map((r) => [r.id, r.name]));
  return (
    <Page title="Проверка доступа" crumbs="Ответ движка прав — тот же, что при реальном действии">
      <Card>
        <div className="grid3">
          <Field label="Кто"><select className="select" value={user} onChange={(e) => setUser(e.target.value)}>
            <option value="">— выберите —</option>{(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
          </select></Field>
          <Field label="Что хочет сделать"><select className="select" value={perm} onChange={(e) => setPerm(e.target.value)}>
            <option value="">— выберите —</option>
            {(cat.data?.groups ?? []).map((g) => (
              <optgroup key={g} label={g}>{cat.data!.permissions.filter((p) => p.group === g).map((p) => <option key={p.key} value={p.key}>{p.title}</option>)}</optgroup>
            ))}
          </select></Field>
          <Field label="Где"><UnitSelect value={unit} onChange={setUnit} allowEmpty emptyLabel="— где-нибудь —" /></Field>
        </div>
      </Card>
      {ok && q.data && (
        <section className="card">
          <div className="card__body row" style={{ gap: 18 }}>
            <span className="hero__num" style={{ fontSize: 56, color: q.data.effect === 'deny' ? 'var(--danger)' : q.data.effect === 'approval' ? 'var(--warn)' : 'var(--good)' }}>
              {q.data.effect === 'allow' ? 'Да' : q.data.effect === 'approval' ? 'С визой' : 'Нет'}
            </span>
            <div className="stack" style={{ gap: 4 }}>
              <EffectBadge e={q.data.effect} />
              <span>{q.data.reason}</span>
              {!!q.data.approver_role_ids.length && <span className="muted small">Визирует: {q.data.approver_role_ids.map((r) => roleName[r] ?? r).join(', ')}</span>}
              {q.data.effect === 'approval' && !q.data.approver_role_ids.length && <span className="muted small">Визирует любой с правом «Одобрять и отклонять запросы» в этом подразделении, кроме самого просящего</span>}
            </div>
          </div>
        </section>
      )}
    </Page>
  );
}
