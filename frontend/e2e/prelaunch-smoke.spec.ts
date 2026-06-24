import { expect, test, type Page } from '@playwright/test';

const mockToken = 'mock_debug_access_token';

async function seedMockSession(page: Page, role: 'user' | 'admin') {
  await page.addInitScript(({ token, nextRole }) => {
    window.localStorage.setItem('bet_tma_jwt_token', token);
    window.localStorage.setItem('bet_tma_debug_role', nextRole);
    window.localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    window.localStorage.setItem('bet_tma_mock_vk_group_member', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_messages_allowed', 'true');
    window.localStorage.setItem('bet_tma_mock_vk_notifications_allowed', 'true');
  }, { token: mockToken, nextRole: role });
}

test.describe('prelaunch smoke without payments', () => {
  test('user can open feed, profile setup, and support chat with a file', async ({ page }) => {
    await seedMockSession(page, 'user');
    await page.goto('/');

    const userNav = page.getByRole('navigation', { name: /Навигация приложения/ });
    await expect(userNav).toBeVisible();
    await expect(userNav.getByRole('button', { name: 'Лента' })).toHaveAttribute('aria-current', 'page');

    await userNav.getByRole('button', { name: 'Профиль' }).click();
    await expect(page.getByText('Профиль Shamrai')).toBeVisible();
    await expect(page.getByText('Синхронизация Telegram')).toBeVisible();
    await expect(page.getByText('Синхронизация VK')).toBeVisible();
    await expect(page.getByText('Web Push уведомления')).toBeVisible();

    await userNav.getByRole('button', { name: 'Чат' }).click();
    await expect(page.getByRole('button', { name: /Личный бот/ })).toBeVisible();
    await page.getByRole('button', { name: /Поддержка/ }).click();
    await expect(page.getByPlaceholder('Поиск по истории поддержки')).toBeVisible();

    await page.locator('input[type="file"]').setInputFiles({
      name: 'smoke-note.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('prelaunch smoke file'),
    });
    await expect(page.getByText('smoke-note.txt')).toBeVisible();
    await page.getByPlaceholder('Подпись к вложению...').fill('Файл для smoke');
    await page.locator('button[title="Отправить сообщение Shamrai"]').click();

    await expect(page.getByText('Файл для smoke')).toBeVisible();
    await expect(page.getByText('smoke-note.txt').first()).toBeVisible();
    await expect(page.getByLabel('Скачать вложение').last()).toBeVisible();
  });

  test('admin can open chat operations, reply, and see delivery statuses in CRM', async ({ page }) => {
    await seedMockSession(page, 'admin');
    await page.goto('/');

    const adminNav = page.getByRole('navigation', { name: /Навигация администратора/ });
    await expect(adminNav).toBeVisible();

    await adminNav.getByRole('button', { name: 'Клиенты' }).click();
    await expect(page.getByText('CRM клиентов')).toBeVisible();
    await expect(page.getByLabel('Статусы авторизации и синхронизации клиента').first()).toBeVisible();
    await expect(page.getByText('готов').first()).toBeVisible();
    await expect(page.getByText('нет разрешения').first()).toBeVisible();
    await expect(page.getByText('push включен').first()).toBeVisible();
    await expect(page.getByText(/побед[аы]? подряд|0 матчей/).first()).toBeVisible();
    await expect(page.getByText('Открыть').first()).toBeVisible();

    await adminNav.getByRole('button', { name: 'Чаты' }).click();
    await expect(page.getByRole('heading', { name: 'Чаты' })).toBeVisible();
    await page.getByRole('button', { name: /Иван/ }).first().click();
    await expect(page.getByPlaceholder('Поиск в диалоге')).toBeVisible();

    await page.getByPlaceholder('Ответить от имени Shamrai...').fill('Smoke ответ без платежей');
    await page.locator('button[title="Отправить сообщение клиенту"]').click();
    await expect(page.locator('.admin-chat-message-row').filter({ hasText: 'Smoke ответ без платежей' }).last()).toBeVisible();
    await expect(page.getByText(/Диалог закрыт|Сообщение отправлено/)).toBeVisible();
  });
});
