import { API_BASE_URL, AUTH_EXPIRED_EVENT } from '../config/api';
import { formatApiErrorMessage } from './errors';
import { clearStoredAuthToken, getStoredAuthToken } from '../utils/authStorage';
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

export async function requestApi<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = getStoredAuthToken();
  const isFormData = options.body instanceof FormData;

  const headers = {
    ...(!isFormData && options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...identityDeviceHeader(),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  };

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

    if (response.status === 401) {
      clearStoredAuthToken();
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
