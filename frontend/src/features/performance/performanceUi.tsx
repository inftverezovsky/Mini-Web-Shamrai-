import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  Activity,
  BarChart3,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Download,
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
  return source === 'private' ? 'Закрытая' : 'Лента';
}

function resultLabel(status: PerformanceBetItem['status']) {
  return status === 'win' ? 'Win' : 'Loss';
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
    <div className="grid grid-cols-4 overflow-hidden rounded-xl border border-white/10 bg-slate-950/35 p-1">
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
    <div className={`${minHeightClass} rounded-xl border border-white/10 bg-slate-950/35 px-2.5 py-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]`}>
      <div className="text-[8px] font-black uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className={`mt-0.5 text-base font-black tabular-nums ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.06em] text-slate-600">{hint}</div>}
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
      className={`smooth-surface rounded-[22px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] ${className}`}
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
      <span className="rounded-md border border-emerald-300/20 bg-emerald-300/10 px-1.5 py-0.5 text-emerald-100">{summary.wins}W</span>
      <span className="rounded-md border border-rose-300/20 bg-rose-300/10 px-1.5 py-0.5 text-rose-100">{summary.losses}L</span>
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
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">
          <Activity className="h-3 w-3 text-cyan-300" />
          {title}
        </div>
        <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">
          {results.length ? `${wins}W / ${losses}L` : 'Нет серии'}
        </div>
      </div>
      <div className="mt-1.5 flex gap-1">
        {(results.length ? results : Array.from({ length: 8 }, () => null)).slice(0, 14).map((result, index) => (
          <span
            key={`${result || 'empty'}:${index}`}
            title={result === 'win' ? 'Win' : result === 'loss' ? 'Loss' : 'Нет результата'}
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
      <StatTile label="Ставок" value={summary.bets} hint={`${summary.wins}W / ${summary.losses}L`} minHeightClass="min-h-[62px]" />
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
    <section className="relative overflow-hidden rounded-[24px] border border-white/10 bg-[radial-gradient(circle_at_20%_0%,rgba(34,211,238,0.16),transparent_34%),linear-gradient(135deg,rgba(15,23,42,0.96),rgba(8,13,28,0.92))] p-3.5 shadow-[0_18px_60px_rgba(2,6,23,0.32)]">
      <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/35 to-transparent" />
      <div className="grid gap-3 lg:grid-cols-[1fr_390px]">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.13em] text-cyan-100">
            <TrendingUp className="h-3.5 w-3.5" />
            {eyebrow}
          </div>
          <h2 className="mt-1.5 text-xl font-black leading-tight text-white sm:text-2xl">{title}</h2>
          <div className={`mt-3 text-3xl font-black leading-none tabular-nums sm:text-4xl ${positive ? 'text-emerald-200' : 'text-rose-200'}`}>
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
        <div className="grid gap-2">
          {controls}
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <div className="rounded-xl border border-white/10 bg-slate-950/40 px-2.5 py-1.5">
              <div className="text-[8px] font-black uppercase tracking-[0.1em] text-slate-500">Текущая серия</div>
              <div className={`mt-0.5 text-xs font-black ${summary.current_streak_type === 'loss' ? 'text-rose-300' : summary.current_streak_type === 'win' ? 'text-emerald-300' : 'text-white'}`}>
                {summary.current_streak ? `${summary.current_streak} ${summary.current_streak_type === 'win' ? 'W' : 'L'}` : 'Нет серии'}
              </div>
            </div>
            {actions}
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
      className={`inline-flex min-h-[38px] min-w-[44px] items-center justify-center rounded-xl border transition-all active:scale-[0.98] disabled:opacity-50 ${toneClass}`}
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
    <div className="grid grid-cols-2 gap-2">
      <IconActionButton title="Скачать CSV" disabled={exporting !== null} onClick={onCsv}>
        {exporting === 'csv' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      </IconActionButton>
      <IconActionButton title="Скачать XLSX" disabled={exporting !== null} onClick={onXlsx} tone="emerald">
        {exporting === 'xlsx' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
      </IconActionButton>
    </div>
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
  const pathMotion = reduceMotion
    ? {}
    : {
        initial: { pathLength: 0, opacity: 0 },
        animate: { pathLength: 1, opacity: 1 },
        transition: { duration: 0.75, ease: [0.16, 1, 0.3, 1] as const },
      };

  return (
    <div className="relative overflow-hidden rounded-[22px] border border-white/10 bg-white/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-1.5 text-xs font-black text-white">
            <Sparkles className="h-3.5 w-3.5 text-cyan-200" />
            {title}
          </h3>
          <p className="mt-0.5 text-[9px] font-bold text-slate-500">Кумулятивно по дням расчета · коснитесь точки</p>
        </div>
        <div className={`text-right text-xs font-black tabular-nums ${profitTone(latest)}`}>{formatStatsValue(latest, valueMode)}</div>
      </div>
      {coords.length ? (
        <div className="relative" onPointerLeave={() => setActiveIndex(null)}>
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
          {best ? <span className="text-emerald-200">· лидер {best.label}</span> : null}
          {weak && weak.summary.profit_units < 0 ? <span className="text-rose-200">· просадка {weak.label}</span> : null}
        </span>
      ) : 'Нет данных'}
    >
      <div className="space-y-2.5">
        {visible.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-xl border border-emerald-300/15 bg-emerald-300/[0.06] p-2.5">
              <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-emerald-100">
                <TrendingUp className="h-3 w-3" />
                Что тянет
              </div>
              <div className="mt-1.5 truncate text-xs font-black text-white">{best?.label ?? 'Нет данных'}</div>
              <div className="mt-0.5 text-[9px] font-bold text-slate-400">
                {best ? `${formatStatsValue(best.summary.profit_units, valueMode)} · ROI ${pct(best.summary.roi)}` : 'Пока нет выраженного лидера'}
              </div>
            </div>
            <div className="rounded-xl border border-rose-300/15 bg-rose-300/[0.055] p-2.5">
              <div className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.1em] text-rose-100">
                <TrendingDown className="h-3 w-3" />
                Что просаживает
              </div>
              <div className="mt-1.5 truncate text-xs font-black text-white">
                {weak && weak.summary.profit_units < 0 ? weak.label : 'Критичной зоны нет'}
              </div>
              <div className="mt-0.5 text-[9px] font-bold text-slate-400">
                {weak && weak.summary.profit_units < 0
                  ? `${formatStatsValue(weak.summary.profit_units, valueMode)} · ROI ${pct(weak.summary.roi)}`
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
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span className="min-w-0 truncate font-bold text-slate-100">{item.label}</span>
                <span className={`shrink-0 font-black tabular-nums ${summaryTone(item.summary)}`}>
                  {formatStatsValue(item.summary.profit_units, valueMode)} · ROI {pct(item.summary.roi)}
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
  valueMode,
}: {
  feed: PerformanceSummary;
  privateSummary: PerformanceSummary;
  valueMode: StatsValueMode;
}) {
  const rows = [
    { key: 'feed', label: 'Лента', icon: <TrendingUp className="h-4 w-4 text-emerald-300" />, summary: feed },
    { key: 'private', label: 'Закрытые', icon: <Target className="h-4 w-4 text-cyan-300" />, summary: privateSummary },
  ];
  return (
    <CollapsiblePanel title="Лента против закрытых" summary="2 источника">
      <div className="grid gap-2 sm:grid-cols-2">
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
      <div className="flex items-start justify-between gap-2">
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
            Расчет: {formatDateTime(bet.resolved_at)} · КФ {bet.coefficient.toFixed(2)}
            {bet.outcome ? ` · ${bet.outcome}` : ''}
          </div>
          {bet.bookmaker_names.length ? (
            <div className="mt-0.5 truncate text-[9px] font-bold text-slate-600">{bet.bookmaker_names.join(', ')}</div>
          ) : null}
        </div>
        <div className={`shrink-0 text-right text-sm font-black tabular-nums ${positive ? 'text-emerald-300' : 'text-rose-300'}`}>
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
  emptyLabel = 'Нет рассчитанных ставок в этом срезе',
}: {
  data: PerformanceTimelineResponse | null;
  expandedMonths: ExpandedMap;
  expandedDays: ExpandedMap;
  onToggleMonth: (key: string) => void;
  onToggleDay: (key: string) => void;
  valueMode: StatsValueMode;
  emptyLabel?: string;
}) {
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
            className="smooth-surface overflow-hidden rounded-[20px] border border-white/10 bg-white/[0.04]"
          >
            <button
              type="button"
              onClick={() => onToggleMonth(month.key)}
              aria-expanded={monthOpen}
              className="smooth-pressable flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-all hover:bg-white/[0.035]"
            >
              <div className="flex min-w-0 items-center gap-2">
                <motion.span
                  animate={{ rotate: monthOpen ? 90 : 0 }}
                  transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                  className="grid h-4 w-4 shrink-0 place-items-center text-slate-400"
                >
                  <ChevronRight className="h-4 w-4" />
                </motion.span>
                <div>
                  <div className="text-xs font-black text-white">{month.label}</div>
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
                      className="smooth-surface rounded-xl border border-white/10 bg-black/15"
                    >
                      <button
                        type="button"
                        onClick={() => onToggleDay(day.key)}
                        aria-expanded={dayOpen}
                        className="smooth-pressable flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left transition-all hover:bg-white/[0.03]"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <motion.span
                            animate={{ rotate: dayOpen ? 90 : 0 }}
                            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                            className="grid h-4 w-4 shrink-0 place-items-center text-slate-400"
                          >
                            <ChevronRight className="h-4 w-4" />
                          </motion.span>
                          <div>
                            <div className="text-[11px] font-black text-white">{day.label}</div>
                            <MiniSummary summary={day.summary} valueMode={valueMode} />
                          </div>
                        </div>
                        {day.summary.profit_units >= 0 ? <TrendingUp className="h-4 w-4 shrink-0 text-emerald-300" /> : <TrendingDown className="h-4 w-4 shrink-0 text-rose-300" />}
                      </button>
                      <SmoothCollapse open={dayOpen} className="border-t border-white/10">
                        <div className="space-y-1.5 p-1.5">
                          {day.bets.map((bet) => <BetResultRow key={bet.id} bet={bet} valueMode={valueMode} compact />)}
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
  sourceSplit?: { feed: PerformanceSummary; private: PerformanceSummary };
  bookmakerBreakdown: PerformanceBreakdownItem[];
  sportBreakdown: PerformanceBreakdownItem[];
  valueMode: StatsValueMode;
}) {
  return (
    <div className="grid gap-2.5 xl:grid-cols-[1.15fr_0.85fr]">
      <ProfitCurve data={data} valueMode={valueMode} />
      <div className="grid gap-2.5">
        {sourceSplit ? (
          <SourceSplitPanel feed={sourceSplit.feed} privateSummary={sourceSplit.private} valueMode={valueMode} />
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
}: {
  data: PerformanceTimelineResponse | null;
  expandedMonths: ExpandedMap;
  expandedDays: ExpandedMap;
  onToggleMonth: (key: string) => void;
  onToggleDay: (key: string) => void;
  valueMode: StatsValueMode;
  title?: string;
  aside?: React.ReactNode;
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
      />
    </CollapsiblePanel>
  );
}
