import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';

async function seedCompatSession(page: Page, role: 'user' | 'admin' = 'user') {
  await page.addInitScript(({ token, debugRole }) => {
    Object.defineProperty(window, 'Telegram', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', debugRole);
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    window.localStorage.setItem('bet_tma_mock_vk_group_member', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_messages_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_notifications_allowed', 'true');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
  }, { token: mockToken, debugRole: role });
}

async function seedTelegramWebViewSession(page: Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'user');
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    window.localStorage.setItem('bet_tma_mock_vk_group_member', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_messages_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_notifications_allowed', 'true');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
    (window as any).Telegram = {
      WebApp: {
        initData: 'mock_debug_user',
        initDataUnsafe: {
          user: {
            id: 123456789,
            first_name: 'Иван',
            last_name: 'Подписчик',
            username: 'debug_user',
          },
        },
        isActive: true,
        platform: 'android',
        devicePerformanceClass: 1,
        ready: () => undefined,
        expand: () => undefined,
        close: () => undefined,
        onEvent: () => undefined,
        offEvent: () => undefined,
      },
    };
  }, { token: mockToken });
}

function collectRuntimeErrors(page: Page) {
  const runtimeErrors: string[] = [];

  page.on('console', (message) => {
    const text = message.text();
    const expectedOfflineApiNoise =
      text.includes('Failed to load resource') &&
      (text.includes('net::ERR_CONNECTION_REFUSED') ||
        text.includes('Could not connect to localhost: Connection refused'));
    if (message.type() === 'error' && !expectedOfflineApiNoise) {
      runtimeErrors.push(text);
    }
  });

  page.on('pageerror', (error) => {
    runtimeErrors.push(error.message);
  });

  page.on('requestfailed', (request) => {
    runtimeErrors.push(`Request failed: ${request.method()} ${request.url()} (${request.failure()?.errorText || 'unknown error'})`);
  });

  return runtimeErrors;
}

test.describe('@compat browser matrix smoke', () => {
  test('loads the browser/PWA hub without Telegram SDK, keeps navigation in viewport, and serves the service worker', async ({ page }) => {
    const runtimeErrors = collectRuntimeErrors(page);
    await seedCompatSession(page);

    await page.goto('/?perf_profile=lowPower');

    const nav = page.getByRole('navigation', { name: /Навигация приложения/ }).first();
    await expect(nav).toBeVisible({ timeout: 15_000 });
    await expect(nav.getByRole('button', { name: 'Лента' })).toHaveAttribute('aria-current', 'page');
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.performanceProfile)).toBe('lowPower');
    await expect.poll(() => page.evaluate(() => Boolean((window as any).Telegram))).toBe(false);

    const navButtons = nav.getByRole('button');
    for (let index = 0; index < await navButtons.count(); index += 1) {
      await expect(navButtons.nth(index)).toBeInViewport({ ratio: 1 });
    }

    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await expect(nav).toBeVisible();

    await nav.getByRole('button', { name: 'Профиль' }).click();
    await expect(page.getByText('Профиль Shamrai')).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);

    const serviceWorkerStatus = await page.evaluate(async () => {
      const response = await fetch('/service-worker.js', { cache: 'no-store' });
      if (!response.ok) return `http-${response.status}`;
      if (!('serviceWorker' in navigator)) return 'unsupported';

      try {
        const registration = await navigator.serviceWorker.register('/service-worker.js');
        await registration.unregister();
        return 'registered';
      } catch (error) {
        return error instanceof Error ? `error-${error.name}` : 'error-unknown';
      }
    });

    expect(serviceWorkerStatus).toMatch(/^(registered|unsupported)$/);
    expect(runtimeErrors).toEqual([]);
  });

  test('opens admin stats and settings with debug auth and no runtime crash', async ({ page }) => {
    const runtimeErrors = collectRuntimeErrors(page);
    await seedCompatSession(page, 'admin');

    await page.goto('/app?perf_profile=lowPower');

    const nav = page.getByRole('navigation', { name: /Навигация администратора/ }).first();
    await expect(nav).toBeVisible({ timeout: 15_000 });

    await nav.getByRole('button', { name: 'Статистика' }).click();
    await expect(nav.getByRole('button', { name: 'Статистика' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText('Статистика Shamrai').first()).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);

    await nav.getByRole('button', { name: 'Настройки' }).click();
    await expect(nav.getByRole('button', { name: 'Настройки' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('heading', { name: 'Настройки' }).first()).toBeVisible({ timeout: 15_000 });
    await page.reload();
    await expect(page.getByRole('navigation', { name: /Навигация администратора/ }).first()).toBeVisible({ timeout: 15_000 });

    expect(runtimeErrors).toEqual([]);
  });

  test('keeps Telegram Android WebView-like low-power session stable with VK-linked mock state', async ({ page }) => {
    const runtimeErrors = collectRuntimeErrors(page);
    await seedTelegramWebViewSession(page);

    await page.goto('/?perf_profile=lowPower&tgWebAppPlatform=android&tgWebAppVersion=8.0');

    const nav = page.getByRole('navigation', { name: /Навигация приложения/ }).first();
    await expect(nav).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.performanceProfile)).toBe('lowPower');
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.telegramSurface)).toBe('true');
    await nav.getByRole('button', { name: 'Профиль' }).click();
    await expect(page.getByText('Профиль Shamrai')).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);

    expect(runtimeErrors).toEqual([]);
  });
});
