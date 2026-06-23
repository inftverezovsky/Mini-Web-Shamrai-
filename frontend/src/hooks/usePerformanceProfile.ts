import { useEffect, useMemo, useState } from 'react';
import {
  getTelegramWebApp,
  hasTelegramLaunchParams,
  isTelegramMiniApp,
  TELEGRAM_SDK_READY_EVENT,
} from '../utils/telegramSdk';

export type PerformanceProfile = 'full' | 'balanced' | 'lowPower';

interface ConnectionInfo {
  effectiveType?: string;
  saveData?: boolean;
}

interface TelegramWebAppPerformanceInfo {
  isActive?: boolean;
  platform?: string;
  version?: string;
  devicePerformanceClass?: number | string;
  onEvent?: (eventType: string, eventHandler: () => void) => void;
  offEvent?: (eventType: string, eventHandler: () => void) => void;
}

interface PerformanceSnapshot {
  profile: PerformanceProfile;
  isAppVisible: boolean;
  isTelegramSurface: boolean;
  prefersReducedMotion: boolean;
}

export interface PerformanceProfileState extends PerformanceSnapshot {
  isLowPower: boolean;
  isBalanced: boolean;
  shouldReduceMotion: boolean;
  canBulkPreload: boolean;
  canPreloadOnIntent: boolean;
  canPlayIntroVideo: boolean;
}

const DEV_OVERRIDE_STORAGE_KEY = 'shamrai_performance_profile_override';
let rootBindingCount = 0;

function isPerformanceProfile(value: string | null): value is PerformanceProfile {
  return value === 'full' || value === 'balanced' || value === 'lowPower';
}

function readDevProfileOverride(): PerformanceProfile | null {
  if (!import.meta.env.DEV || typeof window === 'undefined') return null;

  const params = new URLSearchParams(window.location.search);
  const queryOverride = params.get('perf_profile') || params.get('performance_profile');
  if (queryOverride === 'clear') {
    localStorage.removeItem(DEV_OVERRIDE_STORAGE_KEY);
    return null;
  }
  if (isPerformanceProfile(queryOverride)) {
    localStorage.setItem(DEV_OVERRIDE_STORAGE_KEY, queryOverride);
    return queryOverride;
  }

  const storedOverride = localStorage.getItem(DEV_OVERRIDE_STORAGE_KEY);
  return isPerformanceProfile(storedOverride) ? storedOverride : null;
}

