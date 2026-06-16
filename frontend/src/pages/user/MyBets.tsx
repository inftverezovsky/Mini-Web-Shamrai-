import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  Download,
  FileSpreadsheet,
  Loader2,
  Target,
  TrendingDown,
  TrendingUp,
  Trophy,
} from 'lucide-react';

import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import { EMPTY_SUMMARY, MiniSummary, PeriodSelector, StatTile, pct, signed, summaryTone } from '../../features/performance/performanceUi';
import { PerformanceBetItem, PerformanceSummary, PerformanceTimelineResponse, PeriodFilter } from '../../schemas/schemas';
import { apiFetch, downloadApiFile } from '../../utils/api';

type ExpandedMap = Record<string, boolean>;

function resultLabel(status: PerformanceBetItem['status']) {
  return status === 'win' ? 'Выигрыш' : 'Проигрыш';
}

function resultClass(status: PerformanceBetItem['status']) {
  return status === 'win'
    ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100'
    : 'border-rose-300/25 bg-rose-400/10 text-rose-100';
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

function SummaryStrip({ summary }: { summary: PerformanceSummary }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <StatTile label="Ставок" value={summary.bets} hint={`${summary.wins}W / ${summary.losses}L`} />
      <StatTile label="Прибыль" value={`${signed(summary.profit_units)}u`} tone={summaryTone(summary)} />
      <StatTile label="ROI" value={pct(summary.roi)} tone={summary.roi >= 0 ? 'text-emerald-300' : 'text-rose-300'} />
      <StatTile label="Winrate" value={pct(summary.winrate)} tone="text-cyan-200" />
    </div>
  );
}

