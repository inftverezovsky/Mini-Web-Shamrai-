import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  BarChart3,
  Loader2,
  Search,
  ShieldAlert,
  Sparkles,
  Target,
  Trophy,
  UploadCloud,
  Users,
} from 'lucide-react';

import {
  EMPTY_SUMMARY,
  ExpandedMap,
  BreakdownBars,
  CollapsiblePanel,
  ExecutiveScoreboard,
  ExportActions,
  ExportStatusPanel,
  IconActionButton,
  MomentumStrip,
  OverviewAnalytics,
  PeriodSelector,
  StatsHero,
  StatsKpiGrid,
  StatsSkeleton,
  StatTile,
  StatsValueMode,
  TimelineSectionBlock,
  formatStatsValue,
  pct,
  profitTone,
  recentResultCodes,
  streakLabel,
  summaryTone,
} from '../../features/performance/performanceStatsUi';
import {
  AdminClientStatsItem,
  AdminClientTimelineResponse,
  BookmakerResponse,
  PerformanceBetItem,
  PerformanceSummary,
  PerformanceTimelineResponse,
  PeriodFilter,
  StatsDriveExportJob,
  StatsDriveExportScope,
} from '../../schemas/schemas';
import { apiFetch, downloadApiFile } from '../../utils/api';
import {
  ADMIN_TAB_QUERY_STALE_TIME,
  adminStatsDashboardQueryKey,
  fetchAdminAuthorTimeline,
  fetchAdminShamraiTimeline,
  fetchAdminStatsDashboard,
  type AdminStatsDashboardData,
} from '../../utils/tabPrefetch';
import AdminStatsBetRow from './AdminStatsBetRow';

type StatsTab = 'all' | 'feed' | 'private' | 'paid_set' | 'clients';

const driveScopeLabels: Record<StatsDriveExportScope, string> = {
  all: 'Все',
  shamrai: 'Шамрай',
  clients: 'Клиенты',
  crm: 'CRM',
};

function summarizeClientSide(bets: PerformanceBetItem[]): PerformanceSummary {
  if (!bets.length) return EMPTY_SUMMARY;
  const wins = bets.filter((bet) => bet.status === 'win').length;
  const losses = bets.filter((bet) => bet.status === 'loss').length;
  const profit = bets.reduce((sum, bet) => sum + Number(bet.profit_units || 0), 0);
  const coefficientSum = bets.reduce((sum, bet) => sum + Number(bet.coefficient || 0), 0);
  return {
    bets: bets.length,
    wins,
    losses,
    winrate: Number(((wins / bets.length) * 100).toFixed(2)),
    roi: Number(((profit / bets.length) * 100).toFixed(2)),
    profit_units: Number(profit.toFixed(2)),
    average_coefficient: Number((coefficientSum / bets.length).toFixed(2)),
    max_win_streak: 0,
    max_loss_streak: 0,
    current_streak: 0,
    current_streak_type: null,
  };
}

async function loadClientTimeline(clientId: number, period: PeriodFilter) {
  return apiFetch<AdminClientTimelineResponse>(`/admin/stats/clients/${clientId}?period=${encodeURIComponent(period)}`);
}

function breakdownClientSide(
  bets: PerformanceBetItem[],
  key: 'bookmaker_names' | 'sport_type',
  emptyLabel: string,
) {
  const groups = new Map<string, { label: string; bets: PerformanceBetItem[] }>();
  bets.forEach((bet) => {
    const labels = key === 'bookmaker_names'
      ? (bet.bookmaker_names.length ? bet.bookmaker_names : [emptyLabel])
      : [bet.sport_type || emptyLabel];
    labels.forEach((label) => {
      const groupKey = label.toLowerCase();
      if (!groups.has(groupKey)) groups.set(groupKey, { label, bets: [] });
      groups.get(groupKey)!.bets.push(bet);
    });
  });
  return Array.from(groups.entries())
    .map(([keyValue, group]) => ({
      key: keyValue,
      label: group.label,
      summary: summarizeClientSide(group.bets),
    }))
    .sort((left, right) => right.summary.bets - left.summary.bets || left.label.localeCompare(right.label));
}

