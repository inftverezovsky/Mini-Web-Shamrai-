import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { apiFetch } from '../../utils/api';
import { BookmakerResponse, PaginatedResponse } from '../../schemas/schemas';
import { isOtherBookmaker } from '../../constants/bookmakers';
import EmojiTextField from '../../components/EmojiTextField';
import { BookmakerLogoFrame } from '../../components/LogoFrame';
import SmoothCollapse from '../../components/SmoothCollapse';
import { useAuth } from '../../context/AuthContext';
import { isPrivilegedRole } from '../../utils/roles';
import {
  AlertTriangle,
  BadgeCheck,
  Calendar,
  CheckSquare,
  ChevronDown,
  Clock,
  Filter,
  Layers3,
  Loader2,
  Save,
  Search,
  ShieldCheck,
  Sliders,
  Tags,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';

type StatsDisplayMode = 'percent' | 'flat';
type ActivityFilter = 'all' | 'active' | 'empty' | 'guarantee';

interface CRMUser {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  is_web_only?: boolean;
  role: string;
  stats_display_mode: string;
  has_active_subscription: boolean;
  subscription_end_date: string | null;
  purchased_bets_balance?: number;
  matches_remaining: number;
  guarantee_active: boolean;
  guarantee_opened_from_bet_id: string | null;
  guarantee_closed_at: string | null;
  bookmakers: BookmakerResponse[];
  other_bookmaker_name: string | null;
  client_group: string | null;
  client_tag: string | null;
  ab_group: string | null;
  tg_chat_joined: boolean;
  badges: Array<{ id: number; title: string; icon_type: string }>;
  recent_match_results: ClientRecentMatchResult[];
}

interface ClientRecentMatchResult {
  bet_id: string;
  status: 'win' | 'loss';
  taken_at: string | null;
}

const filterLabels: Record<ActivityFilter, string> = {
  all: 'Все',
  active: 'Активные',
  empty: 'Без матчей',
  guarantee: 'Гарантия',
};

function getDisplayName(user: CRMUser) {
  const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
  return fullName || user.username || (user.is_web_only ? 'Web/VK клиент' : `ID ${user.telegram_id}`);
}

function getClientIdLabel(user: Pick<CRMUser, 'telegram_id' | 'is_web_only'>) {
  return user.is_web_only ? 'Web/VK клиент' : `ID: ${user.telegram_id}`;
}

function cleanText(value: string) {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function getMatchBalance(user: Pick<CRMUser, 'purchased_bets_balance' | 'matches_remaining'>) {
  return user.purchased_bets_balance !== undefined && user.purchased_bets_balance !== 0
    ? user.purchased_bets_balance
    : user.matches_remaining || 0;
}

function pluralRu(value: number, one: string, few: string, many: string) {
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debouncedValue;
}

function getMatchStreak(results: ClientRecentMatchResult[]) {
  const currentStatus = results[0]?.status;
  if (!currentStatus) return null;

  let count = 0;
  for (const result of results) {
    if (result.status !== currentStatus) break;
    count += 1;
  }

  const noun = currentStatus === 'win'
    ? pluralRu(count, 'победа', 'победы', 'побед')
    : pluralRu(count, 'поражение', 'поражения', 'поражений');

  return {
    count,
    status: currentStatus,
    label: `${count} ${noun} подряд`,
  };
}

export default function AdminCRM() {
  const { user: currentAdmin } = useAuth();
  const [searchTerm, setSearchTerm] = useState('');
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>('all');
  const [groupFilter, setGroupFilter] = useState('all');
  const [tagFilter, setTagFilter] = useState('all');
  const debouncedSearchTerm = useDebouncedValue(searchTerm, 250);

  const [selectedUser, setSelectedUser] = useState<CRMUser | null>(null);
  const [editStatsMode, setEditStatsMode] = useState<StatsDisplayMode>('percent');
  const [editBkIds, setEditBkIds] = useState<number[]>([]);
  const [editBookmakersOpen, setEditBookmakersOpen] = useState(false);
  const [editOtherBookmakerName, setEditOtherBookmakerName] = useState('');
  const [editClientGroup, setEditClientGroup] = useState('');
  const [editClientTag, setEditClientTag] = useState('');
  const [matchDelta, setMatchDelta] = useState('');
  const [saving, setSaving] = useState(false);
  const canDeleteClients = isPrivilegedRole(currentAdmin?.role);

  const bookmakersQuery = useQuery<BookmakerResponse[]>({
    queryKey: ['bookmakers'],
    queryFn: ({ signal }) => apiFetch<BookmakerResponse[]>('/bookmakers', { signal }),
    staleTime: 5 * 60_000,
  });

  const usersQuery = useInfiniteQuery<PaginatedResponse<CRMUser>, Error>({
    queryKey: ['admin-users-page', debouncedSearchTerm, activityFilter, groupFilter, tagFilter],
    initialPageParam: null as string | null,
    enabled: Boolean(currentAdmin),
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: '50' });
      if (pageParam) params.set('cursor', String(pageParam));
      if (debouncedSearchTerm.trim()) params.set('q', debouncedSearchTerm.trim());
      if (activityFilter !== 'all') params.set('activity', activityFilter);
      if (groupFilter !== 'all') params.set('group', groupFilter);
      if (tagFilter !== 'all') params.set('tag', tagFilter);
      return apiFetch<PaginatedResponse<CRMUser>>(`/admin/users-page?${params.toString()}`, { signal });
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_cursor : undefined),
    staleTime: 20_000,
  });

  const users = useMemo(() => {
    const byId = new Map<number, CRMUser>();
    usersQuery.data?.pages.forEach((page) => {
      page.items.forEach((user) => {
        if (user.role === 'user') byId.set(user.telegram_id, user);
      });
    });
    return Array.from(byId.values());
  }, [usersQuery.data]);
  const bookmakers = useMemo(() => bookmakersQuery.data ?? [], [bookmakersQuery.data]);
  const loading = usersQuery.isLoading || bookmakersQuery.isLoading;
  const paginationMeta = {
    filteredTotal: usersQuery.data?.pages[0]?.filtered_total ?? users.length,
    hasMore: Boolean(usersQuery.hasNextPage),
  };

  const loadCRM = useCallback(async () => {
    await Promise.all([usersQuery.refetch(), bookmakersQuery.refetch()]);
  }, [bookmakersQuery, usersQuery]);

  const groups = useMemo(() => (
    Array.from(new Set(users.map(user => user.client_group?.trim()).filter(Boolean) as string[])).sort()
  ), [users]);

  const tags = useMemo(() => (
    Array.from(new Set(users.map(user => user.client_tag?.trim()).filter(Boolean) as string[])).sort()
  ), [users]);

  const filteredUsers = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();

    return users.filter(user => {
      const searchable = [
        user.first_name,
        user.last_name,
        user.username,
        user.telegram_id,
        getMatchBalance(user),
        user.client_group,
        user.client_tag,
        user.other_bookmaker_name,
      ].join(' ').toLowerCase();

      const matchesSearch = !normalizedSearch || searchable.includes(normalizedSearch);
      const matchesActivity =
        activityFilter === 'all' ||
        (activityFilter === 'active' && user.has_active_subscription) ||
        (activityFilter === 'empty' && !user.guarantee_active && getMatchBalance(user) <= 0) ||
        (activityFilter === 'guarantee' && user.guarantee_active);
      const matchesGroup = groupFilter === 'all' || user.client_group === groupFilter;
      const matchesTag = tagFilter === 'all' || user.client_tag === tagFilter;

      return matchesSearch && matchesActivity && matchesGroup && matchesTag;
    });
  }, [activityFilter, groupFilter, searchTerm, tagFilter, users]);

  const summary = useMemo(() => {
    const active = users.filter(user => user.has_active_subscription).length;
    const empty = users.filter(user => !user.guarantee_active && getMatchBalance(user) <= 0).length;
    const guarantee = users.filter(user => user.guarantee_active).length;
    return { active, empty, guarantee };
  }, [users]);

  const otherBookmakerSelected = useMemo(() => (
    bookmakers.some(bookmaker => isOtherBookmaker(bookmaker) && editBkIds.includes(bookmaker.id))
  ), [bookmakers, editBkIds]);
  const selectedEditBookmakerNames = useMemo(() => (
    bookmakers
      .filter(bookmaker => editBkIds.includes(bookmaker.id))
      .map(bookmaker => bookmaker.name)
  ), [bookmakers, editBkIds]);

  const openEditModal = (user: CRMUser) => {
    setSelectedUser(user);
    setEditStatsMode(user.stats_display_mode === 'flat' ? 'flat' : 'percent');
    setEditBkIds(user.bookmakers.map(bookmaker => bookmaker.id));
    setEditBookmakersOpen(false);
    setEditOtherBookmakerName(user.other_bookmaker_name || '');
    setEditClientGroup(user.client_group || '');
    setEditClientTag(user.client_tag || '');
    setMatchDelta('');
  };

  const closeEditModal = () => {
    if (saving) return;
    setSelectedUser(null);
  };

  const handleToggleBk = (bkId: number) => {
    const bookmaker = bookmakers.find(item => item.id === bkId);
    setEditBkIds(prev => {
      const next = prev.includes(bkId)
        ? prev.filter(id => id !== bkId)
        : [...prev, bkId];
      if (bookmaker && isOtherBookmaker(bookmaker) && !next.includes(bkId)) {
        setEditOtherBookmakerName('');
      }
      return next;
    });
  };

  const parseMatchDelta = () => {
    const trimmed = matchDelta.trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    if (!Number.isInteger(parsed)) {
      throw new Error('Введите корректное целое число матчей');
    }
    return parsed;
  };

  const buildPayload = (closeGuarantee = false) => ({
    stats_display_mode: editStatsMode,
    bookmaker_ids: editBkIds,
    matches_delta: parseMatchDelta(),
    close_guarantee: closeGuarantee,
    client_group: cleanText(editClientGroup),
    client_tag: cleanText(editClientTag),
    other_bookmaker_name: otherBookmakerSelected ? cleanText(editOtherBookmakerName) : null,
  });

  const handleSave = async () => {
    if (!selectedUser) return;

    let payload: ReturnType<typeof buildPayload>;
    try {
      payload = buildPayload(false);
    } catch (err: any) {
      notifyError(err.message || 'Проверьте количество матчей');
      return;
    }

    if (payload.matches_delta && payload.matches_delta < 0) {
      const confirmed = await confirmDestructive({
        title: 'Уменьшить баланс',
        message: `Уменьшить баланс клиента на ${Math.abs(payload.matches_delta)} матчей?`,
        confirmLabel: 'Уменьшить',
      });
      if (!confirmed) return;
    }

    try {
      setSaving(true);
      await apiFetch(`/admin/users/${selectedUser.telegram_id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      notifySuccess('Карточка клиента обновлена');
      setSelectedUser(null);
      await loadCRM();
    } catch (err: any) {
      notifyError(err.message || 'Ошибка обновления клиента');
    } finally {
      setSaving(false);
    }
  };

  const handleRevokeSub = async () => {
    if (!selectedUser) return;
    const currentBalance = getMatchBalance(selectedUser);
    const confirmed = await confirmDestructive({
      title: currentBalance < 0 ? 'Обнулить долг' : 'Обнулить матчи',
      message: currentBalance < 0
        ? `Обнулить отрицательный баланс клиента ${currentBalance} до 0?`
        : 'Обнулить остаток матчей у клиента?',
      confirmLabel: 'Обнулить',
      tone: currentBalance < 0 ? 'warning' : 'danger',
    });
    if (!confirmed) return;
    setMatchDelta(String(-currentBalance));
  };

  const handleCloseGuarantee = async () => {
    if (!selectedUser) return;
    const confirmed = await confirmDestructive({
      title: 'Закрыть гарантию',
      message: 'Закрыть гарантию вручную?',
      confirmLabel: 'Закрыть',
      tone: 'warning',
    });
    if (!confirmed) return;

    let payload: ReturnType<typeof buildPayload>;
    try {
      payload = buildPayload(true);
    } catch (err: any) {
      notifyError(err.message || 'Проверьте количество матчей');
      return;
    }

    try {
      setSaving(true);
      await apiFetch(`/admin/users/${selectedUser.telegram_id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      notifySuccess('Гарантия закрыта вручную');
      setSelectedUser(null);
      await loadCRM();
    } catch (err: any) {
      notifyError(err.message || 'Ошибка закрытия гарантии');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteUser = async () => {
    if (!selectedUser || !canDeleteClients) return;

    const confirmed = await confirmDestructive({
      title: 'Удалить клиента',
      message: `Удалить ${getDisplayName(selectedUser)} из базы? Заявки, оплаты, баланс и CRM-метки клиента будут удалены.`,
      confirmLabel: 'Удалить',
    });
    if (!confirmed) return;

    const deletedUserId = selectedUser.telegram_id;
    try {
      setSaving(true);
      await apiFetch(`/admin/users/${deletedUserId}`, {
        method: 'DELETE',
      });
      notifySuccess('Клиент удален из базы');
      setSelectedUser(null);
      await loadCRM();
    } catch (err: any) {
      notifyError(err.message || 'Ошибка удаления клиента');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
      </div>
    );
  }

  if (usersQuery.isError || bookmakersQuery.isError) {
    const message = usersQuery.error?.message || (bookmakersQuery.error as Error | null)?.message || 'Ошибка загрузки клиентов';
    return (
      <div className="space-y-3 rounded-2xl border border-rose-500/25 bg-rose-500/10 p-4 text-center text-xs text-rose-100">
        <AlertTriangle className="mx-auto h-6 w-6 text-rose-300" />
        <p className="font-bold">{message}</p>
        <button
          type="button"
          onClick={() => void loadCRM()}
          className="rounded-xl border border-rose-300/30 bg-rose-300/10 px-3 py-2 font-black uppercase tracking-wider text-rose-50"
        >
          Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-slide-up pb-10">
      <datalist id="client-group-options">
        {groups.map(group => <option key={group} value={group} />)}
      </datalist>
      <datalist id="client-tag-options">
        {tags.map(tag => <option key={tag} value={tag} />)}
      </datalist>

      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-black text-white flex items-center uppercase tracking-wider">
            <Users className="w-5 h-5 text-cyan-300 mr-2" />
            Клиенты
          </h2>
        </div>
        <div className="bg-cyan-500/10 border border-cyan-500/25 text-cyan-300 text-xs px-3 py-1 rounded-full font-black">
          {filteredUsers.length}/{paginationMeta.filteredTotal || users.length}
        </div>
      </div>
      {paginationMeta.hasMore && (
        <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-cyan-100">
          Показаны первые {users.length} клиентов. Уточните поиск или фильтр, чтобы сузить список.
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div className="bg-white/5 border border-white/10 rounded-2xl p-3">
          <div className="text-[8px] text-slate-500 font-black uppercase tracking-wider">Активные</div>
          <div className="mt-1 text-lg font-black text-emerald-400">{summary.active}</div>
        </div>
        <div className="bg-white/5 border border-white/10 rounded-2xl p-3">
          <div className="text-[8px] text-slate-500 font-black uppercase tracking-wider">Без матчей</div>
          <div className="mt-1 text-lg font-black text-slate-300">{summary.empty}</div>
        </div>
        <div className="bg-white/5 border border-white/10 rounded-2xl p-3">
          <div className="text-[8px] text-slate-500 font-black uppercase tracking-wider">Гарантия</div>
          <div className="mt-1 text-lg font-black text-amber-300">{summary.guarantee}</div>
        </div>
      </div>

      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">
          <Search className="w-4 h-4" />
        </span>
        <input
          type="text"
          value={searchTerm}
          onChange={event => setSearchTerm(event.target.value)}
          placeholder="Поиск по имени, username, ID, группе или метке..."
          className="w-full bg-slate-900/60 border border-slate-700/60 focus:border-cyan-400/50 rounded-xl pl-9 pr-3 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none transition-all font-semibold"
        />
      </div>

      <div className="space-y-2">
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {(Object.keys(filterLabels) as ActivityFilter[]).map(filter => (
            <button
              key={filter}
              type="button"
              onClick={() => setActivityFilter(filter)}
              className={`shrink-0 border px-3 py-1.5 rounded-xl text-[9px] font-black uppercase tracking-wider transition-all ${
                activityFilter === filter
                  ? 'bg-cyan-500 text-slate-950 border-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.26)]'
                  : 'bg-slate-900/50 border-slate-700/50 text-slate-400 hover:text-white'
              }`}
            >
              {filterLabels[filter]}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <label className="relative">
            <Layers3 className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
            <select
              value={groupFilter}
              onChange={event => setGroupFilter(event.target.value)}
              className="w-full appearance-none bg-slate-900/60 border border-slate-700/60 focus:border-cyan-400/50 rounded-xl pl-8 pr-3 py-2.5 text-[10px] text-white focus:outline-none font-bold"
            >
              <option value="all">Все группы</option>
              {groups.map(group => <option key={group} value={group}>{group}</option>)}
            </select>
          </label>

          <label className="relative">
            <Filter className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
            <select
              value={tagFilter}
              onChange={event => setTagFilter(event.target.value)}
              className="w-full appearance-none bg-slate-900/60 border border-slate-700/60 focus:border-cyan-400/50 rounded-xl pl-8 pr-3 py-2.5 text-[10px] text-white focus:outline-none font-bold"
            >
              <option value="all">Все метки</option>
              {tags.map(tag => <option key={tag} value={tag}>{tag}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="space-y-3">
        {filteredUsers.length === 0 ? (
          <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-6 text-center text-slate-500 text-xs rounded-2xl">
            Клиенты не найдены.
          </div>
        ) : (
          filteredUsers.map(user => {
            const recentResults = user.recent_match_results || [];
            const streak = getMatchStreak(recentResults);
            const chronologicalResults = [...recentResults].reverse();
            const matchBalance = getMatchBalance(user);
            const hasMatchDebt = matchBalance < 0;

            return (
              <button
                key={user.telegram_id}
                type="button"
                onClick={() => openEditModal(user)}
                className="w-full bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-lg hover:border-cyan-400/35 active:scale-[0.99] transition-all text-left text-xs"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <h4 className="font-extrabold text-white leading-snug truncate">
                      {getDisplayName(user)}
                    </h4>
                    <p className="text-[10px] text-slate-400 font-bold truncate">
                      {user.username ? `@${user.username}` : 'без юзернейма'} • {getClientIdLabel(user)}
                    </p>
                  </div>

                  {hasMatchDebt ? (
                    <span className="shrink-0 bg-rose-500/10 border border-rose-500/30 text-rose-300 text-[9px] font-black px-2 py-1 rounded-lg flex items-center tracking-wider uppercase">
                      <AlertTriangle className="w-3.5 h-3.5 mr-0.5" /> {matchBalance} матч.
                    </span>
                  ) : user.has_active_subscription ? (
                    <span className="shrink-0 bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-[9px] font-black px-2 py-1 rounded-lg flex items-center tracking-wider uppercase">
                      <ShieldCheck className="w-3.5 h-3.5 mr-0.5" /> {matchBalance} матч.
                    </span>
                  ) : (
                    <span className="shrink-0 bg-slate-900 border border-slate-700/50 text-slate-500 text-[9px] font-black px-2 py-1 rounded-lg flex items-center tracking-wider uppercase">
                      <Clock className="w-3.5 h-3.5 mr-0.5" /> Демо
                    </span>
                  )}
                </div>

                <div className="mt-3 bg-slate-950/35 border border-white/10 rounded-xl px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-[9px] font-black uppercase tracking-wider truncate ${
                      streak?.status === 'win'
                        ? 'text-emerald-300'
                        : streak?.status === 'loss'
                          ? 'text-rose-300'
                          : 'text-slate-500'
                    }`}>
                      {streak ? streak.label : 'Истории матчей нет'}
                    </span>
                    <span className="shrink-0 text-[8px] text-slate-600 font-black uppercase tracking-wider">
                      10 посл.
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-1.5">
                    {Array.from({ length: 10 }).map((_, index) => {
                      const result = chronologicalResults[index];
                      return (
                        <span
                          key={`${user.telegram_id}-result-${index}`}
                          className={`h-2.5 w-2.5 rounded-full border transition-all ${
                            result?.status === 'win'
                              ? 'bg-emerald-400 border-emerald-300 shadow-[0_0_10px_rgba(52,211,153,0.45)]'
                              : result?.status === 'loss'
                                ? 'bg-rose-500 border-rose-300 shadow-[0_0_10px_rgba(244,63,94,0.35)]'
                                : 'bg-slate-800/80 border-slate-700/70'
                          }`}
                          aria-label={
                            result?.status === 'win'
                              ? 'Победа'
                              : result?.status === 'loss'
                                ? 'Поражение'
                                : 'Нет матча'
                          }
                        />
                      );
                    })}
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="bg-cyan-500/10 border border-cyan-500/20 text-cyan-300 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                    {user.client_group || 'Без группы'}
                  </span>
                  <span className="bg-fuchsia-500/10 border border-fuchsia-500/20 text-fuchsia-300 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                    {user.client_tag || 'Без метки'}
                  </span>
                  <span className="bg-white/5 border border-white/10 text-slate-400 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                    БК: {user.bookmakers.length}
                  </span>
                  {user.guarantee_active && (
                    <span className="bg-amber-500/10 border border-amber-500/25 text-amber-300 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                      Гарантия
                    </span>
                  )}
                  <span className="bg-white/5 border border-white/10 text-slate-500 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                    A/B: {user.ab_group || 'A'}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>

      {usersQuery.hasNextPage && (
        <button
          type="button"
          onClick={() => void usersQuery.fetchNextPage()}
          disabled={usersQuery.isFetchingNextPage}
          className="mx-auto flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-300/10 px-4 text-xs font-black text-cyan-100 transition-all hover:bg-cyan-300/15 disabled:opacity-50"
        >
          {usersQuery.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : <Users className="h-4 w-4" />}
          <span>{usersQuery.isFetchingNextPage ? 'Загружаем...' : 'Загрузить еще клиентов'}</span>
        </button>
      )}

      {selectedUser && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="bg-[#0C1226]/95 border border-white/10 max-w-sm w-full max-h-[88vh] overflow-y-auto p-6 rounded-3xl space-y-5 relative shadow-2xl animate-scale-up">
            <button
              type="button"
              onClick={closeEditModal}
              disabled={saving}
              className="absolute top-4 right-4 text-slate-450 hover:text-white disabled:opacity-40 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="space-y-0.5 text-center pr-6">
              <h3 className="text-white text-base font-black truncate">Карточка клиента</h3>
              <p className="text-slate-450 text-[10px] uppercase font-bold truncate">
                {getDisplayName(selectedUser)} • {selectedUser.username ? `@${selectedUser.username}` : getClientIdLabel(selectedUser)}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1.5">
                <span className="text-[9px] text-slate-500 font-black uppercase tracking-wider flex items-center">
                  <Layers3 className="w-3.5 h-3.5 text-cyan-300 mr-1.5" />
                  Группа
                </span>
                <EmojiTextField
                  type="text"
                  list="client-group-options"
                  value={editClientGroup}
                  onValueChange={setEditClientGroup}
                  placeholder="VIP, новые..."
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-400/50 text-xs font-bold"
                />
              </label>

              <label className="space-y-1.5">
                <span className="text-[9px] text-slate-500 font-black uppercase tracking-wider flex items-center">
                  <Tags className="w-3.5 h-3.5 text-fuchsia-300 mr-1.5" />
                  Метка
                </span>
                <EmojiTextField
                  type="text"
                  list="client-tag-options"
                  value={editClientTag}
                  onValueChange={setEditClientTag}
                  placeholder="топ, важный..."
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-600 focus:outline-none focus:border-fuchsia-400/50 text-xs font-bold"
                />
              </label>
            </div>

            <div className="space-y-2 text-xs">
              <h4 className="font-extrabold flex items-center uppercase tracking-wider text-[9px] text-slate-400">
                <Calendar className="w-3.5 h-3.5 text-indigo-400 mr-1.5 shrink-0" />
                Абонемент по матчам
              </h4>
              <div className={`rounded-xl border px-3 py-2 flex items-center justify-between gap-2 ${
                getMatchBalance(selectedUser) < 0
                  ? 'bg-rose-500/10 border-rose-500/25 text-rose-200'
                  : getMatchBalance(selectedUser) > 0
                    ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-200'
                    : 'bg-slate-900/70 border-slate-700/70 text-slate-300'
              }`}>
                <span className="text-[9px] font-black uppercase tracking-wider">
                  Текущий баланс
                </span>
                <span className="text-xs font-black">
                  {getMatchBalance(selectedUser)} матч.
                </span>
              </div>
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <input
                  type="number"
                  value={matchDelta}
                  onChange={event => setMatchDelta(event.target.value)}
                  placeholder={`Изменить баланс: сейчас ${getMatchBalance(selectedUser)}`}
                  className="min-w-0 bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white focus:outline-none focus:border-indigo-400/50 text-xs font-bold"
                />
                <button
                  type="button"
                  onClick={handleRevokeSub}
                  className="bg-rose-500/10 border border-rose-500/20 text-rose-400 hover:bg-rose-500 hover:text-white px-3 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all"
                >
                  Обнулить
                </button>
              </div>
              <div className="flex space-x-1.5 pt-1">
                <button
                  type="button"
                  onClick={() => setMatchDelta('5')}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-300 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
                >
                  +5 матчей
                </button>
                <button
                  type="button"
                  onClick={() => setMatchDelta('10')}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-300 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
                >
                  +10 матчей
                </button>
                <button
                  type="button"
                  onClick={() => setMatchDelta('')}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-400 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
                >
                  Сбросить
                </button>
              </div>
              {selectedUser.guarantee_active && (
                <button
                  type="button"
                  onClick={handleCloseGuarantee}
                  disabled={saving}
                  className="w-full bg-amber-500/10 border border-amber-500/25 text-amber-400 hover:bg-amber-500 hover:text-slate-950 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all disabled:opacity-50"
                >
                  Закрыть гарантию победой
                </button>
              )}
            </div>

            <div className="space-y-2 text-xs">
              <h4 className="font-extrabold flex items-center uppercase tracking-wider text-[9px] text-slate-400">
                <Sliders className="w-3.5 h-3.5 text-indigo-400 mr-1.5 shrink-0" />
                Режим отображения статистики
              </h4>
              <div className="flex bg-slate-900 border border-slate-700 p-0.5 rounded-xl">
                <button
                  type="button"
                  onClick={() => setEditStatsMode('percent')}
                  className={`flex-1 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-wider transition-all ${
                    editStatsMode === 'percent' ? 'bg-indigo-500 text-white shadow-neon-indigo' : 'text-slate-500'
                  }`}
                >
                  Проценты (%)
                </button>
                <button
                  type="button"
                  onClick={() => setEditStatsMode('flat')}
                  className={`flex-1 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-wider transition-all ${
                    editStatsMode === 'flat' ? 'bg-indigo-500 text-white shadow-neon-indigo' : 'text-slate-500'
                  }`}
                >
                  Флэт
                </button>
              </div>
            </div>

            <div className="space-y-2 text-xs">
              <button
                type="button"
                onClick={() => setEditBookmakersOpen((current) => !current)}
                aria-expanded={editBookmakersOpen}
                className="flex w-full items-center justify-between gap-3 rounded-2xl border border-slate-700/80 bg-slate-900/55 px-3 py-2.5 text-left transition-all hover:border-indigo-400/45 hover:bg-slate-900"
              >
                <span className="min-w-0">
                  <span className="flex items-center font-extrabold uppercase tracking-wider text-[9px] text-slate-400">
                    <CheckSquare className="w-3.5 h-3.5 text-indigo-400 mr-1.5 shrink-0" />
                    Букмекерские конторы
                  </span>
                  <span className="mt-1 block truncate text-[10px] font-semibold text-slate-300">
                    {selectedEditBookmakerNames.length > 0
                      ? selectedEditBookmakerNames.join(', ')
                      : 'БК не выбраны'}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="rounded-xl border border-white/10 bg-slate-950/55 px-2.5 py-1.5 text-right">
                    <span className="block text-[8px] uppercase font-bold tracking-wider text-slate-500">Выбрано</span>
                    <span className="block text-[11px] font-black text-white">{editBkIds.length}</span>
                  </span>
                  <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-indigo-200">
                    <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${editBookmakersOpen ? 'rotate-180' : ''}`} />
                  </span>
                </span>
              </button>

              <SmoothCollapse open={editBookmakersOpen}>
                <div className="grid grid-cols-2 gap-1.5 max-h-[150px] overflow-y-auto pr-1">
                  {bookmakers.map(bookmaker => {
                    const isChecked = editBkIds.includes(bookmaker.id);
                    return (
                      <button
                        key={bookmaker.id}
                        type="button"
                        onClick={() => handleToggleBk(bookmaker.id)}
                        className={`min-w-0 p-2 border text-[9px] font-bold text-left rounded-lg flex items-center space-x-1.5 transition-all ${
                          isChecked
                            ? 'bg-indigo-500/10 border-indigo-500/35 text-indigo-300'
                            : 'bg-slate-900/40 border-slate-800 text-slate-500'
                        }`}
                      >
                        <span className={`w-3 h-3 rounded flex items-center justify-center text-[7px] shrink-0 ${isChecked ? 'bg-indigo-500 text-white' : 'border border-slate-700'}`}>
                          {isChecked && <BadgeCheck className="w-2.5 h-2.5" />}
                        </span>
                        <BookmakerLogoFrame bookmaker={bookmaker} size="compact" active={isChecked} />
                        <span className="truncate">{bookmaker.name}</span>
                      </button>
                    );
                  })}
                </div>
              </SmoothCollapse>

              {otherBookmakerSelected && (
                <SmoothCollapse open={editBookmakersOpen}>
                  <EmojiTextField
                    type="text"
                    value={editOtherBookmakerName}
                    onValueChange={setEditOtherBookmakerName}
                    placeholder="Название другой БК"
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-600 focus:outline-none focus:border-indigo-400/50 text-xs font-bold"
                  />
                </SmoothCollapse>
              )}
            </div>

            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-3 rounded-2xl flex items-center justify-center space-x-1.5 transition-all shadow-neon-green text-xs"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <Save className="w-4 h-4" />
                  <span>Сохранить изменения</span>
                </>
              )}
            </button>

            {canDeleteClients && (
              <button
                type="button"
                onClick={handleDeleteUser}
                disabled={saving}
                className="w-full bg-rose-500/10 hover:bg-rose-500 border border-rose-500/25 hover:border-rose-400 active:scale-[0.98] disabled:opacity-50 text-rose-300 hover:text-white font-black py-3 rounded-2xl flex items-center justify-center space-x-1.5 transition-all text-xs"
              >
                {saving ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    <span>Удалить клиента</span>
                  </>
                )}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