function readTelegramPlatform(webApp: TelegramWebAppPerformanceInfo | undefined) {
  if (webApp?.platform) return webApp.platform.toLowerCase();
  if (typeof window === 'undefined') return '';

  const source = `${window.location.search || ''}&${window.location.hash || ''}`;
  const match = source.match(/(?:^|[&#?])tgWebAppPlatform=([^&#]+)/i);
  return match ? decodeURIComponent(match[1]).toLowerCase() : '';
}

function readTelegramPerformanceClass(webApp: TelegramWebAppPerformanceInfo | undefined) {
  const rawValue = webApp?.devicePerformanceClass;
  const parsedValue = typeof rawValue === 'string' ? Number(rawValue) : rawValue;
  return Number.isFinite(parsedValue) ? Number(parsedValue) : null;
}

function getPerformanceSnapshot(): PerformanceSnapshot {
  if (typeof window === 'undefined') {
    return {
      profile: 'full',
      isAppVisible: true,
      isTelegramSurface: false,
      prefersReducedMotion: false,
    };
  }

  const webApp = getTelegramWebApp<TelegramWebAppPerformanceInfo>();
  const connection = (navigator as Navigator & { connection?: ConnectionInfo }).connection;
  const effectiveType = connection?.effectiveType?.toLowerCase() || '';
  const saveData = Boolean(connection?.saveData);
  const hardwareConcurrency = navigator.hardwareConcurrency || 8;
  const deviceMemory = Number((navigator as Navigator & { deviceMemory?: number }).deviceMemory);
  const platform = readTelegramPlatform(webApp);
  const userAgent = navigator.userAgent || '';
  const isAndroid = platform === 'android' || /Android/i.test(userAgent);
  const isTelegramSurface = isTelegramMiniApp() || hasTelegramLaunchParams();
  const telegramPerformanceClass = readTelegramPerformanceClass(webApp);
  const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  const isDocumentVisible = document.visibilityState !== 'hidden';
  const isTelegramActive = webApp?.isActive !== false;
  const override = readDevProfileOverride();

  if (override) {
    return {
      profile: override,
      isAppVisible: isDocumentVisible && isTelegramActive,
      isTelegramSurface,
      prefersReducedMotion,
    };
  }

  const verySlowNetwork = saveData || effectiveType === 'slow-2g' || effectiveType === '2g';
  const slowNetwork = verySlowNetwork || effectiveType === '3g';
  const tinyHardware = hardwareConcurrency <= 2 || (Number.isFinite(deviceMemory) && deviceMemory > 0 && deviceMemory <= 2);
  const constrainedHardware = hardwareConcurrency <= 4 || (Number.isFinite(deviceMemory) && deviceMemory > 0 && deviceMemory <= 4);
  const weakTelegramDevice = isTelegramSurface && isAndroid && (constrainedHardware || slowNetwork || (telegramPerformanceClass !== null && telegramPerformanceClass <= 1));

  let profile: PerformanceProfile = 'full';
  if (prefersReducedMotion || verySlowNetwork || tinyHardware || weakTelegramDevice) {
    profile = 'lowPower';
  } else if (isTelegramSurface || slowNetwork || constrainedHardware || (telegramPerformanceClass !== null && telegramPerformanceClass <= 2)) {
    profile = 'balanced';
  }

  return {
    profile,
    isAppVisible: isDocumentVisible && isTelegramActive,
    isTelegramSurface,
    prefersReducedMotion,
  };
}

export function usePerformanceProfile(): PerformanceProfileState {
  const [snapshot, setSnapshot] = useState<PerformanceSnapshot>(() => getPerformanceSnapshot());

  useEffect(() => {
    let disposed = false;
    const syncSnapshot = () => {
      if (!disposed) setSnapshot(getPerformanceSnapshot());
    };
    const webApp = getTelegramWebApp<TelegramWebAppPerformanceInfo>();

    syncSnapshot();
    document.addEventListener('visibilitychange', syncSnapshot);
    window.addEventListener('pageshow', syncSnapshot);
    window.addEventListener('pagehide', syncSnapshot);
    window.addEventListener(TELEGRAM_SDK_READY_EVENT, syncSnapshot);
    webApp?.onEvent?.('activated', syncSnapshot);
    webApp?.onEvent?.('deactivated', syncSnapshot);

    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', syncSnapshot);
      window.removeEventListener('pageshow', syncSnapshot);
      window.removeEventListener('pagehide', syncSnapshot);
      window.removeEventListener(TELEGRAM_SDK_READY_EVENT, syncSnapshot);
      webApp?.offEvent?.('activated', syncSnapshot);
      webApp?.offEvent?.('deactivated', syncSnapshot);
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    rootBindingCount += 1;
    root.dataset.performanceProfile = snapshot.profile;
    root.dataset.shamraiAppVisible = snapshot.isAppVisible ? 'true' : 'false';
    root.dataset.telegramSurface = snapshot.isTelegramSurface ? 'true' : 'false';
    root.classList.toggle('shamrai-low-power', snapshot.profile === 'lowPower');
    root.classList.toggle('shamrai-balanced-performance', snapshot.profile === 'balanced');
    root.classList.toggle('shamrai-app-hidden', !snapshot.isAppVisible);

    return () => {
      rootBindingCount = Math.max(0, rootBindingCount - 1);
      if (rootBindingCount > 0) return;
      root.classList.remove('shamrai-low-power', 'shamrai-balanced-performance', 'shamrai-app-hidden');
      delete root.dataset.performanceProfile;
      delete root.dataset.shamraiAppVisible;
      delete root.dataset.telegramSurface;
    };
  }, [snapshot]);

  return useMemo(() => {
    const isLowPower = snapshot.profile === 'lowPower';
    const isBalanced = snapshot.profile === 'balanced';
    const shouldReduceMotion = isLowPower || snapshot.prefersReducedMotion || !snapshot.isAppVisible;

    return {
      ...snapshot,
      isLowPower,
      isBalanced,
      shouldReduceMotion,
      canBulkPreload: snapshot.profile === 'full' && snapshot.isAppVisible && !snapshot.isTelegramSurface,
      canPreloadOnIntent: !isLowPower && snapshot.isAppVisible,
      canPlayIntroVideo: !isLowPower && snapshot.isAppVisible,
    };
  }, [snapshot]);
}
