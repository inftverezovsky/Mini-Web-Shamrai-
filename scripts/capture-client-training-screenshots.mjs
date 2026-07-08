import { mkdir, readFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const require = createRequire(path.join(repoRoot, 'frontend', 'package.json'));
const { chromium } = require('playwright');
const baseUrl = process.env.CLIENT_TRAINING_BASE_URL || 'http://127.0.0.1:5177/';
const outputDir = path.join(repoRoot, 'docs', 'client-training', 'assets');
const rawDir = path.join(outputDir, 'raw');
const viewport = { width: 390, height: 844 };

const defaultStorage = {
  vkLinked: false,
  vkMessagesAllowed: false,
  vkGroupMember: true,
  vkNotificationsAllowed: false,
  webPushEnabled: false,
};

function trainingStorage(overrides = {}) {
  return {
    ...defaultStorage,
    ...overrides,
  };
}

async function seedClientState(page, storage) {
  await page.addInitScript((state) => {
    localStorage.clear();
    localStorage.setItem('bet_tma_jwt_token', 'mock_debug_access_token');
    localStorage.setItem('bet_tma_debug_role', 'user');
    localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    localStorage.setItem('bet_tma_mock_bookmaker_ids', JSON.stringify([1, 2, 4]));
    localStorage.setItem('bet_tma_mock_preferences', JSON.stringify({
      alert_min_coef: 1.5,
      odds_drop_notifications_enabled: true,
      is_night_mode: false,
      night_mode_start: '23:00',
      night_mode_end: '08:00',
      preferred_sports: ['football', 'hockey'],
      stats_display_mode: 'percent',
    }));

    if (state.vkLinked) {
      localStorage.setItem('bet_tma_mock_vk_user_id', 'vk_mock_741852963');
    }
    localStorage.setItem('bet_tma_mock_vk_group_member', String(state.vkGroupMember));
    localStorage.setItem('bet_tma_mock_vk_messages_allowed', String(state.vkMessagesAllowed));
    localStorage.setItem('bet_tma_mock_vk_notifications_allowed', String(state.vkNotificationsAllowed));
    localStorage.setItem('bet_tma_mock_web_push_enabled', String(state.webPushEnabled));
  }, storage);
}

async function openClientPage(browser, storage = trainingStorage(), options = {}) {
  const contextOptions = {
    viewport,
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    reducedMotion: 'reduce',
  };
  if (options.permissions) {
    contextOptions.permissions = options.permissions;
  }
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  await seedClientState(page, storage);
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.waitForTimeout(1600);
  return { context, page };
}

async function clickBottomTab(page, label) {
  const tab = page.locator('button').filter({ hasText: label }).last();
  await tab.click();
  await page.waitForTimeout(1200);
}

async function openSection(page, selector) {
  const section = page.locator(selector).first();
  await section.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'nearest' }));
  await page.waitForTimeout(300);
  const button = section.locator('button[aria-expanded]').first();
  const expanded = await button.getAttribute('aria-expanded').catch(() => null);
  if (expanded !== 'true') {
    await button.click();
    await page.waitForTimeout(700);
  }
  await section.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'nearest' }));
  await page.waitForTimeout(300);
}

async function captureRaw(page, fileName) {
  const rawPath = path.join(rawDir, fileName);
  await page.screenshot({ path: rawPath, animations: 'disabled' });
  return rawPath;
}

function calloutHtml(annotation) {
  const width = annotation.width || 156;
  return `
    <div class="marker" style="left:${annotation.x - 13}px;top:${annotation.y - 13}px;">${annotation.number}</div>
    <div class="callout" style="left:${annotation.labelX}px;top:${annotation.labelY}px;width:${width}px;">
      <strong>${annotation.number}</strong>
      <span>${annotation.text}</span>
    </div>
  `;
}

