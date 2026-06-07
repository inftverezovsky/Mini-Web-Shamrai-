export const TELEGRAM_SDK_READY_EVENT = 'shamrai:telegram-sdk-ready';

let sdkLoadPromise: Promise<void> | null = null;

export function getTelegramWebApp<T = any>(): T | undefined {
  return (window as any).Telegram?.WebApp as T | undefined;
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
