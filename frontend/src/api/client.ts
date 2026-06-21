import { API_BASE_URL, AUTH_EXPIRED_EVENT } from '../config/api';
import { formatApiErrorDetail } from './errors';
import { clearStoredAuthToken, getStoredAuthToken } from '../utils/authStorage';

export async function requestApi<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = getStoredAuthToken();
  const isFormData = options.body instanceof FormData;

  const headers = {
    ...(!isFormData && options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
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
    const message = formatApiErrorDetail(errorData.detail) || `HTTP error! Status: ${response.status}`;

    if (response.status === 401) {
      clearStoredAuthToken();
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { endpoint, message } }));
    }

    throw new Error(message);
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
