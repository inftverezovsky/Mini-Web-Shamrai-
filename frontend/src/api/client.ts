import { API_BASE_URL, AUTH_EXPIRED_EVENT, CSRF_HEADER_NAME } from '../config/api';
import { formatApiErrorMessage } from './errors';
import { clearStoredAuthToken, getRequestAuthToken } from '../utils/authStorage';
import { clearCsrfToken, csrfHeaderForRequest, requestNeedsCsrf } from '../utils/csrf';
import { identityDeviceHeader } from '../utils/identityDevice';

export class ApiRequestError extends Error {
  status: number;
  retryAfterSeconds: number | null;

  constructor(message: string, status: number, retryAfterSeconds: number | null) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function shouldHandleAuthExpired(endpoint: string) {
  const cleanEndpoint = endpoint.split('?', 1)[0];
  return cleanEndpoint !== '/users/me/presence';
}

export async function requestApi<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = getRequestAuthToken();
  const isFormData = options.body instanceof FormData;

  const headers = new Headers(options.headers || undefined);
  if (!isFormData && options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  Object.entries(identityDeviceHeader()).forEach(([name, value]) => headers.set(name, value));
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (requestNeedsCsrf(options) && !headers.has(CSRF_HEADER_NAME)) {
    const csrfHeaders = await csrfHeaderForRequest(options);
    Object.entries(csrfHeaders).forEach(([name, value]) => headers.set(name, value));
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api${endpoint}`, {
      credentials: 'include',
      ...options,
      headers,
    });
  } catch (error) {
    throw new Error('Не удалось подключиться к серверу. Проверьте интернет и попробуйте еще раз.');
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const message = formatApiErrorMessage(response.status, errorData.detail);
    const retryAfterHeader = response.headers.get('Retry-After');
    const retryAfterSeconds = retryAfterHeader ? Number(retryAfterHeader) : NaN;

    if (response.status === 401 && shouldHandleAuthExpired(endpoint)) {
      clearStoredAuthToken();
      clearCsrfToken();
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { endpoint, message } }));
    }

    throw new ApiRequestError(
      message,
      response.status,
      Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0 ? retryAfterSeconds : null,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const responseText = await response.text();
  if (!responseText.trim()) {
    return undefined as T;
  }

  return JSON.parse(responseText) as T;
}
