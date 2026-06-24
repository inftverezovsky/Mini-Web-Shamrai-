import { describe, expect, it } from 'vitest';
import {
  normalizeConnectionSetupActionUrl,
  resolveProfileSetupIntent,
} from '../src/utils/profileSetup';

describe('profile setup deep links', () => {
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
});
