import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('../src/utils/api', () => ({
  apiFetch: apiFetchMock,
}));

vi.mock('../src/utils/telegramSdk', () => ({
  isTelegramMiniApp: () => false,
}));

function setBrowserEnv({
  permission = 'default',
  userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36',
  maxTouchPoints = 0,
  standalone = false,
  pushSubscription = null as PushSubscription | null,
  secure = true,
  pushManager = true,
  notification = true,
} = {}) {
  const registration = {
    pushManager: {
      getSubscription: vi.fn().mockResolvedValue(pushSubscription),
      subscribe: vi.fn().mockResolvedValue(pushSubscription || {
        toJSON: () => ({ endpoint: 'https://push.example/subscribed', keys: { p256dh: 'p256dh-value', auth: 'auth-value' } }),
      }),
    },
    showNotification: vi.fn().mockResolvedValue(undefined),
    update: vi.fn().mockResolvedValue(undefined),
  };
  const serviceWorker = {
    getRegistration: vi.fn().mockResolvedValue(registration),
    register: vi.fn().mockResolvedValue(registration),
    ready: Promise.resolve(registration),
  };

  const notificationApi = notification ? {
    permission,
    requestPermission: vi.fn().mockResolvedValue(permission),
  } : undefined;

  vi.stubGlobal('window', {
    isSecureContext: secure,
    PushManager: pushManager ? function PushManager() {} : undefined,
    Notification: notificationApi,
    AudioContext: function AudioContext() {},
    matchMedia: vi.fn().mockReturnValue({ matches: standalone }),
    atob: (value: string) => Buffer.from(value, 'base64').toString('binary'),
    setTimeout,
    clearTimeout,
  });
  vi.stubGlobal('navigator', {
    userAgent,
    maxTouchPoints,
    standalone,
    serviceWorker,
  });
  if (notification) {
    vi.stubGlobal('Notification', notificationApi);
  } else {
    vi.stubGlobal('Notification', undefined);
  }
  return { registration, serviceWorker };
}

describe('web push readiness', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ public_key: 'BElkTestPublicKey' });
  });

  it('asks desktop browsers for permission instead of skipping setup', async () => {
    setBrowserEnv({ permission: 'default' });
    const { getWebPushReadiness } = await import('../src/utils/webPush');

    const state = await getWebPushReadiness();

    expect(state.status).toBe('permission_required');
    expect(state.canRequest).toBe(true);
    expect(state.subscription).toBe('missing');
    expect(state.fallbackRecommended).toBe(false);
    expect(state.platformHint).toBe('desktop_web_push');
  });

  it('requires iOS users to install the PWA before push setup', async () => {
    setBrowserEnv({
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1',
      maxTouchPoints: 5,
      standalone: false,
    });
    const { getWebPushReadiness } = await import('../src/utils/webPush');

    const state = await getWebPushReadiness();

    expect(state.status).toBe('needs_install');
    expect(state.canRequest).toBe(false);
    expect(state.subscription).toBe('missing');
    expect(state.fallbackRecommended).toBe(true);
    expect(state.platformHint).toBe('ios_install_required');
  });

  it('reports denied browser permission as a fallback case', async () => {
    setBrowserEnv({ permission: 'denied' });
    const { getWebPushReadiness } = await import('../src/utils/webPush');

    const state = await getWebPushReadiness();

    expect(state.status).toBe('denied');
    expect(state.canRequest).toBe(false);
    expect(state.subscription).toBe('missing');
    expect(state.fallbackRecommended).toBe(true);
    expect(state.platformHint).toBe('permission_denied');
  });

  it('saves an existing browser subscription and reports sound as locked before user gesture', async () => {
    const pushSubscription = {
      toJSON: () => ({ endpoint: 'https://push.example/existing', keys: { p256dh: 'p256dh-value', auth: 'auth-value' } }),
    } as PushSubscription;
    setBrowserEnv({ permission: 'granted', pushSubscription });
    const { getWebPushReadiness } = await import('../src/utils/webPush');

    const state = await getWebPushReadiness();

    expect(state.status).toBe('subscribed');
    expect(state.subscription).toBe('saved');
    expect(state.sound).toBe('locked');
    expect(state.fallbackRecommended).toBe(false);
    expect(apiFetchMock).toHaveBeenCalledWith('/signals/web-push/subscription', expect.objectContaining({ method: 'PUT' }));
  });

  it('requests notification permission before fetching keys during one-click setup', async () => {
    const order: string[] = [];
    apiFetchMock.mockImplementation(async (path: string) => {
      order.push(path === '/signals/web-push/public-key' ? 'load-key' : 'save-subscription');
      return path === '/signals/web-push/public-key'
        ? { public_key: 'BElkTestPublicKey' }
        : { status: 'ok' };
    });
    setBrowserEnv({ permission: 'default' });
    const notificationApi = window.Notification as unknown as { requestPermission: ReturnType<typeof vi.fn> };
    notificationApi.requestPermission.mockImplementation(async () => {
      order.push('request-permission');
      return 'granted';
    });
    const { registerWebPushSubscription } = await import('../src/utils/webPush');

    const result = await registerWebPushSubscription();

    expect(result.status).toBe('subscribed');
    expect(order[0]).toBe('request-permission');
    expect(order).toEqual(['request-permission', 'load-key', 'save-subscription']);
  });
});
