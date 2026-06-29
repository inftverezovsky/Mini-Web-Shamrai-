import { describe, expect, it } from 'vitest';
import { buildProfitCurveStatCards } from '../src/features/performance/performanceUi';

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
});
