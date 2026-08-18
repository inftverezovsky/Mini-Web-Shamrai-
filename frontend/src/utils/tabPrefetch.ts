import type { QueryClient } from '@tanstack/react-query';

import type { AdminShellTabId, UserTabId } from '../components/BottomNavigation';
import {
  AdminAuthorTimelineResponse,
  AdminClientsStatsResponse,
  BetResponse,
  BookmakerResponse,
  PaginatedResponse,
  PerformanceTimelineResponse,
  PeriodFilter,
  StatsPeriodFilter,
  ProfileDashboardResponse,
  SubscriptionPlanResponse,
} from '../schemas/schemas';
import { apiFetch } from './api';

export const TAB_QUERY_STALE_TIME = 5 * 60_000;
export const ADMIN_TAB_QUERY_STALE_TIME = 60_000;

export const BETS_FEED_QUERY_KEY = ['bets-feed-page'] as const;
export const BOOKMAKERS_QUERY_KEY = ['bookmakers'] as const;
export const TARIFFS_QUERY_KEY = ['tariffs-dashboard'] as const;

export type GlobalStatsPeriod = Extract<PeriodFilter, 'all' | 'month'>;

export const myBetsTimelineQueryKey = (period: PeriodFilter = 'all') => ['my-bets-timeline', period] as const;
export const globalStatsQueryKey = (period: GlobalStatsPeriod = 'all') => ['global-stats', period] as const;
export const GLOBAL_STATS_QUERY_KEY = globalStatsQueryKey('all');
export const profileDashboardQueryKey = (telegramId: number | null | undefined) => ['profile-dashboard', telegramId] as const;
export const adminUsersPageQueryKey = (
  searchTerm = '',
  activityFilter = 'all',
  groupFilter = 'all',
  tagFilter = 'all',
  bookmakerFilter = 'all',
) => ['admin-users-page', searchTerm, activityFilter, groupFilter, tagFilter, bookmakerFilter] as const;
export const adminStatsDashboardQueryKey = (period: StatsPeriodFilter = 'all') => ['admin-stats-dashboard', period] as const;

export interface GlobalStatsData {
  period: GlobalStatsPeriod;
  period_label: string;
  winrate: number;
  roi: number;
  net_profit: number;
  total_bets: number;
  won_bets: number;
  lost_bets: number;
  refund_bets: number;
  average_coefficient: number;
  chart_points: Array<{ month: string; profit: number }>;
}

export interface TariffsDashboardData {
  plans: SubscriptionPlanResponse[];
  referralDiscountPercent: number;
}

interface ReferralDiscountPayload {
  program_enabled?: boolean;
  discount_enabled?: boolean;
  referral_discount_percent?: number;
}

export interface AdminStatsDashboardData {
  authorTimeline: AdminAuthorTimelineResponse;
  shamraiTimeline: PerformanceTimelineResponse;
  clients: AdminClientsStatsResponse;
  bookmakers: BookmakerResponse[];
}

export type AdminDashboardTabId = 'bets' | 'promo-content' | 'broadcast' | 'requests' | 'results';

interface PrefetchTabContext {
  loadChunk?: () => Promise<unknown>;
  userTelegramId?: number | null;
}

const intentPrefetches = new Map<string, Promise<void>>();

function runPrefetchIntent(key: string, work: () => Promise<void>) {
  const inFlight = intentPrefetches.get(key);
  if (inFlight) return inFlight;

  const promise = work()
    .catch(() => undefined)
    .finally(() => {
      intentPrefetches.delete(key);
    });
  intentPrefetches.set(key, promise);
  return promise;
}

function prefetchChunk(loadChunk?: () => Promise<unknown>) {
  return loadChunk?.().then(() => undefined) ?? Promise.resolve();
}

export function fetchBetsFeedPage(pageParam: string | null = null, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: '20' });
  if (pageParam) params.set('cursor', pageParam);
  return apiFetch<PaginatedResponse<BetResponse>>(`/bets/feed-page?${params.toString()}`, { signal });
}

export function fetchMyBetsTimeline(period: PeriodFilter = 'all', signal?: AbortSignal) {
  const endpoint = `/users/me/bets/timeline?period=${encodeURIComponent(period)}`;
  return signal ? apiFetch<PerformanceTimelineResponse>(endpoint, { signal }) : apiFetch<PerformanceTimelineResponse>(endpoint);
}

