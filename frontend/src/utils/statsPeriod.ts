import type { StatsPeriodFilter } from '../schemas/schemas';

const EXACT_STATS_MONTH_RE = /^[1-9]\d{3}-(0[1-9]|1[0-2])$/;

export function isExactStatsMonth(value: string): value is `${number}-${number}` {
  return EXACT_STATS_MONTH_RE.test(value);
}

export function statsMonthInputValue(period: StatsPeriodFilter) {
  return isExactStatsMonth(period) ? period : '';
}

export function currentStatsMonthKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}