function filterTimelineBySource(data: PerformanceTimelineResponse | null, source: StatsTab): PerformanceTimelineResponse | null {
  if (!data || source === 'all' || source === 'clients') return data;
  const filteredBets: PerformanceBetItem[] = [];
  const nextTimeline = data.timeline
    .map((month) => {
      const days = month.days
        .map((day) => {
          const bets = day.bets.filter((bet) => bet.source_type === source);
          filteredBets.push(...bets);
          return {
            ...day,
            bets,
            summary: summarizeClientSide(bets),
          };
        })
        .filter((day) => day.bets.length > 0);
      return {
        ...month,
        days,
        summary: summarizeClientSide(days.flatMap((day) => day.bets)),
      };
    })
    .filter((month) => month.days.length > 0);
  return {
    ...data,
    summary: data.source_split[source] ?? EMPTY_SUMMARY,
    timeline: nextTimeline,
    bookmaker_breakdown: breakdownClientSide(filteredBets, 'bookmaker_names', 'Без БК'),
    sport_breakdown: breakdownClientSide(filteredBets, 'sport_type', 'Без спорта'),
  };
}

function ClientSituationBadge({ client }: { client: AdminClientStatsItem }) {
  const toneClass = {
    success: 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100',
    warning: 'border-amber-300/25 bg-amber-400/10 text-amber-100',
    danger: 'border-rose-300/25 bg-rose-400/10 text-rose-100',
    neutral: 'border-white/10 bg-white/[0.05] text-slate-300',
  }[client.situation.tone];
  return (
    <span className={`rounded-full border px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] ${toneClass}`}>
      {client.situation.label}
    </span>
  );
}

function ResultStrip({ results }: { results: Array<'win' | 'loss'> }) {
  if (!results.length) {
    return <span className="text-[10px] font-bold text-slate-600">Нет серии</span>;
  }
  return (
    <div className="flex gap-1.5">
      {results.slice(0, 8).map((result, index) => (
        <span
          key={`${result}:${index}`}
          className={`h-2.5 w-2.5 rounded-full shadow-[0_0_12px_rgba(255,255,255,0.08)] ${result === 'win' ? 'bg-emerald-300' : 'bg-rose-300'}`}
        />
      ))}
    </div>
  );
}