export function fetchGlobalStats(period: GlobalStatsPeriod = 'all', signal?: AbortSignal) {
  const endpoint = `/stats/global?period=${encodeURIComponent(period)}`;
  return signal ? apiFetch<GlobalStatsData>(endpoint, { signal }) : apiFetch<GlobalStatsData>(endpoint);
}

export function fetchProfileDashboard(signal?: AbortSignal) {
  return signal
    ? apiFetch<ProfileDashboardResponse>('/users/me/profile-dashboard', { signal })
    : apiFetch<ProfileDashboardResponse>('/users/me/profile-dashboard');
}

export async function fetchTariffsDashboard(): Promise<TariffsDashboardData> {
  const [plans, referral] = await Promise.all([
    apiFetch<SubscriptionPlanResponse[]>('/subscriptions/plans'),
    apiFetch<ReferralDiscountPayload>('/users/me/referral'),
  ]);
  const referralDiscountEnabled = referral.program_enabled !== false && referral.discount_enabled !== false;

  return {
    plans,
    referralDiscountPercent: referralDiscountEnabled ? referral.referral_discount_percent ?? 0 : 0,
  };
}

export function fetchAdminUsersPage<T = any>(
  pageParam: string | null = null,
  filters: {
    searchTerm?: string;
    activityFilter?: string;
    groupFilter?: string;
    tagFilter?: string;
    bookmakerFilter?: string;
  } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ limit: '50' });
  if (pageParam) params.set('cursor', pageParam);
  if (filters.searchTerm?.trim()) params.set('q', filters.searchTerm.trim());
  if (filters.activityFilter && filters.activityFilter !== 'all') params.set('activity', filters.activityFilter);
  if (filters.groupFilter && filters.groupFilter !== 'all') params.set('group', filters.groupFilter);
  if (filters.tagFilter && filters.tagFilter !== 'all') params.set('tag', filters.tagFilter);
  if (filters.bookmakerFilter && filters.bookmakerFilter !== 'all') params.set('bookmaker_id', filters.bookmakerFilter);
  return apiFetch<PaginatedResponse<T>>(`/admin/users-page?${params.toString()}`, { signal });
}

export function fetchBookmakers(signal?: AbortSignal) {
  return apiFetch<BookmakerResponse[]>('/bookmakers', { signal });
}

export function fetchAdminAuthorTimeline(period: StatsPeriodFilter = 'all', signal?: AbortSignal) {
  const endpoint = `/admin/stats/author-timeline?period=${encodeURIComponent(period)}`;
  return signal ? apiFetch<AdminAuthorTimelineResponse>(endpoint, { signal }) : apiFetch<AdminAuthorTimelineResponse>(endpoint);
}

export function fetchAdminShamraiTimeline(period: StatsPeriodFilter = 'all', signal?: AbortSignal) {
  const endpoint = `/admin/stats/shamrai-timeline?period=${encodeURIComponent(period)}`;
  return signal ? apiFetch<PerformanceTimelineResponse>(endpoint, { signal }) : apiFetch<PerformanceTimelineResponse>(endpoint);
}

export async function fetchAdminStatsDashboard(period: StatsPeriodFilter = 'all', signal?: AbortSignal): Promise<AdminStatsDashboardData> {
  const periodQuery = `period=${encodeURIComponent(period)}`;
  const [authorTimeline, shamraiTimeline, clients, bookmakers] = await Promise.all([
    fetchAdminAuthorTimeline(period, signal),
    fetchAdminShamraiTimeline(period, signal),
    signal
      ? apiFetch<AdminClientsStatsResponse>(`/admin/stats/clients?${periodQuery}`, { signal })
      : apiFetch<AdminClientsStatsResponse>(`/admin/stats/clients?${periodQuery}`),
    fetchBookmakers(signal),
  ]);

  return {
    authorTimeline,
    shamraiTimeline,
    clients,
    bookmakers,
  };
}

