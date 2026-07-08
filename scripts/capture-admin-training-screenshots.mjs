import { mkdir, readFile, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const require = createRequire(path.join(repoRoot, 'frontend', 'package.json'));
const { chromium } = require('playwright');
const baseUrl = process.env.ADMIN_TRAINING_BASE_URL || 'http://127.0.0.1:5177/';
const outputDir = path.join(repoRoot, 'docs', 'admin-training', 'assets');
const rawDir = path.join(outputDir, 'raw');
const viewport = { width: 1365, height: 900 };

async function seedAdminState(page) {
  await page.addInitScript(() => {
    localStorage.clear();
    localStorage.setItem('bet_tma_jwt_token', 'mock_debug_access_token');
    localStorage.setItem('bet_tma_debug_role', 'admin');
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
  });
}

async function openAdminPage(browser) {
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await seedAdminState(page);
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.waitForTimeout(1800);
  await page.getByRole('button', { name: /^Панель$/ }).first().waitFor({ timeout: 7000 });
  return { context, page };
}

async function waitForSettledUi(page) {
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.waitForTimeout(900);
}

async function scrollToTop(page) {
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.querySelectorAll('.app-scroll-panel').forEach((element) => {
      element.scrollTo({ top: 0, left: 0 });
    });
  });
  await page.waitForTimeout(250);
}

async function clickButton(page, label) {
  await page.getByRole('button', { name: new RegExp(`^${label}$`) }).first().click();
  await waitForSettledUi(page);
  await scrollToTop(page);
}

async function clickButtonByText(page, label) {
  await page.locator('button').filter({ hasText: label }).first().click();
  await waitForSettledUi(page);
  await scrollToTop(page);
}

async function captureRaw(page, fileName) {
  const rawPath = path.join(rawDir, fileName);
  await page.screenshot({ path: rawPath, animations: 'disabled' });
  return rawPath;
}

