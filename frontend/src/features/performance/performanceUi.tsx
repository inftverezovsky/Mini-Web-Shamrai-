import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Loader2,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';

import {
  PerformanceBetItem,
  PerformanceBreakdownItem,
  PerformanceSummary,
  PerformanceTimelineResponse,
  PeriodFilter,
  StatsDriveExportJob,
} from '../../schemas/schemas';
import SmoothCollapse from '../../components/SmoothCollapse';

export type StatsValueMode = 'rub' | 'flats';
export type ExpandedMap = Record<string, boolean>;

export const STATS_UNIT_STAKE_RUB = 10000;

export const PERIOD_OPTIONS: Array<{ value: PeriodFilter; label: string }> = [
  { value: 'week', label: 'Неделя' },
  { value: 'month', label: 'Месяц' },
  { value: 'quarter', label: 'Квартал' },
  { value: 'all', label: 'Все' },
];

export const EMPTY_SUMMARY: PerformanceSummary = {
  bets: 0,
  wins: 0,
  losses: 0,
  winrate: 0,
  roi: 0,
  profit_units: 0,
  average_coefficient: 0,
  max_win_streak: 0,
  max_loss_streak: 0,
  current_streak: 0,
  current_streak_type: null,
};

export function signed(value: number, suffix = '') {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}${suffix}`;
}

export function flats(value: number) {
  return signed(value, ' фл.');
}

export function rublesFromUnits(value: number) {
  const amount = value * STATS_UNIT_STAKE_RUB;
  return `${amount > 0 ? '+' : ''}${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(amount)} ₽`;
}

export function formatStatsValue(value: number, mode: StatsValueMode) {
  return mode === 'flats' ? flats(value) : rublesFromUnits(value);
}

export function pct(value: number) {
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}

export function summaryTone(summary: PerformanceSummary) {
  if (summary.profit_units > 0) return 'text-emerald-300';
  if (summary.profit_units < 0) return 'text-rose-300';
  return 'text-slate-200';
}

export function profitTone(value: number) {
  if (value > 0) return 'text-emerald-300';
  if (value < 0) return 'text-rose-300';
  return 'text-slate-200';
}

export function formatDateTime(value: string | null) {
  if (!value) return '-';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function sourceLabel(source: PerformanceBetItem['source_type']) {
  if (source === 'paid_set') return 'Набор';
  return source === 'private' ? 'Закрытая' : 'Лента';
}

export function resultLabel(status: 'win' | 'loss') {
  return status === 'win' ? 'Победа' : 'Неудача';
}

function pluralRu(value: number, one: string, few: string, many: string) {
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function resultCountLabel(status: 'win' | 'loss', count: number) {
  return status === 'win'
    ? `${count} ${pluralRu(count, 'победа', 'победы', 'побед')}`
    : `${count} ${pluralRu(count, 'неудача', 'неудачи', 'неудач')}`;
}

export function streakLabel(summary: Pick<PerformanceSummary, 'current_streak' | 'current_streak_type'>) {
  if (!summary.current_streak || !summary.current_streak_type) return 'Нет серии';
  return `${summary.current_streak} ${resultLabel(summary.current_streak_type)}`;
}

export function buildProfitCurvePoints(data: PerformanceTimelineResponse | null) {
  const days = (data?.timeline ?? [])
    .flatMap((month) => month.days.map((day) => ({ key: day.key, label: day.label, profit: day.summary.profit_units })))
    .sort((left, right) => left.key.localeCompare(right.key));
  let cumulative = 0;
  return days.map((day) => {
    cumulative += Number(day.profit || 0);
    return { ...day, value: Number(cumulative.toFixed(2)) };
  });
}

export function flattenTimelineBets(data: PerformanceTimelineResponse | null) {
  return (data?.timeline ?? [])
    .flatMap((month) => month.days.flatMap((day) => day.bets))
    .sort((left, right) => new Date(right.resolved_at).getTime() - new Date(left.resolved_at).getTime());
}

export function recentResultCodes(data: PerformanceTimelineResponse | null, limit = 14): Array<'win' | 'loss'> {
  return flattenTimelineBets(data).slice(0, limit).map((bet) => bet.status);
}

function bestBreakdownItem(items: PerformanceBreakdownItem[]) {
  return [...items]
    .filter((item) => item.summary.bets > 0)
    .sort((left, right) => (
      right.summary.profit_units - left.summary.profit_units
      || right.summary.roi - left.summary.roi
      || right.summary.bets - left.summary.bets
    ))[0];
}

function weakBreakdownItem(items: PerformanceBreakdownItem[]) {
  return [...items]
    .filter((item) => item.summary.bets > 0)
    .sort((left, right) => (
      left.summary.profit_units - right.summary.profit_units
      || left.summary.roi - right.summary.roi
      || right.summary.bets - left.summary.bets
    ))[0];
}

export function PeriodSelector({
  value,
  onChange,
  activeTone = 'emerald',
}: {
  value: PeriodFilter;
  onChange: (value: PeriodFilter) => void;
  activeTone?: 'emerald' | 'cyan';
}) {
  const activeClass = activeTone === 'cyan'
    ? 'bg-cyan-200/15 text-cyan-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]'
    : 'bg-emerald-300/15 text-emerald-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]';

  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-white/10 bg-slate-950/35 p-1 sm:grid-cols-4">
      {PERIOD_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`min-h-[30px] rounded-lg px-1.5 text-[9px] font-black uppercase tracking-[0.08em] transition-all active:scale-[0.98] ${
            value === option.value
              ? activeClass
              : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = 'text-white',
  minHeightClass = 'min-h-[60px]',
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: string;
  minHeightClass?: string;
}) {
  return (
    <div className={`${minHeightClass} min-w-0 transform-gpu rounded-xl border border-white/10 bg-slate-950/35 px-2.5 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] will-change-transform`}>
      <div className="min-w-0 break-words text-[8px] font-black uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className={`mt-0.5 min-w-0 break-words text-base font-black tabular-nums ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 min-w-0 break-words text-[8px] font-bold uppercase tracking-[0.06em] text-slate-600">{hint}</div>}
    </div>
  );
}

