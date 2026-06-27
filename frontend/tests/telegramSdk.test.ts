import { afterEach, describe, expect, it, vi } from 'vitest';
import { hasTelegramInitDataLaunchParam, hasTelegramLaunchParams } from '../src/utils/telegramSdk';

function setLocation(path: string) {
  const [searchPart, hashPart = ''] = path.split('#', 2);
  const queryIndex = searchPart.indexOf('?');
  vi.stubGlobal('window', {
    location: {
      search: queryIndex >= 0 ? searchPart.slice(queryIndex) : '',
      hash: hashPart ? `#${hashPart}` : '',
    },
  });
}

describe('telegramSdk launch params', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('distinguishes signed init data from Telegram shell-only params', () => {
    setLocation('/?tgWebAppVersion=8.0&tgWebAppPlatform=tdesktop');

    expect(hasTelegramLaunchParams()).toBe(true);
    expect(hasTelegramInitDataLaunchParam()).toBe(false);
  });

  it('detects signed init data in search and hash params', () => {
    setLocation('/?tgWebAppData=user%3D%257B%257D%26hash%3Dabc');
    expect(hasTelegramInitDataLaunchParam()).toBe(true);

    setLocation('/#/app?tgWebAppData=user%3D%257B%257D%26hash%3Dabc');
    expect(hasTelegramInitDataLaunchParam()).toBe(true);
  });
});