function BetRow({ bet }: { bet: PerformanceBetItem }) {
  const profitTone = bet.profit_units >= 0 ? 'text-emerald-300' : 'text-rose-300';
  return (
    <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="break-words text-sm font-black leading-snug text-white">{bet.event_name}</h4>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {bet.bookmakers.map((bookmaker) => (
              <span
                key={bookmaker.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-slate-900/70 py-0.5 pl-1 pr-2 text-[9px] font-bold text-slate-200"
              >
                <BookmakerLogoFrame bookmaker={bookmaker as any} size="badge" className="rounded-full" />
                {bookmaker.name}
              </span>
            ))}
            {bet.sport_type && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-slate-900/70 py-0.5 pl-1 pr-2 text-[9px] font-bold text-slate-200">
                <SportIconFrame label={bet.sport_type} size="compact" className="rounded-full" />
                {bet.sport_type}
              </span>
            )}
            <span className={`rounded-full border px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] ${resultClass(bet.status)}`}>
              {resultLabel(bet.status)}
            </span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-base font-black ${profitTone}`}>{signed(bet.profit_units)}u</div>
          <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">КФ {bet.coefficient.toFixed(2)}</div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] font-bold text-slate-500">
        <span>Расчет: {formatDateTime(bet.resolved_at)}</span>
        {bet.outcome && <span className="text-slate-400">Исход: {bet.outcome}</span>}
        <span>{bet.source_type === 'private' ? 'Закрытая выдача' : 'Лента'}</span>
      </div>
    </div>
  );
}

export default function MyBets() {
  const [data, setData] = useState<PerformanceTimelineResponse | null>(null);
  const [period, setPeriod] = useState<PeriodFilter>('all');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedMonths, setExpandedMonths] = useState<ExpandedMap>({});
  const [expandedDays, setExpandedDays] = useState<ExpandedMap>({});

  const loadMyBets = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const timeline = await apiFetch<PerformanceTimelineResponse>(`/users/me/bets/timeline?period=${encodeURIComponent(period)}`);
      setData(timeline);
      setExpandedMonths({
        [timeline.default_expanded_month_key]: true,
      });
      setExpandedDays({
        [timeline.default_expanded_day_key]: true,
      });
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки ставок');
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    void loadMyBets();
  }, [loadMyBets]);

  const summary = data?.summary ?? EMPTY_SUMMARY;
  const bestBookmaker = useMemo(() => {
    return data?.bookmaker_breakdown
      ?.filter((item) => item.summary.bets > 0)
      .sort((a, b) => b.summary.roi - a.summary.roi)[0];
  }, [data]);

  const exportMyBets = async (format: 'csv' | 'xlsx') => {
    try {
      setExporting(format);
      setError(null);
      const params = new URLSearchParams({ period, format });
      await downloadApiFile(`/users/me/bets/timeline/export?${params.toString()}`, `shamrai_my_bets_${period}.${format}`);
    } catch (err: any) {
      setError(err.message || 'Не удалось скачать экспорт');
    } finally {
      setExporting(null);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center space-y-3">
        <Loader2 className="h-7 w-7 animate-spin text-emerald-500" />
        <span className="text-xs text-slate-400">Собираем вашу статистику...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center space-y-2 p-6 text-center text-xs text-rose-400">
        <AlertCircle className="h-8 w-8" />
        <span>Ошибка: {error}</span>
        <button type="button" onClick={loadMyBets} className="text-indigo-300 underline">Повторить</button>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-10">
      <section className="rounded-3xl border border-white/10 bg-white/[0.045] p-4 shadow-glass backdrop-blur-xl">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em] text-emerald-200">
              <CircleDollarSign className="h-4 w-4" />
              Моя эффективность
            </div>
            <h2 className="mt-1 text-xl font-black text-white">Купленные прогнозы</h2>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-[380px]">
            <PeriodSelector value={period} onChange={setPeriod} />
            <div className="grid grid-cols-[1fr_auto_auto] gap-2">
              <div className={`rounded-2xl border border-white/10 bg-slate-950/45 px-3 py-2 text-right ${summaryTone(summary)}`}>
                <div className="text-lg font-black">{signed(summary.profit_units)}u</div>
                <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{data?.period_label || 'profit'}</div>
              </div>
              <button
                type="button"
                onClick={() => void exportMyBets('csv')}
                disabled={exporting !== null}
                title="Скачать CSV"
                className="inline-flex min-h-[48px] min-w-[54px] items-center justify-center rounded-2xl border border-white/10 bg-white/[0.05] text-slate-200 transition-all hover:bg-white/[0.09] disabled:opacity-50"
              >
                <Download className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => void exportMyBets('xlsx')}
                disabled={exporting !== null}
                title="Скачать XLSX"
                className="inline-flex min-h-[48px] min-w-[54px] items-center justify-center rounded-2xl border border-emerald-300/20 bg-emerald-300/10 text-emerald-100 transition-all hover:bg-emerald-300/15 disabled:opacity-50"
              >
                <FileSpreadsheet className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        <div className="mt-4">
          <SummaryStrip summary={summary} />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <StatTile label="Средний КФ" value={summary.average_coefficient.toFixed(2)} tone="text-indigo-200" />
          <StatTile
            label="Текущая серия"
            value={summary.current_streak ? `${summary.current_streak} ${summary.current_streak_type === 'win' ? 'W' : 'L'}` : '—'}
            tone={summary.current_streak_type === 'win' ? 'text-emerald-300' : summary.current_streak_type === 'loss' ? 'text-rose-300' : 'text-white'}
          />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div className="rounded-2xl border border-white/10 bg-slate-950/30 p-3">
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
              <TrendingUp className="h-3.5 w-3.5 text-emerald-300" />
              Лента
            </div>
            <MiniSummary summary={data?.source_split.feed ?? EMPTY_SUMMARY} />
          </div>
          <div className="rounded-2xl border border-white/10 bg-slate-950/30 p-3">
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
              <Target className="h-3.5 w-3.5 text-cyan-300" />
              Закрытые
            </div>
            <MiniSummary summary={data?.source_split.private ?? EMPTY_SUMMARY} />
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.16em] text-slate-400">
            <CalendarDays className="h-4 w-4 text-cyan-300" />
            История по расчету
          </h3>
          {bestBookmaker && (
            <span className="text-[10px] font-bold text-slate-500">Лучший БК: {bestBookmaker.label}</span>
          )}
        </div>

        {!data?.timeline.length ? (
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-8 text-center">
            <Trophy className="mx-auto h-10 w-10 text-slate-600" />
            <p className="mt-2 text-xs font-semibold text-slate-400">Пока нет купленных рассчитанных ставок</p>
          </div>
        ) : (
          data.timeline.map((month) => {
            const monthOpen = expandedMonths[month.key] ?? false;
            return (
              <div key={month.key} className="overflow-hidden rounded-3xl border border-white/10 bg-white/[0.04]">
                <button
                  type="button"
                  onClick={() => setExpandedMonths((current) => ({ ...current, [month.key]: !monthOpen }))}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {monthOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-slate-500" />}
                    <div>
                      <div className="text-sm font-black text-white">{month.label}</div>
                      <MiniSummary summary={month.summary} />
                    </div>
                  </div>
                  <div className={`shrink-0 text-right text-sm font-black ${summaryTone(month.summary)}`}>
                    {signed(month.summary.profit_units)}u
                  </div>
                </button>

                {monthOpen && (
                  <div className="space-y-2 border-t border-white/10 p-3">
                    {month.days.map((day) => {
                      const dayOpen = expandedDays[day.key] ?? false;
                      return (
                        <div key={day.key} className="rounded-2xl border border-white/10 bg-black/15">
                          <button
                            type="button"
                            onClick={() => setExpandedDays((current) => ({ ...current, [day.key]: !dayOpen }))}
                            className="flex w-full items-center justify-between gap-2 px-3 py-3 text-left"
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
          })
        )}
      </section>

      {Boolean(data?.sport_breakdown.length || data?.bookmaker_breakdown.length) && (
        <section className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-4">
            <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.14em] text-slate-400">
              <BarChart3 className="h-4 w-4 text-cyan-300" />
              По спорту
            </h3>
            <div className="mt-3 space-y-2">
              {data?.sport_breakdown.slice(0, 5).map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-slate-950/30 px-3 py-2">
                  <span className="text-xs font-bold text-white">{item.label}</span>
                  <span className={`text-xs font-black ${summaryTone(item.summary)}`}>{signed(item.summary.profit_units)}u · ROI {pct(item.summary.roi)}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-4">
            <h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.14em] text-slate-400">
              <BarChart3 className="h-4 w-4 text-emerald-300" />
              По БК
            </h3>
            <div className="mt-3 space-y-2">
              {data?.bookmaker_breakdown.slice(0, 5).map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-slate-950/30 px-3 py-2">
                  <span className="text-xs font-bold text-white">{item.label}</span>
                  <span className={`text-xs font-black ${summaryTone(item.summary)}`}>{signed(item.summary.profit_units)}u · ROI {pct(item.summary.roi)}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
