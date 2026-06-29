export const TELEGRAM_SDK_READY_EVENT = 'shamrai:telegram-sdk-ready';

let sdkLoadPromise: Promise<void> | null = null;

export function getTelegramWebApp<T = any>(): T | undefined {
  return (window as any).Telegram?.WebApp as T | undefined;
}

export function isTelegramMiniApp(): boolean {
  if (typeof window === 'undefined') return false;
  const webApp = getTelegramWebApp<{ initData?: string; initDataUnsafe?: { user?: unknown } }>();
  return Boolean(webApp && typeof webApp === 'object' && (webApp.initData || webApp.initDataUnsafe?.user));
}

export function hasTelegramLaunchParams(): boolean {
  if (typeof window === 'undefined') return false;
  const source = `${window.location.search || ''}&${window.location.hash || ''}`;
  return /(?:^|[&#?])tgWebApp(?:Data|Version|Platform|ThemeParams)=/i.test(source);
}

function queryStringFromLocationPart(source: string): string {
  const cleanSource = source.replace(/^[#?&]+/, '');
  const queryStart = cleanSource.indexOf('?');
  return queryStart >= 0 ? cleanSource.slice(queryStart + 1) : cleanSource;
}

function getTelegramLaunchParam(source: string, paramName: string): string {
  if (!source) return '';

  try {
    return new URLSearchParams(queryStringFromLocationPart(source)).get(paramName) || '';
  } catch {
    return '';
  }
}

export function getTelegramLaunchInitData(): string {
  if (typeof window === 'undefined') return '';

  return getTelegramLaunchParam(window.location.search || '', 'tgWebAppData')
    || getTelegramLaunchParam(window.location.hash || '', 'tgWebAppData');
}

export function hasTelegramInitDataLaunchParam(): boolean {
  return Boolean(getTelegramLaunchInitData());
}

export async function ensureTelegramSdk(timeoutMs = 2200): Promise<void> {
  if (getTelegramWebApp()) return;
  if (typeof document === 'undefined') return;

  if (!sdkLoadPromise) {
    sdkLoadPromise = new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.dispatchEvent(new Event(TELEGRAM_SDK_READY_EVENT));
        resolve();
      };

      const existingScript = document.querySelector<HTMLScriptElement>(
        'script[data-shamrai-telegram-sdk="true"], script[src="https://telegram.org/js/telegram-web-app.js"]'
      );

      if (existingScript) {
        existingScript.addEventListener('load', finish, { once: true });
        existingScript.addEventListener('error', finish, { once: true });
        window.setTimeout(finish, timeoutMs);
        return;
      }

      const script = document.createElement('script');
      script.src = 'https://telegram.org/js/telegram-web-app.js';
      script.async = true;
      script.dataset.shamraiTelegramSdk = 'true';
      script.addEventListener('load', finish, { once: true });
      script.addEventListener('error', finish, { once: true });
      document.head.appendChild(script);
      window.setTimeout(finish, timeoutMs);
    });
  }

  await Promise.race([
    sdkLoadPromise,
    new Promise<void>((resolve) => window.setTimeout(resolve, timeoutMs)),
  ]);
}
