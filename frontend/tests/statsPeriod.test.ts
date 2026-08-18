import { describe, expect, it } from 'vitest';

import {
  currentStatsMonthKey,
  isExactStatsMonth,
  statsMonthInputValue,
} from '../src/utils/statsPeriod';

describe('admin stats exact month helpers', () => {
  it('recognizes only valid calendar month keys', () => {
    expect(isExactStatsMonth('2026-02')).toBe(true);
    expect(isExactStatsMonth('2026-2')).toBe(false);
    expect(isExactStatsMonth('2026-13')).toBe(false);
    expect(isExactStatsMonth('month')).toBe(false);
  });

  it('keeps the native month input empty for preset periods', () => {
    expect(statsMonthInputValue('all')).toBe('');
    expect(statsMonthInputValue('month')).toBe('');
    expect(statsMonthInputValue('2026-02')).toBe('2026-02');
  });

  it('builds the current month key in local calendar time', () => {
    expect(currentStatsMonthKey(new Date(2026, 7, 2, 10, 0, 0))).toBe('2026-08');
  });
});
