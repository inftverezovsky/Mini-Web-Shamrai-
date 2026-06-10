import React, { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../../utils/api';
import {
  AlertTriangle,
  Check,
  Clock3,
  Crown,
  History,
  Loader2,
  Shield,
  ShieldCheck,
  ShieldOff,
  UserPlus,
  X,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import {
  AppRole,
  StaffRole,
  isPrivilegedRole,
  roleBadgeClass,
  roleLabel,
} from '../../utils/roles';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';

interface StaffUser {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  role: AppRole;
  created_at?: string;
}

interface AuditLog {
  id: number;
  actor_id: number | null;
  actor_username: string | null;
  actor_first_name: string | null;
  target_user_id: number | null;
  target_username: string | null;
  target_first_name: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

interface PendingRoleChange {
  user: StaffUser;
  nextRole: AppRole;
}

const actionLabels: Record<string, string> = {
  role_changed: 'Изменение роли',
  staff_granted: 'Выдача доступа',
  user_updated: 'Обновление CRM',
  user_deleted: 'Удаление клиента',
};

const roleOptions: StaffRole[] = ['owner', 'admin', 'moderator'];

function getDisplayName(user: Pick<StaffUser, 'telegram_id' | 'username' | 'first_name' | 'last_name'>) {
  const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
  return fullName || user.username || `ID ${user.telegram_id}`;
}

function formatAuditDetails(log: AuditLog) {
  const from = typeof log.details.from === 'string' ? roleLabel(log.details.from) : null;
  const to = typeof log.details.to === 'string' ? roleLabel(log.details.to) : null;
  if (from && to) return `${from} -> ${to}`;

  const matches = log.details.matches_remaining as { from?: number; to?: number; delta?: number } | undefined;
  if (matches && typeof matches.delta === 'number') {
    return `Матчи: ${matches.from ?? 0} -> ${matches.to ?? 0}`;
  }

  if (typeof log.details.deleted_user_id === 'number') {
    return `Удален клиент ID ${log.details.deleted_user_id}`;
  }

  const keys = Object.keys(log.details || {});
  return keys.length ? keys.join(', ') : 'без деталей';
}

export default function AdminAccess() {
  const { user: currentAdmin } = useAuth();
  const [admins, setAdmins] = useState<StaffUser[]>([]);
  const [auditLog, setAuditLog] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [telegramId, setTelegramId] = useState('');
  const [grantRole, setGrantRole] = useState<StaffRole>('admin');
  const [pendingRoleChange, setPendingRoleChange] = useState<PendingRoleChange | null>(null);

  const canManageAccess = isPrivilegedRole(currentAdmin?.role);
  const ownerExists = admins.some(admin => admin.role === 'owner');

  const assignableRoles = useMemo(() => {
    if (currentAdmin?.role === 'owner' || !ownerExists) return roleOptions;
    return roleOptions.filter(role => role !== 'owner');
  }, [currentAdmin?.role, ownerExists]);

  const loadAccessData = async () => {
    try {
      setLoading(true);
      const [staffList, logs] = await Promise.all([
        apiFetch('/admin/admins'),
        apiFetch('/admin/audit-log?limit=60'),
      ]);
      setAdmins(staffList);
      setAuditLog(logs);
    } catch (err: any) {
      notifyError(err.message || 'Ошибка загрузки доступа');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAccessData();
  }, []);

  const handleGrant = async () => {
    const cleanId = Number(telegramId.trim());
    if (!Number.isInteger(cleanId) || cleanId <= 0) {
      notifyError('Введите корректный Telegram ID');
      return;
    }
    if (!canManageAccess) {
      notifyError('Недостаточно прав для управления доступом');
      return;
    }
    const confirmed = await confirmDestructive({
      title: 'Выдать доступ',
      message: `Назначить роль "${roleLabel(grantRole)}" пользователю ${cleanId}?`,
      confirmLabel: 'Выдать',
      tone: 'warning',
    });
    if (!confirmed) {
      return;
    }

    try {
      setSaving(true);
      await apiFetch('/admin/admins/grant', {
        method: 'POST',
        body: JSON.stringify({ telegram_id: cleanId, role: grantRole }),
      });
      setTelegramId('');
      await loadAccessData();
      notifySuccess('Доступ обновлен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось выдать доступ');
    } finally {
      setSaving(false);
    }
  };

  const commitRoleChange = async () => {
    if (!pendingRoleChange) return;
    try {
      setSaving(true);
      await apiFetch(`/admin/users/${pendingRoleChange.user.telegram_id}`, {
        method: 'PUT',
        body: JSON.stringify({ role: pendingRoleChange.nextRole }),
      });
      setPendingRoleChange(null);
      await loadAccessData();
      notifySuccess('Роль пользователя обновлена');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось изменить роль');
    } finally {
      setSaving(false);
    }
  };

  const requestRoleChange = (user: StaffUser, nextRole: AppRole) => {
    if (!canManageAccess) {
      notifyError('Недостаточно прав для управления доступом');
      return;
    }
    if (user.telegram_id === currentAdmin?.telegram_id && user.role !== nextRole) {
      notifyError('Нельзя менять собственную роль из интерфейса');
      return;
    }
    setPendingRoleChange({ user, nextRole });
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-lg space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-xs font-black text-white uppercase tracking-wider flex items-center">
              <UserPlus className="w-4 h-4 text-emerald-400 mr-1.5 shrink-0" />
              Администраторы
            </h3>
            <p className="text-[10px] text-slate-500 mt-1 leading-normal">
              Выдача доступа по Telegram ID и смена роли команды.
            </p>
          </div>
          <span className={`border text-[8px] font-black uppercase tracking-wider rounded-lg px-2 py-1 shrink-0 ${roleBadgeClass(currentAdmin?.role)}`}>
            {roleLabel(currentAdmin?.role)}
          </span>
        </div>

        <div className="space-y-2">
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <input
              type="number"
              value={telegramId}
              onChange={event => setTelegramId(event.target.value)}
              placeholder="Telegram ID"
              disabled={!canManageAccess}
              className="min-w-0 bg-slate-900/60 border border-slate-700/60 focus:border-emerald-500/50 disabled:opacity-55 rounded-xl px-3 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none transition-all font-semibold"
            />
            <button
              onClick={handleGrant}
              disabled={saving || !telegramId.trim() || !canManageAccess}
              className="bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 text-[10px] font-black px-3 py-2.5 rounded-xl flex items-center justify-center gap-1.5 transition-all shadow-neon-green"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
              <span>Добавить</span>
            </button>
          </div>

          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {assignableRoles.map(role => (
              <button
                key={role}
                onClick={() => setGrantRole(role)}
                disabled={!canManageAccess}
                className={`shrink-0 border px-3 py-1.5 rounded-xl text-[9px] font-black uppercase tracking-wider transition-all disabled:opacity-45 ${
                  grantRole === role
                    ? roleBadgeClass(role)
                    : 'bg-slate-900/50 border-slate-700/50 text-slate-400 hover:text-white'
                }`}
              >
                {roleLabel(role)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-3">
        {admins.map(admin => (
          <div
            key={admin.telegram_id}
            className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-lg space-y-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h4 className="font-extrabold text-white text-xs leading-snug truncate">
                  {getDisplayName(admin)}
                </h4>
                <p className="text-[10px] text-slate-400 font-bold truncate mt-0.5">
                  {admin.username ? `@${admin.username}` : 'без юзернейма'} • ID: {admin.telegram_id}
                </p>
              </div>
              <span className={`border text-[8px] font-black uppercase tracking-wider rounded-lg px-2 py-1 shrink-0 flex items-center gap-1 ${roleBadgeClass(admin.role)}`}>
                {admin.role === 'owner' ? <Crown className="w-3 h-3" /> : <ShieldCheck className="w-3 h-3" />}
                {roleLabel(admin.role)}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-1.5">
              {roleOptions.map(role => {
                const disabled =
                  saving ||
                  !canManageAccess ||
                  admin.role === role ||
                  (role === 'owner' && currentAdmin?.role !== 'owner' && ownerExists);
                return (
                  <button
                    key={role}
                    onClick={() => requestRoleChange(admin, role)}
                    disabled={disabled}
                    className={`min-h-[34px] border rounded-xl text-[8.5px] font-black uppercase tracking-wider flex items-center justify-center gap-1 transition-all disabled:opacity-35 ${
                      admin.role === role
                        ? roleBadgeClass(role)
                        : 'bg-slate-900/50 border-slate-700/50 text-slate-400 hover:text-white'
                    }`}
                  >
                    <Shield className="w-3.5 h-3.5" />
                    {roleLabel(role)}
                  </button>
                );
              })}
              <button
                onClick={() => requestRoleChange(admin, 'user')}
                disabled={saving || !canManageAccess || admin.telegram_id === currentAdmin?.telegram_id}
                className="min-h-[34px] border border-rose-500/25 bg-rose-500/10 text-rose-400 rounded-xl text-[8.5px] font-black uppercase tracking-wider flex items-center justify-center gap-1 transition-all hover:bg-rose-500 hover:text-white disabled:opacity-35"
              >
                <ShieldOff className="w-3.5 h-3.5" />
                Снять
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-lg space-y-3">
        <h3 className="text-xs font-black text-white uppercase tracking-wider flex items-center">
          <History className="w-4 h-4 text-sky-300 mr-1.5 shrink-0" />
          Журнал действий
        </h3>
        {auditLog.length === 0 ? (
          <div className="bg-slate-900/50 border border-slate-800 text-slate-500 rounded-xl p-4 text-center text-xs">
            Записей пока нет.
          </div>
        ) : (
          <div className="space-y-2.5 max-h-[340px] overflow-y-auto pr-1">
            {auditLog.map(log => (
              <div key={log.id} className="bg-slate-900/50 border border-slate-800 rounded-xl p-3 text-xs space-y-1.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-white font-black truncate">
                      {actionLabels[log.action] || log.action}
                    </div>
                    <div className="text-[10px] text-slate-500 font-bold truncate">
                      {log.actor_username ? `@${log.actor_username}` : log.actor_first_name || `ID ${log.actor_id || '-'}`}
                      {' -> '}
                      {log.target_username ? `@${log.target_username}` : log.target_first_name || `ID ${log.target_user_id || '-'}`}
                    </div>
                  </div>
                  <span className="text-[9px] text-slate-500 font-bold shrink-0 flex items-center gap-1">
                    <Clock3 className="w-3 h-3" />
                    {new Date(log.created_at).toLocaleDateString('ru-RU')}
                  </span>
                </div>
                <div className="text-[10px] text-slate-400 font-semibold">
                  {formatAuditDetails(log)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {pendingRoleChange && (
        <div className="fixed inset-0 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="bg-[#0C1226]/95 border border-white/10 max-w-sm w-full p-5 rounded-3xl space-y-4 shadow-2xl animate-scale-up">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-2xl bg-amber-500/10 border border-amber-500/25 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-5 h-5 text-amber-400" />
              </div>
              <div className="min-w-0">
                <h3 className="text-white text-sm font-black uppercase tracking-wider">
                  Подтвердить доступ
                </h3>
                <p className="text-[11px] text-slate-400 leading-normal mt-1">
                  {getDisplayName(pendingRoleChange.user)}: {roleLabel(pendingRoleChange.user.role)} &gt; {roleLabel(pendingRoleChange.nextRole)}
                </p>
              </div>
              <button
                onClick={() => setPendingRoleChange(null)}
                className="ml-auto text-slate-500 hover:text-white transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setPendingRoleChange(null)}
                className="bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 py-2.5 rounded-2xl text-[10px] font-black uppercase tracking-wider transition-all"
              >
                Отмена
              </button>
              <button
                onClick={commitRoleChange}
                disabled={saving}
                className="bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-slate-950 py-2.5 rounded-2xl text-[10px] font-black uppercase tracking-wider transition-all flex items-center justify-center gap-1.5"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Применить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