function ClientLeaderboard({
  clients,
  selectedClientId,
  loading,
  onOpenClient,
}: {
  clients: AdminClientStatsItem[];
  selectedClientId?: number;
  loading: boolean;
  onOpenClient: (client: AdminClientStatsItem) => void;
}) {
  if (!clients.length) {
    return (
      <div className="rounded-[26px] border border-white/10 bg-white/[0.04] p-8 text-center text-sm font-bold text-slate-500">
        Клиенты по этому запросу не найдены
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {clients.map((client, index) => {
        const active = selectedClientId === client.telegram_id;
        const needsAttention = client.situation.tone === 'danger' || (client.summary.current_streak_type === 'loss' && client.summary.current_streak >= 2);
        return (
          <button
            key={client.telegram_id}
            type="button"
            onClick={() => onOpenClient(client)}
            className={`smooth-pressable w-full overflow-hidden rounded-[24px] border p-3 text-left transition-all active:scale-[0.995] ${
              active
                ? 'border-cyan-200/35 bg-cyan-200/[0.08] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]'
                : 'border-white/10 bg-white/[0.04] hover:border-cyan-200/25 hover:bg-white/[0.065]'
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 gap-3">
                {client.photo_url ? (
                  <img
                    src={client.photo_url}
                    alt={`Фото ${client.name}`}
                    className="h-10 w-10 shrink-0 rounded-2xl border border-white/10 object-cover"
                  />
                ) : (
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-950/45 text-xs font-black text-slate-300">
                    {index + 1}
                  </div>
                )}
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="truncate text-sm font-black text-white">{client.name}</div>
                    <ClientSituationBadge client={client} />
                    {needsAttention ? (
                      <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] text-amber-100">
                        Внимание
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 truncate text-[10px] font-bold text-slate-500">
                    ID {client.telegram_id}
                    {client.username ? ` / @${client.username}` : ''}
                    {client.client_group ? ` / ${client.client_group}` : ''}
                    {client.client_tag ? ` / ${client.client_tag}` : ''}
                  </div>
                  <div className="mt-2"><ResultStrip results={client.recent_results} /></div>
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className={`text-base font-black tabular-nums ${summaryTone(client.summary)}`}>
                  {formatStatsValue(client.summary.profit_units, 'flats')}
                </div>
                <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">
                  ROI {pct(client.summary.roi)} / {client.summary.bets} ставок
                </div>
                <div className="mt-2 text-[9px] font-black uppercase tracking-[0.12em] text-cyan-200">
                  {loading && active ? 'Загрузка' : 'Открыть'}
                </div>
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ClientPulse({ clients }: { clients: AdminClientStatsItem[] }) {
  const attentionClients = clients.filter((client) => (
    client.situation.tone === 'danger'
    || (client.summary.current_streak_type === 'loss' && client.summary.current_streak >= 3)
  ));
  const bestClient = [...clients]
    .filter((client) => client.summary.bets > 0)
    .sort((left, right) => right.summary.profit_units - left.summary.profit_units)[0];
  const lossRunClient = [...clients]
    .filter((client) => client.summary.current_streak_type === 'loss')
    .sort((left, right) => right.summary.current_streak - left.summary.current_streak)[0];

  const rows = [
    {
      key: 'attention',
      label: 'Нужно внимание',
      value: attentionClients.length,
      hint: attentionClients[0]?.name ?? 'Критичных серий нет',
      tone: attentionClients.length ? 'text-rose-200' : 'text-emerald-200',
      icon: <ShieldAlert className="h-4 w-4 text-rose-300" />,
    },
    {
      key: 'best',
      label: 'Лучший клиент',
      value: bestClient ? formatStatsValue(bestClient.summary.profit_units, 'flats') : '-',
      hint: bestClient ? bestClient.name : 'Нет расчетов',
      tone: bestClient ? summaryTone(bestClient.summary) : 'text-slate-400',
      icon: <Trophy className="h-4 w-4 text-emerald-300" />,
    },
    {
      key: 'run',
      label: 'Серия минусов',
      value: lossRunClient ? streakLabel(lossRunClient.summary) : '-',
      hint: lossRunClient ? lossRunClient.name : 'Нет активной серии',
      tone: lossRunClient ? 'text-rose-200' : 'text-slate-400',
      icon: <AlertCircle className="h-4 w-4 text-amber-300" />,
    },
  ];

  return (
    <section className="grid gap-2 sm:grid-cols-3">
      {rows.map((row) => (
        <div key={row.key} className="rounded-[24px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">
            {row.icon}
            {row.label}
          </div>
          <div className={`mt-2 text-lg font-black tabular-nums ${row.tone}`}>{row.value}</div>
          <div className="mt-1 truncate text-[10px] font-bold text-slate-500">{row.hint}</div>
        </div>
      ))}
    </section>
  );
}

function ExportPanel({
  tab,
  driveScope,
  setDriveScope,
  driveLoading,
  driveJob,
  driveError,
  exportToDrive,
}: {
  tab: StatsTab;
  driveScope: StatsDriveExportScope;
  setDriveScope: (scope: StatsDriveExportScope) => void;
  driveLoading: boolean;
  driveJob: StatsDriveExportJob | null;
  driveError: string | null;
  exportToDrive: () => void;
}) {
  return (
    <section className="rounded-[26px] border border-cyan-200/15 bg-cyan-200/[0.055] p-3">
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(5.5rem,0.28fr)]">
        <div className="grid grid-cols-3 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/35 p-1">
          {(['all', 'shamrai', 'clients'] as const).map((scope) => {
            const active = (tab === 'clients' ? 'clients' : driveScope) === scope;
            return (
              <button
                key={scope}
                type="button"
                onClick={() => setDriveScope(scope)}
                disabled={tab === 'clients'}
                className={`min-h-[36px] rounded-xl px-2 text-[9px] font-black uppercase tracking-[0.08em] transition-all disabled:cursor-not-allowed ${
                  active
                    ? 'bg-cyan-200/15 text-cyan-50'
                    : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200 disabled:hover:bg-transparent disabled:hover:text-slate-500'
                }`}
              >
                {driveScopeLabels[scope]}
              </button>
            );
          })}
        </div>
        <IconActionButton title="Выгрузить на Google Диск" disabled={driveLoading} onClick={exportToDrive} tone="cyan">
          {driveLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
        </IconActionButton>
      </div>
      <ExportStatusPanel job={driveJob} error={driveError} title="Google Drive" compact />
    </section>
  );
}

interface AdminStatsProps {
  active?: boolean;
}

export default function AdminStats({ active = true }: AdminStatsProps = {}) {
  const queryClient = useQueryClient();
  const [selectedClient, setSelectedClient] = useState<AdminClientTimelineResponse | null>(null);
  const [tab, setTab] = useState<StatsTab>('all');
  const [period, setPeriod] = useState<PeriodFilter>('all');
  const [clientQuery, setClientQuery] = useState('');
  const [clientLoading, setClientLoading] = useState(false);
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null);
  const [driveScope, setDriveScope] = useState<StatsDriveExportScope>('all');
  const [driveJob, setDriveJob] = useState<StatsDriveExportJob | null>(null);
  const [driveLoading, setDriveLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [driveError, setDriveError] = useState<string | null>(null);
  const [expandedMonths, setExpandedMonths] = useState<ExpandedMap>({});
  const [expandedDays, setExpandedDays] = useState<ExpandedMap>({});
  const clientDetailRef = useRef<HTMLDivElement | null>(null);

  const statsDashboardQuery = useQuery<AdminStatsDashboardData>({
    queryKey: adminStatsDashboardQueryKey(period),
    queryFn: ({ signal }) => fetchAdminStatsDashboard(period, signal),
    enabled: active,
    staleTime: ADMIN_TAB_QUERY_STALE_TIME,
  });

  const authorData = statsDashboardQuery.data?.authorTimeline ?? null;
  const shamraiData = statsDashboardQuery.data?.shamraiTimeline ?? null;
  const clientsData = statsDashboardQuery.data?.clients ?? null;
  const bookmakers: BookmakerResponse[] = statsDashboardQuery.data?.bookmakers ?? [];
  const loading = statsDashboardQuery.isLoading || (statsDashboardQuery.isFetching && !statsDashboardQuery.data);
  const error = statsDashboardQuery.error?.message || actionError;
  const refetchStats = statsDashboardQuery.refetch;

  useEffect(() => {
    if (active) return;
    void queryClient.cancelQueries({ queryKey: adminStatsDashboardQueryKey(period), exact: true });
  }, [active, period, queryClient]);

  const loadStats = useCallback(() => {
    setActionError(null);
    void refetchStats();
  }, [refetchStats]);

  useEffect(() => {
    setActionError(null);
    setSelectedClient(null);
    setExpandedMonths({});
    setExpandedDays({});
  }, [period]);

  useEffect(() => {
    if (!active || !driveJob || driveJob.status === 'completed' || driveJob.status === 'failed') return;
    const timer = window.setInterval(async () => {
      try {
        const freshJob = await apiFetch<StatsDriveExportJob>(`/admin/stats/drive-export/${driveJob.id}`);
        setDriveJob(freshJob);
        if (freshJob.status === 'completed' || freshJob.status === 'failed') {
          setDriveLoading(false);
        }
      } catch (err: any) {
        setDriveError(err.message || 'Не удалось обновить статус Google Drive');
        setDriveLoading(false);
      }
    }, 2200);
    return () => window.clearInterval(timer);
  }, [active, driveJob]);

  const sourceTimeline = tab === 'all' ? shamraiData : authorData;
  const selectedTimeline = useMemo(() => filterTimelineBySource(sourceTimeline, tab), [sourceTimeline, tab]);
  const summary = tab === 'clients'
    ? clientsData?.summary ?? EMPTY_SUMMARY
    : selectedTimeline?.summary ?? EMPTY_SUMMARY;
  const valueMode: StatsValueMode = tab === 'clients' ? 'flats' : 'rub';
  const displayTitle = tab === 'clients'
    ? 'Клиентская статистика'
    : tab === 'all'
      ? 'Статистика Shamrai'
      : authorData?.author.name || 'Статистика Shamrai';
  const displayEyebrow = tab === 'clients'
    ? 'Клиенты'
    : tab === 'feed'
      ? 'Лента'
      : tab === 'private'
        ? 'Закрытые'
        : tab === 'paid_set'
          ? 'Наборы'
          : 'Статистика Shamrai';

  const filteredClients = useMemo(() => {
    const query = clientQuery.trim().toLowerCase();
    const clients = clientsData?.clients ?? [];
    if (!query) return clients;
    return clients.filter((client) => (
      client.name.toLowerCase().includes(query)
      || String(client.telegram_id).includes(query)
      || String(client.username || '').toLowerCase().includes(query)
      || String(client.client_group || '').toLowerCase().includes(query)
      || String(client.client_tag || '').toLowerCase().includes(query)
    ));
  }, [clientsData, clientQuery]);
  const clientBreakdown = useMemo(() => (
    clientsData?.clients.map((client) => ({
      key: String(client.telegram_id),
      label: client.name,
      summary: client.summary,
    })) ?? []
  ), [clientsData]);
  const momentumResults = useMemo(() => (
    tab === 'clients'
      ? (clientsData?.clients ?? []).flatMap((client) => client.recent_results).slice(0, 14)
      : recentResultCodes(selectedTimeline, 14)
  ), [clientsData, selectedTimeline, tab]);

  const openClient = async (client: AdminClientStatsItem) => {
    try {
      setClientLoading(true);
      const data = await loadClientTimeline(client.telegram_id, period);
      setSelectedClient(data);
      setExpandedMonths({});
      setExpandedDays({});
      window.setTimeout(() => {
        clientDetailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 80);
    } catch (err: any) {
      setActionError(err.message || 'Не удалось загрузить клиента');
    } finally {
      setClientLoading(false);
    }
  };

  const refreshAuthorTimeline = useCallback(async () => {
    const [freshTimeline, freshShamraiTimeline] = await Promise.all([
      fetchAdminAuthorTimeline(period),
      fetchAdminShamraiTimeline(period),
    ]);
    queryClient.setQueryData<AdminStatsDashboardData>(adminStatsDashboardQueryKey(period), (current) => (
      current ? { ...current, authorTimeline: freshTimeline, shamraiTimeline: freshShamraiTimeline } : current
    ));
  }, [period, queryClient]);

  const refreshSelectedClientTimeline = useCallback(async () => {
    if (!selectedClient) return;
    const freshClient = await loadClientTimeline(selectedClient.user.telegram_id, period);
    setSelectedClient(freshClient);
  }, [period, selectedClient]);

  const exportStats = async (format: 'csv' | 'xlsx') => {
    try {
      setExporting(format);
      setActionError(null);
      const scope = tab === 'clients' ? 'clients' : (format === 'xlsx' ? 'shamrai' : 'author');
      const source = tab === 'clients' ? 'all' : tab;
      const params = new URLSearchParams({ scope, format, period, source });
      await downloadApiFile(`/admin/stats/export?${params.toString()}`, `shamrai_stats_${scope}_${period}.${format}`);
    } catch (err: any) {
      setActionError(err.message || 'Не удалось скачать экспорт');
    } finally {
      setExporting(null);
    }
  };

  const exportToDrive = async () => {
    try {
      setDriveLoading(true);
      setDriveError(null);
      const scope = tab === 'clients' ? 'clients' : driveScope;
      const job = await apiFetch<StatsDriveExportJob>('/admin/stats/drive-export', {
        method: 'POST',
        body: JSON.stringify({ scope, period, formats: ['google_sheet'] }),
      });
      setDriveJob(job);
      if (job.status === 'completed' || job.status === 'failed') {
        setDriveLoading(false);
      }
    } catch (err: any) {
      setDriveError(err.message || 'Не удалось запустить выгрузку в Google Drive');
      setDriveLoading(false);
    }
  };

  if (loading) {
    return <StatsSkeleton />;
  }

  if (error) {
    return (
      <div className="mx-auto max-w-md rounded-[26px] border border-rose-500/25 bg-rose-500/10 p-8 text-center">
        <AlertCircle className="mx-auto h-8 w-8 text-rose-400" />
        <h4 className="mt-3 text-sm font-bold text-white">Ошибка соединения</h4>
        <p className="mt-2 text-xs text-slate-400">{error}</p>
        <button
          type="button"
          onClick={loadStats}
          className="mt-4 rounded-xl bg-white/10 px-4 py-2 text-xs font-bold text-white transition-all hover:bg-white/15 active:scale-[0.98]"
        >
          Попробовать снова
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-10">
      <StatsHero
        title={displayTitle}
        eyebrow={displayEyebrow}
        periodLabel={tab === 'clients' ? clientsData?.period_label ?? 'Выбранный период' : selectedTimeline?.period_label ?? 'Выбранный период'}
        summary={summary}
        valueMode={valueMode}
        controls={<PeriodSelector value={period} onChange={setPeriod} activeTone="cyan" />}
        actions={<ExportActions exporting={exporting} onCsv={() => void exportStats('csv')} onXlsx={() => void exportStats('xlsx')} />}
      />

      <MomentumStrip
        results={momentumResults}
        title={tab === 'clients' ? 'Сводный импульс клиентов' : 'Последние расчеты'}
      />

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        {([
          ['all', 'Обзор', Trophy],
          ['feed', 'Лента', BarChart3],
          ['private', 'Закрытые', Target],
          ['paid_set', 'Наборы', Sparkles],
          ['clients', 'Клиенты', Users],
        ] as const).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={`flex min-h-[44px] items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-xs font-black uppercase tracking-[0.12em] transition-all active:scale-[0.98] ${
              tab === value
                ? 'border-cyan-200/40 bg-cyan-200/15 text-cyan-50'
                : 'border-white/10 bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]'
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      <ExecutiveScoreboard summary={summary} valueMode={valueMode} />

      {tab !== 'clients' ? (
        <>
          <OverviewAnalytics
            data={selectedTimeline}
            sourceSplit={sourceTimeline?.source_split}
            bookmakerBreakdown={selectedTimeline?.bookmaker_breakdown ?? []}
            sportBreakdown={selectedTimeline?.sport_breakdown ?? []}
            valueMode={valueMode}
          />
          <TimelineSectionBlock
            data={selectedTimeline}
            expandedMonths={expandedMonths}
            expandedDays={expandedDays}
            onToggleMonth={(key) => setExpandedMonths((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
            onToggleDay={(key) => setExpandedDays((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
            valueMode={valueMode}
            title="Ставки по расчету"
            renderBet={(bet) => (
              <AdminStatsBetRow
                bet={bet}
                valueMode={valueMode}
                bookmakers={bookmakers}
                onChanged={refreshAuthorTimeline}
              />
            )}
          />
        </>
      ) : (
        <section className="space-y-4">
          <ClientPulse clients={clientsData?.clients ?? []} />

          <div className="grid gap-3 xl:grid-cols-[0.85fr_1.15fr]">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-1">
              <StatTile label="Клиентов" value={clientsData?.clients_count ?? 0} hint="в CRM" tone="text-cyan-100" />
              <StatTile label="Активных" value={clientsData?.active_clients_count ?? 0} hint="баланс или гарантия" tone="text-emerald-200" />
              <StatTile label="Со статистикой" value={clientsData?.active_clients_with_stats_count ?? 0} hint={clientsData?.period_label ?? 'срез'} tone="text-indigo-200" />
            </div>
            <BreakdownBars
              title="Топ клиентов"
              items={clientBreakdown}
              valueMode="flats"
              icon={<Users className="h-4 w-4 text-cyan-300" />}
              limit={6}
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
            <div className="space-y-3">
              <CollapsiblePanel
                title="Рейтинг клиентов"
                icon={<Users className="h-4 w-4 text-cyan-300" />}
                summary={`${filteredClients.length} клиентов`}
                actions={(
                  <label className="flex min-h-[40px] w-full min-w-0 items-center gap-2 rounded-2xl border border-white/10 bg-slate-950/40 px-3 text-xs text-slate-300 sm:w-auto sm:min-w-[220px]">
                    <Search className="h-4 w-4 text-slate-500" />
                    <input
                      value={clientQuery}
                      onChange={(event) => setClientQuery(event.target.value)}
                      placeholder="Поиск клиента"
                      className="min-w-0 flex-1 bg-transparent font-bold outline-none placeholder:text-slate-600"
                    />
                  </label>
                )}
              >
                <ClientLeaderboard
                  clients={filteredClients}
                  selectedClientId={selectedClient?.user.telegram_id}
                  loading={clientLoading}
                  onOpenClient={(client) => void openClient(client)}
                />
              </CollapsiblePanel>
            </div>

            <div ref={clientDetailRef} className="scroll-mt-4">
              {selectedClient ? (
                <div className="space-y-4 rounded-[28px] border border-cyan-200/20 bg-cyan-200/[0.055] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em] text-cyan-100">
                        <ShieldAlert className="h-4 w-4" />
                        Карточка клиента
                      </div>
                      <h3 className="mt-1 text-lg font-black text-white">{selectedClient.user.name}</h3>
                      <p className="mt-1 text-xs font-bold text-slate-400">{selectedClient.situation.description}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSelectedClient(null)}
                      className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-200 transition-all hover:bg-white/[0.1] active:scale-[0.98]"
                    >
                      Закрыть
                    </button>
                  </div>
                  {clientLoading ? (
                    <div className="flex justify-center py-6">
                      <Loader2 className="h-6 w-6 animate-spin text-cyan-200" />
                    </div>
                  ) : (
                    <>
                      <MomentumStrip results={selectedClient.recent_results} title="Импульс клиента" compact />
                      <StatsKpiGrid summary={selectedClient.summary} valueMode="flats" />
                      <OverviewAnalytics
                        data={selectedClient}
                        sourceSplit={selectedClient.source_split}
                        bookmakerBreakdown={selectedClient.bookmaker_breakdown}
                        sportBreakdown={selectedClient.sport_breakdown}
                        valueMode="flats"
                      />
                      <TimelineSectionBlock
                        data={selectedClient}
                        expandedMonths={expandedMonths}
                        expandedDays={expandedDays}
                        onToggleMonth={(key) => setExpandedMonths((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
                        onToggleDay={(key) => setExpandedDays((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
                        valueMode="flats"
                        title="Ставки клиента"
                        aside={<span className={`text-xs font-black tabular-nums ${profitTone(selectedClient.summary.profit_units)}`}>{formatStatsValue(selectedClient.summary.profit_units, 'flats')}</span>}
                        renderBet={(bet) => (
                          <AdminStatsBetRow
                            bet={bet}
                            valueMode="flats"
                            bookmakers={bookmakers}
                            onChanged={refreshSelectedClientTimeline}
                          />
                        )}
                      />
                    </>
                  )}
                </div>
              ) : (
                <div className="rounded-[28px] border border-white/10 bg-white/[0.04] p-8 text-center">
                  <Users className="mx-auto h-9 w-9 text-slate-600" />
                  <p className="mt-3 text-sm font-black text-white">Выберите клиента</p>
                  <p className="mt-1 text-xs font-bold text-slate-500">Карточка покажет итог, график и историю ставок.</p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <ExportPanel
        tab={tab}
        driveScope={driveScope}
        setDriveScope={setDriveScope}
        driveLoading={driveLoading}
        driveJob={driveJob}
        driveError={driveError}
        exportToDrive={() => void exportToDrive()}
      />
    </div>
  );
}