function ExecutiveMetric({
  label,
  value,
  hint,
  tone = 'text-white',
  icon,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="smooth-surface relative min-h-[86px] min-w-0 transform-gpu overflow-hidden rounded-2xl border border-white/10 bg-slate-950/35 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.055)] will-change-transform">
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-white/18 to-transparent" />
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 break-words text-[8px] font-black uppercase tracking-[0.12em] text-slate-500">{label}</div>
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-xl border border-white/10 bg-white/[0.045] text-slate-300">
          {icon}
        </span>
      </div>
      <div className={`mt-2 min-w-0 break-words text-xl font-black leading-none tabular-nums ${tone}`}>{value}</div>
      {hint ? <div className="mt-1 min-w-0 break-words text-[9px] font-bold text-slate-500">{hint}</div> : null}
    </div>
  );
}

export function ExecutiveScoreboard({
  summary,
  valueMode,
}: {
  summary: PerformanceSummary;
  valueMode: StatsValueMode;
}) {
  const positive = summary.profit_units >= 0;
  const volumeLabel = valueMode === 'rub'
    ? `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(summary.bets * STATS_UNIT_STAKE_RUB)} ₽`
    : `${summary.bets} ставок`;
  const riskLabel = summary.current_streak_type === 'loss' && summary.current_streak >= 2
    ? `${summary.current_streak} минуса подряд`
    : streakLabel(summary);

  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
      <ExecutiveMetric
        label="Прибыль"
        value={formatStatsValue(summary.profit_units, valueMode)}
        hint={positive ? 'плюсовая зона' : 'нужно внимание'}
        tone={summaryTone(summary)}
        icon={positive ? <TrendingUp className="h-4 w-4 text-emerald-300" /> : <TrendingDown className="h-4 w-4 text-rose-300" />}
      />
      <ExecutiveMetric
        label="ROI"
        value={pct(summary.roi)}
        hint="эффективность среза"
        tone={summary.roi >= 0 ? 'text-emerald-200' : 'text-rose-200'}
        icon={<Target className="h-4 w-4 text-cyan-200" />}
      />
      <ExecutiveMetric
        label="Проход"
        value={pct(summary.winrate)}
        hint={`${resultCountLabel('win', summary.wins)} / ${resultCountLabel('loss', summary.losses)}`}
        tone="text-cyan-100"
        icon={<Activity className="h-4 w-4 text-cyan-200" />}
      />
      <ExecutiveMetric
        label="Объем"
        value={volumeLabel}
        hint={`${summary.bets} ставок`}
        tone="text-slate-100"
        icon={<BarChart3 className="h-4 w-4 text-slate-300" />}
      />
      <ExecutiveMetric
        label="Серия"
        value={riskLabel}
        hint={`макс. побед ${summary.max_win_streak} / минус ${summary.max_loss_streak}`}
        tone={summary.current_streak_type === 'loss' ? 'text-rose-200' : summary.current_streak_type === 'win' ? 'text-emerald-200' : 'text-slate-200'}
        icon={<Sparkles className="h-4 w-4 text-amber-200" />}
      />
    </div>
  );
}

