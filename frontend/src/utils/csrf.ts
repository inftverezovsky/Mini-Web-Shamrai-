import { API_BASE_URL, CSRF_HEADER_NAME } from '../config/api';

interface CsrfTokenResponse {
  csrf_token?: unknown;
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
let csrfTokenPromise: Promise<string> | null = null;

export function requestNeedsCsrf(options: RequestInit = {}): boolean {
  const method = (options.method || 'GET').toUpperCase();
  return UNSAFE_METHODS.has(method);
}

export function clearCsrfToken(): void {
  csrfTokenPromise = null;
}

export async function getCsrfToken(): Promise<string> {
  if (!csrfTokenPromise) {
    csrfTokenPromise = fetch(`${API_BASE_URL}/api/auth/csrf`, {
      credentials: 'include',
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error('Не удалось подготовить защищенный запрос. Попробуйте еще раз.');
        }
        const data = await response.json() as CsrfTokenResponse;
        if (typeof data.csrf_token !== 'string' || !data.csrf_token) {
          throw new Error('Сервер не вернул CSRF токен.');
        }
        return data.csrf_token;
      })
      .catch((error) => {
        csrfTokenPromise = null;
        throw error;
      });
  }

  return csrfTokenPromise;
}

export async function csrfHeaderForRequest(options: RequestInit = {}): Promise<Record<string, string>> {
  if (!requestNeedsCsrf(options)) return {};
  return { [CSRF_HEADER_NAME]: await getCsrfToken() };
}
