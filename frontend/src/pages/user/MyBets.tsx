import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertCircle,
  BarChart3,
  RefreshCw,
  Trophy,
} from 'lucide-react';

import {
  EMPTY_SUMMARY,
  ExpandedMap,
  ExportActions,
  PeriodSelector,
  ProfitCurve,
  BreakdownBars,
  MomentumStrip,
  StatsHero,
  StatsKpiGrid,
  StatsSkeleton,
  StatTile,
  TimelineSectionBlock,
  recentResultCodes,
  streakLabel,
} from '../../features/performance/performanceUi';
import { PerformanceSummary, PerformanceTimelineResponse, PeriodFilter } from '../../schemas/schemas';
import { downloadApiFile } from '../../utils/api';
import {
  GLOBAL_STATS_QUERY_KEY,
  TAB_QUERY_STALE_TIME,
  fetchGlobalStats,
  fetchMyBetsTimeline,
  myBetsTimelineQueryKey,
  type GlobalStatsData,
} from '../../utils/tabPrefetch';

type ClientStatsView = 'shamrai' | 'mine';

function globalStatsToTimeline(stats: GlobalStatsData | null): PerformanceTimelineResponse | null {
  if (!stats) return null;

  const summary: PerformanceSummary = {
    bets: Number(stats.total_bets || 0),
    wins: Number(stats.won_bets || 0),
    losses: Number(stats.lost_bets || 0),
    winrate: Number(stats.winrate || 0),
    roi: Number(stats.roi || 0),
    profit_units: Number(stats.net_profit || 0),
    average_coefficient: Number(stats.average_coefficient || 0),
    max_win_streak: 0,
    max_loss_streak: 0,
    current_streak: 0,
    current_streak_type: null,
  };
  let previousProfit = 0;
  const days = (stats.chart_points || []).map((point, index) => {
    const profit = Number(point.profit || 0);
    const delta = Number((profit - previousProfit).toFixed(2));
    previousProfit = profit;
    return {
      key: `global-${String(index).padStart(3, '0')}`,
      label: point.month,
      summary: {
        ...EMPTY_SUMMARY,
        profit_units: delta,
      },
      bets: [],
    };
  });

  return {
    period: 'all',
    period_label: 'Все время',
    summary,
    source_split: {
      all: summary,
      feed: summary,
      private: EMPTY_SUMMARY,
      paid_set: EMPTY_SUMMARY,
    },
    timeline: days.length ? [{
      key: 'global',
      label: 'Динамика',
      summary,
      days,
    }] : [],
    bookmaker_breakdown: [],
    sport_breakdown: [],
    default_expanded_month_key: '',
    default_expanded_day_key: '',
  };
}

