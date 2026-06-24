import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('../src/utils/api', () => ({
  apiFetch: apiFetchMock,
}));

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

function setBrowserEnv(search = '') {
  const assign = vi.fn();
  const open = vi.fn();
  const replaceState = vi.fn();
  const localStorage = memoryStorage();
  const sessionStorage = memoryStorage();
  vi.stubEnv('VITE_VK_ID_APP_ID', '54626979');
  vi.stubEnv('VITE_VK_ID_REDIRECT_URI', 'https://shamra1.pro');
  vi.stubEnv('VITE_ENABLE_DEBUG_AUTH', 'false');
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('sessionStorage', sessionStorage);
  vi.stubGlobal('window', {
    isSecureContext: true,
    location: {
      origin: 'https://shamra1.pro',
      hostname: 'shamra1.pro',
      pathname: '/',
      search,
      hash: '',
      href: `https://shamra1.pro/${search}`,
      assign,
    },
    open,
    history: {
      replaceState,
    },
  });
  return { assign, open, replaceState, localStorage, sessionStorage };
}

describe('VK ID auth helper', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    apiFetchMock.mockReset();
  });

  it('starts backend-driven redirect without setting a speculative cooldown', async () => {
    const { assign, localStorage } = setBrowserEnv();
    apiFetchMock.mockResolvedValue({
      authorize_url: 'https://id.vk.ru/authorize?response_type=code&state=server-state',
      state: 'server-state',
      expires_in: 600,
    });
    const { startVkRedirectFlow, isVkRedirectStartedError, getVkAuthCooldownStatus } = await import('../src/utils/vkId');

    try {
      await startVkRedirectFlow('login');
      throw new Error('redirect should interrupt control flow');
    } catch (error) {
      expect(isVkRedirectStartedError(error)).toBe(true);
    }

    expect(apiFetchMock).toHaveBeenCalledWith('/auth/vk/start', {
      method: 'POST',
      body: JSON.stringify({ action: 'login' }),
    });
    expect(assign).toHaveBeenCalledWith('https://id.vk.ru/authorize?response_type=code&state=server-state');
    expect(getVkAuthCooldownStatus().active).toBe(false);
    expect(localStorage.setItem).not.toHaveBeenCalledWith('shamrai_vk_auth_retry_after', expect.any(String));
  });

  it('opens VK redirect externally inside Telegram runtime', async () => {
    const { assign, open } = setBrowserEnv('?tgWebAppData=signed');
    const openLink = vi.fn();
    (window as any).Telegram = {
      WebApp: {
        initData: 'signed',
        openLink,
      },
    };
    apiFetchMock.mockResolvedValue({
      authorize_url: 'https://id.vk.ru/authorize?response_type=code&state=server-state',
      state: 'server-state',
      expires_in: 600,
    });
    const { startVkRedirectFlow, isVkRedirectStartedError, getVkRedirectMode } = await import('../src/utils/vkId');

    try {
      await startVkRedirectFlow('link');
      throw new Error('redirect should interrupt control flow');
    } catch (error) {
      expect(isVkRedirectStartedError(error)).toBe(true);
      expect(getVkRedirectMode(error)).toBe('external');
    }

    expect(openLink).toHaveBeenCalledWith(
      'https://id.vk.ru/authorize?response_type=code&state=server-state',
      { try_instant_view: false },
    );
    expect(assign).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
  });

  it('consumes VK redirect callback without local flow storage', async () => {
    const { replaceState } = setBrowserEnv('?code=vk-code&device_id=device-1&state=server-state&type=code_v2');
    const { consumeVkRedirectResult } = await import('../src/utils/vkId');

    const result = consumeVkRedirectResult();

    expect(result).toEqual({
      payload: {
        code: 'vk-code',
        device_id: 'device-1',
        state: 'server-state',
      },
    });
    expect(replaceState).toHaveBeenCalledWith(null, '', '/');
  });

  it('completes VK redirect through backend complete endpoint', async () => {
    setBrowserEnv();
    apiFetchMock.mockResolvedValue({
      access_token: 'jwt',
      token_type: 'bearer',
      user: { telegram_id: -1001 },
    });
    const { completeVkRedirect } = await import('../src/utils/vkId');

    const result = await completeVkRedirect({
      payload: {
        code: 'vk-code',
        device_id: 'device-1',
        state: 'server-state',
      },
    });

    expect(apiFetchMock).toHaveBeenCalledWith('/auth/vk/complete', {
      method: 'POST',
      body: JSON.stringify({
        code: 'vk-code',
        device_id: 'device-1',
        state: 'server-state',
      }),
    });
    expect(result).toEqual({
      access_token: 'jwt',
      token_type: 'bearer',
      user: { telegram_id: -1001 },
    });
  });

  it('does not create cooldown for a generic VK redirect error', async () => {
    setBrowserEnv('?error=temporarily_unavailable&error_description=Please%20try%20again');
    const { consumeVkRedirectResult, getVkAuthCooldownStatus } = await import('../src/utils/vkId');

    expect(() => consumeVkRedirectResult()).toThrow('VK ID не завершил вход');
    expect(getVkAuthCooldownStatus().active).toBe(false);
  });
});
