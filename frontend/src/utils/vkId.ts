import { DEBUG_AUTH_ENABLED } from '../config/api';
import { ApiRequestError, apiFetch } from './api';
import { getTelegramWebApp, hasTelegramLaunchParams, isTelegramMiniApp } from './telegramSdk';

export interface VkLinkResponse {
  status: string;
  vk_user_id: string;
  vk_display_name?: string | null;
}

export interface VkLoginResponse {
  access_token: string;
  token_type: string;
  user: any;
}

export type VkRedirectAction = 'login' | 'link';
export type VkRedirectMode = 'same-window' | 'external';

export interface VkRedirectResult {
  payload: {
    code: string;
    device_id: string;
    state: string;
  };
}

export interface VkStartResponse {
  authorize_url: string;
  state: string;
  expires_in: number;
}

export type VkCompleteResponse = VkLoginResponse | VkLinkResponse;

export interface VkAuthCooldownStatus {
  active: boolean;
  retryAt: number | null;
  remainingSeconds: number;
  message: string | null;
}

const VK_AUTH_COOLDOWN_STORAGE_KEY = 'shamrai_vk_auth_retry_after';
const VK_AUTH_RATE_LIMIT_COOLDOWN_MS = 5 * 60 * 1000;

export class VkRedirectStartedError extends Error {
  mode: VkRedirectMode;
  authorizeUrl?: string;

  constructor(mode: VkRedirectMode = 'same-window', authorizeUrl?: string) {
    super('VK redirect authorization started');
    this.name = 'VkRedirectStartedError';
    this.mode = mode;
    this.authorizeUrl = authorizeUrl;
  }
}

export function isVkRedirectStartedError(error: unknown) {
  return error instanceof VkRedirectStartedError
    || (typeof error === 'object' && error !== null && (error as { name?: string }).name === 'VkRedirectStartedError');
}

export function getVkRedirectMode(error: unknown): VkRedirectMode {
  return (
    typeof error === 'object'
    && error !== null
    && (error as { mode?: VkRedirectMode }).mode === 'external'
  )
    ? 'external'
    : 'same-window';
}

function safeStorageSet(storage: Storage | undefined, key: string, value: string) {
  try {
    storage?.setItem(key, value);
  } catch {
    // Browsers can disable storage in private modes; auth should still work.
  }
}

function safeStorageGet(storage: Storage | undefined, key: string) {
  try {
    return storage?.getItem(key) || null;
  } catch {
    return null;
  }
}

function safeStorageRemove(storage: Storage | undefined, key: string) {
  try {
    storage?.removeItem(key);
  } catch {
    // Ignore storage failures.
  }
}

function readVkAuthRetryAt() {
  const rawValue = safeStorageGet(localStorage, VK_AUTH_COOLDOWN_STORAGE_KEY);
  const retryAt = rawValue ? Number(rawValue) : NaN;
  return Number.isFinite(retryAt) && retryAt > 0 ? retryAt : null;
}

export function clearVkAuthCooldown() {
  safeStorageRemove(localStorage, VK_AUTH_COOLDOWN_STORAGE_KEY);
}

export function rememberVkAuthCooldown(durationMs = VK_AUTH_RATE_LIMIT_COOLDOWN_MS) {
  safeStorageSet(localStorage, VK_AUTH_COOLDOWN_STORAGE_KEY, String(Date.now() + durationMs));
}

export function getVkAuthCooldownStatus(now = Date.now()): VkAuthCooldownStatus {
  const retryAt = readVkAuthRetryAt();
  if (!retryAt || retryAt <= now) {
    if (retryAt) clearVkAuthCooldown();
    return {
      active: false,
      retryAt: null,
      remainingSeconds: 0,
      message: null,
    };
  }

  const remainingSeconds = Math.max(1, Math.ceil((retryAt - now) / 1000));
  return {
    active: true,
    retryAt,
    remainingSeconds,
    message: `VK ID временно ограничил попытки входа. Подождите ${Math.ceil(remainingSeconds / 60)} мин. или войдите через Telegram.`,
  };
}

function isVkRateLimitMessage(message: string) {
  return /слишком много|too many|flood|rate|limit|\[9\]/i.test(message);
}

export function rememberVkAuthCooldownForMessage(message: string) {
  if (isVkRateLimitMessage(message)) {
    rememberVkAuthCooldown(VK_AUTH_RATE_LIMIT_COOLDOWN_MS);
  }
}

export function rememberVkAuthCooldownForError(error: unknown, message = '') {
  if (error instanceof ApiRequestError && error.status === 429) {
    rememberVkAuthCooldown(
      error.retryAfterSeconds
        ? error.retryAfterSeconds * 1000
        : VK_AUTH_RATE_LIMIT_COOLDOWN_MS,
    );
    return;
  }
  rememberVkAuthCooldownForMessage(message);
}

