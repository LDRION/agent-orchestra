// Обёртка над fetch: cookie-сессия, CSRF-заголовок из читаемой cookie, ошибки — человеческим текстом.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function csrf(): string {
  // под HTTPS cookie называется __Host-orchestra_csrf: только этот хост, только https, без поддоменов
  const m = document.cookie.match(/(?:^|;\s*)(?:__Host-)?orchestra_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

// Опасное действие (права, ключи, политика) сервер выполняет, только если пароль вводили недавно.
// Окно подтверждения (ReauthGate) регистрирует себя здесь; одновременные запросы ждут одного ввода.
type ReauthAsk = () => Promise<boolean>;
let reauthAsk: ReauthAsk | null = null;
let reauthPending: Promise<boolean> | null = null;
export function onReauth(fn: ReauthAsk | null) { reauthAsk = fn; }
function askReauth(): Promise<boolean> {
  if (!reauthAsk) return Promise.resolve(false);
  reauthPending ??= reauthAsk().finally(() => { reauthPending = null; });
  return reauthPending;
}

export async function api<T = unknown>(method: string, url: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf();
  const r = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  if (r.status === 401 && !url.startsWith('/api/auth/')) {
    window.dispatchEvent(new Event('orchestra:unauthorized'));
  }
  if (r.status === 403 && r.headers.get('X-Orchestra-Reauth') === '1' && !retried) {
    if (await askReauth()) return api<T>(method, url, body, true);
    throw new ApiError(403, 'Действие не выполнено: нужно подтвердить вход паролем');
  }
  const text = await r.text();
  const data = text ? safeJson(text) : null;
  if (!r.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? String((data as { detail: unknown }).detail) : r.statusText;
    throw new ApiError(r.status, detail || `Ошибка ${r.status}`);
  }
  return (data ?? text) as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

/** Загрузка файла (multipart): CSRF — тем же заголовком, Content-Type ставит браузер. */
export async function upload<T>(url: string, form: FormData): Promise<T> {
  const r = await fetch(url, { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
  const data = safeJson(await r.text());
  if (!r.ok) throw new ApiError(r.status, data && typeof data === 'object' && 'detail' in data ? String((data as { detail: unknown }).detail) : r.statusText);
  return data as T;
}

export const get = <T>(url: string) => api<T>('GET', url);
export const post = <T>(url: string, body?: unknown) => api<T>('POST', url, body ?? {});
export const put = <T>(url: string, body?: unknown) => api<T>('PUT', url, body);
export const patch = <T>(url: string, body?: unknown) => api<T>('PATCH', url, body);
export const del = <T>(url: string) => api<T>('DELETE', url);

// ── Типы API ────────────────────────────────────────────────────────────────────
export type Effect = 'allow' | 'approval' | 'deny';

export interface Me {
  user: { id: string; login: string; display_name: string; position: string; is_owner: boolean; must_change_password: boolean };
  memberships: { unit_id: string; unit: string; role_id: string; role: string }[];
  permissions: Record<string, Effect>;
  mfa: { enabled: boolean; required: boolean; recovery_left: number };
}

export interface KnowledgeDoc {
  id: string; title: string; kind: string; source: string; unit_id: string | null; classification: string;
  chars: number; tags: string[]; created_at: string; author: string; body?: string;
}
export interface KnowledgeHit { doc_id: string; title: string; chunk: string; score: number; classification: string }
export interface TryResult {
  text: string; provider: string; model: string; tokens_in: number; tokens_out: number; ms: number; tools: string[];
  label: string; classification: string;
}
// ── интеграции ──
export interface IntField { key: string; title: string; kind: string; hint: string; required: boolean; default: unknown; options: string[] }
export interface IntActionSpec { key: string; title: string; description: string; write: boolean; args: Record<string, string>; required: string[] }
export interface IntegrationKind {
  kind: string; title: string; description: string; icon: string; secret: IntField | null; fields: IntField[];
  actions: IntActionSpec[]; default_trust: string; default_classification: string; demo: boolean;
}
export interface Integration {
  id: string; key: string; kind: string; name: string; enabled: boolean; mode: 'live' | 'demo'; trust_tier: string;
  data_classification: string; settings: Record<string, unknown>; has_secret: boolean; secret_hint: string;
  last_check_at: string | null; last_check_ok: boolean | null; last_check_message: string; hook_path: string | null;
  tools: { key: string; title: string; write: boolean; args: Record<string, string>; required: string[] }[]; secret_once?: string | null;
}
export interface IntegrationActionRow {
  id: string; integration: string; action: string; title: string; args: Record<string, unknown>; status: string;
  run_id: string | null; origin_type: string; result: Record<string, unknown>; error: string; created_at: string;
}

// ── процесс «Протоколы» ──
export interface RefField {
  status: string; value: string | null; label: string | null; mention: string | null; note: string;
  candidates: { id: string; label: string; hint: string }[];
}
export interface PTask {
  id: string; idx: number; item: string | null; state: string; deleted: boolean; confirmed: boolean; stale: boolean;
  external_id: string; send_error: string; can_confirm: boolean; edited: string[];
  title: { value: string; status: string };
  project: RefField; assignee: RefField;
  deadline: { value: string | null; status: string; note: string; mention: string | null; suggestion: string | null };
  priority: { value: 'high' | 'medium' | 'low'; status: string; reason: string };
  risk: { value: string | null; status: string; reason: string; sources: { ref: string; title: string }[] };
  quote: { value: string; status: string; score?: number; corrected?: boolean };
  issues: { field: string; level: 'block' | 'warn' | 'info'; code: string; message: string }[];
}
export interface DirEmployee { id: string; last_name: string; first_name: string; middle_name: string; role: string; label: string; external_id: string }
export interface ProtocolRow {
  id: string; title: string; meeting_date: string | null; status: string; error: string; classification: string;
  created_at: string; progress: { total?: number; done?: number; stage?: string } | null; counts: Record<string, number>;
}
export interface ProtocolDetail extends ProtocolRow {
  text: string; tasks: PTask[]; can_edit: boolean;
  decisions: { item: string | null; text: string; project: RefField; quote: { value: string } }[];
  skipped: { item: string | null; reason: string; quote: { value: string } }[];
  directory: { employees: DirEmployee[]; projects: { id: string; name: string; stage: string; external_id: string }[] };
  senders: { id: string; name: string; mode: string }[];
  meta: { prompt_version?: number; agent?: string; provider?: string; model?: string; seconds?: number;
    refs?: { ref: string; title: string }[]; failed?: { label: string; message: string }[] };
}

export interface SessionRow { id: string; created_at: string; last_seen_at: string; ip: string; user_agent: string; current: boolean }
export interface UsageToday {
  me: { tokens: number; limit: number };
  providers?: { id: string; name: string; tokens: number; limit: number }[];
  users?: { name: string; tokens: number }[];
}

export interface Unit { id: string; name: string; kind: string; parent_id: string | null; description: string; sort: number; members: number }
export interface Grant { permission: string; effect: Effect; approver_role_id: string | null }
export interface Role { id: string; key: string; name: string; description: string; color: string; is_system: boolean; sort: number; members: number; grants: Grant[] }
export interface Permission { key: string; group: string; title: string; hint: string; level: string; approvable: boolean; risky: boolean }
export interface Catalog { groups: string[]; permissions: Permission[] }
export interface User {
  id: string; login: string; display_name: string; position: string; email: string | null; is_owner: boolean;
  is_active: boolean; locked: boolean; must_change_password: boolean; last_login_at: string | null; mfa: boolean;
  memberships: { unit_id: string; role_id: string; expires_at?: string | null }[];
}
export interface Decision { effect: Effect; permission: string; reason: string; approver_role_ids: string[] }

export interface ChainRef { provider_id: string; model: string }
export interface Agent {
  id: string; slug: string; name: string; description: string; role: 'orchestrator' | 'lead' | 'specialist';
  unit_id: string | null; reports_to_id: string | null; capabilities: string[]; tools: string[]; model_chain: ChainRef[];
  classification: string; effort: string; max_tokens: number; enabled: boolean; prompt_version: number | null;
  prompt: string | null; updated_at: string; can?: Record<string, Effect>;
}
export interface Tool { key: string; title: string; description: string; permission: string }
export interface PromptVersion { id: string; version: number; body: string; note: string; status: string; created_at: string; published_at: string | null }

export interface Provider {
  id: string; name: string; kind: string; base_url: string; trust_tier: string; default_model: string; has_key: boolean;
  key_hint: string; redact_pii: boolean; enabled: boolean; settings: Record<string, unknown>; notes: string;
}
export interface Preset { id: string; title: string; kind: string; trust: string; base_url: string; model: string; settings: Record<string, unknown>; key: boolean }
export interface Opt { id: string; title: string }
export interface ProvidersPayload { providers: Provider[]; presets: Preset[]; tiers: Opt[]; classifications: Opt[] }

export interface Step {
  id: string; idx: number; agent_id: string | null; agent_name: string; task: string; depends_on: number[]; status: string;
  attempts: number; output: string; error: string; provider: string; model: string; prompt_version: number | null;
  tools_used: string[]; tokens_in: number; tokens_out: number;
}
export interface Run {
  id: string; title: string; request: string; mode: string; status: string; stage: string; classification: string;
  unit_id: string | null; initiator_id: string; approach: string; result: string; error: string; approval_id: string | null;
  preapproved: string[]; created_at: string; started_at: string | null; finished_at: string | null; tokens_in: number;
  tokens_out: number; steps?: Step[]; approval?: { id: string; status: string; reasons: string[]; note: string };
}
export interface RunEvent { id: number; ts: string; type: string; data: Record<string, unknown> }

export interface Approval {
  id: string; kind: string; title: string; summary: string; reasons: string[]; permissions: { key: string; title: string }[];
  payload: { steps?: { agent: string; task: string }[]; body?: string; changes?: Record<string, unknown> };
  target_type: string; target_id: string; requester: string; requester_id: string; status: string;
  decided_by: string | null; decided_at: string | null; decision_note: string; created_at: string;
}
export interface AuditRow {
  seq: number; ts: string; actor: string; actor_id: string | null; ip: string; action: string; target_type: string;
  target: string; outcome: string; data: Record<string, unknown>; hash: string;
}
export interface Overview {
  runs: Record<string, number>; approvals: number; agents: number; providers: number;
  providers_by_tier: Record<string, number>; usage: { day: string; calls: number; tokens: number }[];
}
export interface SettingsPayload {
  values: { egress_policy: Record<string, string>; default_chain: ChainRef[]; planner_chain: ChainRef[];
    default_classification: string; max_parallel_steps: number; company_name: string; user_daily_tokens: number;
    require_2fa: 'none' | 'privileged' | 'all' };
  editable: Record<string, boolean>; tiers: Opt[]; classifications: Opt[];
}

// ── центр безопасности ──
export interface SecEvent { seq: number; ts: string; actor: string; ip: string; action: string; target: string; outcome: string; data: Record<string, unknown> }
export interface SecurityOverview {
  posture: { key: string; title: string; ok: boolean; hint: string }[];
  day: { failed_logins: number; denied: number; alerts: number; logins: number };
  alerts: SecEvent[]; failed: SecEvent[]; changes: SecEvent[]; chain: { seq: number; hash: string };
  channel: { integration_id: string | null; to: string; options: { id: string; name: string; kind: string }[] };
  can_manage: boolean;
}
export interface SecSession { id: string; user_id: string; user: string; login: string; ip: string; user_agent: string; created_at: string; last_seen_at: string; current: boolean; mfa: boolean }
export interface RequestOptions { roles: { id: string; name: string; description: string; color: string }[]; max_days: number }
