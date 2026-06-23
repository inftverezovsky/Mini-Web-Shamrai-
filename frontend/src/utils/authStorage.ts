import { AUTH_TOKEN_STORAGE_KEY } from '../config/api';

export const MOCK_DEBUG_AUTH_TOKEN = 'mock_debug_access_token';

function isAllowedStoredToken(token: string | null): token is string {
  return token === MOCK_DEBUG_AUTH_TOKEN;
}

export function getStoredAuthToken(): string | null {
  const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  if (!token) return null;
  if (isAllowedStoredToken(token)) return token;
  clearStoredAuthToken();
  return null;
}

export function setStoredAuthToken(token: string): void {
  clearStoredAuthToken();
  if (isAllowedStoredToken(token)) {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  }
}

export function clearStoredAuthToken(): void {
  sessionStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}