export function getVkIdConfig() {
  const appId = import.meta.env.VITE_VK_ID_APP_ID;
  const redirectUri = import.meta.env.VITE_VK_ID_REDIRECT_URI || window.location.origin;
  const debugEnabled = DEBUG_AUTH_ENABLED;
  const configured = Boolean(appId && Number.isFinite(Number(appId)) && redirectUri);
  const redirectOrigin = configured ? new URL(redirectUri, window.location.origin).origin : '';
  const currentOrigin = window.location.origin;
  const originCompatible = Boolean(
    configured
    && currentOrigin === redirectOrigin
    && (window.isSecureContext || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  );
  const canonicalAppUrl = configured ? new URL('/app/', redirectUri).toString() : '';

  return {
    appId,
    redirectUri,
    redirectOrigin,
    canonicalAppUrl,
    debugEnabled,
    configured,
    originCompatible,
    ready: (configured && originCompatible) || debugEnabled,
  };
}

function cleanupVkRedirectQuery(returnPath?: string) {
  if (returnPath) {
    window.history.replaceState(null, '', returnPath);
    return;
  }

  const url = new URL(window.location.href);
  ['code', 'device_id', 'state', 'type', 'expires_in', 'error', 'error_description', 'ext_id'].forEach((key) => {
    url.searchParams.delete(key);
  });
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

export function consumeVkRedirectResult(): VkRedirectResult | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const deviceId = params.get('device_id');
  const state = params.get('state');
  const error = params.get('error');

  if (!code && !error) return null;
  cleanupVkRedirectQuery();

  if (error) {
    throw new Error('VK ID не завершил вход. Попробуйте снова или войдите через Telegram.');
  }

  if (!code || !deviceId || !state) {
    throw new Error('VK ID вернул неполные данные авторизации. Попробуйте снова.');
  }

  return {
    payload: {
      code,
      device_id: deviceId,
      state,
    },
  };
}

export function isVkIdReady() {
  return getVkIdConfig().ready;
}

function shouldOpenVkAuthExternally() {
  return isTelegramMiniApp() || hasTelegramLaunchParams();
}

function openVkAuthorizeUrl(authorizeUrl: string): never {
  if (shouldOpenVkAuthExternally()) {
    const telegramWebApp = getTelegramWebApp<{
      openLink?: (url: string, options?: { try_instant_view?: boolean }) => void;
    }>();
    if (telegramWebApp?.openLink) {
      telegramWebApp.openLink(authorizeUrl, { try_instant_view: false });
      throw new VkRedirectStartedError('external', authorizeUrl);
    }

    const openedWindow = window.open(authorizeUrl, '_blank', 'noopener,noreferrer');
    if (openedWindow) {
      throw new VkRedirectStartedError('external', authorizeUrl);
    }
  }

  window.location.assign(authorizeUrl);
  throw new VkRedirectStartedError('same-window', authorizeUrl);
}

export async function startVkRedirectFlow(action: VkRedirectAction): Promise<never> {
  const { configured, originCompatible, canonicalAppUrl } = getVkIdConfig();
  const cooldown = getVkAuthCooldownStatus();
  if (cooldown.active && cooldown.message) {
    throw new Error(cooldown.message);
  }
  if (!configured) {
    throw new Error('VK ID не настроен. Обратитесь к администратору Shamrai.');
  }
  if (!originCompatible) {
    throw new Error(
      canonicalAppUrl
        ? `VK ID доступен только в защищенной версии: ${canonicalAppUrl}`
      : 'VK ID доступен только в защищенной версии приложения.'
    );
  }

  const start = await apiFetch<VkStartResponse>('/auth/vk/start', {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
  openVkAuthorizeUrl(start.authorize_url);
}

function getDebugVkAuthPayload() {
  if (!DEBUG_AUTH_ENABLED) {
    throw new Error('VK ID не настроен. Обратитесь к администратору Shamrai.');
  }

  return {
    code: 'mock_code',
    device_id: 'mock_device',
    code_verifier: 'mock_verifier',
    state: 'mock_state',
  };
}

export async function linkVkProfile(): Promise<VkLinkResponse> {
  const { configured } = getVkIdConfig();
  if (configured) {
    await startVkRedirectFlow('link');
  }

  const authPayload = getDebugVkAuthPayload();
  return apiFetch<VkLinkResponse>('/auth/vk/link', {
    method: 'POST',
    body: JSON.stringify(authPayload),
  });
}

export async function loginVkProfile(): Promise<VkLoginResponse> {
  const { configured } = getVkIdConfig();
  if (configured) {
    await startVkRedirectFlow('login');
  }

  const authPayload = getDebugVkAuthPayload();
  return apiFetch<VkLoginResponse>('/auth/vk/login', {
    method: 'POST',
    body: JSON.stringify(authPayload),
  });
}

export async function completeVkRedirect(result: VkRedirectResult): Promise<VkCompleteResponse> {
  return apiFetch<VkCompleteResponse>('/auth/vk/complete', {
    method: 'POST',
    body: JSON.stringify(result.payload),
  });
}
