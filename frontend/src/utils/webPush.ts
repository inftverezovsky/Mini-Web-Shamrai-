import { apiFetch } from './api';
import { getIncomingSignalSoundState } from './signalAudio';
import { isTelegramMiniApp } from './telegramSdk';

export type WebPushStatus =
  | 'unsupported'
  | 'needs_install'
  | 'skipped_tma'
  | 'missing_key'
  | 'permission_required'
  | 'subscription_required'
  | 'denied'
  | 'default'
  | 'subscribed'
  | 'failed';

export interface WebPushSubscriptionState {
  status: WebPushStatus;
  message: string;
}

export type WebPushSubscriptionStatus = 'missing' | 'saved';
export type WebPushSoundStatus = 'locked' | 'unlocked' | 'unsupported';
export type WebPushPlatformHint =
  | 'telegram_native'
  | 'ios_install_required'
  | 'unsupported_browser'
  | 'desktop_web_push'
  | 'mobile_web_push'
  | 'missing_vapid'
  | 'permission_denied'
  | 'permission_required'
  | 'subscription_required'
  | 'ready'
  | 'failed';

export interface WebPushReadinessState extends WebPushSubscriptionState {
  canRequest: boolean;
  installed: boolean;
  permission: NotificationPermission | 'unsupported';
  subscription: WebPushSubscriptionStatus;
  sound: WebPushSoundStatus;
  fallbackRecommended: boolean;
  platformHint: WebPushPlatformHint;
}

type WebPushNotificationOptions = NotificationOptions & {
  renotify?: boolean;
  requireInteraction?: boolean;
  vibrate?: number[];
};

function urlBase64ToArrayBuffer(base64String: string): ArrayBuffer {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputBuffer = new ArrayBuffer(rawData.length);
  const outputArray = new Uint8Array(outputBuffer);

  for (let index = 0; index < rawData.length; index += 1) {
    outputArray[index] = rawData.charCodeAt(index);
  }

  return outputBuffer;
}

async function loadVapidPublicKey(): Promise<string> {
  const envKey = import.meta.env.VITE_WEB_PUSH_VAPID_PUBLIC_KEY || '';
  if (envKey) return envKey;

  const response = await apiFetch<{ public_key: string }>('/signals/web-push/public-key');
  return response.public_key || '';
}

function currentNotificationPermission(): NotificationPermission | 'unsupported' {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported';
  return Notification.permission;
}

export function webPushIsSupported(): boolean {
  return (
    typeof window !== 'undefined'
    && typeof navigator !== 'undefined'
    && 'serviceWorker' in navigator
    && 'PushManager' in window
    && 'Notification' in window
    && window.isSecureContext
  );
}

export function pwaIsStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  const navigatorWithStandalone = navigator as Navigator & { standalone?: boolean };
  return Boolean(
    window.matchMedia?.('(display-mode: standalone)').matches
    || navigatorWithStandalone.standalone
  );
}

export function looksLikeMobileDevice(): boolean {
  if (typeof navigator === 'undefined') return false;
  const userAgent = navigator.userAgent || '';
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(userAgent)) return true;

  // iPadOS Safari can present a desktop "Macintosh" user agent.
  return /Macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1;
}

export function looksLikeIosDevice(): boolean {
  if (typeof navigator === 'undefined') return false;
  const userAgent = navigator.userAgent || '';
  return /iPhone|iPad|iPod/i.test(userAgent)
    || (/Macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1);
}

export async function registerPwaServiceWorker(): Promise<void> {
  if (isTelegramMiniApp()) return;
  if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;

  try {
    const registration = await navigator.serviceWorker.register('/service-worker.js');
    await registration.update();
  } catch {
    // PWA shell registration is best-effort; the app itself should still load normally.
  }
}

async function getServiceWorkerRegistration(): Promise<ServiceWorkerRegistration> {
  const existingRegistration = await navigator.serviceWorker.getRegistration('/');
  return existingRegistration || await navigator.serviceWorker.register('/service-worker.js');
}

async function saveSubscription(subscription: PushSubscription): Promise<void> {
  await apiFetch('/signals/web-push/subscription', {
    method: 'PUT',
    body: JSON.stringify(subscription.toJSON()),
  });
}

function baseReadinessState(
  state: WebPushSubscriptionState & {
    canRequest: boolean;
    installed?: boolean;
    permission?: NotificationPermission | 'unsupported';
    subscription?: WebPushSubscriptionStatus;
    fallbackRecommended?: boolean;
    platformHint: WebPushPlatformHint;
  },
): WebPushReadinessState {
  return {
    installed: state.installed ?? pwaIsStandalone(),
    permission: state.permission ?? currentNotificationPermission(),
    subscription: state.subscription ?? 'missing',
    sound: getIncomingSignalSoundState(),
    fallbackRecommended: state.fallbackRecommended ?? false,
    ...state,
  };
}

