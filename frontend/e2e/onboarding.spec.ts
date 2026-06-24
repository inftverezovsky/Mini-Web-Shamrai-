import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';

async function seedOnboardingSession(page: Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'user');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
    if (!window.sessionStorage.getItem('shamrai_onboarding_e2e_seeded')) {
      window.localStorage.setItem('bet_tma_mock_is_onboarded', 'false');
      window.localStorage.removeItem('bet_tma_mock_vk_user_id');
      window.sessionStorage.setItem('shamrai_onboarding_e2e_seeded', 'true');
    }
  }, { token: mockToken });
}

test('user completes the 6-step welcome quiz in low power mode', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await seedOnboardingSession(page);
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

  await expect(page.getByRole('heading', { name: 'БК и спорт-интересы' })).toBeVisible();
  await page.getByRole('button', { name: /Фонбет/ }).click();
  await page.getByRole('button', { name: /БетБум/ }).click();
  await page.getByRole('button', { name: /^Футбол$/ }).click();
  await page.getByRole('button', { name: /^Теннис$/ }).click();
  await page.getByRole('button', { name: 'Рассчитать модель' }).click();

  await expect(page.getByRole('heading', { name: 'Ваша модель собрана' })).toBeVisible();
  await expect(page.getByText(/FOMO-ретро окно/)).toBeVisible();
  await page.getByRole('button', { name: /Я понимаю/ }).click();
  await page.getByRole('button', { name: 'Войти в Analytics Hub' }).click();

  await page.goto('/?perf_profile=lowPower');
  await expect(page.getByRole('navigation', { name: /Навигация приложения/ })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