export default function MyBets() {
  const [expandedMonths, setExpandedMonths] = useState<ExpandedMap>({});
  const [expandedDays, setExpandedDays] = useState<ExpandedMap>({});
  const [view, setView] = useState<ClientStatsView>('shamrai');
  const [period, setPeriod] = useState<PeriodFilter>('all');
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const timelineQuery = useQuery<PerformanceTimelineResponse>({
    queryKey: myBetsTimelineQueryKey(period),
    queryFn: () => fetchMyBetsTimeline(period),
    staleTime: TAB_QUERY_STALE_TIME,
  });

  const shamraiStatsQuery = useQuery<GlobalStatsData>({
    queryKey: GLOBAL_STATS_QUERY_KEY,
    queryFn: fetchGlobalStats,
    staleTime: TAB_QUERY_STALE_TIME,
  });

  const data = timelineQuery.data ?? null;
  const loading = timelineQuery.isLoading;
  const error = timelineQuery.error?.message || exportError;
  const summary = data?.summary ?? EMPTY_SUMMARY;
  const momentumResults = recentResultCodes(data, 14);
  const shamraiTimeline = globalStatsToTimeline(shamraiStatsQuery.data ?? null);
  const shamraiSummary = shamraiTimeline?.summary ?? EMPTY_SUMMARY;

  const exportMyBets = async (format: 'csv' | 'xlsx') => {
    try {
      setExporting(format);
      setExportError(null);
      const params = new URLSearchParams({ period, format });
      await downloadApiFile(`/users/me/bets/timeline/export?${params.toString()}`, `shamrai_my_bets_${period}.${format}`);
    } catch (err: any) {
      setExportError(err.message || 'Не удалось скачать экспорт');
    } finally {
      setExporting(null);
    }
  };

  if (loading) {
    return <StatsSkeleton />;
  }

  if (error) {
    return (
      <div className="mx-auto max-w-md rounded-[26px] border border-rose-500/25 bg-rose-500/10 p-8 text-center">
        <AlertCircle className="mx-auto h-8 w-8 text-rose-400" />
        <h4 className="mt-3 text-sm font-bold text-white">Ошибка загрузки</h4>
        <p className="mt-2 text-xs text-slate-400">{error}</p>
        <button
          type="button"
          onClick={() => void timelineQuery.refetch()}
          className="mx-auto mt-4 inline-flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2 text-xs font-bold text-white transition-all hover:bg-white/15 active:scale-[0.98]"
        >
          <RefreshCw className="h-4 w-4" />
          Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-10">
      <div className="grid grid-cols-2 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/35 p-1">
        {([
          ['shamrai', 'Статистика Shamrai'],
          ['mine', 'Моя статистика'],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setView(value)}
            className={`min-h-[38px] rounded-xl px-2 text-[10px] font-black uppercase tracking-[0.08em] transition-all active:scale-[0.98] ${
              view === value
                ? 'bg-cyan-200/15 text-cyan-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]'
                : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'shamrai' ? (
        <>
          <StatsHero
            title="Статистика Shamrai"
            eyebrow="Общая платформа"
            periodLabel={shamraiTimeline?.period_label || 'Все время'}
            summary={shamraiSummary}
            valueMode="rub"
            controls={(
              <div className="rounded-2xl border border-white/10 bg-slate-950/40 px-3 py-3">
                <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.13em] text-slate-500">
                  <BarChart3 className="h-4 w-4 text-cyan-300" />
                  Верифицированные расчеты
                </div>
                <div className="mt-1 text-sm font-black text-white">{shamraiSummary.bets} ставок</div>
              </div>
            )}
            actions={<div className="min-h-[48px]" />}
          />
          <StatsKpiGrid summary={shamraiSummary} valueMode="rub" />
          <ProfitCurve data={shamraiTimeline} valueMode="rub" title="Динамика Shamrai" />
        </>
      ) : (
        <>
          <StatsHero
            title="Моя статистика"
            eyebrow="Взятые прогнозы"
            periodLabel={data?.period_label || 'Выбранный период'}
            summary={summary}
            valueMode="flats"
            controls={<PeriodSelector value={period} onChange={setPeriod} />}
            actions={<ExportActions exporting={exporting} onCsv={() => void exportMyBets('csv')} onXlsx={() => void exportMyBets('xlsx')} />}
          />

          <MomentumStrip results={momentumResults} title="Мой импульс" />

          <StatsKpiGrid summary={summary} valueMode="flats" showProfit={false} />

          <div className="grid grid-cols-2 gap-2">
            <StatTile label="Макс. серия побед" value={summary.max_win_streak} tone="text-emerald-200" />
            <StatTile
              label="Текущая серия"
              value={summary.current_streak ? streakLabel(summary) : '-'}
              tone={summary.current_streak_type === 'win' ? 'text-emerald-300' : summary.current_streak_type === 'loss' ? 'text-rose-300' : 'text-white'}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <BreakdownBars title="Букмекеры" items={data?.bookmaker_breakdown ?? []} valueMode="flats" icon={<Trophy className="h-4 w-4 text-emerald-300" />} limit={4} />
            <BreakdownBars title="Виды спорта" items={data?.sport_breakdown ?? []} valueMode="flats" icon={<Trophy className="h-4 w-4 text-cyan-300" />} limit={4} />
          </div>

          <TimelineSectionBlock
            data={data}
            expandedMonths={expandedMonths}
            expandedDays={expandedDays}
            onToggleMonth={(key) => setExpandedMonths((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
            onToggleDay={(key) => setExpandedDays((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
            valueMode="flats"
            title="История ставок"
          />

          {!data?.timeline.length ? (
            <div className="rounded-[28px] border border-white/10 bg-white/[0.04] p-8 text-center">
              <Trophy className="mx-auto h-10 w-10 text-slate-600" />
              <p className="mt-2 text-xs font-semibold text-slate-400">Пока нет купленных рассчитанных ставок</p>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
