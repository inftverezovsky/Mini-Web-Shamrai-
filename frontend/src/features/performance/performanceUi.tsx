import React from 'react';

import { PerformanceSummary, PeriodFilter } from '../../schemas/schemas';

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

export function pct(value: number) {
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}

export function summaryTone(summary: PerformanceSummary) {
  if (summary.profit_units > 0) return 'text-emerald-300';
  if (summary.profit_units < 0) return 'text-rose-300';
  return 'text-slate-200';
}

export function StatTile({
  label,
  value,
  hint,
  tone = 'text-white',
  minHeightClass = 'min-h-[76px]',
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: string;
  minHeightClass?: string;
}) {
  return (
    <div className={`${minHeightClass} rounded-2xl border border-white/10 bg-slate-950/35 px-3 py-3`}>
      <div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-500">{label}</div>
      <div className={`mt-1 text-lg font-black ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-600">{hint}</div>}
    </div>
  );
}

export function MiniSummary({
  summary,
  gapClass = 'gap-2',
}: {
  summary: PerformanceSummary;
  gapClass?: string;
}) {
  return (
    <div className={`flex flex-wrap items-center ${gapClass} text-[10px] font-black uppercase tracking-[0.1em]`}>
      <span className="rounded-lg border border-white/10 bg-white/[0.05] px-2 py-1 text-slate-300">{summary.bets} ставок</span>
      <span className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-2 py-1 text-emerald-100">{summary.wins}W</span>
      <span className="rounded-lg border border-rose-300/20 bg-rose-300/10 px-2 py-1 text-rose-100">{summary.losses}L</span>
      <span className={`rounded-lg border border-white/10 bg-white/[0.05] px-2 py-1 ${summaryTone(summary)}`}>
        {signed(summary.profit_units)}u
      </span>
      <span className="rounded-lg border border-white/10 bg-white/[0.05] px-2 py-1 text-cyan-100">ROI {pct(summary.roi)}</span>
    </div>
  );
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
    ? 'bg-cyan-200/15 text-cyan-50'
    : 'bg-emerald-300/15 text-emerald-50';

  return (
    <div className="grid grid-cols-4 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/35 p-1">
      {PERIOD_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`min-h-[34px] rounded-xl px-2 text-[10px] font-black uppercase tracking-[0.1em] transition-all ${
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
