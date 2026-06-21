import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { UserResponse } from '../schemas/schemas';
import { AUTH_EXPIRED_EVENT, apiFetch } from '../utils/api';
import { API_BASE_URL, DEBUG_AUTH_ENABLED, DEBUG_ROLE_STORAGE_KEY } from '../config/api';
import { clearStoredAuthToken, getStoredAuthToken, setStoredAuthToken } from '../utils/authStorage';
import { ensureTelegramSdk, getTelegramWebApp, hasTelegramLaunchParams } from '../utils/telegramSdk';
import { consumeVkRedirectResult, isVkRedirectStartedError, loginVkProfile } from '../utils/vkId';

export interface TelegramWidgetPayload {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  phone?: string;
  phone_number?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

interface TelegramBotAuthStartResponse {
  auth_token: string;
  bot_url: string;
  expires_at: string;
}

interface TelegramBotAuthStatusResponse {
  status: 'pending' | 'confirmed' | 'expired' | 'consumed';
  access_token?: string;
  token_type?: string;
  user?: UserResponse;
}

interface AuthContextType {
  token: string | null;
  user: UserResponse | null;
  loading: boolean;
  error: string | null;
  login: () => Promise<void>;
  loginWithVk: () => Promise<void>;
  loginWithTelegramWidget: (payload: TelegramWidgetPayload) => Promise<void>;
  loginWithTelegramBot: () => Promise<void>;
  logout: () => void;
  setUser: React.Dispatch<React.SetStateAction<UserResponse | null>>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TELEGRAM_BOT_AUTH_POLL_INTERVAL_MS = 1800;

function wait(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(getStoredAuthToken());
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const bootStartedRef = useRef(false);

  const API_URL = API_BASE_URL;
  const allowDebugAuth = DEBUG_AUTH_ENABLED;
  const allowLocalMock = allowDebugAuth;

  const createMockUser = useCallback((): UserResponse => {
    const mockRole = localStorage.getItem(DEBUG_ROLE_STORAGE_KEY) || 'user';
    const isAdmin = mockRole === 'admin';
    const onboardedOverride = localStorage.getItem('bet_tma_mock_is_onboarded');
    const isOnboarded = onboardedOverride === null ? false : onboardedOverride === 'true';
    const now = new Date().toISOString();

    return {
      telegram_id: isAdmin ? 987654321 : 123456789,
      username: isAdmin ? 'debug_admin' : 'debug_user',
      first_name: isAdmin ? 'Алексей' : 'Иван',
      last_name: isAdmin ? 'Админ' : 'Подписчик',
      phone: null,
      photo_url: null,
      is_web_only: false,
      role: isAdmin ? 'admin' : 'user',
      stats_display_mode: 'percent',
      bankroll: 50000,
      is_onboarded: isOnboarded,
      experience_level: 'amateur',
      bankroll_size: 'mid',
      favorite_sports: [],
      risk_tolerance: 'balanced',
      primary_bookmaker: 'fonbet',
      vk_user_id: localStorage.getItem('bet_tma_mock_vk_user_id'),
      vk_group_member: localStorage.getItem('bet_tma_mock_vk_group_member') === 'true',
      vk_messages_allowed: localStorage.getItem('bet_tma_mock_vk_messages_allowed') === 'true',
      vk_notifications_allowed: localStorage.getItem('bet_tma_mock_vk_notifications_allowed') === 'true',
      currency_preference: 'RUB',
      purchased_bets_balance: 12,
      free_bets_available: 0,
      matches_remaining: 12,
      guarantee_active: false,
      guarantee_opened_from_bet_id: null,
      guarantee_closed_at: null,
      onboarding_goal: 'profit',
      ab_group: null,
      tg_chat_joined: true,
      has_used_shield: false,
      alert_min_coef: 1.5,
      odds_drop_notifications_enabled: true,
      is_night_mode: false,
      night_mode_start: '23:00',
      night_mode_end: '08:00',
      preferred_sports: [],
      other_bookmaker_name: null,
      client_group: null,
      client_tag: null,
      created_at: now,
      updated_at: now,
      bookmakers: [],
      badges: [],
    };
  }, []);

  const clearStoredAuth = useCallback(() => {
    setToken(null);
    setUser(null);
    clearStoredAuthToken();
  }, []);

  const applyLoginResponse = useCallback((data: { access_token: string; user: UserResponse }) => {
    setToken(data.access_token);
    setUser(data.user);
    setStoredAuthToken(data.access_token);
    setError(null);
  }, []);

  const fetchCurrentUser = useCallback(async (candidateToken: string) => {
    const response = await fetch(`${API_URL}/api/users/me`, {
      credentials: 'include',
      headers: {
        Authorization: `Bearer ${candidateToken}`,
      },
    });

    if (!response.ok) {
      throw new Error('Сессия устарела');
    }

    const freshUser = await response.json();
    setToken(candidateToken);
    setUser(freshUser);
    setStoredAuthToken(candidateToken);
    setError(null);
  }, [API_URL]);

  const runTelegramMiniAppLogin = useCallback(async (initData: string) => {
    const response = await fetch(`${API_URL}/api/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ initData }),
    });

    if (!response.ok) {
      throw new Error('Авторизация Telegram на сервере не удалась');
    }

    applyLoginResponse(await response.json());
  }, [API_URL, applyLoginResponse]);

  const login = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const storedToken = getStoredAuthToken();
      if (allowLocalMock && storedToken === 'mock_debug_access_token') {
        setToken(storedToken);
        setUser(createMockUser());
        return;
      }

      if (storedToken) {
        try {
          await fetchCurrentUser(storedToken);
          return;
        } catch {
          clearStoredAuth();
        }
      }

      let initData = '';
      if (getTelegramWebApp() || hasTelegramLaunchParams()) {
        await ensureTelegramSdk(1200);
        const tg = getTelegramWebApp<{ initData?: string }>();
        initData = tg?.initData || '';
      }

      if (initData) {
        await runTelegramMiniAppLogin(initData);
        return;
      }

      if (allowDebugAuth) {
        const mockRole = localStorage.getItem(DEBUG_ROLE_STORAGE_KEY) || 'user';
        await runTelegramMiniAppLogin(mockRole === 'admin' ? 'mock_debug_admin' : 'mock_debug_user');
        return;
      }

      clearStoredAuth();
    } catch (err: any) {
      console.error('Auth context login error:', err);
      if (allowLocalMock) {
        const mockToken = 'mock_debug_access_token';
        setToken(mockToken);
        setUser(createMockUser());
        setStoredAuthToken(mockToken);
        setError(null);
        return;
      }
      setError(err.message || 'Ошибка подключения к серверу авторизации');
    } finally {
      setLoading(false);
    }
  }, [
    allowDebugAuth,
    allowLocalMock,
    clearStoredAuth,
    createMockUser,
    fetchCurrentUser,
    runTelegramMiniAppLogin,
  ]);

  const loginWithVk = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await loginVkProfile();
      applyLoginResponse(data);
    } catch (err: any) {
      if (isVkRedirectStartedError(err)) return;
      setError(err?.message || 'Не удалось войти через VK ID');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [applyLoginResponse]);

  const handleVkRedirectResult = useCallback(async () => {
    let redirectResult: ReturnType<typeof consumeVkRedirectResult> = null;
    try {
      redirectResult = consumeVkRedirectResult();
    } catch (err: any) {
      setError(err?.message || 'Не удалось обработать ответ VK ID');
      setLoading(false);
      return true;
    }

    if (!redirectResult) return false;

    try {
      setLoading(true);
      setError(null);

      if (redirectResult.action === 'login') {
        const data = await apiFetch<{ access_token: string; user: UserResponse }>('/auth/vk/login', {
          method: 'POST',
          body: JSON.stringify(redirectResult.payload),
        });
        applyLoginResponse(data);
        return true;
      }

      const storedToken = getStoredAuthToken();
      if (!storedToken) {
        throw new Error('Telegram-сессия устарела. Войдите через Telegram и повторите привязку VK.');
      }

      await apiFetch('/auth/vk/link', {
        method: 'POST',
        body: JSON.stringify(redirectResult.payload),
      });
      await fetchCurrentUser(storedToken);
      return true;
    } catch (err: any) {
      setError(err?.message || 'Не удалось завершить авторизацию VK ID');
      return true;
    } finally {
      setLoading(false);
    }
  }, [applyLoginResponse, fetchCurrentUser]);

  const loginWithTelegramWidget = useCallback(async (payload: TelegramWidgetPayload) => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`${API_URL}/api/auth/telegram-widget`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error('Telegram Login Widget не прошел проверку');
      }

      applyLoginResponse(await response.json());
    } catch (err: any) {
      setError(err?.message || 'Не удалось войти через Telegram');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [API_URL, applyLoginResponse]);

  const loginWithTelegramBot = useCallback(async () => {
    try {
      setError(null);
      const session = await apiFetch<TelegramBotAuthStartResponse>('/auth/telegram/bot-session', {
        method: 'POST',
      });

      window.open(session.bot_url, '_blank', 'noopener,noreferrer');

      const expiresAt = new Date(session.expires_at).getTime();
      while (Date.now() < expiresAt) {
        await wait(TELEGRAM_BOT_AUTH_POLL_INTERVAL_MS);
        const authStatus = await apiFetch<TelegramBotAuthStatusResponse>(
          `/auth/telegram/bot-session/${encodeURIComponent(session.auth_token)}`,
        );

        if (authStatus.status === 'confirmed' && authStatus.access_token && authStatus.user) {
          applyLoginResponse({
            access_token: authStatus.access_token,
            user: authStatus.user,
          });
          return;
        }

        if (authStatus.status === 'expired' || authStatus.status === 'consumed') {
          break;
        }
      }

      throw new Error('Ссылка Telegram-входа устарела. Нажмите кнопку еще раз.');
    } catch (err: any) {
      setError(err?.message || 'Не удалось войти через Telegram');
      throw err;
    }
  }, [applyLoginResponse]);

  useEffect(() => {
    if (bootStartedRef.current) return;
    bootStartedRef.current = true;
    let cancelled = false;

    async function bootAuth() {
      const vkRedirectHandled = await handleVkRedirectResult();
      if (!cancelled && !vkRedirectHandled) {
        await login();
      }
    }

    void bootAuth();
    return () => {
      cancelled = true;
    };
  }, [handleVkRedirectResult, login]);

  useEffect(() => {
    const handleAuthExpired = () => {
      clearStoredAuth();
      void login();
    };

    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, [clearStoredAuth, login]);

  const logout = () => {
    clearStoredAuth();
  };

  return (
    <AuthContext.Provider
      value={{
        token,
        user,
        loading,
        error,
        login,
        loginWithVk,
        loginWithTelegramWidget,
        loginWithTelegramBot,
        logout,
        setUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