export function CollapsiblePanel({
  title,
  icon,
  children,
  summary,
  actions,
  defaultOpen = false,
  className = '',
  bodyClassName = 'mt-3',
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  summary?: React.ReactNode;
  actions?: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  bodyClassName?: string;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const reduceMotion = useReducedMotion();

  return (
    <section
      className={`smooth-surface transform-gpu rounded-[22px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] will-change-transform ${className}`}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          aria-expanded={open}
          aria-label={`${open ? 'Скрыть' : 'Показать'} ${title}`}
          onClick={() => setOpen((current) => !current)}
          className="group smooth-pressable flex min-w-0 flex-1 items-center justify-between gap-2 rounded-xl text-left transition-all"
        >
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-2 text-xs font-black text-white">
              {icon}
              <span className="truncate">{title}</span>
            </span>
            {summary ? (
              <span className="min-w-0 text-[9px] font-bold text-slate-500">
                {summary}
              </span>
            ) : null}
          </span>
          <span
            aria-hidden="true"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-slate-950/45 text-slate-300 transition-all group-hover:border-cyan-200/25 group-hover:text-cyan-100"
          >
            <motion.span
              animate={reduceMotion ? undefined : { rotate: open ? 180 : 0 }}
              transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
              className="grid place-items-center"
            >
              <ChevronDown className="h-3.5 w-3.5" />
            </motion.span>
          </span>
        </button>
        {actions ? (
          <div className="shrink-0" onClick={(event) => event.stopPropagation()}>
            {actions}
          </div>
        ) : null}
      </div>
      <SmoothCollapse open={open} className={bodyClassName}>
        {children}
      </SmoothCollapse>
    </section>
  );
}

export function MiniSummary({
  summary,
  valueMode = 'flats',
  gapClass = 'gap-2',
}: {
  summary: PerformanceSummary;
  valueMode?: StatsValueMode;
  gapClass?: string;
}) {
  return (
    <div className={`flex flex-wrap items-center ${gapClass} text-[8.5px] font-black uppercase tracking-[0.08em]`}>
      <span className="rounded-md border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-slate-300">{summary.bets} ставок</span>
      <span className="rounded-md border border-emerald-300/20 bg-emerald-300/10 px-1.5 py-0.5 text-emerald-100">{resultCountLabel('win', summary.wins)}</span>
      <span className="rounded-md border border-rose-300/20 bg-rose-300/10 px-1.5 py-0.5 text-rose-100">{resultCountLabel('loss', summary.losses)}</span>
      <span className="rounded-md border border-cyan-300/20 bg-cyan-300/10 px-1.5 py-0.5 text-cyan-100">Проход {pct(summary.winrate)}</span>
      <span className={`rounded-md border border-white/10 bg-white/[0.05] px-1.5 py-0.5 ${summaryTone(summary)}`}>
        {formatStatsValue(summary.profit_units, valueMode)}
      </span>
      <span className="rounded-md border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-cyan-100">ROI {pct(summary.roi)}</span>
    </div>
  );
}

export function MomentumStrip({
  results,
  title = 'Последние расчеты',
  compact = false,
}: {
  results: Array<'win' | 'loss'>;
  title?: string;
  compact?: boolean;
}) {
  const wins = results.filter((result) => result === 'win').length;
  const losses = results.length - wins;
  return (
    <div className={`rounded-xl border border-white/10 bg-slate-950/35 ${compact ? 'px-2.5 py-1.5' : 'p-2.5'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">
          <Activity className="h-3 w-3 text-cyan-300" />
          {title}
        </div>
        <div className="min-w-0 text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">
          {results.length ? `${resultCountLabel('win', wins)} / ${resultCountLabel('loss', losses)}` : 'Нет серии'}
        </div>
      </div>
      <div className="mt-1.5 flex gap-1">
        {(results.length ? results : Array.from({ length: 8 }, () => null)).slice(0, 14).map((result, index) => (
          <span
            key={`${result || 'empty'}:${index}`}
            title={result === 'win' ? resultLabel('win') : result === 'loss' ? resultLabel('loss') : 'Нет результата'}
            className={`h-2 flex-1 rounded-full ${
              result === 'win'
                ? 'bg-emerald-300 shadow-[0_0_14px_rgba(52,211,153,0.25)]'
                : result === 'loss'
                  ? 'bg-rose-300 shadow-[0_0_14px_rgba(251,113,133,0.22)]'
                  : 'bg-white/[0.08]'
            }`}
          />
        ))}
      </div>
    </div>
  );
}

export function StatsKpiGrid({
  summary,
  valueMode,
  showProfit = true,
}: {
  summary: PerformanceSummary;
  valueMode: StatsValueMode;
  showProfit?: boolean;
}) {
  const gridClass = showProfit ? 'grid-cols-2 gap-2 lg:grid-cols-5' : 'grid-cols-2 gap-2 lg:grid-cols-4';
  return (
    <div className={`grid ${gridClass}`}>
      <StatTile label="Ставок" value={summary.bets} hint={`${resultCountLabel('win', summary.wins)} / ${resultCountLabel('loss', summary.losses)}`} minHeightClass="min-h-[62px]" />
      {showProfit ? (
        <StatTile label="Прибыль" value={formatStatsValue(summary.profit_units, valueMode)} tone={summaryTone(summary)} minHeightClass="min-h-[62px]" />
      ) : null}
      <StatTile label="ROI" value={pct(summary.roi)} tone={summary.roi >= 0 ? 'text-emerald-300' : 'text-rose-300'} minHeightClass="min-h-[62px]" />
      <StatTile label="Проход" value={pct(summary.winrate)} tone="text-cyan-200" minHeightClass="min-h-[62px]" />
      <StatTile label="Средний КФ" value={summary.average_coefficient.toFixed(2)} tone="text-indigo-200" minHeightClass="min-h-[62px]" />
    </div>
  );
}

export function StatsHero({
  title,
  eyebrow,
  periodLabel,
  summary,
  valueMode,
  controls,
  actions,
}: {
  title: string;
  eyebrow: string;
  periodLabel: string;
  summary: PerformanceSummary;
  valueMode: StatsValueMode;
  controls: React.ReactNode;
  actions: React.ReactNode;
}) {
  const positive = summary.profit_units >= 0;
  return (
    <section className="relative isolate transform-gpu overflow-hidden rounded-[26px] border border-white/10 bg-[radial-gradient(circle_at_20%_0%,rgba(34,211,238,0.18),transparent_34%),radial-gradient(circle_at_82%_18%,rgba(16,185,129,0.09),transparent_28%),linear-gradient(135deg,rgba(15,23,42,0.96),rgba(8,13,28,0.92))] p-3.5 shadow-[0_18px_60px_rgba(2,6,23,0.34)] will-change-transform">
      <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/45 to-transparent" />
      <div className="pointer-events-none absolute bottom-0 right-8 h-px w-1/2 bg-gradient-to-r from-transparent via-emerald-200/20 to-transparent" />
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,32rem)]">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.13em] text-cyan-100">
            <TrendingUp className="h-3.5 w-3.5" />
            {eyebrow}
          </div>
          <h2 className="mt-1.5 min-w-0 break-words text-xl font-black leading-tight text-white sm:text-2xl">{title}</h2>
          <div className={`mt-3 min-w-0 break-words text-2xl font-black leading-none tabular-nums sm:text-4xl ${positive ? 'text-emerald-200' : 'text-rose-200'}`}>
            {formatStatsValue(summary.profit_units, valueMode)}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-400">
            <span>{periodLabel}</span>
            <span className="text-slate-700">/</span>
            <span>ROI {pct(summary.roi)}</span>
            <span className="text-slate-700">/</span>
            <span>{summary.bets} ставок</span>
          </div>
        </div>
        <div className="grid min-w-0 content-start gap-2">
          <div className="min-w-0">{controls}</div>
          <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(7rem,0.38fr)] lg:grid-cols-[minmax(0,1fr)_minmax(8rem,0.36fr)]">
            <div className="min-h-[70px] rounded-2xl border border-white/10 bg-slate-950/40 px-3 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.045)]">
              <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">Риск сейчас</div>
              <div className={`mt-0.5 truncate text-xs font-black ${summary.current_streak_type === 'loss' ? 'text-rose-300' : summary.current_streak_type === 'win' ? 'text-emerald-300' : 'text-white'}`}>
                {streakLabel(summary)}
              </div>
              <div className="mt-1 text-[8px] font-bold uppercase tracking-[0.08em] text-slate-600">
                Макс. серии {summary.max_win_streak}/{summary.max_loss_streak}
              </div>
            </div>
            <div className="min-w-0">{actions}</div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function IconActionButton({
  title,
  disabled,
  onClick,
  children,
  tone = 'neutral',
}: {
  title: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
  tone?: 'neutral' | 'emerald' | 'cyan';
}) {
  const toneClass = {
    neutral: 'border-white/10 bg-white/[0.05] text-slate-200 hover:bg-white/[0.09]',
    emerald: 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100 hover:bg-emerald-300/15',
    cyan: 'border-cyan-200/25 bg-cyan-200/12 text-cyan-50 hover:bg-cyan-200/18',
  }[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`flex min-h-[44px] w-full min-w-[44px] items-center justify-center rounded-xl border transition-all active:scale-[0.98] disabled:opacity-50 ${toneClass}`}
    >
      {children}
    </button>
  );
}

export function ExportActions({
  exporting,
  onCsv,
  onXlsx,
}: {
  exporting: 'csv' | 'xlsx' | null;
  onCsv: () => void;
  onXlsx: () => void;
}) {
  return (
    <div className="grid h-full min-w-0 grid-cols-2 gap-2">
      <IconActionButton title="Скачать CSV" disabled={exporting !== null} onClick={onCsv}>
        {exporting === 'csv' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      </IconActionButton>
      <IconActionButton title="Скачать XLSX" disabled={exporting !== null} onClick={onXlsx} tone="emerald">
        {exporting === 'xlsx' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
      </IconActionButton>
    </div>
  );
}

function driveJobLabel(job: StatsDriveExportJob | null) {
  if (!job) return null;
  if (job.status === 'completed') return 'Отчет готов';
  if (job.status === 'failed') return 'Ошибка выгрузки';
  if (job.status === 'running') return 'Создаем отчет';
  return 'Отчет в очереди';
}

export function ExportStatusPanel({
  job,
  error,
  title = 'Google Drive',
  compact = false,
}: {
  job: StatsDriveExportJob | null;
  error: string | null;
  title?: string;
  compact?: boolean;
}) {
  if (!job && !error) return null;

  const failed = Boolean(error || job?.status === 'failed');
  const completed = job?.status === 'completed';
  const Icon = completed ? CheckCircle2 : failed ? AlertCircle : Loader2;
  const toneClass = failed
    ? 'border-rose-300/20 bg-rose-400/[0.075] text-rose-100'
    : completed
      ? 'border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-100'
      : 'border-cyan-200/18 bg-cyan-200/[0.055] text-cyan-100';
  const message = error || job?.error || driveJobLabel(job);

  return (
    <section className={`transform-gpu rounded-[22px] border ${toneClass} ${compact ? 'p-2.5' : 'p-3'} shadow-[inset_0_1px_0_rgba(255,255,255,0.055)] will-change-transform`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-white/10 bg-slate-950/38">
            <Icon className={`h-4 w-4 ${!completed && !failed ? 'animate-spin' : ''}`} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.12em] text-slate-500">
              <Cloud className="h-3 w-3 text-cyan-200" />
              {title}
            </div>
            <div className="mt-0.5 truncate text-[10px] font-black uppercase tracking-[0.08em] text-current">
              {message}
            </div>
          </div>
        </div>
        {job?.formats?.length ? (
          <div className="hidden shrink-0 text-right text-[8px] font-black uppercase tracking-[0.1em] text-slate-500 sm:block">
            {job.formats.join(', ')}
          </div>
        ) : null}
      </div>
      {job?.links?.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {job.links.slice(0, 6).map((link) => (
            <a
              key={`${link.format}:${link.id}`}
              href={link.url}
              target="_blank"
              rel="noreferrer"
              className="smooth-pressable inline-flex max-w-full items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.06] px-2.5 py-1.5 text-[9px] font-bold text-cyan-50 transition-all hover:bg-white/[0.1] active:scale-[0.98]"
            >
              <ExternalLink className="h-3 w-3 shrink-0" />
              <span className="truncate">{link.title}</span>
            </a>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ProfitCurve({
  data,
  valueMode,
  title = 'Динамика прибыли',
}: {
  data: PerformanceTimelineResponse | null;
  valueMode: StatsValueMode;
  title?: string;
}) {
  const reduceMotion = useReducedMotion();
  const [activeIndex, setActiveIndex] = React.useState<number | null>(null);
  const idSuffix = React.useId().replace(/:/g, '');
  const lineGradientId = `profitLineGrad${idSuffix}`;
  const areaGradientId = `profitAreaGrad${idSuffix}`;
  const points = buildProfitCurvePoints(data);
  const width = 420;
  const height = 150;
  const padding = 18;
  const values = points.map((point) => point.value);
  const maxValue = Math.max(...values, 1);
  const minValue = Math.min(...values, -1);
  const range = maxValue - minValue || 2;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;
  const coords = points.map((point, index) => {
    const x = padding + (index / Math.max(points.length - 1, 1)) * chartWidth;
    const ratio = (point.value - minValue) / range;
    const y = padding + chartHeight - ratio * chartHeight;
    return { ...point, x, y };
  });
  const zeroY = padding + chartHeight - ((0 - minValue) / range) * chartHeight;
  const linePath = coords.reduce((path, coord, index) => {
    if (index === 0) return `M ${coord.x} ${coord.y}`;
    const prev = coords[index - 1];
    const xc = (prev.x + coord.x) / 2;
    return `${path} Q ${prev.x} ${prev.y}, ${xc} ${(prev.y + coord.y) / 2} T ${coord.x} ${coord.y}`;
  }, '');
  const areaPath = coords.length
    ? `${linePath} L ${coords[coords.length - 1].x} ${height - padding} L ${coords[0].x} ${height - padding} Z`
    : '';
  const latest = points[points.length - 1]?.value ?? 0;
  const activePoint = activeIndex === null ? null : coords[activeIndex];
  const maxPoint = coords.length ? coords.reduce((best, point) => (point.value > best.value ? point : best), coords[0]) : null;
  const minPoint = coords.length ? coords.reduce((weak, point) => (point.value < weak.value ? point : weak), coords[0]) : null;
  const pathMotion = reduceMotion
    ? {}
    : {
        initial: { pathLength: 0, opacity: 0 },
        animate: { pathLength: 1, opacity: 1 },
        transition: { duration: 0.75, ease: [0.16, 1, 0.3, 1] as const },
      };

  return (
    <div className="relative transform-gpu overflow-hidden rounded-[22px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] will-change-transform">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-xs font-black text-white">
            <Sparkles className="h-3.5 w-3.5 text-cyan-200" />
            {title}
          </h3>
          <p className="mt-0.5 text-[9px] font-bold text-slate-500">Кумулятивно по дням расчета, наведите на точку</p>
        </div>
        <div className={`min-w-0 text-left text-xs font-black tabular-nums sm:text-right ${profitTone(latest)}`}>{formatStatsValue(latest, valueMode)}</div>
      </div>
      {coords.length ? (
        <div className="relative" onPointerLeave={() => setActiveIndex(null)}>
          <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-3">
            <div className="rounded-xl border border-white/10 bg-slate-950/32 px-2 py-1.5">
              <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">Макс</div>
              <div className={`mt-0.5 truncate text-[10px] font-black tabular-nums ${maxPoint ? profitTone(maxPoint.value) : 'text-slate-400'}`}>
                {maxPoint ? formatStatsValue(maxPoint.value, valueMode) : '-'}
              </div>
            </div>
            <div className="rounded-xl border border-white/10 bg-slate-950/32 px-2 py-1.5">
              <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">Сейчас</div>
              <div className={`mt-0.5 truncate text-[10px] font-black tabular-nums ${profitTone(latest)}`}>
                {formatStatsValue(latest, valueMode)}
              </div>
            </div>
            <div className="rounded-xl border border-white/10 bg-slate-950/32 px-2 py-1.5">
              <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">Мин</div>
              <div className={`mt-0.5 truncate text-[10px] font-black tabular-nums ${minPoint ? profitTone(minPoint.value) : 'text-slate-400'}`}>
                {minPoint ? formatStatsValue(minPoint.value, valueMode) : '-'}
              </div>
            </div>
          </div>
          <svg viewBox={`0 0 ${width} ${height}`} className="mt-2 h-[150px] w-full overflow-visible">
            <defs>
              <linearGradient id={lineGradientId} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#38bdf8" />
                <stop offset="58%" stopColor={latest >= 0 ? '#34d399' : '#fb7185'} />
                <stop offset="100%" stopColor={latest >= 0 ? '#a7f3d0' : '#fecdd3'} />
              </linearGradient>
              <linearGradient id={areaGradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={latest >= 0 ? '#10b981' : '#f43f5e'} stopOpacity="0.24" />
                <stop offset="100%" stopColor={latest >= 0 ? '#10b981' : '#f43f5e'} stopOpacity="0" />
              </linearGradient>
            </defs>
            <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="rgba(255,255,255,0.05)" strokeDasharray="4 5" />
            <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="rgba(255,255,255,0.08)" />
            <line
              x1={padding}
              y1={Math.max(padding, Math.min(height - padding, zeroY))}
              x2={width - padding}
              y2={Math.max(padding, Math.min(height - padding, zeroY))}
              stroke="rgba(125,211,252,0.32)"
              strokeDasharray="6 7"
            />
            <path d={areaPath} fill={`url(#${areaGradientId})`} />
            <motion.path
              d={linePath}
              fill="none"
              stroke={`url(#${lineGradientId})`}
              strokeWidth="4"
              strokeLinecap="round"
              strokeLinejoin="round"
              {...pathMotion}
            />
            {coords.map((coord, index) => {
              const isLast = index === coords.length - 1;
              const active = activeIndex === index;
              return (
                <motion.circle
                  key={`${coord.key}:${index}`}
                  cx={coord.x}
                  cy={coord.y}
                  r={active || isLast ? 4.5 : 2.6}
                  fill="#08101f"
                  stroke={coord.value >= 0 ? '#34d399' : '#fb7185'}
                  strokeWidth="2"
                  className="cursor-pointer"
                  onPointerEnter={() => setActiveIndex(index)}
                  onPointerDown={() => setActiveIndex(index)}
                  animate={reduceMotion ? undefined : { scale: isLast ? [1, 1.12, 1] : 1 }}
                  transition={reduceMotion ? undefined : { duration: 1.4, repeat: isLast ? Infinity : 0 }}
                />
              );
            })}
            {maxPoint ? (
              <circle cx={maxPoint.x} cy={maxPoint.y} r="6.6" fill="none" stroke="rgba(52,211,153,0.32)" strokeWidth="1.5" />
            ) : null}
            {minPoint && minPoint.value < 0 ? (
              <circle cx={minPoint.x} cy={minPoint.y} r="6.6" fill="none" stroke="rgba(251,113,133,0.3)" strokeWidth="1.5" />
            ) : null}
          </svg>
          {activePoint ? (
            <div
              className="pointer-events-none absolute z-10 min-w-[116px] -translate-x-1/2 rounded-xl border border-white/10 bg-slate-950/95 px-2.5 py-1.5 text-center shadow-[0_18px_45px_rgba(2,6,23,0.55)]"
              style={{
                left: `${(activePoint.x / width) * 100}%`,
                top: `${Math.max(8, (activePoint.y / height) * 100 - 8)}%`,
              }}
            >
              <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{activePoint.label}</div>
              <div className={`mt-1 text-xs font-black tabular-nums ${profitTone(activePoint.value)}`}>
                {formatStatsValue(activePoint.value, valueMode)}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-2 flex h-[150px] items-center justify-center rounded-xl border border-dashed border-white/10 bg-slate-950/30 text-center text-[11px] font-bold text-slate-500">
          Нет рассчитанных ставок для графика
        </div>
      )}
    </div>
  );
}

export function BreakdownBars({
  title,
  items,
  valueMode,
  icon,
  limit = 6,
}: {
  title: string;
  items: PerformanceBreakdownItem[];
  valueMode: StatsValueMode;
  icon: React.ReactNode;
  limit?: number;
}) {
  const visible = items.slice(0, limit);
  const maxAbs = Math.max(...visible.map((item) => Math.abs(item.summary.profit_units)), 1);
  const best = bestBreakdownItem(visible);
  const weak = weakBreakdownItem(visible);
  return (
    <CollapsiblePanel
      title={title}
      icon={icon}
      summary={visible.length ? (
        <span className="flex flex-wrap items-center gap-1.5">
          <span>{visible.length} позиций</span>
          {best ? <span className="text-emerald-200">лидер {best.label}</span> : null}
          {weak && weak.summary.profit_units < 0 ? <span className="text-rose-200">просадка {weak.label}</span> : null}
        </span>
      ) : 'Нет данных'}
    >
      <div className="space-y-2.5">
        {visible.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-xl border border-emerald-300/15 bg-emerald-300/[0.06] p-2.5">
              <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-emerald-100">
                <TrendingUp className="h-3 w-3" />
                Драйвер прибыли
              </div>
              <div className="mt-1.5 truncate text-xs font-black text-white">{best?.label ?? 'Нет данных'}</div>
              <div className="mt-0.5 text-[9px] font-bold text-slate-400">
                {best ? `${formatStatsValue(best.summary.profit_units, valueMode)} / ROI ${pct(best.summary.roi)}` : 'Пока нет выраженного лидера'}
              </div>
            </div>
            <div className="rounded-xl border border-rose-300/15 bg-rose-300/[0.055] p-2.5">
              <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-rose-100">
                <TrendingDown className="h-3 w-3" />
                Зона просадки
              </div>
              <div className="mt-1.5 truncate text-xs font-black text-white">
                {weak && weak.summary.profit_units < 0 ? weak.label : 'Критичной зоны нет'}
              </div>
              <div className="mt-0.5 text-[9px] font-bold text-slate-400">
                {weak && weak.summary.profit_units < 0
                  ? `${formatStatsValue(weak.summary.profit_units, valueMode)} / ROI ${pct(weak.summary.roi)}`
                  : 'Минусовая зона не выделяется'}
              </div>
            </div>
          </div>
        ) : null}
        {visible.length ? visible.map((item) => {
          const percent = Math.max(8, Math.min(100, (Math.abs(item.summary.profit_units) / maxAbs) * 100));
          const positive = item.summary.profit_units >= 0;
          return (
            <div key={item.key} className="space-y-1">
              <div className="flex flex-col gap-1 text-[11px] sm:flex-row sm:items-center sm:justify-between sm:gap-2">
                <span className="min-w-0 truncate font-bold text-slate-100">{item.label}</span>
                <span className={`min-w-0 break-words font-black tabular-nums sm:shrink-0 sm:text-right ${summaryTone(item.summary)}`}>
                  {formatStatsValue(item.summary.profit_units, valueMode)} / ROI {pct(item.summary.roi)}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-slate-950/60">
                <div
                  className={`h-full rounded-full ${positive ? 'bg-emerald-300' : 'bg-rose-300'}`}
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          );
        }) : (
          <div className="rounded-xl border border-dashed border-white/10 bg-slate-950/25 p-4 text-center text-[11px] font-bold text-slate-500">
            Нет данных для разбора
          </div>
        )}
      </div>
    </CollapsiblePanel>
  );
}

export function SourceSplitPanel({
  feed,
  privateSummary,
  paidSetSummary,
  valueMode,
}: {
  feed: PerformanceSummary;
  privateSummary: PerformanceSummary;
  paidSetSummary: PerformanceSummary;
  valueMode: StatsValueMode;
}) {
  const rows = [
    { key: 'feed', label: 'Лента', icon: <TrendingUp className="h-4 w-4 text-emerald-300" />, summary: feed },
    { key: 'private', label: 'Закрытые', icon: <Target className="h-4 w-4 text-cyan-300" />, summary: privateSummary },
    { key: 'paid_set', label: 'Наборы', icon: <Sparkles className="h-4 w-4 text-amber-300" />, summary: paidSetSummary },
  ];
  return (
    <CollapsiblePanel title="Источники" summary="3 источника">
      <div className="grid gap-2 sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.key} className="rounded-xl border border-white/10 bg-slate-950/35 p-2.5">
            <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-500">
              {row.icon}
              {row.label}
            </div>
            <div className={`mt-1.5 text-base font-black tabular-nums ${summaryTone(row.summary)}`}>
              {formatStatsValue(row.summary.profit_units, valueMode)}
            </div>
            <MiniSummary summary={row.summary} valueMode={valueMode} gapClass="gap-1.5" />
          </div>
        ))}
      </div>
    </CollapsiblePanel>
  );
}

