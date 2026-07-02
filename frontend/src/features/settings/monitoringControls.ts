export type MonitoringQueryKind = 'summary' | 'logs' | 'online' | 'parser';

export const MONITORING_SUMMARY_REFETCH_INTERVAL_MS = 30_000;

export interface MonitoringQueryState {
  settingsActive: boolean;
  monitoringTabActive: boolean;
  liveEnabled: boolean;
  logsOpen?: boolean;
}

export function shouldEnableMonitoringQuery(
  kind: MonitoringQueryKind,
  state: MonitoringQueryState,
) {
  const visible = state.settingsActive && state.monitoringTabActive;
  if (!visible) return false;

  if (kind === 'summary') return state.liveEnabled;
  if (kind === 'logs') return Boolean(state.logsOpen);

  return false;
}

export function monitoringRefetchInterval(
  kind: MonitoringQueryKind,
  state: MonitoringQueryState,
) {
  return kind === 'summary' && shouldEnableMonitoringQuery(kind, state)
    ? MONITORING_SUMMARY_REFETCH_INTERVAL_MS
    : false;
}
