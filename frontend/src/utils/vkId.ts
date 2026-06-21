import * as VKID from '@vkid/sdk';
import type { AuthResponse } from '@vkid/sdk';
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

const VK_CODE_VERIFIER_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
const VK_REDIRECT_FLOW_STORAGE_KEY = 'shamrai_vk_redirect_flow';
const VK_REDIRECT_MAX_AGE_MS = 10 * 60 * 1000;
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
  url.searchParams.set('state', state);
  url.searchParams.set('prompt', '');
  url.searchParams.set('v', VK_ID_SDK_VERSION);
  url.searchParams.set('sdk_type', 'vkid');
  url.searchParams.set('app_id', appId);
  url.searchParams.set('redirect_uri', redirectUri);
  return url.toString();
}

function isVkAuthResponse(value: unknown): value is AuthResponse {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<AuthResponse>;
  return Boolean(candidate.code && candidate.device_id && candidate.state);
}

export function isVkRedirectStartedError(error: unknown) {
  return error instanceof VkRedirectStartedError
    || (typeof error === 'object' && error !== null && (error as { name?: string }).name === 'VkRedirectStartedError');
}

function formatVkSdkError(error: unknown) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error.trim();
  if (typeof error === 'object' && error !== null) {
    const candidate = error as { error?: string; error_description?: string; code?: number };
    if (candidate.error_description) {
      try {
        const parsed = JSON.parse(candidate.error_description);
        if (parsed?.error_description) return String(parsed.error_description);
        if (parsed?.error) return String(parsed.error);
      } catch {
        return candidate.error_description;
      }
    }
    if (candidate.error) return candidate.error;
    if (candidate.code === 102) return 'Окно VK ID было закрыто до завершения авторизации';
  }
  return 'VK ID не вернул результат авторизации';
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

  const rawFlow = sessionStorage.getItem(VK_REDIRECT_FLOW_STORAGE_KEY);
  if (!rawFlow) return null;

  sessionStorage.removeItem(VK_REDIRECT_FLOW_STORAGE_KEY);

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
  sessionStorage.setItem(VK_REDIRECT_FLOW_STORAGE_KEY, JSON.stringify({
    action,
    state,
    codeVerifier,
    returnPath,
    createdAt: Date.now(),
  } satisfies VkRedirectFlow));
  window.location.assign(redirectUrl);
  throw new VkRedirectStartedError();
}

async function requestVkAuthPayload() {
  const { appId, redirectUri, configured, ready, originCompatible, canonicalAppUrl } = getVkIdConfig();
  if (!ready) {
    if (configured && !originCompatible) {
      throw new Error(
        canonicalAppUrl
          ? `VK ID доступен только в защищенной версии: ${canonicalAppUrl}`
          : 'VK ID доступен только в защищенной версии приложения.'
      );
    }
    throw new Error('VK ID не настроен. Обратитесь к администратору Shamrai.');
  }

  if (configured) {
    const codeVerifier = generateVkOAuthToken();
    const state = `shamrai_vk_${Date.now()}_${generateVkOAuthToken(24)}`;

    VKID.Config.init({
      app: Number(appId),
      redirectUrl: redirectUri,
      state,
      codeVerifier,
      mode: VKID.ConfigAuthMode.InNewWindow,
      responseMode: VKID.ConfigResponseMode.Callback,
    });

    let authResult: unknown;
    try {
      authResult = await VKID.Auth.login({ scheme: VKID.Scheme.DARK });
    } catch (error) {
      throw new Error(formatVkSdkError(error));
    }

    if (!isVkAuthResponse(authResult)) {
      throw new Error('VK ID не вернул код авторизации');
    }
    if (authResult.state !== state) {
      throw new Error('VK ID вернул некорректный state');
    }

    return {
      code: authResult.code,
      device_id: authResult.device_id,
      code_verifier: codeVerifier,
      state: authResult.state,
    };
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

  const authPayload = await requestVkAuthPayload();
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

  const authPayload = await requestVkAuthPayload();
  return apiFetch<VkLoginResponse>('/auth/vk/login', {
    method: 'POST',
    body: JSON.stringify(authPayload),
  });
}
