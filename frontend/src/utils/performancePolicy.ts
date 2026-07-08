export type PerformanceProfileName = 'full' | 'balanced' | 'lowPower';

export interface BrowserConnectionInfo {
  effectiveType?: string;
  saveData?: boolean;
}

export interface BrowserHardwareHints {
  hardwareConcurrency?: number | null;
  deviceMemory?: number | null;
}

export interface BulkPrefetchPolicyInput {
  profile: PerformanceProfileName;
  isAppVisible: boolean;
  isTelegramSurface: boolean;
  connection?: BrowserConnectionInfo | null;
  documentVisibilityState?: DocumentVisibilityState | 'hidden' | 'visible' | 'prerender' | undefined;
  isConstrainedDevice?: boolean;
  globalPerformanceModeEnabled?: boolean;
}

const VERY_SLOW_CONNECTION_TYPES = new Set(['slow-2g', '2g']);
const SLOW_PREFETCH_CONNECTION_TYPES = new Set(['slow-2g', '2g', '3g']);

function normalizeEffectiveType(connection?: BrowserConnectionInfo | null) {
  return connection?.effectiveType?.toLowerCase() || '';
}

export function isVerySlowConnection(connection?: BrowserConnectionInfo | null) {
  return Boolean(connection?.saveData) || VERY_SLOW_CONNECTION_TYPES.has(normalizeEffectiveType(connection));
}

export function isSlowConnectionForPrefetch(connection?: BrowserConnectionInfo | null) {
  return Boolean(connection?.saveData) || SLOW_PREFETCH_CONNECTION_TYPES.has(normalizeEffectiveType(connection));
}

export function readBrowserConnection(
  navigatorLike: (Navigator & { connection?: BrowserConnectionInfo }) | undefined =
    typeof navigator === 'undefined' ? undefined : navigator as Navigator & { connection?: BrowserConnectionInfo },
) {
  return navigatorLike?.connection ?? null;
}

export function readSafeDeviceMemory(
  navigatorLike: (Navigator & { deviceMemory?: number }) | undefined =
    typeof navigator === 'undefined' ? undefined : navigator as Navigator & { deviceMemory?: number },
) {
  if (!navigatorLike || !('deviceMemory' in navigatorLike)) return null;
  const value = Number(navigatorLike.deviceMemory);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function readSafeHardwareConcurrency(
  navigatorLike: Pick<Navigator, 'hardwareConcurrency'> | undefined =
    typeof navigator === 'undefined' ? undefined : navigator,
) {
  const value = Number(navigatorLike?.hardwareConcurrency);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function isTinyHardware({ hardwareConcurrency, deviceMemory }: BrowserHardwareHints) {
  return (hardwareConcurrency != null && hardwareConcurrency <= 2)
    || (deviceMemory != null && deviceMemory <= 2);
}

export function isConstrainedHardware({ hardwareConcurrency, deviceMemory }: BrowserHardwareHints) {
  return (hardwareConcurrency != null && hardwareConcurrency <= 4)
    || (deviceMemory != null && deviceMemory <= 4);
}

export function canBulkPrefetch(input: BulkPrefetchPolicyInput) {
  if (input.globalPerformanceModeEnabled) return false;
  if (input.profile !== 'full') return false;
  if (!input.isAppVisible || input.documentVisibilityState === 'hidden') return false;
  if (input.isTelegramSurface) return false;
  if (input.isConstrainedDevice) return false;
  if (isSlowConnectionForPrefetch(input.connection)) return false;
  return true;
}

export function canIntentPrefetch(input: {
  profile: PerformanceProfileName;
  isAppVisible: boolean;
  documentVisibilityState?: DocumentVisibilityState | 'hidden' | 'visible' | 'prerender' | undefined;
}) {
  return input.profile !== 'lowPower'
    && input.isAppVisible
    && input.documentVisibilityState !== 'hidden';
}

export function canPrefetchAdminChunk(input: {
  userIsStaff: boolean;
  currentRouteIsAdmin?: boolean;
  explicitAdminIntent?: boolean;
}) {
  return input.userIsStaff && Boolean(input.currentRouteIsAdmin || input.explicitAdminIntent);
}

export function dataWorkerThresholdForProfile(profile: PerformanceProfileName) {
  if (profile === 'lowPower') return 60;
  if (profile === 'balanced') return 100;
  return 150;
}

export function supportsWebSocketRuntime() {
  return typeof WebSocket !== 'undefined';
}

export function supportsIntersectionObserverRuntime() {
  return typeof IntersectionObserver !== 'undefined';
}

export function supportsServiceWorkerRuntime() {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
}