export async function getWebPushReadiness(): Promise<WebPushReadinessState> {
  if (isTelegramMiniApp()) {
    return baseReadinessState({
      status: 'skipped_tma',
      message: 'Telegram Mini App получает сигналы через нативный бот.',
      canRequest: false,
      installed: true,
      permission: 'unsupported',
      fallbackRecommended: false,
      platformHint: 'telegram_native',
    });
  }

  const installed = pwaIsStandalone();
  if (looksLikeIosDevice() && !installed) {
    return baseReadinessState({
      status: 'needs_install',
      message: 'Добавьте Shamrai на экран телефона и откройте установленную иконку.',
      canRequest: false,
      installed,
      permission: currentNotificationPermission(),
      fallbackRecommended: true,
      platformHint: 'ios_install_required',
    });
  }

  if (!webPushIsSupported()) {
    return baseReadinessState({
      status: 'unsupported',
      message: 'Этот браузер не поддерживает Web Push для установленного веб-приложения.',
      canRequest: false,
      installed,
      permission: currentNotificationPermission(),
      fallbackRecommended: true,
      platformHint: 'unsupported_browser',
    });
  }

  const publicKey = await loadVapidPublicKey();
  if (!publicKey) {
    return baseReadinessState({
      status: 'missing_key',
      message: 'VAPID public key не настроен.',
      canRequest: false,
      installed,
      permission: Notification.permission,
      fallbackRecommended: true,
      platformHint: 'missing_vapid',
    });
  }

  if (Notification.permission === 'denied') {
    return baseReadinessState({
      status: 'denied',
      message: 'Системные уведомления для Shamrai запрещены в настройках браузера или телефона.',
      canRequest: false,
      installed,
      permission: Notification.permission,
      fallbackRecommended: true,
      platformHint: 'permission_denied',
    });
  }

  if (Notification.permission !== 'granted') {
    return baseReadinessState({
      status: 'permission_required',
      message: 'Разрешите уведомления, чтобы получать сигналы в шторку телефона.',
      canRequest: true,
      installed,
      permission: Notification.permission,
      platformHint: looksLikeMobileDevice() ? 'mobile_web_push' : 'desktop_web_push',
    });
  }

  try {
    const registration = await getServiceWorkerRegistration();
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      return baseReadinessState({
        status: 'subscription_required',
        message: 'Подключите push-канал Shamrai для этого устройства.',
        canRequest: true,
        installed,
        permission: Notification.permission,
        platformHint: 'subscription_required',
      });
    }

    await saveSubscription(subscription);
    return baseReadinessState({
      status: 'subscribed',
      message: 'Web Push подключен.',
      canRequest: false,
      installed,
      permission: Notification.permission,
      subscription: 'saved',
      platformHint: 'ready',
    });
  } catch (error: any) {
    return baseReadinessState({
      status: 'failed',
      message: error?.message || 'Не удалось проверить Web Push.',
      canRequest: true,
      installed,
      permission: Notification.permission,
      fallbackRecommended: true,
      platformHint: 'failed',
    });
  }
}

export async function registerWebPushSubscription(): Promise<WebPushSubscriptionState> {
  if (isTelegramMiniApp()) {
    return { status: 'skipped_tma', message: 'Telegram Mini App получает сигналы через нативный бот.' };
  }

  if (!webPushIsSupported()) {
    return { status: 'unsupported', message: 'Web Push доступен только в HTTPS/PWA браузере.' };
  }

  const publicKey = await loadVapidPublicKey();
  if (!publicKey) {
    return { status: 'missing_key', message: 'VAPID public key не настроен.' };
  }

  let permission = Notification.permission;
  if (permission === 'default') {
    permission = await Notification.requestPermission();
  }

  if (permission === 'denied') {
    return { status: 'denied', message: 'Браузер запретил push-уведомления.' };
  }

  if (permission !== 'granted') {
    return { status: 'default', message: 'Push-уведомления не включены.' };
  }

  try {
    const registration = await getServiceWorkerRegistration();
    const existingSubscription = await registration.pushManager.getSubscription();
    const subscription = existingSubscription || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToArrayBuffer(publicKey),
    });

    await saveSubscription(subscription);

    return { status: 'subscribed', message: 'Web Push подключен.' };
  } catch (error: any) {
    return { status: 'failed', message: error?.message || 'Не удалось подключить Web Push.' };
  }
}

export async function showWebPushTestNotification(): Promise<WebPushSubscriptionState> {
  if (!webPushIsSupported()) {
    return { status: 'unsupported', message: 'Тестовое push-уведомление недоступно в этом браузере.' };
  }
  if (Notification.permission !== 'granted') {
    return { status: 'permission_required', message: 'Сначала разрешите уведомления для Shamrai.' };
  }

  try {
    const registration = await getServiceWorkerRegistration();
    const options: WebPushNotificationOptions = {
      body: 'Тестовый сигнал: звук мягкий, push-канал активен.',
      tag: 'shamrai-test-notification',
      data: { url: '/app?open=web-bot-chat', type: 'test_notification' },
      icon: '/brand/shamrai-favicon.png',
      badge: '/brand/shamrai-favicon.png',
      vibrate: [50],
      renotify: true,
      requireInteraction: false,
    };
    await registration.showNotification('Shamrai уведомления готовы', options);
    return { status: 'subscribed', message: 'Тестовое уведомление отправлено.' };
  } catch (error: any) {
    return { status: 'failed', message: error?.message || 'Не удалось показать тестовое уведомление.' };
  }
}
