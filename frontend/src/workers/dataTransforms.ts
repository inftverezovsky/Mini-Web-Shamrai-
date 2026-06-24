export type SortDirection = 'asc' | 'desc';

export interface SortableSignalItem {
  id: number | string;
  created_at?: string | null;
  text?: string | null;
  type?: string | null;
  status?: string | null;
  event_name?: string | null;
  sport_type?: string | null;
  data?: Record<string, unknown> | null;
}

export interface SignalFilterOptions {
  query?: string;
  status?: string;
  type?: string;
  sortBy?: 'created_at' | 'id';
  sortDirection?: SortDirection;
  limit?: number;
}

export interface SignalProcessResult<TSignal extends SortableSignalItem> {
  items: TSignal[];
  total: number;
}

export interface SortableBetItem {
  id: number | string;
  created_at?: string | null;
  resolved_at?: string | null;
  event_name?: string | null;
  sport_type?: string | null;
  status?: string | null;
  coefficient?: string | number | null;
  profit_units?: string | number | null;
}

export interface BetHistoryOptions {
  query?: string;
  status?: string;
  sortBy?: 'created_at' | 'resolved_at' | 'coefficient' | 'id';
  sortDirection?: SortDirection;
  limit?: number;
}

export interface PerformanceStatsResult {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  refunds: number;
  pending: number;
  winrate: number;
  roi: number;
  profitUnits: number;
  averageCoefficient: number;
}

function normalizeText(value: unknown) {
  return String(value ?? '').trim().toLowerCase();
}

function toTime(value: unknown) {
  const time = new Date(String(value ?? '')).getTime();
  return Number.isFinite(time) ? time : 0;
}

function toNumber(value: unknown) {
  const numeric = typeof value === 'number' ? value : Number(String(value ?? '').replace(',', '.'));
  return Number.isFinite(numeric) ? numeric : 0;
}

function compareValues(left: unknown, right: unknown, direction: SortDirection) {
  const normalizedDirection = direction === 'desc' ? -1 : 1;
  if (typeof left === 'number' || typeof right === 'number') {
    return (toNumber(left) - toNumber(right)) * normalizedDirection;
  }
  return String(left ?? '').localeCompare(String(right ?? ''), 'ru') * normalizedDirection;
}

function signalSearchText(signal: SortableSignalItem) {
  const data = signal.data || {};
  return [
    signal.text,
    signal.type,
    signal.status,
    signal.event_name,
    signal.sport_type,
    data.event_name,
    data.outcome,
    data.sport_type,
    data.forecast_status,
  ].map(normalizeText).join(' ');
}

function betSearchText(bet: SortableBetItem) {
  return [
    bet.event_name,
    bet.sport_type,
    bet.status,
  ].map(normalizeText).join(' ');
}

function compareSignalCreatedAt(left: SortableSignalItem, right: SortableSignalItem) {
  const timeDelta = toTime(left.created_at) - toTime(right.created_at);
  return timeDelta || compareValues(left.id, right.id, 'asc');
}

export function mergeSignalHistory<TSignal extends SortableSignalItem>(
  currentSignals: readonly TSignal[],
  incomingSignals: readonly TSignal[],
  limit = 160,
) {
  const byId = new Map<TSignal['id'], TSignal>();
  currentSignals.forEach((signal) => byId.set(signal.id, signal));
  incomingSignals.forEach((signal) => byId.set(signal.id, signal));

  const mergedSignals = Array.from(byId.values()).sort(compareSignalCreatedAt);
  return limit > 0 ? mergedSignals.slice(-limit) : mergedSignals;
}

