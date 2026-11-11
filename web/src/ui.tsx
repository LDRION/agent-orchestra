import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { get, type Effect, type Me, type Opt, type Unit } from './api';
import { Icon } from './icons';

// ── Уведомления ─────────────────────────────────────────────────────────────────
type Toast = { id: number; text: string; kind: 'ok' | 'error' };
const ToastCtx = createContext<(text: string, kind?: 'ok' | 'error') => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: 'ok' | 'error' = 'ok') => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'error' ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast toast--${t.kind}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// ── Текущий человек и его права ─────────────────────────────────────────────────
export const MeCtx = createContext<Me | null>(null);
export function useMe(): Me {
  const me = useContext(MeCtx);
  if (!me) throw new Error('нет сессии');
  return me;
}
/** allow — можно, approval — по визе, deny — нельзя. Владелец видит allow на всё. */
export function useCan() {
  const me = useMe();
  return (perm: string, accept: Effect[] = ['allow']) => accept.includes(me.permissions[perm] ?? 'deny');
}

export function useUnits() {
  return useQuery({ queryKey: ['units'], queryFn: () => get<Unit[]>('/api/iam/units'), staleTime: 60_000 });
}

// ── Подписи ─────────────────────────────────────────────────────────────────────
export const STATUS: Record<string, [string, string]> = {
  planning: ['Планирует', 'pill--accent pill--busy'],
  awaiting_approval: ['Ждёт визы', 'pill--warn'],
  queued: ['В очереди', 'pill--info'],
  running: ['Выполняется', 'pill--accent pill--busy'],
  succeeded: ['Готово', 'pill--good'],
  failed: ['Ошибка', 'pill--danger'],
  cancelled: ['Остановлено', ''],
  rejected: ['Отклонено', 'pill--danger'],
  interrupted: ['Прервано', 'pill--warn'],
  pending: ['Ожидает', ''],
  done: ['Готово', 'pill--good'],
  skipped: ['Пропущен', ''],
  approved: ['Одобрено', 'pill--good'],
  expired: ['Истекло', ''],
  draft: ['Черновик', ''],
  active: ['Работает', 'pill--good'],
  archived: ['Архив', ''],
};
export function Status({ s }: { s: string }) {
  const [t, c] = STATUS[s] ?? [s, ''];
  return <span className={`pill ${c}`}>{t}</span>;
}

export const TIERS: Record<string, [string, string]> = {
  local: ['Локально', 'pill--good'],
  private: ['Частный сервер', 'pill--info'],
  cloud_ru: ['Облако РФ', 'pill--violet'],
  cloud_foreign: ['Зарубежное облако', 'pill--warn'],
};
export function Tier({ t }: { t: string }) {
  const [label, c] = TIERS[t] ?? [t, ''];
  return <span className={`pill ${c}`}>{label}</span>;
}

export const CLASSES: Record<string, string> = {
  public: 'Открыто', internal: 'Для служебного пользования', confidential: 'Конфиденциально', secret: 'Строго конфиденциально',
};
export function Classification({ c }: { c: string }) {
  const cls = c === 'confidential' || c === 'secret' ? 'pill--danger' : c === 'public' ? 'pill--good' : '';
  return <span className={`pill pill--plain ${cls}`}><Icon name="lock" size={11} />{CLASSES[c] ?? c}</span>;
}

export const EFFECT_LABEL: Record<Effect | 'none', string> = { allow: 'Можно', approval: 'С визой', deny: 'Запрещено', none: '—' };
export function EffectBadge({ e }: { e: Effect | 'none' }) {
  const icon = e === 'allow' ? 'check' : e === 'approval' ? 'clock' : e === 'deny' ? 'ban' : '';
  return <span className={`eff eff--${e}`}>{icon && <Icon name={icon} />}{EFFECT_LABEL[e]}</span>;
}

export const ROLE_LABEL: Record<string, string> = { orchestrator: 'Оркестратор', lead: 'Руководитель', specialist: 'Специалист', service: 'Служебный' };

export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  return same ? d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

// ── Каркасные компоненты ────────────────────────────────────────────────────────
export function Page({ title, crumbs, actions, children }: { title: string; crumbs?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => { document.title = `${title} · Оркестр`; }, [title]);
  return (
    <>
      <header className="main__head">
        <div>
          {crumbs && <div className="main__crumbs">{crumbs}</div>}
          <h1>{title}</h1>
        </div>
        <div className="main__actions">{actions}</div>
      </header>
      <div className="main__body"><div className="page">{children}</div></div>
    </>
  );
}

export function Card({ title, sub, actions, children, pad = true, className = '' }:
  { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <div className="card__head">
          <h2>{title}</h2>
          {sub && <span className="muted">{sub}</span>}
          <span className="spacer" />
          {actions}
        </div>
      )}
      {pad ? <div className="card__body">{children}</div> : children}
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function Empty({ title, text, children }: { title: string; text?: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{text && <span>{text}</span>}{children}</div>;
}

export function Modal({ title, onClose, children, foot, wide }:
  { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? 'modal--wide' : ''}`} role="dialog" aria-modal="true">
        <div className="modal__head"><h2>{title}</h2><span className="spacer" />
          <button className="btn btn--ghost btn--icon btn--sm" onClick={onClose} aria-label="Закрыть"><Icon name="x" /></button>
        </div>
        <div className="modal__body">{children}</div>
        {foot && <div className="modal__foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Popover({ onClose, children, style }: { onClose: () => void; children: ReactNode; style?: React.CSSProperties }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    setTimeout(() => document.addEventListener('mousedown', h));
    window.addEventListener('keydown', k);
    return () => { document.removeEventListener('mousedown', h); window.removeEventListener('keydown', k); };
  }, [onClose]);
  return <div ref={ref} className="popover" style={style}>{children}</div>;
}

export function UnitSelect({ value, onChange, allowEmpty, emptyLabel = '— не задано —' }:
  { value: string | null; onChange: (v: string | null) => void; allowEmpty?: boolean; emptyLabel?: string }) {
  const { data: units = [] } = useUnits();
  const ordered = orderTree(units);
  return (
    <select className="select" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
      {allowEmpty && <option value="">{emptyLabel}</option>}
      {ordered.map(([u, depth]) => <option key={u.id} value={u.id}>{'  '.repeat(depth)}{u.name}</option>)}
    </select>
  );
}

export function orderTree(units: Unit[]): [Unit, number][] {
  const kids = new Map<string | null, Unit[]>();
  units.forEach((u) => kids.set(u.parent_id, [...(kids.get(u.parent_id) ?? []), u]));
  const out: [Unit, number][] = [];
  const walk = (pid: string | null, d: number) => (kids.get(pid) ?? []).forEach((u) => { out.push([u, d]); walk(u.id, d + 1); });
  walk(null, 0);
  return out;
}

export function OptSelect({ value, options, onChange }: { value: string; options: Opt[]; onChange: (v: string) => void }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}
    </select>
  );
}

export const CLASS_OPTS: Opt[] = Object.entries(CLASSES).map(([id, title]) => ({ id, title }));
