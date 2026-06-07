import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { UserResponse } from '../schemas/schemas';
import { AUTH_EXPIRED_EVENT } from '../utils/api';
import { ensureTelegramSdk, getTelegramWebApp } from '../utils/telegramSdk';

interface AuthContextType {
  token: string | null;
  user: UserResponse | null;
  loading: boolean;
  error: string | null;
  login: () => Promise<void>;
  logout: () => void;
  setUser: React.Dispatch<React.SetStateAction<UserResponse | null>>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(localStorage.getItem('bet_tma_jwt_token'));
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // In production (Docker/nginx), use relative path so nginx proxies /api to backend.
  // In development, fall back to localhost:8000.
  const API_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '');
  const allowDebugAuth = import.meta.env.VITE_ENABLE_DEBUG_AUTH === 'true';
  const allowLocalMock = allowDebugAuth;
  const debugRoleStorageKey = 'bet_tma_debug_role';

  const createMockUser = useCallback((): UserResponse => {
    const mockRole = localStorage.getItem(debugRoleStorageKey) || 'user';
    const isAdmin = mockRole === 'admin';
    const onboardedOverride = localStorage.getItem('bet_tma_mock_is_onboarded');
    const isOnboarded = onboardedOverride === null ? false : onboardedOverride === 'true';
    const now = new Date().toISOString();

    return {
      telegram_id: isAdmin ? 987654321 : 123456789,
      username: isAdmin ? 'debug_admin' : 'debug_user',
      first_name: isAdmin ? 'Алексей' : 'Иван',
      last_name: isAdmin ? 'Админ' : 'Подписчик',
      role: isAdmin ? 'admin' : 'user',
      stats_display_mode: 'percent',
      bankroll: 50000,
      is_onboarded: isOnboarded,
      experience_level: 'amateur',
      bankroll_size: 'mid',
      favorite_sports: [],
      risk_tolerance: 'balanced',
      primary_bookmaker: 'fonbet',
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
      is_night_mode: false,
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

  const login = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Resolve Telegram SDK references or use browser debug settings
      await ensureTelegramSdk();
      const tg = getTelegramWebApp<{ initData?: string }>();
      let initData = tg?.initData || '';
      if (!initData) {
        if (!allowDebugAuth) {
          throw new Error('Откройте приложение внутри Telegram');
        }
        const mockRole = localStorage.getItem(debugRoleStorageKey) || 'user';
        initData = mockRole === 'admin' ? 'mock_debug_admin' : 'mock_debug_user';
      }

      const response = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ initData }),
      });

      if (!response.ok) {
        throw new Error('Авторизация на сервере не удалась');
      }

      const data = await response.json();
      setToken(data.access_token);
      setUser(data.user);
      localStorage.setItem('bet_tma_jwt_token', data.access_token);
    } catch (err: any) {
      console.error('Auth context login error:', err);
      if (allowLocalMock) {
        const mockToken = 'mock_debug_access_token';
        setToken(mockToken);
        setUser(createMockUser());
        localStorage.setItem('bet_tma_jwt_token', mockToken);
        setError(null);
        return;
      }
      setError(err.message || 'Ошибка подключения к серверу авторизации');
    } finally {
      setLoading(false);
    }
  }, [API_URL, allowDebugAuth, allowLocalMock, createMockUser, debugRoleStorageKey]);

  useEffect(() => {
    login();
  }, [login]);

  useEffect(() => {
    const handleAuthExpired = () => {
      setToken(null);
      setUser(null);
      localStorage.removeItem('bet_tma_jwt_token');
      void login();
    };

    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, [login]);

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('bet_tma_jwt_token');
  };

  return (
    <AuthContext.Provider value={{ token, user, loading, error, login, logout, setUser }}>
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