export function prefetchUserTab(queryClient: QueryClient, tab: UserTabId, context: PrefetchTabContext = {}) {
  return runPrefetchIntent(`user:${tab}:${context.userTelegramId ?? 'anon'}`, async () => {
    await Promise.all([
      prefetchChunk(context.loadChunk),
      tab === 'feed'
        ? queryClient.prefetchInfiniteQuery({
            queryKey: BETS_FEED_QUERY_KEY,
            initialPageParam: null as string | null,
            queryFn: ({ pageParam, signal }) => fetchBetsFeedPage((pageParam as string | null) ?? null, signal),
            getNextPageParam: (lastPage: PaginatedResponse<BetResponse>) => (
              lastPage.has_more ? lastPage.next_cursor : undefined
            ),
            staleTime: TAB_QUERY_STALE_TIME,
          })
        : Promise.resolve(),
      tab === 'stats' || tab === 'my_bets'
        ? Promise.all([
            queryClient.prefetchQuery({
              queryKey: myBetsTimelineQueryKey('all'),
              queryFn: ({ signal }) => fetchMyBetsTimeline('all', signal),
              staleTime: TAB_QUERY_STALE_TIME,
            }),
            queryClient.prefetchQuery({
              queryKey: globalStatsQueryKey('all'),
              queryFn: ({ signal }) => fetchGlobalStats('all', signal),
              staleTime: TAB_QUERY_STALE_TIME,
            }),
          ]).then(() => undefined)
        : Promise.resolve(),
      tab === 'billing'
        ? queryClient.prefetchQuery({
            queryKey: TARIFFS_QUERY_KEY,
            queryFn: fetchTariffsDashboard,
            staleTime: TAB_QUERY_STALE_TIME,
          })
        : Promise.resolve(),
      tab === 'profile' && context.userTelegramId != null
        ? queryClient.prefetchQuery({
            queryKey: profileDashboardQueryKey(context.userTelegramId),
            queryFn: ({ signal }) => fetchProfileDashboard(signal),
            staleTime: TAB_QUERY_STALE_TIME,
          })
        : Promise.resolve(),
    ]);
  });
}

export function prefetchAdminTab(queryClient: QueryClient, tab: AdminShellTabId, context: PrefetchTabContext = {}) {
  return runPrefetchIntent(`admin:${tab}:${context.userTelegramId ?? 'anon'}`, async () => {
    await Promise.all([
      prefetchChunk(context.loadChunk),
      tab === 'stats'
        ? queryClient.prefetchQuery({
            queryKey: adminStatsDashboardQueryKey('all'),
            queryFn: ({ signal }) => fetchAdminStatsDashboard('all', signal),
            staleTime: ADMIN_TAB_QUERY_STALE_TIME,
          })
        : Promise.resolve(),
      tab === 'clients'
        ? Promise.all([
            queryClient.prefetchQuery({
              queryKey: BOOKMAKERS_QUERY_KEY,
              queryFn: ({ signal }) => fetchBookmakers(signal),
              staleTime: TAB_QUERY_STALE_TIME,
            }),
            queryClient.prefetchInfiniteQuery({
              queryKey: adminUsersPageQueryKey(),
              initialPageParam: null as string | null,
              queryFn: ({ pageParam, signal }) => fetchAdminUsersPage((pageParam as string | null) ?? null, {}, signal),
              getNextPageParam: (lastPage: PaginatedResponse<any>) => (
                lastPage.has_more ? lastPage.next_cursor : undefined
              ),
              staleTime: ADMIN_TAB_QUERY_STALE_TIME,
            }),
          ]).then(() => undefined)
        : Promise.resolve(),
    ]);
  });
}

const adminDashboardChunkLoaders: Record<AdminDashboardTabId, () => Promise<unknown>> = {
  bets: () => import('../pages/admin/AdminBets'),
  'promo-content': () => import('../pages/admin/AdminPromoContent'),
  broadcast: () => import('../pages/admin/AdminBroadcast'),
  requests: () => import('../pages/admin/AdminBroadcast'),
  results: () => import('../pages/admin/AdminResults'),
};

export function prefetchAdminDashboardTab(tab: AdminDashboardTabId) {
  return runPrefetchIntent(`admin-dashboard:${tab}`, () => prefetchChunk(adminDashboardChunkLoaders[tab]));
}
