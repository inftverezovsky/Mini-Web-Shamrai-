import { describe, expect, it } from 'vitest';
import {
  CLIENT_CHECKOUT_ROI_PERCENT,
  PROFIT_SIMULATOR_ROI_MODE,
  resolveProfitSimulatorRoi,
  shouldFetchGlobalRoi,
} from '../src/utils/profitSimulator';

describe('profit simulator ROI', () => {
  it('uses the requested 30% checkout ROI even when global stats currently return 18.5%', () => {
    expect(CLIENT_CHECKOUT_ROI_PERCENT).toBe(30);
    expect(PROFIT_SIMULATOR_ROI_MODE).toBe('checkout_static');
    expect(resolveProfitSimulatorRoi(18.5)).toBe(30);
    expect(shouldFetchGlobalRoi()).toBe(false);
  });

  it('can switch back to verified global ROI when that source is enabled later', () => {
    expect(resolveProfitSimulatorRoi(24.2, 'global_stats')).toBe(24.2);
    expect(resolveProfitSimulatorRoi(-5, 'global_stats')).toBe(30);
    expect(resolveProfitSimulatorRoi(null, 'global_stats')).toBe(30);
    expect(shouldFetchGlobalRoi('global_stats')).toBe(true);
  });
});
