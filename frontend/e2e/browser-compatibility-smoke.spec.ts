import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';

async function seedCompatSession(page: Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'user');
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    window.localStorage.setItem('bet_tma_mock_vk_group_member', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_messages_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_notifications_allowed', 'true');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
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

  return runtimeErrors;
}

test.describe('@compat browser matrix smoke', () => {
  test('loads the hub, keeps navigation in viewport, and serves the service worker', async ({ page }) => {
    const runtimeErrors = collectRuntimeErrors(page);
    await seedCompatSession(page);

    await page.goto('/?perf_profile=lowPower');

    const nav = page.getByRole('navigation', { name: /Навигация приложения/ }).first();
    await expect(nav).toBeVisible({ timeout: 15_000 });
    await expect(nav.getByRole('button', { name: 'Лента' })).toHaveAttribute('aria-current', 'page');

    const viewport = page.viewportSize();
    const navBox = await nav.boundingBox();
    expect(navBox).not.toBeNull();
    expect(viewport).not.toBeNull();

    if (navBox && viewport) {
      expect(navBox.x).toBeGreaterThanOrEqual(-1);
      expect(navBox.x + navBox.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(navBox.y).toBeGreaterThanOrEqual(-1);
      expect(navBox.y + navBox.height).toBeLessThanOrEqual(viewport.height + 1);
    }

    await nav.getByRole('button', { name: 'Профиль' }).click();
    await expect(page.getByText('Профиль Shamrai')).toBeVisible();

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
});
