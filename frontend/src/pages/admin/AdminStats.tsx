import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Loader2,
  Search,
  ShieldAlert,
  Target,
  TrendingDown,
  TrendingUp,
  Trophy,
  UploadCloud,
  Users,
} from 'lucide-react';

import { EMPTY_SUMMARY, MiniSummary, PeriodSelector, StatTile, pct, signed, summaryTone } from '../../features/performance/performanceUi';
import {
  AdminAuthorTimelineResponse,
  AdminClientStatsItem,
  AdminClientsStatsResponse,
  AdminClientTimelineResponse,
  PerformanceBetItem,
  PerformanceSummary,
  PerformanceTimelineResponse,
  PeriodFilter,
  StatsDriveExportJob,
  StatsDriveExportScope,
} from '../../schemas/schemas';
import { apiFetch, downloadApiFile } from '../../utils/api';

type StatsTab = 'all' | 'feed' | 'private' | 'clients';
type ExpandedMap = Record<string, boolean>;

const driveScopeLabels: Record<StatsDriveExportScope, string> = {
  all: 'Все',
  shamrai: 'Шамрай',
  clients: 'Клиенты',
};

function driveStatusLabel(job: StatsDriveExportJob | null) {
  if (!job) return null;
  if (job.status === 'completed') return 'Готово';
  if (job.status === 'failed') return 'Ошибка';
  if (job.status === 'running') return 'Создаем';
  return 'В очереди';
}

function sourceLabel(source: PerformanceBetItem['source_type']) {
  return source === 'private' ? 'Закрытая выдача' : 'Лента';
}

function formatDateTime(value: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
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

function SummaryGrid({ summary }: { summary: PerformanceSummary }) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
      <StatTile label="Ставок" value={summary.bets} hint={`${summary.wins}W / ${summary.losses}L`} minHeightClass="min-h-[82px]" />
      <StatTile label="Profit" value={`${signed(summary.profit_units)}u`} tone={summaryTone(summary)} minHeightClass="min-h-[82px]" />
      <StatTile label="ROI" value={pct(summary.roi)} tone={summary.roi >= 0 ? 'text-emerald-300' : 'text-rose-300'} minHeightClass="min-h-[82px]" />
      <StatTile label="Winrate" value={pct(summary.winrate)} tone="text-cyan-200" minHeightClass="min-h-[82px]" />
      <StatTile label="Средний КФ" value={summary.average_coefficient.toFixed(2)} tone="text-indigo-200" minHeightClass="min-h-[82px]" />
    </div>
  );
}

