export type AppRole = 'user' | 'moderator' | 'admin' | 'owner';
export type StaffRole = Exclude<AppRole, 'user'>;

export const ROLE_LABELS: Record<AppRole, string> = {
  user: 'Пользователь',
  moderator: 'Модератор',
  admin: 'Администратор',
  owner: 'Владелец',
};

export const STAFF_ROLES: StaffRole[] = ['owner', 'admin', 'moderator'];
export const ACCESS_ASSIGN_ROLES: StaffRole[] = ['admin', 'moderator'];

export function isStaffRole(role: string | null | undefined) {
  return role === 'owner' || role === 'admin' || role === 'moderator';
}

export function isPrivilegedRole(role: string | null | undefined) {
  return role === 'owner' || role === 'admin';
}

export function roleLabel(role: string | null | undefined) {
  return ROLE_LABELS[(role || 'user') as AppRole] || 'Пользователь';
}

export function roleBadgeClass(role: string | null | undefined) {
  if (role === 'owner') return 'bg-fuchsia-500/10 border-fuchsia-500/25 text-fuchsia-300';
  if (role === 'admin') return 'bg-amber-500/10 border-amber-500/25 text-amber-400';
  if (role === 'moderator') return 'bg-sky-500/10 border-sky-500/25 text-sky-300';
  return 'bg-slate-900 border-slate-700/50 text-slate-500';
}
