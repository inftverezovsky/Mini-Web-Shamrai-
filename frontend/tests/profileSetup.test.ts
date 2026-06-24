import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  PROFILE_SETUP_NAVIGATION_EVENT,
  connectionSetupActionPresentation,
  normalizeConnectionSetupActionUrl,
  navigateToConnectionSetupAction,
  resolveProfileSetupIntent,
  shouldRunConnectionSetupInline,
} from '../src/utils/profileSetup';

describe('profile setup deep links', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('starts the exact VK ID connection flow', () => {
    expect(resolveProfileSetupIntent('?open=profile&setup=vk', '#connect-vk')).toEqual({
      setup: 'vk',
      section: 'vk',
      targetId: 'connect-vk',
      autoAction: 'vk-link',
    });
  });

  it('starts the exact Telegram connection flow', () => {
    expect(resolveProfileSetupIntent('?open=profile&setup=telegram', '#connect-telegram')).toEqual({
      setup: 'telegram',
      section: 'telegram',
      targetId: 'connect-telegram',
      autoAction: 'telegram',
    });
  });

  it('opens Web Push without auto-requesting browser permission', () => {
    expect(resolveProfileSetupIntent('?open=profile&setup=notifications', '#web-push')).toEqual({
      setup: 'notifications',
      section: 'notifications',
      targetId: 'web-push',
      autoAction: null,
    });
  });

  it('keeps legacy identity links as a safe scroll-only fallback', () => {
    expect(resolveProfileSetupIntent('?open=profile&setup=identity', '#connect-identity')).toEqual({
      setup: 'identity',
      section: null,
      targetId: 'connect-identity',
      autoAction: null,
    });
  });

  it('upgrades stored legacy chat action links by action id', () => {
    expect(normalizeConnectionSetupActionUrl(
      'connect-vk',
      'https://shamra1.pro/app?open=profile&setup=identity#connect-identity',
    )).toBe('/app?open=profile&setup=vk#connect-vk');

    expect(normalizeConnectionSetupActionUrl(
      'allow-vk-messages',
      'https://shamra1.pro/app?open=profile&setup=vk#connect-vk',
    )).toBe('/app?open=profile&setup=vk-messages#connect-vk');
  });

  it('presents Web Push as a one-click inline setup action', () => {
    expect(shouldRunConnectionSetupInline('enable-web-push')).toBe(true);
    expect(connectionSetupActionPresentation('enable-web-push', 'Включить Web Push')).toEqual(
      expect.objectContaining({
        title: 'Включить Web Push',
        caption: expect.stringContaining('одним нажатием'),
        busyLabel: 'Включаю уведомления...',
        successLabel: 'Уведомления включены',
      }),
    );
  });

  it('presents social setup actions as guided automatic flows', () => {
    expect(shouldRunConnectionSetupInline('connect-vk')).toBe(false);
    expect(connectionSetupActionPresentation('connect-vk', 'Подключить VK ID')).toEqual(
      expect.objectContaining({
        title: 'Подключить VK ID',
        caption: expect.stringContaining('VK ID'),
        busyLabel: 'Открываю подключение...',
      }),
    );
    expect(connectionSetupActionPresentation('allow-vk-messages', 'Разрешить сообщения VK')).toEqual(
      expect.objectContaining({
        title: 'Разрешить сообщения VK',
        caption: expect.stringContaining('разрешение'),
      }),
    );
  });

  it('navigates setup actions inside the SPA without a document reload', () => {
    let currentUrl = new URL('https://shamra1.pro/app?open=web-bot-chat');
    const listeners = new Map<string, Set<(event: Event) => void>>();
    const pushState = vi.fn((_state: unknown, _title: string, nextUrl?: string | URL | null) => {
      currentUrl = new URL(String(nextUrl || currentUrl.pathname), currentUrl.origin);
    });

    class TestCustomEvent<T> extends Event {
      detail: T;

      constructor(type: string, init?: CustomEventInit<T>) {
        super(type);
        this.detail = init?.detail as T;
      }
    }

    vi.stubGlobal('CustomEvent', TestCustomEvent);
    vi.stubGlobal('window', {
      location: {
        get origin() {
          return currentUrl.origin;
        },
        get pathname() {
          return currentUrl.pathname;
        },
        get search() {
          return currentUrl.search;
        },
        get hash() {
          return currentUrl.hash;
        },
      },
      history: { pushState },
      addEventListener: vi.fn((type: string, listener: (event: Event) => void) => {
        listeners.set(type, (listeners.get(type) || new Set()).add(listener));
      }),
      removeEventListener: vi.fn((type: string, listener: (event: Event) => void) => {
        listeners.get(type)?.delete(listener);
      }),
      dispatchEvent: vi.fn((event: Event) => {
        listeners.get(event.type)?.forEach((listener) => listener(event));
        return true;
      }),
    });

    const received: Array<CustomEvent['detail']> = [];
    window.addEventListener(PROFILE_SETUP_NAVIGATION_EVENT, (event) => {
      received.push((event as CustomEvent).detail);
    });

    const handled = navigateToConnectionSetupAction(
      'connect-vk',
      'https://shamra1.pro/app?open=profile&setup=identity#connect-identity',
    );

    expect(handled).toBe(true);
    expect(pushState).toHaveBeenCalledWith(null, '', '/app?open=profile&setup=vk#connect-vk');
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe('/app?open=profile&setup=vk#connect-vk');
    expect(received).toEqual([
      expect.objectContaining({
        actionId: 'connect-vk',
        url: '/app?open=profile&setup=vk#connect-vk',
        intent: expect.objectContaining({
          setup: 'vk',
          targetId: 'connect-vk',
          autoAction: 'vk-link',
        }),
      }),
    ]);
  });
});