function calloutHtml(annotation) {
  const width = annotation.width || 250;
  return `
    <div class="marker" style="left:${annotation.x - 15}px;top:${annotation.y - 15}px;">${annotation.number}</div>
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
    deviceScaleFactor: 1,
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
            width: 30px;
            height: 30px;
            place-items: center;
            border: 2px solid #0f172a;
            border-radius: 999px;
            background: #fde047;
            color: #020617;
            font-size: 14px;
            font-weight: 900;
            box-shadow: 0 0 0 4px rgba(253, 224, 71, 0.32), 0 12px 28px rgba(0, 0, 0, 0.45);
          }
          .callout {
            position: absolute;
            z-index: 4;
            display: flex;
            gap: 9px;
            align-items: flex-start;
            min-height: 42px;
            padding: 10px 11px;
            border: 1px solid rgba(103, 232, 249, 0.78);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.9);
            color: #f8fafc;
            font-size: 13px;
            font-weight: 700;
            line-height: 1.26;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.48);
          }
          .callout strong {
            display: grid;
            width: 22px;
            height: 22px;
            flex: 0 0 auto;
            place-items: center;
            border-radius: 999px;
            background: #fde047;
            color: #020617;
            font-size: 11px;
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
    file: '01-admin-panel-feed.png',
    annotations: [
      { number: 1, x: 122, y: 116, labelX: 306, labelY: 112, width: 265, text: 'Боковое меню переключает основные разделы админки.' },
      { number: 2, x: 440, y: 149, labelX: 585, labelY: 87, width: 275, text: 'Внутренние вкладки панели: Лента, Рассылки, Заявки, Результаты.' },
      { number: 3, x: 449, y: 234, labelX: 582, labelY: 208, width: 278, text: 'Новая публикация добавляет прогноз или текст в клиентскую Ленту.' },
      { number: 4, x: 528, y: 407, labelX: 742, labelY: 330, width: 290, text: 'Заполните матч, исход, коэффициент, БК, категорию и описание.' },
      { number: 5, x: 548, y: 806, labelX: 736, labelY: 740, width: 280, text: 'Кнопка публикует материал в Ленту после проверки полей.' },
    ],
    prepare: async () => {},
  },
  {
    file: '02-admin-broadcast-forecast.png',
    annotations: [
      { number: 1, x: 448, y: 149, labelX: 600, labelY: 86, width: 260, text: 'Откройте «Рассылки» внутри панели управления.' },
      { number: 2, x: 494, y: 228, labelX: 662, labelY: 208, width: 260, text: 'Режим «Прогноз» отправляет анонс закрытого прогноза.' },
      { number: 3, x: 500, y: 353, labelX: 716, labelY: 306, width: 290, text: 'Коэффициент, верный кэф и БК задают смысл и аудиторию анонса.' },
      { number: 4, x: 487, y: 530, labelX: 715, labelY: 502, width: 305, text: 'Описание анонса объясняет клиенту, почему стоит нажать «Взять».' },
      { number: 5, x: 549, y: 643, labelX: 715, labelY: 631, width: 282, text: 'После отправки заявки клиентов появятся во вкладке «Заявки».' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Рассылки');
    },
  },
  {
    file: '03-admin-broadcast-set.png',
    annotations: [
      { number: 1, x: 622, y: 228, labelX: 708, labelY: 206, width: 265, text: 'Режим «Набор» нужен для платного набора или ручной продажи.' },
      { number: 2, x: 1043, y: 315, labelX: 926, labelY: 364, width: 300, text: 'Счётчик показывает, сколько клиентов подходит под выбранные БК.' },
      { number: 3, x: 540, y: 442, labelX: 710, labelY: 420, width: 300, text: 'Заполните матч, исход, спорт, коэффициент и стоимость в рублях.' },
      { number: 4, x: 482, y: 641, labelX: 708, labelY: 612, width: 300, text: 'Выберите БК, приложите скрин купона и добавьте описание набора.' },
      { number: 5, x: 548, y: 822, labelX: 710, labelY: 792, width: 278, text: '«Отправить набор» создаёт входящие заявки для обработки.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Рассылки');
      await clickButton(page, 'Набор');
    },
  },
  {
    file: '04-admin-requests.png',
    annotations: [
      { number: 1, x: 942, y: 160, labelX: 585, labelY: 84, width: 275, text: 'Во вкладке «Заявки» видны клиенты, которые нажали «Взять».' },
      { number: 2, x: 469, y: 245, labelX: 625, labelY: 214, width: 300, text: 'Фильтры статусов отделяют ожидание, отправленные, ручные и отменённые заявки.' },
      { number: 3, x: 533, y: 357, labelX: 720, labelY: 332, width: 300, text: 'Группы заявок собираются по матчу или набору. Раскройте группу стрелкой.' },
      { number: 4, x: 1089, y: 356, labelX: 920, labelY: 408, width: 305, text: '«Остановить» прекращает новые действия по матчу или набору.' },
      { number: 5, x: 468, y: 528, labelX: 658, labelY: 512, width: 312, text: 'Внутри группы выберите клиентов, отправьте полную ставку или закройте вручную.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Заявки');
      await page.getByText('Рубин - Краснодар').first().waitFor({ timeout: 5000 });
      const firstGroup = page.locator('button[aria-expanded]').filter({ hasText: 'Рубин - Краснодар' }).first();
      await firstGroup.click().catch(() => undefined);
      await waitForSettledUi(page);
    },
  },
  {
    file: '05-admin-results.png',
    annotations: [
      { number: 1, x: 437, y: 149, labelX: 590, labelY: 84, width: 278, text: '«Результаты» показывает прогнозы, которым нужно поставить исход.' },
      { number: 2, x: 503, y: 261, labelX: 706, labelY: 218, width: 295, text: 'Карточка содержит матч, исход, БК, тип выдачи и исходный коэффициент.' },
      { number: 3, x: 486, y: 555, labelX: 708, labelY: 516, width: 302, text: 'Блок «Упал до» сохраняет и отправляет уведомление о просадке кэфа.' },
      { number: 4, x: 597, y: 650, labelX: 710, labelY: 642, width: 300, text: 'Поставьте результат: Победа, Неудача или Возврат.' },
      { number: 5, x: 591, y: 720, labelX: 710, labelY: 718, width: 300, text: 'Редактирование и удаление доступны до закрытия результата.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Результаты');
    },
  },
  {
    file: '06-admin-stats.png',
    annotations: [
      { number: 1, x: 137, y: 156, labelX: 304, labelY: 162, width: 260, text: 'Раздел «Статистика» открывается из бокового меню.' },
      { number: 2, x: 449, y: 151, labelX: 605, labelY: 90, width: 292, text: 'Переключатели меняют срез: прогнозы, клиенты и детализация.' },
      { number: 3, x: 507, y: 285, labelX: 720, labelY: 244, width: 295, text: 'Верхние карточки показывают ключевые метрики периода.' },
      { number: 4, x: 523, y: 432, labelX: 720, labelY: 406, width: 305, text: 'Период и фильтры меняют расчёт статистики и таблицы.' },
      { number: 5, x: 1235, y: 206, labelX: 934, labelY: 238, width: 305, text: 'Экспорт выгружает отчёты для анализа и передачи в таблицы.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Статистика');
      await page.getByText('Статистика Shamrai').first().waitFor({ timeout: 10000 });
      await page.getByRole('button', { name: /Обзор/ }).first().waitFor({ timeout: 10000 });
      await page.waitForTimeout(700);
    },
  },
  {
    file: '07-admin-crm.png',
    annotations: [
      { number: 1, x: 137, y: 194, labelX: 304, labelY: 190, width: 260, text: 'CRM открывается через раздел «Клиенты».' },
      { number: 2, x: 538, y: 158, labelX: 720, labelY: 96, width: 305, text: 'Поиск и фильтры помогают найти клиента по имени, ID, группе, метке или БК.' },
      { number: 3, x: 499, y: 327, labelX: 720, labelY: 292, width: 310, text: 'Карточка клиента показывает приоритет, каналы связи, баланс и выбранные БК.' },
      { number: 4, x: 1132, y: 327, labelX: 972, labelY: 386, width: 285, text: 'Нажмите «Открыть», чтобы изменить профиль клиента.' },
      { number: 5, x: 494, y: 616, labelX: 720, labelY: 594, width: 310, text: 'Цветные сегменты показывают последние результаты клиента.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Клиенты');
    },
  },
  {
    file: '08-admin-crm-card.png',
    annotations: [
      { number: 1, x: 898, y: 107, labelX: 600, labelY: 74, width: 285, text: 'Модальная карточка открывается поверх CRM и фокусирует одного клиента.' },
      { number: 2, x: 877, y: 264, labelX: 570, labelY: 232, width: 300, text: 'Здесь меняются группа, метка и сегментация клиента.' },
      { number: 3, x: 877, y: 401, labelX: 570, labelY: 378, width: 300, text: 'Баланс матчей можно увеличить, обнулить или закрыть гарантию.' },
      { number: 4, x: 889, y: 586, labelX: 570, labelY: 560, width: 300, text: 'Блок БК хранит список контор клиента для таргетинга прогнозов.' },
      { number: 5, x: 884, y: 809, labelX: 570, labelY: 782, width: 300, text: 'После изменений обязательно нажмите «Сохранить изменения».' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Клиенты');
      await page.locator('button').filter({ hasText: 'Открыть' }).first().click();
      await page.getByRole('dialog').waitFor({ timeout: 5000 });
      await waitForSettledUi(page);
    },
  },
  {
    file: '09-admin-chats.png',
    annotations: [
      { number: 1, x: 136, y: 231, labelX: 304, labelY: 190, width: 258, text: 'Раздел «Чаты» открывает диалоги клиентов с Shamrai.' },
      { number: 2, x: 424, y: 184, labelX: 570, labelY: 122, width: 292, text: 'Фильтры «Открытые/Закрытые» и поиск помогают управлять очередью.' },
      { number: 3, x: 449, y: 357, labelX: 570, labelY: 318, width: 302, text: 'Слева список диалогов, бейдж «ответ» показывает, где нужно вмешаться.' },
      { number: 4, x: 833, y: 214, labelX: 970, labelY: 164, width: 290, text: 'Справа открывается история выбранного клиента и поиск по диалогу.' },
      { number: 5, x: 859, y: 813, labelX: 970, labelY: 760, width: 285, text: 'Внизу поле ответа: текст, вложения и отправка от имени Shamrai.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Чаты');
      await page.locator('.admin-chat-conversation-row button').first().click().catch(() => undefined);
      await waitForSettledUi(page);
    },
  },
  {
    file: '10-admin-settings-texts.png',
    annotations: [
      { number: 1, x: 138, y: 284, labelX: 304, labelY: 210, width: 260, text: '«Настройки» содержит тексты, режимы, интеграции, визуал, мониторинг и маркетинг.' },
      { number: 2, x: 501, y: 207, labelX: 720, labelY: 152, width: 300, text: 'Подвкладки настроек переключают разные группы управления.' },
      { number: 3, x: 432, y: 443, labelX: 650, labelY: 402, width: 300, text: 'Слева список шаблонов сообщений для Telegram, VK и web-чата.' },
      { number: 4, x: 847, y: 443, labelX: 1010, labelY: 402, width: 280, text: 'Справа редактируется текст выбранного шаблона.' },
      { number: 5, x: 869, y: 636, labelX: 1010, labelY: 594, width: 280, text: 'Переменные помогают подставлять матч, БК, коэффициент и ссылки.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Настройки');
    },
  },
  {
    file: '11-admin-settings-switches.png',
    annotations: [
      { number: 1, x: 563, y: 207, labelX: 720, labelY: 150, width: 292, text: 'Откройте «Системные рубильники» для глобальных режимов.' },
      { number: 2, x: 502, y: 409, labelX: 716, labelY: 364, width: 300, text: 'Доступ и режимы управляют maintenance, регистрациями, опросом и оплатой.' },
      { number: 3, x: 505, y: 565, labelX: 716, labelY: 520, width: 300, text: 'Экстренная пауза рассылок останавливает массовые исходящие отправки.' },
      { number: 4, x: 507, y: 715, labelX: 716, labelY: 676, width: 300, text: 'Опасные операции вроде сброса сессий используйте только осознанно.' },
      { number: 5, x: 1059, y: 823, labelX: 846, labelY: 790, width: 300, text: 'После изменения флагов нажмите «Сохранить изменения».' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Настройки');
      await clickButtonByText(page, 'Системные рубильники');
    },
  },
  {
    file: '12-admin-settings-integrations.png',
    annotations: [
      { number: 1, x: 699, y: 207, labelX: 820, labelY: 150, width: 292, text: 'Интеграции закрыты отдельным паролем, потому что там есть секретные поля.' },
      { number: 2, x: 811, y: 427, labelX: 930, labelY: 376, width: 300, text: 'Открытие сейфа нужно только администратору с правом менять внешние сервисы.' },
      { number: 3, x: 709, y: 535, labelX: 930, labelY: 500, width: 300, text: 'Введите пароль в интерфейсе, не передавайте его в чат и не сохраняйте в документах.' },
      { number: 4, x: 941, y: 535, labelX: 930, labelY: 592, width: 300, text: 'После открытия доступны поля Telegram, VK, платежей, Web Push, Drive и URL.' },
    ],
    prepare: async (page) => {
      await clickButton(page, 'Настройки');
      await clickButtonByText(page, 'Интеграции');
    },
  },
];

async function run() {
  await mkdir(rawDir, { recursive: true });
  await mkdir(outputDir, { recursive: true });

  const browser = await chromium.launch({
    headless: process.env.ADMIN_TRAINING_HEADLESS === 'false' ? false : true,
  });

  try {
    for (const scenario of scenarios) {
      const { context, page } = await openAdminPage(browser);
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