export function sortFilterSignals<TSignal extends SortableSignalItem>(
  signals: readonly TSignal[],
  options: SignalFilterOptions = {},
): SignalProcessResult<TSignal> {
  const query = normalizeText(options.query);
  const status = normalizeText(options.status);
  const type = normalizeText(options.type);
  const sortBy = options.sortBy || 'created_at';
  const sortDirection = options.sortDirection || 'asc';

  const filteredSignals = signals.filter((signal) => {
    if (status && normalizeText(signal.status ?? signal.data?.forecast_status) !== status) return false;
    if (type && normalizeText(signal.type) !== type) return false;
    return !query || signalSearchText(signal).includes(query);
  });

  const sortedSignals = [...filteredSignals].sort((left, right) => {
    if (sortBy === 'created_at') {
      const delta = (toTime(left.created_at) - toTime(right.created_at)) * (sortDirection === 'desc' ? -1 : 1);
      return delta || compareValues(left.id, right.id, sortDirection);
    }
    return compareValues(left.id, right.id, sortDirection);
  });

  return {
    items: options.limit && options.limit > 0 ? sortedSignals.slice(0, options.limit) : sortedSignals,
    total: filteredSignals.length,
  };
}

export function sortBetHistory<TBet extends SortableBetItem>(
  bets: readonly TBet[],
  options: BetHistoryOptions = {},
) {
  const query = normalizeText(options.query);
  const status = normalizeText(options.status);
  const sortBy = options.sortBy || 'created_at';
  const sortDirection = options.sortDirection || 'desc';

  const filteredBets = bets.filter((bet) => {
    if (status && normalizeText(bet.status) !== status) return false;
    return !query || betSearchText(bet).includes(query);
  });

  const sortedBets = [...filteredBets].sort((left, right) => {
    if (sortBy === 'created_at' || sortBy === 'resolved_at') {
      const delta = (toTime(left[sortBy]) - toTime(right[sortBy])) * (sortDirection === 'desc' ? -1 : 1);
      return delta || compareValues(left.id, right.id, sortDirection);
    }
    if (sortBy === 'coefficient') {
      const delta = (toNumber(left.coefficient) - toNumber(right.coefficient)) * (sortDirection === 'desc' ? -1 : 1);
      return delta || compareValues(left.id, right.id, sortDirection);
    }
    return compareValues(left.id, right.id, sortDirection);
  });

  return options.limit && options.limit > 0 ? sortedBets.slice(0, options.limit) : sortedBets;
}

export function calculatePerformanceStats(bets: readonly SortableBetItem[]): PerformanceStatsResult {
  const stats = bets.reduce((acc, bet) => {
    const coefficient = toNumber(bet.coefficient);
    const status = normalizeText(bet.status);
    const explicitProfit = bet.profit_units === null || bet.profit_units === undefined
      ? null
      : toNumber(bet.profit_units);

    if (status === 'win') {
      acc.wins += 1;
      acc.profitUnits += explicitProfit ?? Math.max(0, coefficient - 1);
      acc.coefficientSum += coefficient;
      acc.coefficientCount += coefficient > 0 ? 1 : 0;
    } else if (status === 'loss') {
      acc.losses += 1;
      acc.profitUnits += explicitProfit ?? -1;
      acc.coefficientSum += coefficient;
      acc.coefficientCount += coefficient > 0 ? 1 : 0;
    } else if (status === 'refund') {
      acc.refunds += 1;
      acc.profitUnits += explicitProfit ?? 0;
    } else {
      acc.pending += 1;
    }

    return acc;
  }, {
    wins: 0,
    losses: 0,
    refunds: 0,
    pending: 0,
    profitUnits: 0,
    coefficientSum: 0,
    coefficientCount: 0,
  });

  const settled = stats.wins + stats.losses;
  const total = bets.length;
  const averageCoefficient = stats.coefficientCount > 0 ? stats.coefficientSum / stats.coefficientCount : 0;

  return {
    total,
    settled,
    wins: stats.wins,
    losses: stats.losses,
    refunds: stats.refunds,
    pending: stats.pending,
    winrate: settled > 0 ? (stats.wins / settled) * 100 : 0,
    roi: settled > 0 ? (stats.profitUnits / settled) * 100 : 0,
    profitUnits: stats.profitUnits,
    averageCoefficient,
  };
}
