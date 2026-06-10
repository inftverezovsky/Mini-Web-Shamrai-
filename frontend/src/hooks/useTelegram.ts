import { useEffect, useState } from 'react';
import { ensureTelegramSdk, getTelegramWebApp, TELEGRAM_SDK_READY_EVENT } from '../utils/telegramSdk';

declare global {
  interface Window {
    Telegram?: {
      WebApp: TelegramWebApp;
    };
  }
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: {
    user?: TelegramUser;
  };
  ready: () => void;
  expand: () => void;
  close: () => void;
  openInvoice?: (url: string, callback?: (status: string) => void) => void;
}

export interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
  is_premium?: boolean;
}

export function useTelegram() {
  const [isReady, setIsReady] = useState(false);
  const [user, setUser] = useState<TelegramUser | null>(null);
  const [initData, setInitData] = useState<string>('');
  const debugAuthEnabled = import.meta.env.DEV && import.meta.env.VITE_ENABLE_DEBUG_AUTH === 'true';
  const debugRoleStorageKey = 'bet_tma_debug_role';
  
  // Local storage mock configuration to persist role state across page refreshes in browser
  const [mockRole, setMockRole] = useState<'user' | 'admin'>(
    (localStorage.getItem(debugRoleStorageKey) as 'user' | 'admin') || 'user'
  );

  const tg = getTelegramWebApp<TelegramWebApp>();

  useEffect(() => {
    let cancelled = false;

    const syncTelegramState = () => {
      if (cancelled) return;
      const currentTg = getTelegramWebApp<TelegramWebApp>();
      if (currentTg && currentTg.initData) {
        currentTg.ready();
        currentTg.expand();
        setIsReady(true);
        if (currentTg.initDataUnsafe?.user) {
          setUser(currentTg.initDataUnsafe.user);
        }
        setInitData(currentTg.initData);
      } else if (debugAuthEnabled) {
        // Browser fallback (runs outside Telegram client in debug session)
        setIsReady(true);
        const isMockUser = mockRole === 'user';
        setUser({
          id: isMockUser ? 123456789 : 987654321,
          first_name: isMockUser ? 'Иван' : 'Алексей',
          last_name: isMockUser ? 'Подписчик' : 'Админ',
          username: isMockUser ? 'debug_user' : 'debug_admin',
        });
        setInitData(isMockUser ? 'mock_debug_user' : 'mock_debug_admin');
      } else {
        setIsReady(true);
        setUser(null);
        setInitData('');
      }
    };

    window.addEventListener(TELEGRAM_SDK_READY_EVENT, syncTelegramState);
    void ensureTelegramSdk().then(syncTelegramState);

    return () => {
      cancelled = true;
      window.removeEventListener(TELEGRAM_SDK_READY_EVENT, syncTelegramState);
    };
  }, [mockRole, debugAuthEnabled]);

  const toggleMockRole = () => {
    if (!debugAuthEnabled) return;
    const nextRole = mockRole === 'user' ? 'admin' : 'user';
    setMockRole(nextRole);
    localStorage.setItem(debugRoleStorageKey, nextRole);
    window.location.reload(); // Force reload to refresh router auth logic
  };

  return {
    tg,
    user,
    initData,
    isReady,
    isTelegram: !!tg?.initData,
    debugAuthEnabled,
    mockRole,
    toggleMockRole,
    closeApp: () => getTelegramWebApp<TelegramWebApp>()?.close(),
  };
}
