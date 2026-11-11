import { Fragment, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { get, type AuditRow } from '../api';
import { Icon } from '../icons';
import { Card, Empty, Page, errText, useToast, when } from '../ui';

const ACTION: Record<string, string> = {
  'auth.login': 'Вход', 'auth.logout': 'Выход', 'auth.setup': 'Первичная настройка', 'auth.password': 'Смена пароля',
  'iam.user.create': 'Заведён сотрудник', 'iam.user.update': 'Изменён сотрудник', 'iam.user.roles': 'Роли сотрудника',
  'iam.user.password_reset': 'Сброс пароля', 'iam.user.owner': 'Права владельца', 'iam.role.create': 'Создана роль',
  'iam.role.update': 'Изменена роль', 'iam.role.delete': 'Удалена роль', 'iam.role.grants': 'Матрица прав',
  'iam.unit.create': 'Новое подразделение', 'iam.unit.update': 'Изменено подразделение', 'iam.unit.delete': 'Удалено подразделение',
  'agents.create': 'Создан агент', 'agents.update': 'Изменён агент', 'agents.delete': 'Удалён агент', 'agents.import': 'Импорт агента',
  'prompt.draft': 'Черновик промта', 'prompt.published': 'Опубликован промт',
  'providers.create': 'Подключена модель', 'providers.update': 'Изменена модель', 'providers.delete': 'Удалена модель',
  'runs.start': 'Задача поставлена', 'runs.cancel': 'Задача остановлена', 'run.finished': 'Задача завершена',
  'approval.requested': 'Запрос визы', 'approval.approved': 'Виза: одобрено', 'approval.rejected': 'Виза: отклонено',
  'approval.decide': 'Попытка визы', 'settings.update': 'Настройки платформы',
  'auth.password_ok': 'Пароль верен, ждём код', 'auth.2fa_enabled': 'Включён второй фактор',
  'auth.2fa_disabled': 'Отключён второй фактор', 'auth.session_revoked': 'Сессия завершена',
  'iam.user.logout': 'Сессии сотрудника завершены', 'iam.user.2fa_reset': 'Сброс второго фактора',
  'knowledge.create': 'Документ в базу знаний', 'knowledge.delete': 'Документ удалён из базы', 'prompt.try': 'Проверка промта',
  'integrations.create': 'Подключена система', 'integrations.update': 'Изменена интеграция', 'integrations.delete': 'Удалена интеграция',
  'integration.call': 'Вызов внешней системы', 'integration.proposed': 'Агент предложил запись', 'integration.rejected': 'Запись отклонена',
  'integration.inbound': 'Входящее событие', 'protocol.create': 'Протокол загружен', 'protocol.processed': 'Протокол разобран',
  'protocol.task_edit': 'Правка задачи протокола', 'protocol.task_confirm': 'Задача подтверждена', 'protocol.confirm': 'Подтверждение задач',
  'protocol.task_unconfirm': 'Подтверждение снято', 'protocol.task_delete': 'Задача протокола удалена', 'protocol.task_restore': 'Задача восстановлена',
  'protocol.delete': 'Протокол удалён', 'directory.import': 'Справочник из Битрикс24', 'directory.create': 'Запись справочника',
  'directory.update': 'Правка справочника', 'directory.delete': 'Удаление из справочника',
};
const FILTERS = [['', 'Всё'], ['auth', 'Входы'], ['iam', 'Доступы'], ['agents', 'Агенты'], ['prompt', 'Промты'], ['providers', 'Модели'],
  ['run', 'Задачи'], ['approval', 'Визы'], ['knowledge', 'Знания'], ['integration', 'Интеграции'], ['protocol', 'Протоколы'],
  ['access', 'Отказы в доступе'], ['settings', 'Настройки']] as const;

export function Audit() {
  const toast = useToast();
  const [action, setAction] = useState('');
  const [denied, setDenied] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [verify, setVerify] = useState<{ ok: boolean; checked: number; broken_at: number | null } | null>(null);
  const q = useInfiniteQuery({
    queryKey: ['audit', action, denied],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => get<AuditRow[]>(`/api/audit?limit=100&action=${action}&outcome=${denied ? 'denied' : ''}&before=${pageParam}`),
    getNextPageParam: (last) => (last.length === 100 ? last[last.length - 1].seq : undefined),
  });
  const rows = q.data?.pages.flat() ?? [];
  const check = async () => {
    try { setVerify(await get('/api/audit/verify')); } catch (e) { toast(errText(e), 'error'); }
  };
  return (
    <Page title="Журнал аудита" crumbs="Только добавление; каждая запись сцеплена хешем с предыдущей"
      actions={<button className="btn btn--line btn--sm" onClick={check}><Icon name="shield" />Проверить целостность</button>}>
      {verify && (
        <div className={`banner ${verify.ok ? 'banner--ok' : 'banner--danger'}`}>
          <Icon name={verify.ok ? 'check' : 'ban'} size={16} />
          {verify.ok ? `Цепочка цела: проверено ${verify.checked} записей.` : `Цепочка нарушена на записи №${verify.broken_at}: журнал правили в обход платформы.`}
        </div>
      )}
      <div className="row">
        <div className="seg">{FILTERS.map(([k, t]) => <button key={k} className={action === k ? 'active' : ''} onClick={() => setAction(k)}>{t}</button>)}</div>
        <label className="check small"><input type="checkbox" checked={denied} onChange={(e) => setDenied(e.target.checked)} />Только отказы</label>
      </div>
      <Card pad={false}>
        {rows.length ? (
          <table className="table">
            <thead><tr><th>№</th><th>Когда</th><th>Кто</th><th>Что</th><th>Объект</th><th>Итог</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.seq}>
                  <tr className="click" onClick={() => setOpen(open === r.seq ? null : r.seq)}>
                    <td className="mono muted">{r.seq}</td>
                    <td className="small">{when(r.ts)}</td>
                    <td>{r.actor}<div className="muted small mono">{r.ip}</div></td>
                    <td>{ACTION[r.action] ?? r.action}<div className="muted small mono">{r.action}</div></td>
                    <td className="small" style={{ maxWidth: 340 }}>{r.target}</td>
                    <td><span className={`pill ${r.outcome === 'ok' ? 'pill--good' : r.outcome === 'denied' ? 'pill--danger' : 'pill--warn'}`}>{r.outcome === 'ok' ? 'ок' : r.outcome === 'denied' ? 'отказ' : 'сбой'}</span></td>
                  </tr>
                  {open === r.seq && (
                    <tr><td colSpan={6}><pre className="step__out mono" style={{ margin: 0 }}>{JSON.stringify(r.data, null, 2)}{`\n\nхеш: ${r.hash}…`}</pre></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        ) : <Empty title="Событий нет" />}
      </Card>
      {q.hasNextPage && <div className="row"><span className="spacer" /><button className="btn btn--line btn--sm" onClick={() => q.fetchNextPage()}>Ещё</button><span className="spacer" /></div>}
    </Page>
  );
}