async function annotateImage(browser, rawPath, outputPath, annotations) {
  const image = await readFile(rawPath);
  const src = `data:image/png;base64,${image.toString('base64')}`;
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 2,
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await page.setContent(`
    <!doctype html>
    <html lang="ru">
      <head>
        <meta charset="utf-8" />
        <style>
          * { box-sizing: border-box; }
          html, body {
            width: ${viewport.width}px;
            height: ${viewport.height}px;
            margin: 0;
            overflow: hidden;
            background: #020617;
            font-family: Arial, sans-serif;
          }
          .frame {
            position: relative;
            width: ${viewport.width}px;
            height: ${viewport.height}px;
            overflow: hidden;
          }
          img {
            display: block;
            width: ${viewport.width}px;
            height: ${viewport.height}px;
            object-fit: cover;
          }
          .marker {
            position: absolute;
            z-index: 3;
            display: grid;
            width: 26px;
            height: 26px;
            place-items: center;
            border: 2px solid #0f172a;
            border-radius: 999px;
            background: #fde047;
            color: #020617;
            font-size: 13px;
            font-weight: 900;
            box-shadow: 0 0 0 3px rgba(253, 224, 71, 0.32), 0 10px 24px rgba(0, 0, 0, 0.45);
          }
          .callout {
            position: absolute;
            z-index: 4;
            display: flex;
            gap: 7px;
            align-items: flex-start;
            min-height: 34px;
            padding: 8px 9px;
            border: 1px solid rgba(103, 232, 249, 0.78);
            border-radius: 10px;
            background: rgba(2, 6, 23, 0.88);
            color: #f8fafc;
            font-size: 10.5px;
            font-weight: 700;
            line-height: 1.22;
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.46);
          }
          .callout strong {
            display: grid;
            width: 18px;
            height: 18px;
            flex: 0 0 auto;
            place-items: center;
            border-radius: 999px;
            background: #fde047;
            color: #020617;
            font-size: 10px;
          }
          .callout span {
            min-width: 0;
          }
        </style>
      </head>
      <body>
        <div class="frame">
          <img alt="" src="${src}" />
          ${annotations.map(calloutHtml).join('\n')}
        </div>
      </body>
    </html>
  `);
  await page.screenshot({ path: outputPath, animations: 'disabled' });
  await context.close();
}

