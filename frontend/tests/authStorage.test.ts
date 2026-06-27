import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTH_TOKEN_STORAGE_KEY } from '../src/config/api';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      values.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      values.delete(key);
    }),
  };
}

const validJwt = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiIxMjMifQ',
  'signature_123',
].join('.');

describe('authStorage', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('keeps real website auth tokens out of storage when bearer compat is disabled', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'false');
    const localStorage = memoryStorage();
    const sessionStorage = memoryStorage();
    vi.stubGlobal('window', { localStorage, sessionStorage });

    const authStorage = await import('../src/utils/authStorage');
    authStorage.setStoredAuthToken(validJwt);

    expect(localStorage.setItem).not.toHaveBeenCalledWith(AUTH_TOKEN_STORAGE_KEY, validJwt);
    expect(sessionStorage.setItem).not.toHaveBeenCalledWith(AUTH_TOKEN_STORAGE_KEY, validJwt);
    expect(authStorage.getStoredAuthToken()).toBeNull();
  });

  it('persists real website auth tokens only when bearer compat is enabled', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'true');
    const localStorage = memoryStorage();
    const sessionStorage = memoryStorage();
    vi.stubGlobal('window', { localStorage, sessionStorage });

    const firstLoad = await import('../src/utils/authStorage');
    firstLoad.setStoredAuthToken(validJwt);

    expect(localStorage.setItem).toHaveBeenCalledWith(AUTH_TOKEN_STORAGE_KEY, validJwt);

    vi.resetModules();

    const afterReload = await import('../src/utils/authStorage');
    expect(afterReload.getStoredAuthToken()).toBe(validJwt);
  });

  it('keeps debug auth local and clears unsafe stored values', async () => {
    vi.stubEnv('VITE_ENABLE_DEBUG_AUTH', 'true');
    const localStorage = memoryStorage();
    const sessionStorage = memoryStorage();
    vi.stubGlobal('window', { localStorage, sessionStorage });

    const { MOCK_DEBUG_AUTH_TOKEN, getStoredAuthToken, setStoredAuthToken } = await import('../src/utils/authStorage');
    setStoredAuthToken('not-a-jwt');
    expect(getStoredAuthToken()).toBeNull();

    setStoredAuthToken(MOCK_DEBUG_AUTH_TOKEN);
    expect(localStorage.setItem).toHaveBeenCalledWith(AUTH_TOKEN_STORAGE_KEY, MOCK_DEBUG_AUTH_TOKEN);
    expect(getStoredAuthToken()).toBe(MOCK_DEBUG_AUTH_TOKEN);
  });
});