export function BetResultRow({
  bet,
  valueMode,
  compact = false,
}: {
  bet: PerformanceBetItem;
  valueMode: StatsValueMode;
  compact?: boolean;
}) {
  const positive = bet.profit_units >= 0;
  return (
    <div className={`rounded-xl border border-white/10 bg-slate-950/35 ${compact ? 'p-2.5' : 'p-3'}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`rounded-full border px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] ${
              bet.status === 'win'
                ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100'
                : 'border-rose-300/25 bg-rose-400/10 text-rose-100'
            }`}>
              {resultLabel(bet.status)}
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] text-slate-300">
              {sourceLabel(bet.source_type)}
            </span>
            {bet.sport_type && (
              <span className="rounded-full border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[8px] font-bold text-slate-300">
                {bet.sport_type}
              </span>
            )}
          </div>
          <h4 className="mt-1.5 break-words text-xs font-black leading-snug text-white">{bet.event_name}</h4>
          <div className="mt-0.5 text-[9px] font-bold text-slate-500">
            Расчет: {formatDateTime(bet.resolved_at)} / КФ {bet.coefficient.toFixed(2)}
            {bet.outcome ? ` / ${bet.outcome}` : ''}
          </div>
          {bet.bookmaker_names.length ? (
            <div className="mt-0.5 truncate text-[9px] font-bold text-slate-600">{bet.bookmaker_names.join(', ')}</div>
          ) : null}
        </div>
        <div className={`min-w-0 break-words text-left text-sm font-black tabular-nums sm:shrink-0 sm:text-right ${positive ? 'text-emerald-300' : 'text-rose-300'}`}>
          {formatStatsValue(bet.profit_units, valueMode)}
        </div>
      </div>
    </div>
  );
}

export function TimelineFeed({
  data,
  expandedMonths,
  expandedDays,
  onToggleMonth,
  onToggleDay,
  valueMode,
  renderBet,
  emptyLabel = 'Нет рассчитанных ставок в этом срезе',
}: {
  data: PerformanceTimelineResponse | null;
  expandedMonths: ExpandedMap;
  expandedDays: ExpandedMap;
  onToggleMonth: (key: string) => void;
  onToggleDay: (key: string) => void;
  valueMode: StatsValueMode;
  renderBet?: (bet: PerformanceBetItem, options: { compact: boolean; valueMode: StatsValueMode }) => React.ReactNode;
  emptyLabel?: string;
}) {
  const reduceMotion = useReducedMotion();
  if (!data?.timeline.length) {
    return (
      <div className="rounded-[22px] border border-white/10 bg-white/[0.04] p-5 text-center text-xs font-bold text-slate-500">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {data.timeline.map((month) => {
        const monthOpen = expandedMonths[month.key] ?? false;
        return (
          <div
            key={month.key}
            className="smooth-surface overflow-hidden rounded-[20px] border border-white/10 bg-white/[0.04] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
          >
            <button
              type="button"
              onClick={() => onToggleMonth(month.key)}
              aria-expanded={monthOpen}
              className="smooth-pressable grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2.5 text-left transition-all hover:bg-white/[0.035]"
            >
              <div className="flex min-w-0 items-center gap-2">
                <motion.span
                  animate={reduceMotion ? undefined : { rotate: monthOpen ? 90 : 0 }}
                  transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                  className="grid h-4 w-4 shrink-0 place-items-center text-slate-400"
                >
                  <ChevronRight className="h-4 w-4" />
                </motion.span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="truncate text-xs font-black text-white">{month.label}</div>
                    <span className="rounded-lg border border-white/10 bg-slate-950/35 px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] text-slate-400">
                      {month.days.length} дней
                    </span>
                  </div>
                  <MiniSummary summary={month.summary} valueMode={valueMode} />
                </div>
              </div>
              <div className={`shrink-0 text-right text-xs font-black tabular-nums ${summaryTone(month.summary)}`}>
                {formatStatsValue(month.summary.profit_units, valueMode)}
              </div>
            </button>

            <SmoothCollapse open={monthOpen} className="border-t border-white/10">
              <div className="space-y-1.5 p-2">
                {month.days.map((day) => {
                  const dayOpen = expandedDays[day.key] ?? false;
                  return (
                    <div
                      key={day.key}
                      className="smooth-surface overflow-hidden rounded-2xl border border-white/10 bg-black/15"
                    >
                      <button
                        type="button"
                        onClick={() => onToggleDay(day.key)}
                        aria-expanded={dayOpen}
                        className="smooth-pressable grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-2.5 py-2 text-left transition-all hover:bg-white/[0.03]"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <motion.span
                            animate={reduceMotion ? undefined : { rotate: dayOpen ? 90 : 0 }}
                            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                            className="grid h-4 w-4 shrink-0 place-items-center text-slate-400"
                          >
                            <ChevronRight className="h-4 w-4" />
                          </motion.span>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <div className="truncate text-[11px] font-black text-white">{day.label}</div>
                              <span className="rounded-md border border-white/10 bg-white/[0.045] px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] text-slate-500">
                                {day.bets.length} ставок
                              </span>
                            </div>
                            <MiniSummary summary={day.summary} valueMode={valueMode} />
                          </div>
                        </div>
                        <div className={`shrink-0 text-right text-[11px] font-black tabular-nums ${summaryTone(day.summary)}`}>
                          {formatStatsValue(day.summary.profit_units, valueMode)}
                        </div>
                      </button>
                      <SmoothCollapse open={dayOpen} className="border-t border-white/10">
                        <div className="space-y-1.5 p-1.5">
                          {day.bets.map((bet) => (
                            <React.Fragment key={bet.id}>
                              {renderBet
                                ? renderBet(bet, { compact: true, valueMode })
                                : <BetResultRow bet={bet} valueMode={valueMode} compact />}
                            </React.Fragment>
                          ))}
                        </div>
                      </SmoothCollapse>
                    </div>
                  );
                })}
              </div>
            </SmoothCollapse>
          </div>
        );
      })}
    </div>
  );
}

export function SectionHeading({
  icon,
  title,
  aside,
}: {
  icon: React.ReactNode;
  title: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.13em] text-slate-400">
        {icon}
        {title}
      </h3>
      {aside}
    </div>
  );
}

export function StatsSkeleton() {
  return (
    <div className="space-y-3 pb-8">
      <div className="h-40 animate-pulse rounded-[22px] border border-white/10 bg-white/[0.045]" />
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="h-16 animate-pulse rounded-xl border border-white/10 bg-white/[0.04]" />
        ))}
      </div>
      <div className="h-48 animate-pulse rounded-[22px] border border-white/10 bg-white/[0.04]" />
    </div>
  );
}

export function OverviewAnalytics({
  data,
  sourceSplit,
  bookmakerBreakdown,
  sportBreakdown,
  valueMode,
}: {
  data: PerformanceTimelineResponse | null;
  sourceSplit?: { feed: PerformanceSummary; private: PerformanceSummary; paid_set: PerformanceSummary };
  bookmakerBreakdown: PerformanceBreakdownItem[];
  sportBreakdown: PerformanceBreakdownItem[];
  valueMode: StatsValueMode;
}) {
  return (
    <div className="grid gap-2.5 xl:grid-cols-[1.15fr_0.85fr]">
      <ProfitCurve data={data} valueMode={valueMode} />
      <div className="grid gap-2.5">
        {sourceSplit ? (
          <SourceSplitPanel
            feed={sourceSplit.feed}
            privateSummary={sourceSplit.private}
            paidSetSummary={sourceSplit.paid_set}
            valueMode={valueMode}
          />
        ) : null}
        <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-1">
          <BreakdownBars title="Букмекеры" items={bookmakerBreakdown} valueMode={valueMode} icon={<BarChart3 className="h-4 w-4 text-emerald-300" />} limit={4} />
          <BreakdownBars title="Виды спорта" items={sportBreakdown} valueMode={valueMode} icon={<BarChart3 className="h-4 w-4 text-cyan-300" />} limit={4} />
        </div>
      </div>
    </div>
  );
}

export function TimelineSectionBlock({
  data,
  expandedMonths,
  expandedDays,
  onToggleMonth,
  onToggleDay,
  valueMode,
  title = 'Ставки',
  aside,
  renderBet,
}: {
  data: PerformanceTimelineResponse | null;
  expandedMonths: ExpandedMap;
  expandedDays: ExpandedMap;
  onToggleMonth: (key: string) => void;
  onToggleDay: (key: string) => void;
  valueMode: StatsValueMode;
  title?: string;
  aside?: React.ReactNode;
  renderBet?: (bet: PerformanceBetItem, options: { compact: boolean; valueMode: StatsValueMode }) => React.ReactNode;
}) {
  const betsCount = data?.summary.bets ?? 0;
  return (
    <CollapsiblePanel
      title={title}
      icon={<CalendarDays className="h-4 w-4 text-cyan-300" />}
      summary={(
        <span className="flex flex-wrap items-center gap-2">
          <span>{betsCount} ставок</span>
          {aside ? <span>{aside}</span> : null}
        </span>
      )}
    >
      <TimelineFeed
        data={data}
        expandedMonths={expandedMonths}
        expandedDays={expandedDays}
        onToggleMonth={onToggleMonth}
        onToggleDay={onToggleDay}
        valueMode={valueMode}
        renderBet={renderBet}
      />
    </CollapsiblePanel>
  );
}
