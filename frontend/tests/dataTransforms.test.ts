import { describe, expect, it } from 'vitest';
import {
  calculatePerformanceStats,
  mergeSignalHistory,
  sortBetHistory,
  sortFilterSignals,
} from '../src/workers/dataTransforms';

describe('data worker transforms', () => {
  it('deduplicates signals and keeps chronological order with a tail limit', () => {
    const merged = mergeSignalHistory(
      [
        { id: 2, created_at: '2026-01-01T10:00:00.000Z', text: 'old duplicate' },
        { id: 1, created_at: '2026-01-01T09:00:00.000Z', text: 'first' },
      ],
      [
        { id: 2, created_at: '2026-01-01T10:00:00.000Z', text: 'fresh duplicate' },
        { id: 3, created_at: '2026-01-01T11:00:00.000Z', text: 'last' },
      ],
      2,
    );

    expect(merged.map((signal) => signal.id)).toEqual([2, 3]);
    expect(merged[0].text).toBe('fresh duplicate');
  });

  it('filters signals by text and forecast status without mutating the source array', () => {
    const source = [
      { id: 1, created_at: '2026-01-01T09:00:00.000Z', text: 'Футбол тотал', data: { forecast_status: 'accepted' } },
      { id: 2, created_at: '2026-01-01T10:00:00.000Z', text: 'Хоккей победа', data: { forecast_status: 'declined' } },
    ];

    const result = sortFilterSignals(source, {
      query: 'футбол',
      status: 'accepted',
      sortDirection: 'desc',
    });

    expect(result.total).toBe(1);
    expect(result.items[0].id).toBe(1);
    expect(source.map((signal) => signal.id)).toEqual([1, 2]);
  });

  it('sorts bet history by resolved date descending', () => {
    const sorted = sortBetHistory([
      { id: 'a', status: 'win', resolved_at: '2026-01-01T09:00:00.000Z', coefficient: 1.8 },
      { id: 'b', status: 'loss', resolved_at: '2026-01-02T09:00:00.000Z', coefficient: 2.1 },
    ], {
      sortBy: 'resolved_at',
      sortDirection: 'desc',
    });

    expect(sorted.map((bet) => bet.id)).toEqual(['b', 'a']);
  });

  it('calculates winrate, ROI, and average coefficient from settled bets', () => {
    const stats = calculatePerformanceStats([
      { id: 'win', status: 'win', coefficient: 2.2 },
      { id: 'loss', status: 'loss', coefficient: 1.9 },
      { id: 'refund', status: 'refund', coefficient: 1.7 },
      { id: 'pending', status: 'pending', coefficient: 2.5 },
    ]);

    expect(stats.total).toBe(4);
    expect(stats.settled).toBe(2);
    expect(stats.winrate).toBe(50);
    expect(stats.profitUnits).toBeCloseTo(0.2);
    expect(stats.roi).toBeCloseTo(10);
    expect(stats.averageCoefficient).toBeCloseTo(2.05);
  });
});
