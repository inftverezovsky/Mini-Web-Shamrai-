import {
  API_BASE_URL,
  AUTH_EXPIRED_EVENT,
  AUTH_TOKEN_STORAGE_KEY,
  DEBUG_AUTH_ENABLED,
  buildApiUrl,
  buildApiWebSocketUrl,
} from '../config/api';
import { requestApi } from '../api/client';
import { downloadApiFile } from '../api/downloads';

export {
  API_BASE_URL,
  AUTH_EXPIRED_EVENT,
  AUTH_TOKEN_STORAGE_KEY,
  buildApiUrl,
  buildApiWebSocketUrl,
  downloadApiFile,
};

export async function apiFetch<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  if (import.meta.env.DEV && DEBUG_AUTH_ENABLED) {
    const { mockApiFetch } = await import('../api/mock');
    const mockResponse = mockApiFetch(endpoint, options);
    if (mockResponse !== null) return mockResponse as T;
  }

  return requestApi<T>(endpoint, options);
}
