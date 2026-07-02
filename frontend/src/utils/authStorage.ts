import { AUTH_TOKEN_STORAGE_KEY, BEARER_AUTH_COMPAT_ENABLED, DEBUG_AUTH_ENABLED } from '../config/api';

export const MOCK_DEBUG_AUTH_TOKEN = 'mock_debug_access_token';

const JWT_SEGMENT_RE = /^[A-Za-z0-9_-]+$/;
let memoryAuthToken: string | null = null;
let runtimeAuthToken: string | null = null;
let loginRuntimeAuthToken: string | null = null;

function looksLikeJwt(token: string): boolean {
  const parts = token.split('.');
  return parts.length === 3 && parts.every((part) => part.length > 0 && JWT_SEGMENT_RE.test(part));
}

function isAllowedStoredToken(token: string | null): token is string {
  if (token === MOCK_DEBUG_AUTH_TOKEN) return DEBUG_AUTH_ENABLED;
  return BEARER_AUTH_COMPAT_ENABLED && Boolean(token && looksLikeJwt(token));
}

function isAllowedRuntimeToken(token: string | null): token is string {
  if (token === MOCK_DEBUG_AUTH_TOKEN) return DEBUG_AUTH_ENABLED;
  return BEARER_AUTH_COMPAT_ENABLED && Boolean(token && looksLikeJwt(token));
}

function getBrowserStorage(type: 'local' | 'session'): Storage | null {
  if (typeof window === 'undefined') return null;

  try {
    return type === 'local' ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

function readStorage(type: 'local' | 'session', key: string): string | null {
  const storage = getBrowserStorage(type);
  if (!storage) return null;

  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(type: 'local' | 'session', key: string, value: string): boolean {
  const storage = getBrowserStorage(type);
  if (!storage) return false;

  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeStorage(type: 'local' | 'session', key: string): void {
  const storage = getBrowserStorage(type);
  if (!storage) return;

  try {
    storage.removeItem(key);
  } catch {
    // Storage may be unavailable in some embedded WebViews.
  }
}

export function getStoredAuthToken(): string | null {
  const token = memoryAuthToken || readStorage('local', AUTH_TOKEN_STORAGE_KEY) || readStorage('session', AUTH_TOKEN_STORAGE_KEY);
  if (!token) return null;
  if (isAllowedStoredToken(token)) return token;
  clearStoredAuthToken();
  return null;
}

export function getRequestAuthToken(): string | null {
  if (isAllowedRuntimeToken(runtimeAuthToken)) return runtimeAuthToken;
  runtimeAuthToken = null;
  if (loginRuntimeAuthToken && looksLikeJwt(loginRuntimeAuthToken)) return loginRuntimeAuthToken;
  loginRuntimeAuthToken = null;
  return getStoredAuthToken();
}

export function setRuntimeAuthToken(token: string): void {
  runtimeAuthToken = isAllowedRuntimeToken(token) ? token : null;
}

export function setStoredAuthToken(token: string): void {
  clearStoredAuthToken();
  if (!isAllowedStoredToken(token)) return;
  memoryAuthToken = token;

  if (token === MOCK_DEBUG_AUTH_TOKEN) {
    writeStorage('local', AUTH_TOKEN_STORAGE_KEY, token);
    return;
  }

  if (!writeStorage('local', AUTH_TOKEN_STORAGE_KEY, token)) {
    writeStorage('session', AUTH_TOKEN_STORAGE_KEY, token);
  }
}

export function clearStoredAuthToken(): void {
  runtimeAuthToken = null;
  loginRuntimeAuthToken = null;
  memoryAuthToken = null;
  removeStorage('session', AUTH_TOKEN_STORAGE_KEY);
  removeStorage('local', AUTH_TOKEN_STORAGE_KEY);
}

export function applyLoginAuthToken(accessToken?: string | null): string | null {
  if (!accessToken) {
    clearStoredAuthToken();
    return null;
  }

  if (isAllowedStoredToken(accessToken)) {
    setStoredAuthToken(accessToken);
    setRuntimeAuthToken(accessToken);
    return getStoredAuthToken();
  }

  clearStoredAuthToken();
  loginRuntimeAuthToken = looksLikeJwt(accessToken) ? accessToken : null;
  return getRequestAuthToken();
}
