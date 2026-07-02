import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { apiFetch, downloadApiFile } from '../../utils/api';
import { ADMIN_TAB_QUERY_STALE_TIME, BOOKMAKERS_QUERY_KEY, TAB_QUERY_STALE_TIME, adminUsersPageQueryKey, fetchAdminUsersPage, fetchBookmakers } from '../../utils/tabPrefetch';
import { BookmakerResponse, ChatConversationResponse, PaginatedResponse, StatsDriveExportJob } from '../../schemas/schemas';
import { isOtherBookmaker } from '../../constants/bookmakers';
import EmojiTextField from '../../components/EmojiTextField';
import { BookmakerLogoFrame } from '../../components/LogoFrame';
import SmoothCollapse from '../../components/SmoothCollapse';
import { useAuthSelector } from '../../context/AuthContext';
import { isPrivilegedRole } from '../../utils/roles';
import {
  getClientAvatarSources,
  getClientChannelStatuses,
  getCrmClientTagLabel,
  getClientPriority,
  getClientRecentMatchSummary,
  clientMatchesBookmakerFilter,
  getCrmBookmakerPreview,
  getCrmMatchBalance,
  getStableMatchSegments,
  type CrmChannelTone,
  type CrmPriorityTone,
} from '../../utils/adminCrmDisplay';
import {
  Activity,
  AlertTriangle,
  BadgeCheck,
  Calendar,
  CheckSquare,
  ChevronDown,
  Clock,
  Cloud,
  Filter,
  Layers3,
  Loader2,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Tags,
  Trash2,
  TrendingDown,
  Users,
  X,
} from 'lucide-react';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';
import {
  adminWebChatConversationUrl,
  adminWebChatUserUrl,
  dispatchAdminWebChatOpen,
} from '../../utils/adminWebChatNavigation';
import {
  ExportActions,
  ExportStatusPanel,
  IconActionButton,
  StatTile,
} from '../../features/performance/performanceUi';
import { useGlassOverlayGuard } from '../../hooks/useGlassOverlayGuard';

type ActivityFilter = 'all' | 'active' | 'empty' | 'guarantee';

interface CRMUser {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  photo_url?: string | null;
  vk_photo_url?: string | null;
  is_web_only?: boolean;
  role: string;
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
  identity_providers?: string[];
  missing_identity_providers?: string[];
  telegram_connected?: boolean;
  telegram_delivery_enabled?: boolean;
  vk_user_id?: string | null;
  vk_group_member?: boolean;
  vk_messages_allowed?: boolean;
  vk_notifications_allowed?: boolean;
  vk_connected?: boolean;
  vk_delivery_enabled?: boolean;
  web_push_enabled?: boolean;
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

function getCleanTelegramUsername(username: string | null | undefined) {
  return (username || '').trim().replace(/^@/, '');
}

function cleanText(value: string) {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function getMatchBalance(user: Pick<CRMUser, 'purchased_bets_balance' | 'matches_remaining'>) {
  return getCrmMatchBalance(user);
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debouncedValue;
}

function driveStatusLabel(job: StatsDriveExportJob | null) {
  if (!job) return null;
  if (job.status === 'completed') return 'CRM-отчет готов на Google Drive';
  if (job.status === 'failed') return 'Ошибка CRM-выгрузки';
  if (job.status === 'running') return 'Создаем CRM-отчет';
  return 'CRM-отчет в очереди';
}

function ClientResultStrip({ results }: { results: ClientRecentMatchResult[] }) {
  const segments = getStableMatchSegments(results);
  return (
    <div className="grid grid-cols-10 gap-1">
      {segments.map((result, index) => {
        return (
          <span
            key={`result-${index}-${result?.bet_id || 'empty'}`}
            className={`h-2.5 min-w-0 rounded-full border transition-all ${
              result?.status === 'win'
                ? 'border-emerald-200/30 bg-emerald-300'
                : result?.status === 'loss'
                  ? 'border-rose-200/30 bg-rose-300'
                  : 'border-white/10 bg-white/[0.06]'
            }`}
            aria-label={
              result?.status === 'win'
                ? 'Победа'
                : result?.status === 'loss'
                  ? 'Неудача'
                  : 'Нет матча'
            }
          />
        );
      })}
    </div>
  );
}

const channelToneClasses: Record<CrmChannelTone, string> = {
  ready: 'border-emerald-300/25 bg-emerald-300/10 text-emerald-100',
  warning: 'border-amber-300/25 bg-amber-300/10 text-amber-100',
  missing: 'border-rose-300/18 bg-rose-300/[0.08] text-rose-100',
};

const priorityToneClasses: Record<CrmPriorityTone, string> = {
  danger: 'border-rose-300/30 bg-rose-400/12 text-rose-100',
  warning: 'border-amber-300/30 bg-amber-400/12 text-amber-100',
  success: 'border-emerald-300/30 bg-emerald-400/12 text-emerald-100',
  info: 'border-cyan-300/30 bg-cyan-400/12 text-cyan-100',
  muted: 'border-white/10 bg-white/[0.05] text-slate-400',
};

function getPriorityIcon(priority: ReturnType<typeof getClientPriority>) {
  if (priority.label === 'Долг') return TrendingDown;
  if (priority.label === 'Гарантия') return Sparkles;
  if (priority.label === 'Активен') return ShieldCheck;
  if (priority.label === 'Связаться') return Activity;
  return Clock;
}

function getClientInitials(user: CRMUser) {
  const displayName = getDisplayName(user);
  const words = displayName.split(/\s+/).filter(Boolean);
  const initials = words.length > 1
    ? `${words[0][0] || ''}${words[1][0] || ''}`
    : displayName.slice(0, 2);
  return initials.toUpperCase();
}

function ClientAvatar({ user, size = 'row' }: { user: CRMUser; size?: 'row' | 'modal' }) {
  const sources = useMemo(() => getClientAvatarSources({
    photo_url: user.photo_url,
    vk_photo_url: user.vk_photo_url,
  }), [user.photo_url, user.vk_photo_url]);
  const sourceKey = sources.join('\n');
  const [sourceIndex, setSourceIndex] = useState(0);
  const avatarUrl = sources[sourceIndex] ?? null;
  const sizeClass = size === 'modal' ? 'h-14 w-14 rounded-[22px] text-sm' : 'h-11 w-11 rounded-2xl text-[11px]';

  useEffect(() => {
    setSourceIndex(0);
  }, [sourceKey]);

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt=""
        draggable={false}
        onError={() => setSourceIndex((index) => Math.min(index + 1, sources.length))}
        className={`${sizeClass} shrink-0 border border-white/10 bg-slate-950/45 object-cover`}
      />
    );
  }