const scenarios = [
  {
    file: '01-client-feed.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 52, y: 73, labelX: 10, labelY: 14, width: 174, text: 'Карточка прогноза: спорт, статус и матч.' },
      { number: 2, x: 325, y: 182, labelX: 214, labelY: 100, width: 158, text: 'Коэффициент, по которому открыт прогноз.' },
      { number: 3, x: 93, y: 151, labelX: 12, labelY: 216, width: 164, text: 'Кнопка открытия матча и подробностей.' },
      { number: 4, x: 195, y: 815, labelX: 92, labelY: 725, width: 214, text: 'Нижнее меню: лента, чат, статистика, профиль.' },
    ],
    prepare: async () => {},
  },
  {
    file: '02-client-chat.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 72, y: 91, labelX: 14, labelY: 18, width: 160, text: 'Статус web-канала и личного бота.' },
      { number: 2, x: 111, y: 151, labelX: 14, labelY: 196, width: 180, text: 'Переключение между сигналами и поддержкой.' },
      { number: 3, x: 209, y: 395, labelX: 202, labelY: 236, width: 174, text: 'Здесь появляются web-сигналы и ответы поддержки.' },
      { number: 4, x: 75, y: 815, labelX: 95, labelY: 725, width: 218, text: 'Если нужен ответ менеджера, оставайтесь во вкладке Чат.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Чат');
    },
  },
  {
    file: '08-take-announcement.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 146, y: 172, labelX: 18, labelY: 72, width: 190, text: 'Анонс приходит в личный бот и может дублироваться из уведомления.' },
      { number: 2, x: 166, y: 684, labelX: 20, labelY: 548, width: 164, text: '«Взять» отправляет заявку Shamrai на полный прогноз.' },
      { number: 3, x: 278, y: 684, labelX: 206, labelY: 548, width: 164, text: '«Не взять» означает, что клиент пропускает этот анонс.' },
      { number: 4, x: 200, y: 430, labelX: 104, labelY: 730, width: 202, text: 'БК в анонсе показывают, где проверять линию.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Чат');
      await page.waitForTimeout(1200);
      await page.getByText('Анонс закрытого прогноза').first().scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, 260));
      await page.waitForTimeout(500);
    },
  },
  {
    file: '03-client-stats.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 195, y: 27, labelX: 18, labelY: 62, width: 200, text: 'Переключатель: личная статистика или общая статистика Shamrai.' },
      { number: 2, x: 126, y: 124, labelX: 16, labelY: 186, width: 166, text: 'Прибыль во флэтах за выбранный период.' },
      { number: 3, x: 306, y: 142, labelX: 208, labelY: 190, width: 166, text: 'ROI показывает эффективность ставок.' },
      { number: 4, x: 195, y: 254, labelX: 97, labelY: 310, width: 198, text: 'Фильтр периода: неделя, месяц, квартал, все.' },
      { number: 5, x: 92, y: 435, labelX: 20, labelY: 505, width: 170, text: 'Ключевые метрики: ставки, проход, средний КФ.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Статистика');
      await page.waitForTimeout(1200);
    },
  },
  {
    file: '04-profile-web-push.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 52, y: 285, labelX: 14, labelY: 152, width: 164, text: 'Карточка Web Push в профиле.' },
      { number: 2, x: 325, y: 284, labelX: 198, labelY: 190, width: 176, text: 'Статус канала: нужно включить, подключено или запрещено.' },
      { number: 3, x: 126, y: 446, labelX: 14, labelY: 500, width: 176, text: 'Главная кнопка подключения уведомлений.' },
      { number: 4, x: 269, y: 446, labelX: 202, labelY: 500, width: 166, text: 'Обновить статус после разрешения в браузере.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Профиль');
      await page.waitForTimeout(1200);
      await openSection(page, '#web-push');
    },
  },
  {
    file: '05-profile-vk-connect.png',
    storage: trainingStorage({ vkLinked: false, vkMessagesAllowed: false }),
    annotations: [
      { number: 1, x: 51, y: 395, labelX: 14, labelY: 174, width: 166, text: 'Блок подключения VK находится в профиле.' },
      { number: 2, x: 312, y: 395, labelX: 198, labelY: 204, width: 176, text: 'Бейдж показывает, что ещё нужно настроить.' },
      { number: 3, x: 195, y: 428, labelX: 74, labelY: 475, width: 238, text: 'Нажмите «Синхронизировать VK», чтобы привязать VK ID.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Профиль');
      await page.waitForTimeout(1200);
      await openSection(page, '#connect-vk');
    },
  },
  {
    file: '06-profile-vk-delivery.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: false }),
    annotations: [
      { number: 1, x: 178, y: 365, labelX: 14, labelY: 208, width: 184, text: 'VK ID уже привязан к профилю.' },
      { number: 2, x: 299, y: 454, labelX: 198, labelY: 260, width: 176, text: 'Статус личных сообщений VK: доставку нужно разрешить.' },
      { number: 3, x: 117, y: 549, labelX: 16, labelY: 606, width: 176, text: 'Откройте диалог VK и разрешите сообщения сообществу.' },
      { number: 4, x: 274, y: 549, labelX: 200, labelY: 606, width: 174, text: 'После разрешения нажмите «Проверить доступ».' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Профиль');
      await page.waitForTimeout(1200);
      await openSection(page, '#connect-vk');
    },
  },
  {
    file: '07-profile-notifications.png',
    storage: trainingStorage({ vkLinked: true, vkMessagesAllowed: true }),
    annotations: [
      { number: 1, x: 197, y: 238, labelX: 20, labelY: 96, width: 188, text: 'Минимальный коэффициент для push-сигналов.' },
      { number: 2, x: 337, y: 356, labelX: 206, labelY: 170, width: 166, text: 'Уведомления о падении коэффициента.' },
      { number: 3, x: 337, y: 414, labelX: 206, labelY: 520, width: 162, text: 'Ночной режим: не беспокоить в заданное время.' },
      { number: 4, x: 118, y: 500, labelX: 16, labelY: 626, width: 178, text: 'Время начала и окончания тихого режима.' },
      { number: 5, x: 206, y: 556, labelX: 198, labelY: 696, width: 176, text: 'Букмекеры помогают подбирать подходящие прогнозы.' },
    ],
    prepare: async (page) => {
      await clickBottomTab(page, 'Профиль');
      await page.waitForTimeout(1600);
      await page.locator('button').filter({ hasText: 'Умные уведомления' }).first().click();
      await page.waitForTimeout(600);
      await page.locator('button').filter({ hasText: 'Умные уведомления' }).first().evaluate((element) => {
        element.scrollIntoView({ block: 'start', inline: 'nearest' });
        window.scrollBy(0, -8);
      });
      await page.waitForTimeout(500);
    },
  },
];

async function run() {
  await mkdir(rawDir, { recursive: true });
  await mkdir(outputDir, { recursive: true });

  const browser = await chromium.launch({
    headless: process.env.CLIENT_TRAINING_HEADLESS === 'true' ? true : false,
  });

  try {
    for (const scenario of scenarios) {
      const { context, page } = await openClientPage(browser, scenario.storage, {
        permissions: scenario.permissions,
      });
      await scenario.prepare(page);
      const rawPath = await captureRaw(page, scenario.file);
      await context.close();
      await annotateImage(browser, rawPath, path.join(outputDir, scenario.file), scenario.annotations);
      console.log(`captured ${scenario.file}`);
    }
  } finally {
    await browser.close();
    await rm(rawDir, { recursive: true, force: true });
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
