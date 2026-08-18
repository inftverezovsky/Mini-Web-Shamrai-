import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';
const attemptId = '22222222-2222-4222-8222-222222222222';

async function seedPaidFlatCheckout(page: Page) {
  await page.addInitScript(({ token, paymentAttemptId }) => {
    const now = new Date().toISOString();
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'user');
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    window.localStorage.setItem('bet_tma_mock_vk_group_member', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_messages_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_notifications_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_system_settings', JSON.stringify({
      SUBSCRIPTION_PURCHASES_ENABLED: { value: 'true', is_configured: true, updated_at: now },
    }));
    window.localStorage.setItem('shamrai_pending_checkout_v1', JSON.stringify({
      version: 1,
      intent_id: '11111111-1111-4111-8111-111111111111',
      attempt_id: paymentAttemptId,
      provider: 'yookassa',
      plan_id: 1,
      promo_code: null,
      created_at: now,
    }));
    window.localStorage.setItem('bet_tma_mock_payment_attempts', JSON.stringify({
      [paymentAttemptId]: {
        attempt_id: paymentAttemptId,
        checkout_state: 'ready',
        status: 'succeeded',
        requires_flat_setup: true,
      },
    }));
    window.localStorage.removeItem('bet_tma_mock_flat_subscription');
  }, { token: mockToken, paymentAttemptId: attemptId });
}

async function seedAdminSession(page: Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', 'admin');
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.removeItem('bet_tma_mock_admin_users');
    window.localStorage.setItem('shamrai_performance_profile_override', 'lowPower');
  }, { token: mockToken });
}

test.describe('@compat flat subscription safe checkout', () => {
  test('returns from payment, polls the attempt, and configures the first flat', async ({ page }, testInfo) => {
    const runtimeErrors: string[] = [];
    page.on('pageerror', (error) => runtimeErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') runtimeErrors.push(message.text());
    });
    await seedPaidFlatCheckout(page);
    await page.goto(`/app?attempt_id=${attemptId}`);

    await expect(page.getByRole('heading', { name: 'Оплата прошла' })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('heading', { name: 'Укажите размер одного флета' })).toBeVisible();

    await page.getByPlaceholder('Например: 10 000').fill('10 000');
    const activateButton = page.getByRole('button', { name: 'Активировать' });
    await expect(activateButton).toBeEnabled();
    if (testInfo.project.name.startsWith('mobile-')) await activateButton.tap();
    else await activateButton.click();

    await expect.poll(() => runtimeErrors).toEqual([]);
    await expect(page.getByText('Абонемент активен')).toBeVisible();
    await expect(page.getByText('10 000 ₽')).toBeVisible();
  });

  test('admin previews a revision-guarded correction and must enter a reason', async ({ page }) => {
    await seedAdminSession(page);
    await page.goto('/app?perf_profile=lowPower');

    const navigation = page.getByRole('navigation', { name: /Навигация администратора/ }).first();
    await expect(navigation).toBeVisible({ timeout: 15_000 });
    await navigation.getByRole('button', { name: 'Клиенты' }).click();

    await expect(page.getByRole('heading', { name: 'CRM клиентов' })).toBeVisible({ timeout: 15_000 });
    const queues = page.getByRole('region', { name: 'Очереди флетовых абонементов' });
    await expect(queues.getByRole('button', { name: /Нужна настройка/ })).toBeVisible();
    await expect(queues.getByRole('button', { name: /Закрываются/ })).toBeVisible();
    await expect(queues.getByRole('button', { name: /Завершены/ })).toBeVisible();

    await page.getByRole('button', { name: 'Открыть' }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/active · rev\. 1/i)).toBeVisible();

    await dialog.getByRole('textbox', { name: 'Размер флета клиента' }).fill('12 000');
    await dialog.getByRole('button', { name: 'Проверить изменение флета' }).click();

    const preview = dialog.getByRole('region', { name: 'Предпросмотр финансовой корректировки' });
    await expect(preview).toBeVisible();
    await expect(preview.getByText('До', { exact: true })).toBeVisible();
    await expect(preview.getByText('После', { exact: true })).toBeVisible();
    const confirmButton = preview.getByRole('button', { name: 'Подтвердить корректировку' });
    await expect(confirmButton).toBeDisabled();

    await preview.getByRole('textbox', { name: 'Причина финансовой корректировки' }).fill('Клиент уточнил размер флета');
    await expect(confirmButton).toBeEnabled();
    // Keyboard activation exercises the native button without WebKit's flaky
    // pointer-stability wait inside a long, independently scrolling dialog.
    await confirmButton.press('Enter');

    await expect(preview).toBeHidden();
    await expect.poll(() => page.evaluate(() => {
      const users = JSON.parse(window.localStorage.getItem('bet_tma_mock_admin_users') || '[]');
      return users.find((user: { telegram_id: number }) => user.telegram_id === 123456789)?.flat_subscription?.revision;
    })).toBe(2);
    await expect(dialog.getByTestId('flat-subscription-status')).toHaveText(/active · rev\. 2/i);
    await expect(dialog.getByText('12 000', { exact: true })).toBeVisible();
  });
});
