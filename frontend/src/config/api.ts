export const API_BASE_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '');
export const AUTH_TOKEN_STORAGE_KEY = 'bet_tma_jwt_token';
export const DEBUG_AUTH_ENABLED = import.meta.env.DEV && import.meta.env.VITE_ENABLE_DEBUG_AUTH === 'true';
export const DEBUG_ROLE_STORAGE_KEY = 'bet_tma_debug_role';
export const AUTH_EXPIRED_EVENT = 'shamrai:auth-expired';

export function buildApiUrl(path: string): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  if (API_BASE_URL) return `${API_BASE_URL}${cleanPath}`;
  return cleanPath;
}

export function buildApiWebSocketUrl(path: string, params: Record<string, string> = {}): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const base = API_BASE_URL || window.location.origin;
  const url = new URL(cleanPath, base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
  });
  return url.toString();
}
