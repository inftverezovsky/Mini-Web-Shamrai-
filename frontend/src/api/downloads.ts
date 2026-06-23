import { API_BASE_URL, AUTH_EXPIRED_EVENT } from '../config/api';
import { formatApiErrorMessage } from './errors';
import { clearStoredAuthToken, getStoredAuthToken } from '../utils/authStorage';

function filenameFromDisposition(disposition: string | null, fallback: string) {
  if (!disposition) return fallback;
  const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encodedMatch?.[1]) return decodeURIComponent(encodedMatch[1].replace(/"/g, ''));
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  return plainMatch?.[1] || fallback;
}

export async function downloadApiFile(endpoint: string, fallbackFilename: string): Promise<void> {
  const token = getStoredAuthToken();
  const response = await fetch(`${API_BASE_URL}/api${endpoint}`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });

  if (!response.ok) {
    const contentType = response.headers.get('Content-Type') || '';
    const errorData = contentType.includes('application/json')
      ? await response.json().catch(() => ({}))
      : {};
    const message = formatApiErrorMessage(response.status, (errorData as any).detail);

    if (response.status === 401) {
      clearStoredAuthToken();
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { endpoint, message } }));
    }

    throw new Error(message);
  }

  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition'), fallbackFilename);
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
