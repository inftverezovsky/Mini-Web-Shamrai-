import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react';
import { UserResponse } from '../schemas/schemas';
import { AUTH_EXPIRED_EVENT, apiFetch } from '../utils/api';
import { API_BASE_URL, DEBUG_AUTH_ENABLED, DEBUG_ROLE_STORAGE_KEY } from '../config/api';
import {
  MOCK_DEBUG_AUTH_TOKEN,
  clearStoredAuthToken,
  getStoredAuthToken,
  setStoredAuthToken,
} from '../utils/authStorage';
import { formatApiErrorMessage } from '../api/errors';
import {
  ensureTelegramSdk,
  getTelegramLaunchInitData,
  getTelegramWebApp,
  hasTelegramInitDataLaunchParam,
} from '../utils/telegramSdk';
import {
  clearVkAuthCooldown,
  completeVkRedirect,
  consumeVkRedirectResult,
  isVkRedirectStartedError,
  loginVkProfile,
  rememberVkAuthCooldownForError,
} from '../utils/vkId';
import { identityDeviceHeader } from '../utils/identityDevice';
import { clearCsrfToken, csrfHeaderForRequest } from '../utils/csrf';
import {
  createTelegramBotAuthCoordinator,
  type TelegramBotAuthOptions,
} from '../utils/telegramBotAuthCoordinator';

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

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  loading: boolean;
  error: string | null;
}

interface AuthActions {
  login: () => Promise<void>;
  loginWithVk: () => Promise<void>;
  loginWithTelegramWidget: (payload: TelegramWidgetPayload) => Promise<void>;
  loginWithTelegramBot: (options?: TelegramBotAuthOptions) => Promise<void>;
  logout: () => void;
  setUser: React.Dispatch<React.SetStateAction<UserResponse | null>>;
}

type AuthStateUpdater = AuthState | ((current: AuthState) => AuthState);

interface AuthStore {
  getSnapshot: () => AuthState;
  setSnapshot: (updater: AuthStateUpdater) => void;
  subscribe: (listener: () => void) => () => void;
}

const AuthStoreContext = createContext<AuthStore | undefined>(undefined);
const AuthActionsContext = createContext<AuthActions | undefined>(undefined);

const TELEGRAM_BOT_AUTH_POLL_INTERVAL_MS = 1800;