function BetRow({ bet }: { bet: PerformanceBetItem }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`rounded-full border px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] ${
              bet.status === 'win'
                ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100'
                : 'border-rose-300/25 bg-rose-400/10 text-rose-100'
            }`}>
              {bet.status === 'win' ? 'Win' : 'Loss'}
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] text-slate-300">
              {sourceLabel(bet.source_type)}
            </span>
            {bet.sport_type && (
              <span className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-1 text-[9px] font-bold text-slate-300">
                {bet.sport_type}
              </span>
            )}
          </div>
          <h4 className="mt-2 break-words text-sm font-black leading-snug text-white">{bet.event_name}</h4>
          <div className="mt-1 text-[10px] font-bold text-slate-500">
            Расчет: {formatDateTime(bet.resolved_at)} · КФ {bet.coefficient.toFixed(2)}
            {bet.outcome ? ` · ${bet.outcome}` : ''}
          </div>
        </div>
        <div className={`shrink-0 text-right text-base font-black ${bet.profit_units >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
          {signed(bet.profit_units)}u
        </div>
      </div>
    </div>
  );
}

function TimelineSection({
  data,
  expandedMonths,
  expandedDays,
  onToggleMonth,
  onToggleDay,
}: {
  data: PerformanceTimelineResponse | null;
  expandedMonths: ExpandedMap;
  expandedDays: ExpandedMap;
  onToggleMonth: (key: string) => void;
  onToggleDay: (key: string) => void;
}) {
  if (!data?.timeline.length) {
    return (
      <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-8 text-center text-sm font-bold text-slate-500">
        Нет рассчитанных ставок в этом срезе
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {data.timeline.map((month) => {
        const monthOpen = expandedMonths[month.key] ?? false;
        return (
          <div key={month.key} className="overflow-hidden rounded-3xl border border-white/10 bg-white/[0.04]">
            <button
              type="button"
              onClick={() => onToggleMonth(month.key)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
            >
              <div className="flex min-w-0 items-center gap-2">
                {monthOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-slate-500" />}
                <div>
                  <div className="text-sm font-black text-white">{month.label}</div>
                  <MiniSummary summary={month.summary} />
                </div>
              </div>
              <div className={`shrink-0 text-right text-sm font-black ${summaryTone(month.summary)}`}>{signed(month.summary.profit_units)}u</div>
            </button>

            {monthOpen && (
              <div className="space-y-2 border-t border-white/10 p-3">
                {month.days.map((day) => {
                  const dayOpen = expandedDays[day.key] ?? false;
                  return (
                    <div key={day.key} className="rounded-2xl border border-white/10 bg-black/15">
                      <button
                        type="button"
                        onClick={() => onToggleDay(day.key)}
                        className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          {dayOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-slate-500" />}
                          <div>
                            <div className="text-xs font-black text-white">{day.label}</div>
                            <MiniSummary summary={day.summary} />
                          </div>
                        </div>
                        {day.summary.profit_units >= 0 ? <TrendingUp className="h-4 w-4 shrink-0 text-emerald-300" /> : <TrendingDown className="h-4 w-4 shrink-0 text-rose-300" />}
                      </button>
                      {dayOpen && (
                        <div className="space-y-2 border-t border-white/10 p-2">
                          {day.bets.map((bet) => <BetRow key={bet.id} bet={bet} />)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function BreakdownList({
  title,
  items,
  icon,
}: {
  title: string;
  items: PerformanceTimelineResponse['bookmaker_breakdown'];
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-4">
      <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.14em] text-slate-400">
        {icon}
        {title}
      </h3>
      <div className="mt-3 space-y-2">
        {items.slice(0, 7).map((item) => (
          <div key={item.key} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-slate-950/30 px-3 py-2">
            <span className="text-xs font-bold text-white">{item.label}</span>
            <span className={`text-xs font-black ${summaryTone(item.summary)}`}>{signed(item.summary.profit_units)}u · ROI {pct(item.summary.roi)}</span>
          </div>
        ))}
      </div>
    </div>
  );
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

export default function AdminStats() {
  const [authorData, setAuthorData] = useState<AdminAuthorTimelineResponse | null>(null);
  const [clientsData, setClientsData] = useState<AdminClientsStatsResponse | null>(null);
  const [selectedClient, setSelectedClient] = useState<AdminClientTimelineResponse | null>(null);
  const [tab, setTab] = useState<StatsTab>('all');
  const [period, setPeriod] = useState<PeriodFilter>('all');
  const [clientQuery, setClientQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [clientLoading, setClientLoading] = useState(false);
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null);
  const [driveScope, setDriveScope] = useState<StatsDriveExportScope>('all');
  const [driveJob, setDriveJob] = useState<StatsDriveExportJob | null>(null);
  const [driveLoading, setDriveLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [driveError, setDriveError] = useState<string | null>(null);
  const [expandedMonths, setExpandedMonths] = useState<ExpandedMap>({});
  const [expandedDays, setExpandedDays] = useState<ExpandedMap>({});
  const clientDetailRef = useRef<HTMLDivElement | null>(null);

  const loadStats = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setSelectedClient(null);
      const periodQuery = `period=${encodeURIComponent(period)}`;
      const [authorTimeline, clients] = await Promise.all([
        apiFetch<AdminAuthorTimelineResponse>(`/admin/stats/author-timeline?${periodQuery}`),
        apiFetch<AdminClientsStatsResponse>(`/admin/stats/clients?${periodQuery}`),
      ]);
      setAuthorData(authorTimeline);
      setClientsData(clients);
      setExpandedMonths({
        [authorTimeline.default_expanded_month_key]: true,
      });
      setExpandedDays({
        [authorTimeline.default_expanded_day_key]: true,
      });
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки статистики');
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  useEffect(() => {
    if (!driveJob || driveJob.status === 'completed' || driveJob.status === 'failed') return;
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
  }, [driveJob]);

  const selectedTimeline = useMemo(() => filterTimelineBySource(authorData, tab), [authorData, tab]);
  const summary = tab === 'clients'
    ? clientsData?.summary ?? EMPTY_SUMMARY
    : selectedTimeline?.summary ?? EMPTY_SUMMARY;

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

  const openClient = async (client: AdminClientStatsItem) => {
    try {
      setClientLoading(true);
      const data = await apiFetch<AdminClientTimelineResponse>(`/admin/stats/clients/${client.telegram_id}?period=${encodeURIComponent(period)}`);
      setSelectedClient(data);
      setExpandedMonths((current) => ({ ...current, [data.default_expanded_month_key]: true }));
      setExpandedDays((current) => ({ ...current, [data.default_expanded_day_key]: true }));
      window.setTimeout(() => {
        clientDetailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 80);
    } catch (err: any) {
      setError(err.message || 'Не удалось загрузить клиента');
    } finally {
      setClientLoading(false);
    }
  };

  const exportStats = async (format: 'csv' | 'xlsx') => {
    try {
      setExporting(format);
      setError(null);
      const scope = tab === 'clients' ? 'clients' : 'author';
      const source = tab === 'clients' ? 'all' : tab;
      const params = new URLSearchParams({
        scope,
        format,
        period,
        source,
      });
      await downloadApiFile(`/admin/stats/export?${params.toString()}`, `shamrai_stats_${scope}_${period}.${format}`);
    } catch (err: any) {
      setError(err.message || 'Не удалось скачать экспорт');
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
        body: JSON.stringify({
          scope,
          period,
          formats: ['xlsx', 'google_sheet'],
        }),
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
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center space-y-3">
        <Loader2 className="h-8 w-8 animate-spin text-indigo-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Собираем статистику...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-md rounded-2xl border border-rose-500/25 bg-rose-500/10 p-8 text-center">
        <AlertCircle className="mx-auto h-8 w-8 text-rose-400" />
        <h4 className="mt-3 text-sm font-bold text-white">Ошибка соединения</h4>
        <p className="mt-2 text-xs text-slate-400">{error}</p>
        <button
          type="button"
          onClick={loadStats}
          className="mt-4 rounded-xl bg-white/10 px-4 py-2 text-xs font-bold text-white transition-all hover:bg-white/15"
        >
          Попробовать снова
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-10">
      <section className="rounded-3xl border border-white/10 bg-white/[0.045] p-4 shadow-glass backdrop-blur-xl">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em] text-cyan-200">
              <TrendingUp className="h-4 w-4" />
              Статистика автора
            </div>
            <h2 className="mt-1 text-xl font-black text-white">{authorData?.author.name || 'Автор'}</h2>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-[440px]">
            <PeriodSelector value={period} onChange={setPeriod} activeTone="cyan" />
            <div className="grid grid-cols-[1fr_auto_auto] gap-2">
              <div className={`rounded-2xl border border-white/10 bg-slate-950/45 px-3 py-2 text-right ${summaryTone(summary)}`}>
                <div className="text-lg font-black">{signed(summary.profit_units)}u</div>
                <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{authorData?.period_label || 'выбранный срез'}</div>
              </div>
              <button
                type="button"
                onClick={() => void exportStats('csv')}
                disabled={exporting !== null}
                title="Скачать CSV"
                className="inline-flex min-h-[48px] min-w-[54px] items-center justify-center rounded-2xl border border-white/10 bg-white/[0.05] text-[10px] font-black uppercase tracking-[0.1em] text-slate-200 transition-all hover:bg-white/[0.09] disabled:opacity-50"
              >
                <Download className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => void exportStats('xlsx')}
                disabled={exporting !== null}
                title="Скачать XLSX"
                className="inline-flex min-h-[48px] min-w-[54px] items-center justify-center rounded-2xl border border-emerald-300/20 bg-emerald-300/10 text-emerald-100 transition-all hover:bg-emerald-300/15 disabled:opacity-50"
              >
                <FileSpreadsheet className="h-4 w-4" />
              </button>
            </div>
            <div className="rounded-2xl border border-cyan-200/15 bg-cyan-200/[0.055] p-2">
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <div className="grid grid-cols-3 overflow-hidden rounded-xl border border-white/10 bg-slate-950/35 p-1">
                  {(['all', 'shamrai', 'clients'] as const).map((scope) => {
                    const active = (tab === 'clients' ? 'clients' : driveScope) === scope;
                    return (
                      <button
                        key={scope}
                        type="button"
                        onClick={() => setDriveScope(scope)}
                        disabled={tab === 'clients'}
                        className={`min-h-[34px] rounded-lg px-2 text-[9px] font-black uppercase tracking-[0.08em] transition-all disabled:cursor-not-allowed ${
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
                <button
                  type="button"
                  onClick={() => void exportToDrive()}
                  disabled={driveLoading}
                  title="Выгрузить на Google Диск"
                  className="inline-flex min-h-[44px] min-w-[54px] items-center justify-center rounded-xl border border-cyan-200/25 bg-cyan-200/12 text-cyan-50 transition-all hover:bg-cyan-200/18 disabled:opacity-50"
                >
                  {driveLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                </button>
              </div>
              {(driveJob || driveError) && (
                <div className="mt-2 rounded-xl border border-white/10 bg-slate-950/35 px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      {driveJob?.status === 'completed' ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-300" />
                      ) : driveJob?.status === 'failed' || driveError ? (
                        <AlertCircle className="h-4 w-4 shrink-0 text-rose-300" />
                      ) : (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-cyan-200" />
                      )}
                      <span className="truncate text-[10px] font-black uppercase tracking-[0.1em] text-slate-300">
                        {driveError || driveJob?.error || driveStatusLabel(driveJob)}
                      </span>
                    </div>
                    {driveJob?.status === 'completed' && (
                      <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.1em] text-emerald-200">
                        Drive
                      </span>
                    )}
                  </div>
                  {driveJob?.links?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {driveJob.links.slice(0, 6).map((link) => (
                        <a
                          key={`${link.format}:${link.id}`}
                          href={link.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex max-w-full items-center gap-1 rounded-lg border border-white/10 bg-white/[0.06] px-2 py-1 text-[9px] font-bold text-cyan-100 hover:bg-white/[0.1]"
                        >
                          <ExternalLink className="h-3 w-3 shrink-0" />
                          <span className="truncate">{link.title}</span>
                        </a>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
          {([
            ['all', 'Общее', Trophy],
            ['feed', 'Лента', BarChart3],
            ['private', 'Закрытые', Target],
            ['clients', 'Клиенты', Users],
          ] as const).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              className={`flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-xs font-black uppercase tracking-[0.12em] transition-all ${
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

        <div className="mt-4">
          <SummaryGrid summary={summary} />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
          <div className="rounded-2xl border border-white/10 bg-slate-950/30 p-3">
            <div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-500">Лента</div>
            <MiniSummary summary={authorData?.source_split.feed ?? EMPTY_SUMMARY} />
          </div>
          <div className="rounded-2xl border border-white/10 bg-slate-950/30 p-3">
            <div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-500">Закрытые</div>
            <MiniSummary summary={authorData?.source_split.private ?? EMPTY_SUMMARY} />
          </div>
          <div className="rounded-2xl border border-white/10 bg-slate-950/30 p-3">
            <div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-500">Клиенты</div>
            <MiniSummary summary={clientsData?.summary ?? EMPTY_SUMMARY} />
          </div>
        </div>
      </section>

      {tab !== 'clients' ? (
        <>
          <section className="space-y-3">
            <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.16em] text-slate-400">
              <CalendarDays className="h-4 w-4 text-cyan-300" />
              Группировка по дате расчета
            </h3>
            <TimelineSection
              data={selectedTimeline}
              expandedMonths={expandedMonths}
              expandedDays={expandedDays}
              onToggleMonth={(key) => setExpandedMonths((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
              onToggleDay={(key) => setExpandedDays((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
            />
          </section>

          <section className="grid gap-3 lg:grid-cols-2">
            <BreakdownList title="По БК" items={selectedTimeline?.bookmaker_breakdown ?? []} icon={<BarChart3 className="h-4 w-4 text-emerald-300" />} />
            <BreakdownList title="По спорту" items={selectedTimeline?.sport_breakdown ?? []} icon={<BarChart3 className="h-4 w-4 text-cyan-300" />} />
          </section>
        </>
      ) : (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.16em] text-slate-400">
              <Users className="h-4 w-4 text-cyan-300" />
              Клиентские ситуации
            </h3>
            <p className="max-w-md text-[10px] font-bold leading-relaxed text-slate-500">
              Нажмите на клиента, чтобы открыть его ставки: событие, исход, коэффициент, win/loss, ROI и прибыль по дням.
            </p>
            <label className="flex min-h-[40px] min-w-[240px] items-center gap-2 rounded-2xl border border-white/10 bg-slate-950/40 px-3 text-xs text-slate-300">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={clientQuery}
                onChange={(event) => setClientQuery(event.target.value)}
                placeholder="Поиск клиента"
                className="min-w-0 flex-1 bg-transparent font-bold outline-none placeholder:text-slate-600"
              />
            </label>
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <StatTile label="Клиентов" value={clientsData?.clients_count ?? 0} hint="в CRM" tone="text-cyan-100" />
            <StatTile label="Активных" value={clientsData?.active_clients_count ?? 0} hint="баланс или гарантия" tone="text-emerald-200" />
            <StatTile label="Со статистикой" value={clientsData?.active_clients_with_stats_count ?? 0} hint={clientsData?.period_label ?? 'срез'} tone="text-indigo-200" />
          </div>

          <div className="space-y-2">
            {filteredClients.map((client) => (
              <button
                key={client.telegram_id}
                type="button"
                onClick={() => void openClient(client)}
                className="w-full rounded-3xl border border-white/10 bg-white/[0.04] p-3 text-left transition-all hover:border-cyan-200/25 hover:bg-white/[0.065]"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-black text-white">{client.name}</div>
                      <ClientSituationBadge client={client} />
                    </div>
                    <div className="mt-1 text-[10px] font-bold text-slate-500">
                      ID {client.telegram_id}
                      {client.username ? ` · @${client.username}` : ''}
                      {client.client_group ? ` · ${client.client_group}` : ''}
                      {client.client_tag ? ` · ${client.client_tag}` : ''}
                    </div>
                    <div className="mt-2 flex gap-1.5">
                      {client.recent_results.map((result, index) => (
                        <span
                          key={`${client.telegram_id}:${index}`}
                          className={`h-2.5 w-2.5 rounded-full ${result === 'win' ? 'bg-emerald-300' : 'bg-rose-300'}`}
                        />
                      ))}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className={`text-base font-black ${summaryTone(client.summary)}`}>{signed(client.summary.profit_units)}u</div>
                    <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">
                      ROI {pct(client.summary.roi)} · {client.summary.bets} ставок
                    </div>
                    <div className="mt-2 text-[9px] font-black uppercase tracking-[0.12em] text-cyan-200">
                      Открыть ставки
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {selectedClient && (
            <div ref={clientDetailRef} className="scroll-mt-4 rounded-3xl border border-cyan-200/20 bg-cyan-200/[0.055] p-4">
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
                  className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-200"
                >
                  Закрыть
                </button>
              </div>
              {clientLoading ? (
                <div className="flex justify-center py-6">
                  <Loader2 className="h-6 w-6 animate-spin text-cyan-200" />
                </div>
              ) : (
                <div className="mt-4 space-y-4">
                  <SummaryGrid summary={selectedClient.summary} />
                  <TimelineSection
                    data={selectedClient}
                    expandedMonths={expandedMonths}
                    expandedDays={expandedDays}
                    onToggleMonth={(key) => setExpandedMonths((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
                    onToggleDay={(key) => setExpandedDays((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
                  />
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
