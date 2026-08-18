import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';

const apiFetchMock = vi.fn();

vi.mock('../src/utils/api', () => ({
  apiFetch: apiFetchMock,
}));

describe('tab prefetch global stats helpers', () => {
  it('uses period-aware query keys for Shamrai global stats', async () => {
    const { globalStatsQueryKey } = await import('../src/utils/tabPrefetch');

    expect(globalStatsQueryKey('all')).toEqual(['global-stats', 'all']);
    expect(globalStatsQueryKey('month')).toEqual(['global-stats', 'month']);
  });

  it('requests monthly Shamrai global stats with the period query parameter', async () => {
    apiFetchMock.mockResolvedValue({ total_bets: 0 });
    const { fetchGlobalStats } = await import('../src/utils/tabPrefetch');

    await fetchGlobalStats('month');

    expect(apiFetchMock).toHaveBeenCalledWith('/stats/global?period=month');
  });

  it('passes CRM bookmaker filter to paginated admin users request', async () => {
    apiFetchMock.mockResolvedValue({ items: [], has_more: false });
    const { adminUsersPageQueryKey, fetchAdminUsersPage } = await import('../src/utils/tabPrefetch');

    expect(adminUsersPageQueryKey('', 'all', 'all', 'all', '2')).toEqual([
      'admin-users-page',
      '',
      'all',
      'all',
      'all',
      '2',
    ]);

    await fetchAdminUsersPage(null, { bookmakerFilter: '2' });

    expect(apiFetchMock).toHaveBeenCalledWith('/admin/users-page?limit=50&bookmaker_id=2', {
      signal: undefined,
    });
  });

  it('uses the exact selected month for every admin statistics request', async () => {
    apiFetchMock.mockResolvedValue({});
    const { adminStatsDashboardQueryKey, fetchAdminStatsDashboard } = await import('../src/utils/tabPrefetch');

    expect(adminStatsDashboardQueryKey('2026-02')).toEqual(['admin-stats-dashboard', '2026-02']);
    await fetchAdminStatsDashboard('2026-02');

    expect(apiFetchMock).toHaveBeenCalledWith('/admin/stats/author-timeline?period=2026-02');
    expect(apiFetchMock).toHaveBeenCalledWith('/admin/stats/shamrai-timeline?period=2026-02');
    expect(apiFetchMock).toHaveBeenCalledWith('/admin/stats/clients?period=2026-02');
  });

  it('deduplicates repeated intent prefetch promises for the same tab', async () => {
    const { prefetchUserTab } = await import('../src/utils/tabPrefetch');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let resolveChunk: (() => void) | undefined;
    const loadChunk = vi.fn(() => new Promise<void>((resolve) => {
      resolveChunk = resolve;
    }));

    const firstPrefetch = prefetchUserTab(queryClient, 'profile', { loadChunk });
    const secondPrefetch = prefetchUserTab(queryClient, 'profile', { loadChunk });

    expect(secondPrefetch).toBe(firstPrefetch);
    expect(loadChunk).toHaveBeenCalledTimes(1);

    resolveChunk?.();
    await firstPrefetch;
    queryClient.clear();
  });
});