function wait(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function isNetworkFetchMessage(message?: string | null) {
  return Boolean(message && /failed to fetch|networkerror|load failed|network request failed/i.test(message));
}

function authErrorMessage(error: unknown, fallback: string) {
  const message = error instanceof Error
    ? error.message
    : typeof error === 'object' && error && 'message' in error
      ? String((error as { message?: unknown }).message || '')
      : typeof error === 'string'
        ? error
        : '';

  if (isNetworkFetchMessage(message)) {
    return 'Не удалось подключиться к серверу. Проверьте интернет и попробуйте еще раз.';
  }

  return message || fallback;
}

async function responseErrorMessage(response: Response, fallback: string) {
  const errorData = await response.json().catch(() => ({}));
  return formatApiErrorMessage(response.status, errorData?.detail) || fallback;
}

function createAuthStore(initialState: AuthState): AuthStore {
  let snapshot = initialState;
  const listeners = new Set<() => void>();

  return {
    getSnapshot: () => snapshot,
    setSnapshot: (updater) => {
      const nextSnapshot = typeof updater === 'function'
        ? updater(snapshot)
        : updater;

      if (Object.is(snapshot, nextSnapshot)) return;

      snapshot = nextSnapshot;
      listeners.forEach((listener) => listener());
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const storeRef = useRef<AuthStore>();
  if (!storeRef.current) {
    storeRef.current = createAuthStore({
      token: getStoredAuthToken(),
      user: null,
      loading: true,
      error: null,
    });
  }
  const authStore = storeRef.current;
  const telegramBotAuthCoordinatorRef = useRef<ReturnType<typeof createTelegramBotAuthCoordinator>>();
  if (!telegramBotAuthCoordinatorRef.current) {
    telegramBotAuthCoordinatorRef.current = createTelegramBotAuthCoordinator();
  }
  const telegramBotAuthCoordinator = telegramBotAuthCoordinatorRef.current;

  const setAuthState = useCallback((updater: AuthStateUpdater) => {
    authStore.setSnapshot(updater);
  }, [authStore]);

  const setToken = useCallback((token: string | null) => {
    setAuthState((current) => (current.token === token ? current : { ...current, token }));
  }, [setAuthState]);

  const setUser = useCallback<React.Dispatch<React.SetStateAction<UserResponse | null>>>((nextUser) => {
    setAuthState((current) => {
      const resolvedUser = typeof nextUser === 'function'
        ? nextUser(current.user)
        : nextUser;
      return current.user === resolvedUser ? current : { ...current, user: resolvedUser };
    });
  }, [setAuthState]);

  const setLoading = useCallback((loading: boolean) => {
    setAuthState((current) => (current.loading === loading ? current : { ...current, loading }));
  }, [setAuthState]);

  const setError = useCallback((error: string | null) => {
    setAuthState((current) => (current.error === error ? current : { ...current, error }));
  }, [setAuthState]);

  const API_URL = API_BASE_URL;
  const allowDebugAuth = DEBUG_AUTH_ENABLED;
  const allowLocalMock = allowDebugAuth;

  const createMockUser = useCallback((): UserResponse => {
    const mockRole = localStorage.getItem(DEBUG_ROLE_STORAGE_KEY) || 'user';
    const isAdmin = mockRole === 'admin';
    const onboardedOverride = localStorage.getItem('bet_tma_mock_is_onboarded');
    const isOnboarded = onboardedOverride === null ? false : onboardedOverride === 'true';
    const now = new Date().toISOString();
    const vkUserId = localStorage.getItem('bet_tma_mock_vk_user_id');
    const identityProviders = [
      'telegram',
      ...(vkUserId ? ['vk'] : []),
    ];
    const missingIdentityProviders = ['telegram', 'vk'].filter((provider) => !identityProviders.includes(provider));

    return {
      telegram_id: isAdmin ? 987654321 : 123456789,
      username: isAdmin ? 'debug_admin' : 'debug_user',
      first_name: isAdmin ? 'Алексей' : 'Иван',
      last_name: isAdmin ? 'Админ' : 'Подписчик',
      phone: null,
      photo_url: null,
      is_web_only: false,
      identity_complete: missingIdentityProviders.length === 0,
      identity_providers: identityProviders,
      missing_identity_providers: missingIdentityProviders,
      role: isAdmin ? 'admin' : 'user',
      stats_display_mode: 'percent',
      bankroll: 50000,
      is_onboarded: isOnboarded,
      experience_level: 'amateur',
      bankroll_size: 'mid',
      favorite_sports: [],
      risk_tolerance: 'balanced',
      primary_bookmaker: 'fonbet',
      vk_user_id: vkUserId,
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
    setAuthState((current) => (
      current.token === null && current.user === null
        ? current
        : { ...current, token: null, user: null }
    ));
    clearStoredAuthToken();
  }, [setAuthState]);

  const applyLoginResponse = useCallback((data: { access_token: string; user: UserResponse }) => {
    clearVkAuthCooldown();
    setStoredAuthToken(data.access_token);
    setToken(getStoredAuthToken());
    setUser(data.user);
    clearCsrfToken();
    setError(null);
  }, [setError, setToken, setUser]);

  const fetchCurrentUser = useCallback(async (candidateToken?: string | null) => {
    const headers = {
      ...identityDeviceHeader(),
      ...(candidateToken ? { Authorization: `Bearer ${candidateToken}` } : {}),
    };
    const response = await fetch(`${API_URL}/api/users/me`, {
      credentials: 'include',
      headers,
    });

    if (!response.ok) {
      throw new Error('Сессия устарела');
    }

    const freshUser = await response.json();
    setToken(candidateToken ?? null);
    setUser(freshUser);
    if (candidateToken) {
      setStoredAuthToken(candidateToken);
    }
    setError(null);
  }, [API_URL, setError, setToken, setUser]);

  const runTelegramMiniAppLogin = useCallback(async (initData: string) => {
    const response = await fetch(`${API_URL}/api/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...identityDeviceHeader(),
      },
      body: JSON.stringify({ initData }),
    });

    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, 'Авторизация Telegram на сервере не удалась'));
    }

    applyLoginResponse(await response.json());
  }, [API_URL, applyLoginResponse]);

  const login = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const storedToken = getStoredAuthToken();
      if (allowLocalMock && storedToken === MOCK_DEBUG_AUTH_TOKEN) {
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

      try {
        await fetchCurrentUser(null);
        return;
      } catch {
        clearStoredAuthToken();
      }

      let initData = '';
      if (getTelegramWebApp() || hasTelegramInitDataLaunchParam()) {
        await ensureTelegramSdk(1200);
        const tg = getTelegramWebApp<{ initData?: string }>();
        initData = tg?.initData || getTelegramLaunchInitData();
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
        const mockToken = MOCK_DEBUG_AUTH_TOKEN;
        setToken(mockToken);
        setUser(createMockUser());
        setStoredAuthToken(mockToken);
        setError(null);
        return;
      }
      setError(authErrorMessage(err, 'Ошибка подключения к серверу авторизации'));
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
    setError,
    setLoading,
    setToken,
    setUser,
  ]);

  const loginWithVk = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await loginVkProfile();
      applyLoginResponse(data);
    } catch (err: any) {
      if (isVkRedirectStartedError(err)) return;
      const message = authErrorMessage(err, 'Не удалось войти через VK ID');
      rememberVkAuthCooldownForError(err, message);
      setError(message);
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, [applyLoginResponse, setError, setLoading]);

  const handleVkRedirectResult = useCallback(async () => {
    let redirectResult: ReturnType<typeof consumeVkRedirectResult> = null;
    try {
      redirectResult = consumeVkRedirectResult();
    } catch (err: any) {
      setError(authErrorMessage(err, 'Не удалось обработать ответ VK ID'));
      setLoading(false);
      return true;
    }

    if (!redirectResult) return false;

    try {
      setLoading(true);
      setError(null);

      const data = await completeVkRedirect(redirectResult);
      if ('access_token' in data && data.access_token && 'user' in data) {
        applyLoginResponse(data as { access_token: string; user: UserResponse });
        return true;
      }

      await fetchCurrentUser(getStoredAuthToken());
      return true;
    } catch (err: any) {
      const message = authErrorMessage(err, 'Не удалось завершить авторизацию VK ID');
      rememberVkAuthCooldownForError(err, message);
      setError(message);
      return true;
    } finally {
      setLoading(false);
    }
  }, [applyLoginResponse, fetchCurrentUser, setError, setLoading]);

  const loginWithTelegramWidget = useCallback(async (payload: TelegramWidgetPayload) => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`${API_URL}/api/auth/telegram-widget`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...identityDeviceHeader(),
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error('Telegram Login Widget не прошел проверку');
      }

      applyLoginResponse(await response.json());
    } catch (err: any) {
      const message = authErrorMessage(err, 'Не удалось войти через Telegram');
      setError(message);
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, [API_URL, applyLoginResponse, setError, setLoading]);

  const loginWithTelegramBot = useCallback((options: TelegramBotAuthOptions = {}) => (
    telegramBotAuthCoordinator.run(options, async (notifySessionStarted) => {
      try {
        setError(null);
        const session = await apiFetch<TelegramBotAuthStartResponse>('/auth/telegram/bot-session', {
          method: 'POST',
        });

        window.open(session.bot_url, '_blank', 'noopener,noreferrer');
        notifySessionStarted({
          botUrl: session.bot_url,
          expiresAt: session.expires_at,
        });

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
        const message = authErrorMessage(err, 'Не удалось войти через Telegram');
        setError(message);
        throw new Error(message);
      }
    })
  ), [applyLoginResponse, setError, telegramBotAuthCoordinator]);

  useEffect(() => {
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

  const logout = useCallback(() => {
    void (async () => {
      const csrfHeaders = await csrfHeaderForRequest({ method: 'POST' });
      await fetch(`${API_URL}/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          ...identityDeviceHeader(),
          ...csrfHeaders,
        },
        body: '',
      });
    })().catch(() => undefined);
    clearStoredAuth();
    clearCsrfToken();
  }, [API_URL, clearStoredAuth]);

  const actions = useMemo<AuthActions>(() => ({
    login,
    loginWithVk,
    loginWithTelegramWidget,
    loginWithTelegramBot,
    logout,
    setUser,
  }), [
    login,
    loginWithTelegramBot,
    loginWithTelegramWidget,
    loginWithVk,
    logout,
    setUser,
  ]);

  return (
    <AuthStoreContext.Provider value={authStore}>
      <AuthActionsContext.Provider value={actions}>
        {children}
      </AuthActionsContext.Provider>
    </AuthStoreContext.Provider>
  );
}

function useAuthStore() {
  const store = useContext(AuthStoreContext);
  if (!store) {
    throw new Error('useAuthSelector must be used within an AuthProvider');
  }
  return store;
}

export function useAuthSelector<TSelected>(selector: (state: AuthState) => TSelected) {
  const store = useAuthStore();
  return useSyncExternalStore(
    store.subscribe,
    () => selector(store.getSnapshot()),
    () => selector(store.getSnapshot()),
  );
}

export function useAuthActions() {
  const actions = useContext(AuthActionsContext);
  if (!actions) {
    throw new Error('useAuthActions must be used within an AuthProvider');
  }
  return actions;
}

export function useAuth() {
  const state = useAuthSelector((snapshot) => snapshot);
  const actions = useAuthActions();
  return useMemo(() => ({ ...state, ...actions }), [actions, state]);
}
