import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';

async function seedOnboardingSession(page: Page, options: { welcomeQuizEnabled?: boolean } = {}) {
  await page.addInitScript(({ token, welcomeQuizEnabled }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'user');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
    if (welcomeQuizEnabled) {
      window.localStorage.setItem('bet_tma_mock_system_settings', JSON.stringify({
        WELCOME_QUIZ_ENABLED: {
          value: 'true',
          is_configured: true,
          updated_at: new Date().toISOString(),
        },
      }));
    } else {
      window.localStorage.removeItem('bet_tma_mock_system_settings');
    }
    if (!window.sessionStorage.getItem('shamrai_onboarding_e2e_seeded')) {
      window.localStorage.setItem('bet_tma_mock_is_onboarded', 'false');
      window.localStorage.removeItem('bet_tma_mock_vk_user_id');
      window.sessionStorage.setItem('shamrai_onboarding_e2e_seeded', 'true');
    }
  }, { token: mockToken, welcomeQuizEnabled: Boolean(options.welcomeQuizEnabled) });
}

test('user can skip the first-auth VK welcome step and enter the hub', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await seedOnboardingSession(page);
  await page.goto('/?force_onboarding&perf_profile=lowPower');

  await expect(page.getByRole('heading', { name: 'VK' })).toBeVisible();
  await expect(page.getByText('Шаг')).toHaveCount(0);
  await expect(page.getByText('Доставка')).toHaveCount(0);
  await expect(page.getByText(/Можно пропустить и добавить VK позже в профиле/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Что сразу убивает доверие?' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Пропустить' }).click();
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem('bet_tma_mock_is_onboarded'))).toBe('true');

  await page.goto('/?perf_profile=lowPower');
  await expect(page.getByRole('navigation', { name: /Навигация приложения/ })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('user can link VK from the first-auth welcome step and enter the hub', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await seedOnboardingSession(page);
  await page.goto('/?force_onboarding&perf_profile=lowPower');

  await expect(page.getByRole('heading', { name: 'VK' })).toBeVisible();
  await page.getByRole('button', { name: 'Связать VK' }).click();
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem('bet_tma_mock_vk_user_id'))).toBe('vk_mock_741852963');
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem('bet_tma_mock_is_onboarded'))).toBe('true');

  await page.goto('/?perf_profile=lowPower');
  await expect(page.getByRole('navigation', { name: /Навигация приложения/ })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('user completes the admin-enabled 6-step welcome quiz in low power mode', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await seedOnboardingSession(page, { welcomeQuizEnabled: true });
  await page.goto('/?force_onboarding&perf_profile=lowPower');

  await expect(page.getByRole('heading', { name: 'Настроим ленту за 60 секунд' })).toBeVisible();
  await page.getByRole('button', { name: 'Настроить без VK' }).click();

  await expect(page.getByRole('heading', { name: 'Что сразу убивает доверие?' })).toBeVisible();
  await page.getByRole('button', { name: /Поздние сигналы/ }).click();
  await page.getByRole('button', { name: 'Далее' }).click();

  await expect(page.getByRole('heading', { name: 'Цель и уровень' })).toBeVisible();
  await page.getByRole('button', { name: /Быстрые входы по линии/ }).click();
  await page.getByRole('button', { name: /Профи/ }).click();
  await page.getByRole('button', { name: 'Далее' }).click();

  await expect(page.getByRole('heading', { name: 'Банк и риск' })).toBeVisible();
  await page.getByRole('button', { name: /Более 100 000/ }).click();
  await page.getByRole('button', { name: /Агрессивная/ }).click();
  await page.getByRole('button', { name: 'Далее' }).click();

  await expect(page.getByRole('heading', { name: 'Букмекерские конторы' })).toBeVisible();
  await page.getByRole('button', { name: /Фонбет/ }).click();
  await page.getByRole('button', { name: /БетБум/ }).click();
  await page.getByRole('button', { name: /VIP-сопровождение/ }).click();
  await page.getByRole('button', { name: 'Рассчитать модель' }).click();

  await expect(page.getByRole('heading', { name: 'Ваша модель собрана' })).toBeVisible();
  await expect(page.getByText('VIP-вердикт Shamrai')).toHaveCount(0);
  await expect(page.getByText('VIP-контур')).toHaveCount(0);
  await expect(page.getByText(/Персональный флэт/)).toBeVisible();
  await expect(page.getByText(/Ретро-оценка окна/)).toBeVisible();
  await page.getByRole('button', { name: /Я понимаю/ }).click();
  await page.getByRole('button', { name: 'Войти в Analytics Hub' }).click();

  await page.goto('/?perf_profile=lowPower');
  await expect(page.getByRole('navigation', { name: /Навигация приложения/ })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
