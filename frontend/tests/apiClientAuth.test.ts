import { beforeEach, describe, expect, it, vi } from 'vitest';

const validJwt = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiIxMjMifQ',
  'signature_123',
].join('.');

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

function okJson(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function setBrowserEnv() {
  const localStorage = memoryStorage();
  const sessionStorage = memoryStorage();
  const dispatchEvent = vi.fn();
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('sessionStorage', sessionStorage);
  vi.stubGlobal('window', {
    localStorage,
    sessionStorage,
    dispatchEvent,
  });
  return { localStorage, sessionStorage, dispatchEvent };
}

describe('API auth client', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('omits Authorization for real JWTs when bearer compat is disabled', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'false');
    setBrowserEnv();
    const fetchMock = vi.fn().mockResolvedValue(okJson({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { setStoredAuthToken } = await import('../src/utils/authStorage');
    const { requestApi } = await import('../src/api/client');
    setStoredAuthToken(validJwt);

    await requestApi('/users/me');

    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get('Authorization')).toBeNull();
  });

  it('omits Authorization from runtime memory auth when bearer compat is disabled', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'false');
    setBrowserEnv();
    const fetchMock = vi.fn().mockResolvedValue(okJson({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { setRuntimeAuthToken } = await import('../src/utils/authStorage');
    const { requestApi } = await import('../src/api/client');
    setRuntimeAuthToken(validJwt);

    await requestApi('/users/me');

    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get('Authorization')).toBeNull();
  });

  it('adds Authorization from runtime memory auth when bearer compat is enabled', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'true');
    setBrowserEnv();
    const fetchMock = vi.fn().mockResolvedValue(okJson({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { setRuntimeAuthToken } = await import('../src/utils/authStorage');
    const { requestApi } = await import('../src/api/client');
    setRuntimeAuthToken(validJwt);

    await requestApi('/users/me');

    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get('Authorization')).toBe(`Bearer ${validJwt}`);
  });

  it('adds CSRF header to unsafe requests', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'false');
    setBrowserEnv();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ csrf_token: 'csrf-token' }))
      .mockResolvedValueOnce(okJson({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { requestApi } = await import('../src/api/client');

    await requestApi('/users/me/preferences', {
      method: 'PUT',
      body: JSON.stringify({ odds_drop_notifications_enabled: true }),
    });

    expect(fetchMock.mock.calls[0][0]).toContain('/api/auth/csrf');
    const headers = new Headers(fetchMock.mock.calls[1][1].headers);
    expect(headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('clears compat auth on 401 responses', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'true');
    const { localStorage, dispatchEvent } = setBrowserEnv();
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'Session expired' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    const { setStoredAuthToken } = await import('../src/utils/authStorage');
    const { requestApi } = await import('../src/api/client');
    setStoredAuthToken(validJwt);

    await expect(requestApi('/users/me')).rejects.toThrow();

    expect(localStorage.removeItem).toHaveBeenCalledWith('bet_tma_jwt_token');
    expect(dispatchEvent).toHaveBeenCalled();
  });

  it('does not clear auth or restart login after passive presence heartbeat 401', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'true');
    const { localStorage, dispatchEvent } = setBrowserEnv();
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'Session expired' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    const { setStoredAuthToken, getStoredAuthToken } = await import('../src/utils/authStorage');
    const { requestApi } = await import('../src/api/client');
    setStoredAuthToken(validJwt);
    localStorage.removeItem.mockClear();
    dispatchEvent.mockClear();

    await expect(requestApi('/users/me/presence', { method: 'POST' })).rejects.toThrow();

    expect(localStorage.removeItem).not.toHaveBeenCalledWith('bet_tma_jwt_token');
    expect(getStoredAuthToken()).toBe(validJwt);
    expect(dispatchEvent).not.toHaveBeenCalled();
  });
});
