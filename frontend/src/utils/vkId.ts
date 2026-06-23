import { DEBUG_AUTH_ENABLED } from '../config/api';
import { apiFetch } from './api';

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

interface VkRedirectFlow {
  action: VkRedirectAction;
  state: string;
  codeVerifier: string;
  returnPath: string;
  createdAt: number;
}

export interface VkRedirectResult {
  action: VkRedirectAction;
  payload: {
    code: string;
    device_id: string;
    code_verifier: string;
    state: string;
  };
}

export interface VkAuthCooldownStatus {
  active: boolean;
  retryAt: number | null;
  remainingSeconds: number;
  message: string | null;
}

const VK_CODE_VERIFIER_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
const VK_REDIRECT_FLOW_STORAGE_KEY = 'shamrai_vk_redirect_flow';
const VK_AUTH_COOLDOWN_STORAGE_KEY = 'shamrai_vk_auth_retry_after';
const VK_REDIRECT_MAX_AGE_MS = 10 * 60 * 1000;
const VK_AUTH_START_COOLDOWN_MS = 45 * 1000;
const VK_AUTH_RATE_LIMIT_COOLDOWN_MS = 5 * 60 * 1000;
const VK_ID_AUTHORIZE_URL = 'https://id.vk.ru/authorize';
const VK_ID_SDK_VERSION = '2.6.5';

export class VkRedirectStartedError extends Error {
  constructor() {
    super('VK redirect authorization started');
    this.name = 'VkRedirectStartedError';
  }
}

function generateVkOAuthToken(length = 64) {
  const bytes = new Uint8Array(length);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => VK_CODE_VERIFIER_ALPHABET[byte % VK_CODE_VERIFIER_ALPHABET.length]).join('');
}

function base64UrlEncode(buffer: ArrayBuffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return window.btoa(binary).replace(/=*$/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

async function generateVkCodeChallenge(codeVerifier: string) {
  if (!window.crypto?.subtle) {
    throw new Error('Браузер не поддерживает защищенный VK ID вход. Обновите браузер и попробуйте еще раз.');
  }

  const digest = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier));
  return base64UrlEncode(digest);
}

async function buildVkRedirectUrl(appId: string, redirectUri: string, state: string, codeVerifier: string) {
  const codeChallenge = await generateVkCodeChallenge(codeVerifier);
  const url = new URL(VK_ID_AUTHORIZE_URL);
  url.searchParams.set('scheme', 'dark');
  url.searchParams.set('code_challenge', codeChallenge);
  url.searchParams.set('code_challenge_method', 's256');
  url.searchParams.set('client_id', appId);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('response_mode', 'redirect');
  url.searchParams.set('state', state);
  url.searchParams.set('prompt', '');
  url.searchParams.set('v', VK_ID_SDK_VERSION);
  url.searchParams.set('sdk_type', 'vkid');
  url.searchParams.set('app_id', appId);
  url.searchParams.set('redirect_uri', redirectUri);
  return url.toString();
}

export function isVkRedirectStartedError(error: unknown) {
  return error instanceof VkRedirectStartedError
    || (typeof error === 'object' && error !== null && (error as { name?: string }).name === 'VkRedirectStartedError');
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

function readVkRedirectFlow(): string | null {
  return safeStorageGet(localStorage, VK_REDIRECT_FLOW_STORAGE_KEY)
    || safeStorageGet(sessionStorage, VK_REDIRECT_FLOW_STORAGE_KEY);
}

function storeVkRedirectFlow(flow: VkRedirectFlow): void {
  const serializedFlow = JSON.stringify(flow);
  safeStorageSet(localStorage, VK_REDIRECT_FLOW_STORAGE_KEY, serializedFlow);
  safeStorageSet(sessionStorage, VK_REDIRECT_FLOW_STORAGE_KEY, serializedFlow);
}

function clearVkRedirectFlow(): void {
  safeStorageRemove(localStorage, VK_REDIRECT_FLOW_STORAGE_KEY);
  safeStorageRemove(sessionStorage, VK_REDIRECT_FLOW_STORAGE_KEY);
}

export function consumeVkRedirectResult(): VkRedirectResult | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const deviceId = params.get('device_id');
  const state = params.get('state');
  const error = params.get('error');

  if (!code && !error) return null;

  const rawFlow = readVkRedirectFlow();
  if (!rawFlow) return null;

  clearVkRedirectFlow();

  let flow: VkRedirectFlow;
  try {
    flow = JSON.parse(rawFlow) as VkRedirectFlow;
  } catch {
    cleanupVkRedirectQuery();
    throw new Error('Не удалось восстановить сессию VK ID');
  }

  cleanupVkRedirectQuery(flow.returnPath);

  if (!flow.createdAt || Date.now() - flow.createdAt > VK_REDIRECT_MAX_AGE_MS) {
    throw new Error('Сессия VK ID устарела. Запустите вход еще раз.');
  }

  if (error) {
    throw new Error(params.get('error_description') || error);
  }

  if (!code || !deviceId || !state) {
    throw new Error('VK ID вернул неполные данные авторизации');
  }

  if (state !== flow.state) {
    throw new Error('VK ID вернул некорректный state');
  }

  return {
    action: flow.action,
    payload: {
      code,
      device_id: deviceId,
      code_verifier: flow.codeVerifier,
      state,
    },
  };
}

export function isVkIdReady() {
  return getVkIdConfig().ready;
}

export async function startVkRedirectFlow(action: VkRedirectAction): Promise<never> {
  const { appId, redirectUri, configured, originCompatible, canonicalAppUrl } = getVkIdConfig();
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

  const codeVerifier = generateVkOAuthToken();
  const state = `shamrai_vk_${action}_${Date.now()}_${generateVkOAuthToken(24)}`;
  const returnPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;

  const redirectUrl = await buildVkRedirectUrl(String(appId), redirectUri, state, codeVerifier);
  rememberVkAuthCooldown(VK_AUTH_START_COOLDOWN_MS);
  storeVkRedirectFlow({
    action,
    state,
    codeVerifier,
    returnPath,
    createdAt: Date.now(),
  });
  window.location.assign(redirectUrl);
  throw new VkRedirectStartedError();
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