  return (
    <div className={`grid ${sizeClass} shrink-0 place-items-center border border-white/10 bg-slate-950/45 font-black text-cyan-100`}>
      {getClientInitials(user)}
    </div>
  );
}

function ClientPriorityBadge({ user, compact = false }: { user: CRMUser; compact?: boolean }) {
  const priority = getClientPriority(user);
  const Icon = getPriorityIcon(priority);
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl border font-black uppercase tracking-[0.08em] ${compact ? 'px-2 py-1 text-[8px]' : 'px-2.5 py-1.5 text-[9px]'} ${priorityToneClasses[priority.tone]}`}
      title={`${priority.label}: ${priority.detail}`}
      aria-label={`Приоритет клиента: ${priority.label}. ${priority.detail}`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{priority.label}</span>
      <span className="text-current/70">{priority.detail}</span>
    </span>
  );
}

function ClientConnectionBadges({ user }: { user: CRMUser }) {
  const channels = getClientChannelStatuses(user);

  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Статусы авторизации и синхронизации клиента">
      {channels.map((channel) => (
        <span
          key={channel.key}
          className={`inline-flex min-h-[24px] items-center gap-1.5 rounded-lg border px-2 py-1 text-[8px] font-black uppercase tracking-[0.08em] ${channelToneClasses[channel.tone]}`}
          title={channel.detail}
        >
          <span>{channel.shortLabel}</span>
          <span className="text-current/80">{channel.label}</span>
        </span>
      ))}
    </div>
  );
}

function AdminWebChatDialogLink({
  user,
  title,
  children,
  className = '',
}: {
  user: { telegram_id: number };
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  const fallbackUrl = adminWebChatUserUrl(user.telegram_id);

  const handleClick = async (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.stopPropagation();

    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }

    event.preventDefault();
    try {
      const conversation = await apiFetch<ChatConversationResponse>(
        `/chat/admin/conversations/by-user/${encodeURIComponent(String(user.telegram_id))}`,
        { method: 'POST' },
      );
      const conversationUrl = conversation.id ? adminWebChatConversationUrl(conversation.id) : fallbackUrl;
      window.history.pushState({ open: 'admin-web-chat' }, '', conversationUrl);
      dispatchAdminWebChatOpen({
        conversation,
        conversationId: conversation.id,
        userId: user.telegram_id,
      });
      notifySuccess('Диалог с клиентом открыт', 'Чаты');
    } catch (err: any) {
      notifyError(err?.message || 'Не удалось открыть диалог с клиентом');
    }
  };

  return (
    <a
      href={fallbackUrl}
      onClick={handleClick}
      className={`inline-flex min-w-0 items-center rounded-md text-cyan-100 transition hover:text-cyan-50 hover:underline focus:outline-none focus:ring-1 focus:ring-cyan-300/45 ${className}`}
      title={title}
    >
      {children}
    </a>
  );
}

function ClientTelegramContactLine({ user }: { user: CRMUser }) {
  const cleanUsername = getCleanTelegramUsername(user.username);
  const usernameLabel = cleanUsername ? `@${cleanUsername}` : 'без юзернейма';
  const telegramIdLabel = getClientIdLabel(user);

  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1 normal-case tracking-normal">
      {cleanUsername ? (
        <AdminWebChatDialogLink user={user} title="Открыть диалог с клиентом в веб-чате" className="truncate">
          {usernameLabel}
        </AdminWebChatDialogLink>
      ) : (
        <span className="min-w-0 truncate text-slate-500">{usernameLabel}</span>
      )}
      <span className="shrink-0 text-slate-600">/</span>
      <AdminWebChatDialogLink user={user} title="Открыть диалог с клиентом в веб-чате" className="shrink-0">
        {telegramIdLabel}
      </AdminWebChatDialogLink>
    </span>
  );
}

function ClientBookmakerSummary({ user, align = 'end' }: { user: CRMUser; align?: 'start' | 'end' }) {
  const preview = getCrmBookmakerPreview(user.bookmakers, user.other_bookmaker_name);
  const hasBookmakers = preview.visibleBookmakers.length > 0;

  return (
    <div className={`flex min-w-0 flex-wrap gap-1 ${align === 'end' ? 'justify-end' : 'justify-start'}`}>
      {hasBookmakers ? preview.visibleBookmakers.map((bookmaker) => (
        <span
          key={bookmaker.id}
          className="inline-flex h-7 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.045] p-1 text-slate-300"
          title={bookmaker.name}
          aria-label={`БК: ${bookmaker.name}`}
        >
          <BookmakerLogoFrame bookmaker={bookmaker} size="tiny" />
        </span>
      )) : (
        <span className="rounded-lg border border-white/10 bg-white/[0.045] px-2 py-1 text-[8px] font-black uppercase tracking-[0.08em] text-slate-500">
          БК не выбраны
        </span>
      )}
      {preview.extraCount > 0 && (
        <span
          className="rounded-lg border border-cyan-200/16 bg-cyan-200/[0.07] px-2 py-1 text-[8px] font-black text-cyan-100"
          title={`Еще ${preview.extraCount} БК`}
        >
          +{preview.extraCount}
        </span>
      )}
    </div>
  );
}

function ClientIntelligenceRow({
  user,
  onOpen,
}: {
  user: CRMUser;
  onOpen: (user: CRMUser) => void;
}) {
  const recentResults = user.recent_match_results || [];
  const matchSummary = getClientRecentMatchSummary(recentResults);

  return (
    <article
      onClick={() => onOpen(user)}
      className="smooth-pressable group w-full cursor-pointer overflow-hidden rounded-[24px] border border-white/10 bg-white/[0.045] p-3 text-left text-xs shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-all hover:border-cyan-200/28 hover:bg-white/[0.065] active:scale-[0.995]"
    >
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.15fr)_minmax(12rem,0.68fr)_minmax(14rem,0.78fr)_minmax(12rem,0.62fr)] xl:items-center">
        <div className="min-w-0">
          <div className="flex min-w-0 items-start gap-3">
            <ClientAvatar user={user} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="truncate text-sm font-black leading-snug text-white">{getDisplayName(user)}</h4>
              </div>
              <p className="mt-1 truncate text-[10px] font-bold text-slate-500">
                <ClientTelegramContactLine user={user} />
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="rounded-lg border border-cyan-200/16 bg-cyan-200/[0.07] px-2 py-1 text-[8px] font-black uppercase tracking-[0.08em] text-cyan-100">
                  {user.client_group || 'Без группы'}
                </span>
                <span className="rounded-lg border border-amber-200/16 bg-amber-200/[0.06] px-2 py-1 text-[8px] font-black uppercase tracking-[0.08em] text-amber-100">
                  {getCrmClientTagLabel(user.client_tag) || 'Без метки'}
                </span>
                <span className="rounded-lg border border-white/10 bg-white/[0.045] px-2 py-1 text-[8px] font-black uppercase tracking-[0.08em] text-slate-400">
                  A/B {user.ab_group || 'A'}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="min-w-0 rounded-2xl border border-white/10 bg-slate-950/32 p-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <ClientPriorityBadge user={user} />
          </div>
          <div className="mt-2">
            <ClientConnectionBadges user={user} />
          </div>
        </div>

        <div className="min-w-0 rounded-2xl border border-white/10 bg-slate-950/32 p-2.5">
          <div className="grid gap-1">
            <span className={`truncate text-[9px] font-black uppercase tracking-[0.09em] ${
              matchSummary.streak?.status === 'win'
                ? 'text-emerald-200'
                : matchSummary.streak?.status === 'loss'
                  ? 'text-rose-200'
                  : 'text-slate-500'
            }`}>
              {matchSummary.headline}
            </span>
            <span className="truncate text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">
              {matchSummary.splitLabel}
            </span>
          </div>
          <div className="mt-2">
            <ClientResultStrip results={recentResults} />
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 xl:flex-col xl:items-end xl:justify-center">
          <ClientBookmakerSummary user={user} />
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onOpen(user);
            }}
            className="shrink-0 rounded-xl border border-cyan-200/18 bg-cyan-200/[0.07] px-3 py-2 text-[9px] font-black uppercase tracking-[0.1em] text-cyan-100 transition-all group-hover:bg-cyan-200/[0.12]"
          >
            Открыть
          </button>
        </div>
      </div>
    </article>
  );
}

interface AdminCRMProps {
  active?: boolean;
}

export default function AdminCRM({ active = true }: AdminCRMProps = {}) {
  const currentAdmin = useAuthSelector((state) => state.user);
  const [searchTerm, setSearchTerm] = useState('');
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>('all');
  const [groupFilter, setGroupFilter] = useState('all');
  const [tagFilter, setTagFilter] = useState('all');
  const [bookmakerFilter, setBookmakerFilter] = useState('all');
  const debouncedSearchTerm = useDebouncedValue(searchTerm, 250);

  const [selectedUser, setSelectedUser] = useState<CRMUser | null>(null);
  const [editBkIds, setEditBkIds] = useState<number[]>([]);
  const [editBookmakersOpen, setEditBookmakersOpen] = useState(false);
  const [editOtherBookmakerName, setEditOtherBookmakerName] = useState('');
  const [editClientGroup, setEditClientGroup] = useState('');
  const [editClientTag, setEditClientTag] = useState('');
  const [matchDelta, setMatchDelta] = useState('');
  const [saving, setSaving] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<'csv' | 'xlsx' | null>(null);
  const [driveJob, setDriveJob] = useState<StatsDriveExportJob | null>(null);
  const [driveLoading, setDriveLoading] = useState(false);
  const [driveError, setDriveError] = useState<string | null>(null);
  const canDeleteClients = isPrivilegedRole(currentAdmin?.role);

  useGlassOverlayGuard(Boolean(selectedUser));

  useEffect(() => {
    if (!selectedUser) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [selectedUser]);

  const bookmakersQuery = useQuery<BookmakerResponse[]>({
    queryKey: BOOKMAKERS_QUERY_KEY,
    queryFn: ({ signal }) => fetchBookmakers(signal),
    enabled: active,
    staleTime: TAB_QUERY_STALE_TIME,
  });

  const usersQuery = useInfiniteQuery<PaginatedResponse<CRMUser>, Error>({
    queryKey: adminUsersPageQueryKey(debouncedSearchTerm, activityFilter, groupFilter, tagFilter, bookmakerFilter),
    initialPageParam: null as string | null,
    enabled: Boolean(currentAdmin) && active,
    queryFn: ({ pageParam, signal }) => fetchAdminUsersPage<CRMUser>((pageParam as string | null) ?? null, {
      searchTerm: debouncedSearchTerm,
      activityFilter,
      groupFilter,
      tagFilter,
      bookmakerFilter,
    }, signal),
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_cursor : undefined),
    staleTime: ADMIN_TAB_QUERY_STALE_TIME,
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

  useEffect(() => {
    if (!active || !driveJob || driveJob.status === 'completed' || driveJob.status === 'failed') return;
    const timer = window.setInterval(async () => {
      try {
        const freshJob = await apiFetch<StatsDriveExportJob>(`/admin/users/drive-export/${driveJob.id}`);
        setDriveJob(freshJob);
        if (freshJob.status === 'completed' || freshJob.status === 'failed') {
          setDriveLoading(false);
        }
      } catch (err: any) {
        setDriveError(err.message || 'Не удалось обновить статус CRM-выгрузки');
        setDriveLoading(false);
      }
    }, 2200);
    return () => window.clearInterval(timer);
  }, [active, driveJob]);

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
        getCrmClientTagLabel(user.client_tag),
        ...user.bookmakers.map(bookmaker => `${bookmaker.name} ${bookmaker.code}`),
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
      const matchesBookmaker = clientMatchesBookmakerFilter(user, bookmakerFilter);

      return matchesSearch && matchesActivity && matchesGroup && matchesTag && matchesBookmaker;
    });
  }, [activityFilter, bookmakerFilter, groupFilter, searchTerm, tagFilter, users]);

  const summary = useMemo(() => {
    const active = users.filter(user => user.has_active_subscription).length;
    const empty = users.filter(user => !user.guarantee_active && getMatchBalance(user) <= 0).length;
    const guarantee = users.filter(user => user.guarantee_active).length;
    const debt = users.filter(user => getMatchBalance(user) < 0).length;
    const attention = users.filter(user => user.guarantee_active || getMatchBalance(user) < 0).length;
    return { active, empty, guarantee, debt, attention };
  }, [users]);

  const otherBookmakerSelected = useMemo(() => (
    bookmakers.some(bookmaker => isOtherBookmaker(bookmaker) && editBkIds.includes(bookmaker.id))
  ), [bookmakers, editBkIds]);
  const selectedEditBookmakerNames = useMemo(() => (
    bookmakers
      .filter(bookmaker => editBkIds.includes(bookmaker.id))
      .map(bookmaker => bookmaker.name)
  ), [bookmakers, editBkIds]);
  const selectedEditBookmakerSummary = useMemo(() => {
    const otherName = otherBookmakerSelected ? cleanText(editOtherBookmakerName) : null;
    return otherName ? [...selectedEditBookmakerNames, otherName] : selectedEditBookmakerNames;
  }, [editOtherBookmakerName, otherBookmakerSelected, selectedEditBookmakerNames]);

  const openEditModal = (user: CRMUser) => {
    setSelectedUser(user);
    setEditBkIds(user.bookmakers.map(bookmaker => bookmaker.id));
    setEditBookmakersOpen(false);
    setEditOtherBookmakerName(user.other_bookmaker_name || '');
    setEditClientGroup(user.client_group || '');
    setEditClientTag(getCrmClientTagLabel(user.client_tag) || '');
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

  const handleClientExport = async (format: 'csv' | 'xlsx') => {
    const params = new URLSearchParams({ format });
    const cleanSearch = searchTerm.trim();
    if (cleanSearch) params.set('q', cleanSearch);
    if (activityFilter !== 'all') params.set('activity', activityFilter);
    if (groupFilter !== 'all') params.set('group', groupFilter);
    if (tagFilter !== 'all') params.set('tag', tagFilter);
    if (bookmakerFilter !== 'all') params.set('bookmaker_id', bookmakerFilter);

    try {
      setExportingFormat(format);
      await downloadApiFile(`/admin/users/export?${params.toString()}`, `shamrai_clients_crm.${format}`);
      notifySuccess(format === 'xlsx' ? 'CRM-выгрузка XLSX скачана' : 'CRM-выгрузка CSV скачана');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось скачать выгрузку клиентов');
    } finally {
      setExportingFormat(null);
    }
  };

  const handleClientDriveExport = async () => {
    const cleanSearch = searchTerm.trim();
    try {
      setDriveLoading(true);
      setDriveError(null);
      const job = await apiFetch<StatsDriveExportJob>('/admin/users/drive-export', {
        method: 'POST',
        body: JSON.stringify({
          q: cleanSearch || null,
          activity: activityFilter,
          group: groupFilter !== 'all' ? groupFilter : null,
          tag: tagFilter !== 'all' ? tagFilter : null,
          bookmaker_id: bookmakerFilter !== 'all' ? Number.parseInt(bookmakerFilter, 10) : null,
          formats: ['xlsx', 'google_sheet'],
        }),
      });
      setDriveJob(job);
      if (job.status === 'completed' || job.status === 'failed') {
        setDriveLoading(false);
      }
      if (job.status === 'completed') {
        notifySuccess('CRM-отчет выгружен на Google Drive');
      }
    } catch (err: any) {
      setDriveError(err.message || 'Не удалось запустить CRM-выгрузку на Google Drive');
      setDriveLoading(false);
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
        {tags.map(tag => <option key={tag} value={getCrmClientTagLabel(tag) || tag} />)}
      </datalist>

      <section className="relative overflow-hidden rounded-[26px] border border-white/10 bg-[radial-gradient(circle_at_18%_0%,rgba(34,211,238,0.18),transparent_34%),radial-gradient(circle_at_86%_16%,rgba(16,185,129,0.10),transparent_28%),linear-gradient(135deg,rgba(15,23,42,0.96),rgba(8,13,28,0.92))] p-3.5 shadow-[0_18px_60px_rgba(2,6,23,0.34)]">
        <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/45 to-transparent" />
        <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,32rem)]">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.13em] text-cyan-100">
              <Users className="h-3.5 w-3.5" />
              Клиентский cockpit
            </div>
            <h2 className="mt-1.5 text-xl font-black leading-tight text-white sm:text-2xl">CRM клиентов</h2>
            <div className="mt-3 text-3xl font-black leading-none tabular-nums text-cyan-100 sm:text-4xl">
              {filteredUsers.length}/{paginationMeta.filteredTotal || users.length}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-400">
              <span>{summary.active} активных</span>
              <span className="text-slate-700">/</span>
              <span>{summary.empty} без матчей</span>
              <span className="text-slate-700">/</span>
              <span>{summary.attention} требуют внимания</span>
            </div>
          </div>
          <div className="grid min-w-0 content-start gap-2">
            <ExportActions
              exporting={exportingFormat}
              onCsv={() => void handleClientExport('csv')}
              onXlsx={() => void handleClientExport('xlsx')}
            />
            <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(5.5rem,0.28fr)]">
              <div className="flex min-h-[68px] min-w-0 flex-col justify-center rounded-2xl border border-white/10 bg-slate-950/40 px-3 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.045)]">
                <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">Google Drive</div>
                <div className="mt-0.5 truncate text-xs font-black text-cyan-100">
                  {driveStatusLabel(driveJob) ?? 'Готов к выгрузке'}
                </div>
              </div>
              <IconActionButton title="Выгрузить CRM-отчет на Google Drive" disabled={driveLoading} onClick={() => void handleClientDriveExport()} tone="cyan">
                {driveLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cloud className="h-4 w-4" />}
              </IconActionButton>
            </div>
          </div>
        </div>
      </section>

      <ExportStatusPanel job={driveJob} error={driveError} title="CRM отчет" compact />
      {paginationMeta.hasMore && (
        <div className="rounded-2xl border border-slate-300/15 bg-slate-950/35 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-200">
          Показаны первые {users.length} клиентов. Уточните поиск или фильтр, чтобы сузить список.
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <StatTile label="Активные" value={summary.active} hint="с доступом" tone="text-emerald-200" />
        <StatTile label="Без матчей" value={summary.empty} hint="нужен контакт" tone="text-slate-200" />
        <StatTile label="Гарантия" value={summary.guarantee} hint="открыта" tone="text-amber-200" />
        <StatTile label="Долг" value={summary.debt} hint="минусовой баланс" tone={summary.debt ? 'text-rose-200' : 'text-slate-300'} />
      </div>

      <section className="rounded-[24px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.045)]">
        <div className="grid gap-2 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
          <label className="relative min-w-0">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={searchTerm}
              onChange={event => setSearchTerm(event.target.value)}
              placeholder="Поиск по имени, username, ID, группе или метке"
              className="min-h-[44px] w-full rounded-2xl border border-white/10 bg-slate-950/42 pl-9 pr-3 text-xs font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/45"
            />
          </label>
          <div className="flex gap-1.5 overflow-x-auto pb-1 xl:pb-0">
            {(Object.keys(filterLabels) as ActivityFilter[]).map(filter => (
              <button
                key={filter}
                type="button"
                onClick={() => setActivityFilter(filter)}
                className={`smooth-pressable min-h-[38px] shrink-0 rounded-xl border px-3 text-[9px] font-black uppercase tracking-[0.08em] transition-all ${
                  activityFilter === filter
                    ? 'border-cyan-200/45 bg-cyan-200/18 text-cyan-50'
                    : 'border-white/10 bg-slate-950/35 text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
                }`}
              >
                {filterLabels[filter]}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-3">
          <label className="relative">
            <Layers3 className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <select
              value={groupFilter}
              onChange={event => setGroupFilter(event.target.value)}
              className="min-h-[42px] w-full appearance-none rounded-2xl border border-white/10 bg-slate-950/42 pl-8 pr-3 text-[10px] font-bold text-white outline-none focus:border-cyan-300/45"
            >
              <option value="all">Все группы</option>
              {groups.map(group => <option key={group} value={group}>{group}</option>)}
            </select>
          </label>

          <label className="relative">
            <Filter className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <select
              value={tagFilter}
              onChange={event => setTagFilter(event.target.value)}
              className="min-h-[42px] w-full appearance-none rounded-2xl border border-white/10 bg-slate-950/42 pl-8 pr-3 text-[10px] font-bold text-white outline-none focus:border-cyan-300/45"
            >
              <option value="all">Все метки</option>
              {tags.map(tag => <option key={tag} value={tag}>{getCrmClientTagLabel(tag) || tag}</option>)}
            </select>
          </label>

          <label className="relative">
            <BadgeCheck className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <select
              value={bookmakerFilter}
              onChange={event => setBookmakerFilter(event.target.value)}
              className="min-h-[42px] w-full appearance-none rounded-2xl border border-white/10 bg-slate-950/42 pl-8 pr-3 text-[10px] font-bold text-white outline-none focus:border-cyan-300/45"
            >
              <option value="all">Все БК</option>
              {bookmakers.map(bookmaker => (
                <option key={bookmaker.id} value={String(bookmaker.id)}>
                  {bookmaker.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <div className="space-y-3">
        {filteredUsers.length === 0 ? (
          <div className="flex min-h-[128px] flex-col items-center justify-center rounded-[24px] border border-dashed border-white/10 bg-white/[0.04] px-5 py-7 text-center text-xs text-slate-500">
            <Users className="h-8 w-8 shrink-0 text-slate-500" />
            <div className="mt-3 max-w-[28rem] space-y-1">
              <p className="font-black leading-snug text-white">Клиенты не найдены</p>
              <p className="font-bold leading-snug">Измените поиск или фильтры.</p>
            </div>
          </div>
        ) : (
          filteredUsers.map(user => (
            <ClientIntelligenceRow
              key={user.telegram_id}
              user={user}
              onOpen={openEditModal}
            />
          ))
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

      {selectedUser && createPortal((
        <div className="glass-modal-layer shamrai-modal-root fixed inset-0 z-[100] flex items-end justify-center bg-slate-950/72 p-2 backdrop-blur-sm animate-fade-in sm:items-center sm:p-3 lg:justify-end lg:p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="crm-client-edit-title"
            className="relative flex max-h-[calc(100dvh-1rem)] w-full max-w-lg flex-col overflow-y-auto overscroll-contain rounded-[24px] border border-white/10 bg-[#0C1226]/95 p-3 shadow-2xl animate-scale-up sm:max-h-[calc(100dvh-2rem)] sm:rounded-[28px] sm:p-4 lg:h-[calc(100dvh-2rem)] lg:max-h-none lg:max-w-xl lg:p-5"
          >
            <button
              type="button"
              onClick={closeEditModal}
              disabled={saving}
              className="sticky top-0 z-20 -mb-10 ml-auto w-fit rounded-xl border border-white/10 bg-white/[0.05] p-2 text-slate-450 backdrop-blur transition-colors hover:text-white disabled:opacity-40"
              aria-label="Закрыть карточку клиента"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="space-y-3 pr-12">
              <div className="flex min-w-0 items-start gap-3">
                <ClientAvatar user={selectedUser} size="modal" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.12em] text-cyan-100">
                    <Users className="h-3.5 w-3.5" />
                    Карточка клиента
                  </div>
                  <h3 id="crm-client-edit-title" className="mt-1 truncate text-lg font-black text-white">{getDisplayName(selectedUser)}</h3>
                  <p className="mt-0.5 truncate text-[10px] font-bold text-slate-450">
                    <ClientTelegramContactLine user={selectedUser} />
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <ClientPriorityBadge user={selectedUser} compact />
                  </div>
                </div>
              </div>
              <ClientConnectionBadges user={selectedUser} />
              <div className="grid grid-cols-1 gap-2 pt-2 min-[390px]:grid-cols-3">
                <StatTile
                  label="Баланс"
                  value={`${getMatchBalance(selectedUser)}`}
                  hint="матчей"
                  tone={getMatchBalance(selectedUser) < 0 ? 'text-rose-200' : getMatchBalance(selectedUser) > 0 ? 'text-emerald-200' : 'text-slate-200'}
                  minHeightClass="min-h-[58px]"
                />
                <StatTile
                  label="БК"
                  value={selectedUser.bookmakers.length}
                  hint="выбрано"
                  tone="text-cyan-100"
                  minHeightClass="min-h-[58px]"
                />
                <StatTile
                  label="Гарантия"
                  value={selectedUser.guarantee_active ? 'Да' : 'Нет'}
                  hint={selectedUser.guarantee_active ? 'активна' : 'закрыта'}
                  tone={selectedUser.guarantee_active ? 'text-amber-200' : 'text-slate-300'}
                  minHeightClass="min-h-[58px]"
                />
              </div>
            </div>

            <div className="mt-5 space-y-5">
              <section className="rounded-[22px] border border-white/10 bg-white/[0.045] p-3">
                <div className="mb-3 flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-400">
                  <Layers3 className="h-3.5 w-3.5 text-cyan-200" />
                  Профиль
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
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
                      <Tags className="w-3.5 h-3.5 text-amber-300 mr-1.5" />
                      Метка
                    </span>
                    <EmojiTextField
                      type="text"
                      list="client-tag-options"
                      value={editClientTag}
                      onValueChange={setEditClientTag}
                      placeholder="топ, важный..."
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-600 focus:outline-none focus:border-amber-300/50 text-xs font-bold"
                    />
                  </label>
                </div>
              </section>

              <section className="space-y-2 rounded-[22px] border border-white/10 bg-white/[0.045] p-3 text-xs">
                <h4 className="font-extrabold flex items-center uppercase tracking-wider text-[9px] text-slate-400">
                  <Calendar className="w-3.5 h-3.5 text-indigo-400 mr-1.5 shrink-0" />
                  Доступ и баланс
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
                <div className="grid grid-cols-1 gap-2 min-[430px]:grid-cols-[1fr_auto]">
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
                <div className="grid grid-cols-1 gap-1.5 pt-1 min-[430px]:grid-cols-3">
                  <button
                    type="button"
                    onClick={() => setMatchDelta('5')}
                    className="bg-white/5 hover:bg-white/10 text-slate-300 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
                  >
                    +5 матчей
                  </button>
                  <button
                    type="button"
                    onClick={() => setMatchDelta('10')}
                    className="bg-white/5 hover:bg-white/10 text-slate-300 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
                  >
                    +10 матчей
                  </button>
                  <button
                    type="button"
                    onClick={() => setMatchDelta('')}
                    className="bg-white/5 hover:bg-white/10 text-slate-400 py-1.5 rounded-lg text-[9px] font-extrabold uppercase tracking-wider transition-all text-center"
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
              </section>

              <section className="space-y-2 rounded-[22px] border border-white/10 bg-white/[0.045] p-3 text-xs">
                <button
                  type="button"
                  onClick={() => setEditBookmakersOpen((current) => !current)}
                  aria-expanded={editBookmakersOpen}
                  className="flex w-full flex-col items-stretch justify-between gap-3 rounded-2xl border border-slate-700/80 bg-slate-900/55 px-3 py-2.5 text-left transition-all hover:border-indigo-400/45 hover:bg-slate-900 min-[430px]:flex-row min-[430px]:items-center"
                >
                  <span className="min-w-0">
                    <span className="flex items-center font-extrabold uppercase tracking-wider text-[9px] text-slate-400">
                      <CheckSquare className="w-3.5 h-3.5 text-indigo-400 mr-1.5 shrink-0" />
                      Букмекерские конторы
                    </span>
                    <span className="mt-1 block truncate text-[10px] font-semibold text-slate-300">
                      {selectedEditBookmakerSummary.length > 0
                        ? selectedEditBookmakerSummary.join(', ')
                        : 'БК не выбраны'}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center justify-between gap-2 min-[430px]:justify-end">
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
                  <div className="grid max-h-[min(40dvh,220px)] grid-cols-1 gap-1.5 overflow-y-auto pr-1 min-[430px]:grid-cols-2">
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
              </section>

              <section className="sticky bottom-0 z-10 space-y-2 rounded-[22px] border border-white/10 bg-[#0C1226]/95 p-3 shadow-[0_-16px_34px_rgba(12,18,38,0.92)] backdrop-blur">
                <div className="mb-2 flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-400">
                  <Activity className="h-3.5 w-3.5 text-emerald-200" />
                  Действия
                </div>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving}
                  className="flex w-full items-center justify-center space-x-1.5 rounded-2xl bg-emerald-500 py-3 text-xs font-black text-slate-950 shadow-neon-green transition-all hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50"
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
              </section>
            </div>
          </div>
        </div>
      ), document.body)}
    </div>
  );
}
