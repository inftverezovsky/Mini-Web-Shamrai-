import { describe, expect, it } from 'vitest';
import { buildProfitCurveStatCards, positiveStreakLabel } from '../src/features/performance/performanceUi';

describe('performance UI helpers', () => {
  it('shows max and positive current profit without a min card', () => {
    const cards = buildProfitCurveStatCards([
      { value: -2 },
      { value: 4 },
      { value: 3 },
    ]);

    expect(cards).toEqual([
      { key: 'max', label: 'Макс', value: 4 },
      { key: 'current', label: 'Сейчас', value: 3 },
    ]);
  });

  it('hides current profit when the latest point is not positive', () => {
    const cards = buildProfitCurveStatCards([
      { value: 7 },
      { value: 0 },
    ]);

    expect(cards).toEqual([
      { key: 'max', label: 'Макс', value: 7 },
    ]);
  });

  it('shows zero for empty or loss streaks in the client positive streak display', () => {
    expect(positiveStreakLabel({ current_streak: 0, current_streak_type: null })).toBe('0');
    expect(positiveStreakLabel({ current_streak: 3, current_streak_type: 'loss' })).toBe('0');
  });

  it('shows positive streaks starting from one bet', () => {
    expect(positiveStreakLabel({ current_streak: 1, current_streak_type: 'win' })).toBe('1 ставка');
    expect(positiveStreakLabel({ current_streak: 2, current_streak_type: 'win' })).toBe('2 ставки');
  });
});
