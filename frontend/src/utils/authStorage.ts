import { AUTH_TOKEN_STORAGE_KEY } from '../config/api';

export const MOCK_DEBUG_AUTH_TOKEN = 'mock_debug_access_token';

const JWT_SEGMENT_RE = /^[A-Za-z0-9_-]+$/;
let memoryAuthToken: string | null = null;

function looksLikeJwt(token: string): boolean {
  const parts = token.split('.');
  return parts.length === 3 && parts.every((part) => part.length > 0 && JWT_SEGMENT_RE.test(part));
}

function isAllowedStoredToken(token: string | null): token is string {
  return token === MOCK_DEBUG_AUTH_TOKEN || Boolean(token && looksLikeJwt(token));
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
  const token = memoryAuthToken || readStorage('session', AUTH_TOKEN_STORAGE_KEY) || readStorage('local', AUTH_TOKEN_STORAGE_KEY);
  if (!token) return null;
  if (isAllowedStoredToken(token)) return token;
  clearStoredAuthToken();
  return null;
}

export function setStoredAuthToken(token: string): void {
  clearStoredAuthToken();
  if (!isAllowedStoredToken(token)) return;
  memoryAuthToken = token;

  if (token === MOCK_DEBUG_AUTH_TOKEN) {
    writeStorage('local', AUTH_TOKEN_STORAGE_KEY, token);
    return;
  }

  writeStorage('session', AUTH_TOKEN_STORAGE_KEY, token);
}

export function clearStoredAuthToken(): void {
  memoryAuthToken = null;
  removeStorage('session', AUTH_TOKEN_STORAGE_KEY);
  removeStorage('local', AUTH_TOKEN_STORAGE_KEY);
}
