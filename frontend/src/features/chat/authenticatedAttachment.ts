import { API_BASE_URL, BEARER_AUTH_COMPAT_ENABLED } from '../../config/api';
import { getStoredAuthToken } from '../../utils/authStorage';

const CHAT_ATTACHMENT_DOWNLOAD_PATH = /^\/chat\/attachments\/[1-9]\d*\/download$/;

export async function fetchAuthenticatedChatAttachment(
  endpoint: string,
  signal?: AbortSignal,
): Promise<Blob> {
  if (!CHAT_ATTACHMENT_DOWNLOAD_PATH.test(endpoint)) {
    throw new Error('Некорректный URL вложения');
  }

  const headers = new Headers();
  const token = BEARER_AUTH_COMPAT_ENABLED ? getStoredAuthToken() : null;
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${API_BASE_URL}/api${endpoint}`, {
    credentials: 'include',
    headers,
    signal,
  });
  if (!response.ok) {
    throw new Error('Не удалось загрузить вложение');
  }
  return response.blob();
}
