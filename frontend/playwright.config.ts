import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_PORT || 4174);
const baseURL = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-smoke',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chrome-compat',
      grep: /@compat/,
      use: { ...devices['Pixel 7'] },
    },
    {
      name: 'mobile-webkit-compat',
      grep: /@compat/,
      use: { ...devices['iPhone 15'] },
    },
    {
      name: 'webkit-compat',
      grep: /@compat/,
      use: { ...devices['Desktop Safari'] },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    env: {
      ...process.env,
      VITE_ENABLE_DEBUG_AUTH: 'true',
      VITE_TELEGRAM_BOT_USERNAME: 'Shamra1_bot',
      VITE_VK_ID_APP_ID: process.env.VITE_VK_ID_APP_ID || '',
      VITE_VK_ID_REDIRECT_URI: process.env.VITE_VK_ID_REDIRECT_URI || baseURL,
      VITE_VK_GROUP_ID: process.env.VITE_VK_GROUP_ID || '',
      VITE_WEB_PUSH_VAPID_PUBLIC_KEY: process.env.VITE_WEB_PUSH_VAPID_PUBLIC_KEY || '',
    },
  },
});
