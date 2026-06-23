import { apiFetch } from './api';
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

export interface WebPushReadinessState extends WebPushSubscriptionState {
  canRequest: boolean;
  installed: boolean;
  permission: NotificationPermission | 'unsupported';
}

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

export function webPushIsSupported(): boolean {
  return (
    typeof window !== 'undefined'
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

export async function getWebPushReadiness(): Promise<WebPushReadinessState> {
  if (isTelegramMiniApp()) {
    return {
      status: 'skipped_tma',
      message: 'Telegram Mini App получает сигналы через нативный бот.',
      canRequest: false,
      installed: true,
      permission: 'unsupported',
    };
  }

  const installed = pwaIsStandalone();
  if (looksLikeIosDevice() && !installed) {
    return {
      status: 'needs_install',
      message: 'Добавьте Shamrai на экран телефона и откройте установленную иконку.',
      canRequest: false,
      installed,
      permission: 'Notification' in window ? Notification.permission : 'unsupported',
    };
  }

  if (!webPushIsSupported()) {
    return {
      status: 'unsupported',
      message: 'Этот браузер не поддерживает Web Push для установленного веб-приложения.',
      canRequest: false,
      installed,
      permission: 'Notification' in window ? Notification.permission : 'unsupported',
    };
  }

  const publicKey = await loadVapidPublicKey();
  if (!publicKey) {
    return {
      status: 'missing_key',
      message: 'VAPID public key не настроен.',
      canRequest: false,
      installed,
      permission: Notification.permission,
    };
  }

  if (Notification.permission === 'denied') {
    return {
      status: 'denied',
      message: 'Системные уведомления для Shamrai запрещены в настройках браузера или телефона.',
      canRequest: false,
      installed,
      permission: Notification.permission,
    };
  }

  if (Notification.permission !== 'granted') {
    return {
      status: 'permission_required',
      message: 'Разрешите уведомления, чтобы получать сигналы в шторку телефона.',
      canRequest: true,
      installed,
      permission: Notification.permission,
    };
  }

  try {
    const registration = await getServiceWorkerRegistration();
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      return {
        status: 'subscription_required',
        message: 'Подключите push-канал Shamrai для этого устройства.',
        canRequest: true,
        installed,
        permission: Notification.permission,
      };
    }

    await saveSubscription(subscription);
    return {
      status: 'subscribed',
      message: 'Web Push подключен.',
      canRequest: false,
      installed,
      permission: Notification.permission,
    };
  } catch (error: any) {
    return {
      status: 'failed',
      message: error?.message || 'Не удалось проверить Web Push.',
      canRequest: true,
      installed,
      permission: Notification.permission,
    };
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
