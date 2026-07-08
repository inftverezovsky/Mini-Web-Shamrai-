import { describe, expect, it } from 'vitest';
import {
  canBulkPrefetch,
  canIntentPrefetch,
  canPrefetchAdminChunk,
  dataWorkerThresholdForProfile,
  isSlowConnectionForPrefetch,
  readSafeDeviceMemory,
} from '../src/utils/performancePolicy';

describe('performance prefetch policy', () => {
  const fullVisibleBrowser = {
    profile: 'full' as const,
    isAppVisible: true,
    isTelegramSurface: false,
    documentVisibilityState: 'visible' as const,
  };

  it('disables bulk prefetch on saveData and slow effective connections', () => {
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      connection: { saveData: true },
    })).toBe(false);

    expect(isSlowConnectionForPrefetch({ effectiveType: '2g' })).toBe(true);
    expect(isSlowConnectionForPrefetch({ effectiveType: '3g' })).toBe(true);
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      connection: { effectiveType: '3g' },
    })).toBe(false);
  });

  it('disables bulk prefetch when hidden, low-power, Telegram, or constrained', () => {
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      documentVisibilityState: 'hidden',
    })).toBe(false);
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      profile: 'lowPower',
    })).toBe(false);
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      isTelegramSurface: true,
    })).toBe(false);
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      isConstrainedDevice: true,
    })).toBe(false);
  });

  it('allows safe desktop bulk and intent prefetch on a good visible connection', () => {
    expect(canBulkPrefetch({
      ...fullVisibleBrowser,
      connection: { effectiveType: '4g' },
    })).toBe(true);
    expect(canIntentPrefetch({
      profile: 'balanced',
      isAppVisible: true,
      documentVisibilityState: 'visible',
    })).toBe(true);
  });

  it('uses safe deviceMemory feature detection only when the hint exists', () => {
    expect(readSafeDeviceMemory({} as Navigator & { deviceMemory?: number })).toBeNull();
    expect(readSafeDeviceMemory({ deviceMemory: 2 } as Navigator & { deviceMemory?: number })).toBe(2);
    expect(readSafeDeviceMemory({ deviceMemory: Number.NaN } as Navigator & { deviceMemory?: number })).toBeNull();
  });

  it('requires staff/admin route or explicit admin intent before admin chunk prefetch', () => {
    expect(canPrefetchAdminChunk({ userIsStaff: false, explicitAdminIntent: true })).toBe(false);
    expect(canPrefetchAdminChunk({ userIsStaff: true })).toBe(false);
    expect(canPrefetchAdminChunk({ userIsStaff: true, currentRouteIsAdmin: true })).toBe(true);
    expect(canPrefetchAdminChunk({ userIsStaff: true, explicitAdminIntent: true })).toBe(true);
  });
});

describe('worker routing thresholds', () => {
  it('routes lower-power profiles to the worker earlier without changing transform code', () => {
    expect(dataWorkerThresholdForProfile('full')).toBe(150);
    expect(dataWorkerThresholdForProfile('balanced')).toBe(100);
    expect(dataWorkerThresholdForProfile('lowPower')).toBe(60);
  });
});
