import { describe, expect, it } from 'vitest';
import {
  MONITORING_SUMMARY_REFETCH_INTERVAL_MS,
  monitoringRefetchInterval,
  shouldEnableMonitoringQuery,
} from '../src/features/settings/monitoringControls';

const visibleMonitoringState = {
  settingsActive: true,
  monitoringTabActive: true,
  liveEnabled: false,
};

describe('settings monitoring controls', () => {
  it('keeps monitoring network calls manual by default', () => {
    expect(shouldEnableMonitoringQuery('summary', visibleMonitoringState)).toBe(false);
    expect(shouldEnableMonitoringQuery('online', visibleMonitoringState)).toBe(false);
    expect(shouldEnableMonitoringQuery('parser', visibleMonitoringState)).toBe(false);
    expect(monitoringRefetchInterval('summary', visibleMonitoringState)).toBe(false);
  });

  it('enables only summary polling when live monitoring is on and visible', () => {
    const state = { ...visibleMonitoringState, liveEnabled: true };

    expect(shouldEnableMonitoringQuery('summary', state)).toBe(true);
    expect(monitoringRefetchInterval('summary', state)).toBe(MONITORING_SUMMARY_REFETCH_INTERVAL_MS);
    expect(monitoringRefetchInterval('logs', state)).toBe(false);
  });

  it('allows one-shot log loading only while the logs section is open', () => {
    expect(shouldEnableMonitoringQuery('logs', visibleMonitoringState)).toBe(false);
    expect(shouldEnableMonitoringQuery('logs', { ...visibleMonitoringState, logsOpen: true })).toBe(true);
  });

  it('disables all monitoring queries when settings is hidden by keep-alive', () => {
    const state = {
      settingsActive: false,
      monitoringTabActive: true,
      liveEnabled: true,
      logsOpen: true,
    };

    expect(shouldEnableMonitoringQuery('summary', state)).toBe(false);
    expect(shouldEnableMonitoringQuery('logs', state)).toBe(false);
    expect(monitoringRefetchInterval('summary', state)).toBe(false);
  });
});
