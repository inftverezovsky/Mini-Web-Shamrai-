export type ProfitSimulatorRoiMode = 'checkout_static' | 'global_stats';

export const CLIENT_CHECKOUT_ROI_PERCENT = 30;

// Temporary checkout ROI. Switch to 'global_stats' when the simulator should follow Shamrai aggregate stats again.
export const PROFIT_SIMULATOR_ROI_MODE: ProfitSimulatorRoiMode = 'checkout_static';

export function shouldFetchGlobalRoi(
  mode: ProfitSimulatorRoiMode = PROFIT_SIMULATOR_ROI_MODE,
): boolean {
  return mode === 'global_stats';
}

export function resolveProfitSimulatorRoi(
  statsRoi: unknown,
  mode: ProfitSimulatorRoiMode = PROFIT_SIMULATOR_ROI_MODE,
): number {
  if (mode !== 'global_stats') {
    return CLIENT_CHECKOUT_ROI_PERCENT;
  }

  return typeof statsRoi === 'number' && Number.isFinite(statsRoi) && statsRoi > 0
    ? statsRoi
    : CLIENT_CHECKOUT_ROI_PERCENT;
}
