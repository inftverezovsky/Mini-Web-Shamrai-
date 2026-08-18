import { DEBUG_AUTH_ENABLED, DEBUG_ROLE_STORAGE_KEY } from '../config/api';
import { getStoredAuthToken } from '../utils/authStorage';
import { MOCK_BOOKMAKERS } from './mockData/bookmakers';
import { DEFAULT_MOCK_MESSAGE_TEMPLATES } from './mockData/messageTemplates';

const nowIso = () => new Date().toISOString();
const daysAgoIso = (days: number, hour = 18) => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  date.setHours(hour, 0, 0, 0);
  return date.toISOString();
};
const MOCK_PREFS_STORAGE_KEY = 'bet_tma_mock_preferences';
const MOCK_MESSAGE_TEMPLATES_STORAGE_KEY = 'bet_tma_mock_message_templates';
const MOCK_SYSTEM_SETTINGS_STORAGE_KEY = 'bet_tma_mock_system_settings';
const MOCK_MARKETING_WIDGETS_STORAGE_KEY = 'bet_tma_mock_marketing_widgets';
const MOCK_SUPPORT_MESSAGES_STORAGE_KEY = 'bet_tma_mock_support_messages';
const MOCK_FLAT_SUBSCRIPTION_STORAGE_KEY = 'bet_tma_mock_flat_subscription';
const MOCK_PAYMENT_ATTEMPTS_STORAGE_KEY = 'bet_tma_mock_payment_attempts';
const MOCK_AVATAR_CYAN = 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 80 80%22%3E%3Crect width=%2280%22 height=%2280%22 rx=%2224%22 fill=%22%23051b2b%22/%3E%3Ccircle cx=%2240%22 cy=%2232%22 r=%2214%22 fill=%22%2380e0f7%22/%3E%3Cpath d=%22M18 72c4-17 15-25 22-25s18 8 22 25%22 fill=%22%2320c997%22/%3E%3C/svg%3E';
const MOCK_AVATAR_GOLD = 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 80 80%22%3E%3Crect width=%2280%22 height=%2280%22 rx=%2224%22 fill=%22%23221805%22/%3E%3Ccircle cx=%2240%22 cy=%2232%22 r=%2214%22 fill=%22%23f59e0b%22/%3E%3Cpath d=%22M18 72c4-17 15-25 22-25s18 8 22 25%22 fill=%22%23facc15%22/%3E%3C/svg%3E';
const MOCK_AVATAR_GREEN = 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 80 80%22%3E%3Crect width=%2280%22 height=%2280%22 rx=%2224%22 fill=%22%23071917%22/%3E%3Ccircle cx=%2240%22 cy=%2232%22 r=%2214%22 fill=%22%2334d399%22/%3E%3Cpath d=%22M18 72c4-17 15-25 22-25s18 8 22 25%22 fill=%22%2380e0f7%22/%3E%3C/svg%3E';

const DEFAULT_MOCK_PREFERENCES = {
  alert_min_coef: 1.5,
  odds_drop_notifications_enabled: true,
  is_night_mode: false,
  night_mode_start: '23:00',
  night_mode_end: '08:00',
  preferred_sports: [],
  stats_display_mode: 'percent',
};

const MOCK_SERVICE_FORMAT_LABELS: Record<string, string> = {
  auto_fast: 'Сигнал сразу',
  logic_review: 'С объяснением',
  vip_support: 'VIP-сопровождение',
  distance_report: 'Отчёт по дистанции',
};

function normalizeMockServiceFormat(value: unknown) {
  return typeof value === 'string' && value in MOCK_SERVICE_FORMAT_LABELS ? value : 'auto_fast';
}

function buildMockVipVerdict(serviceFormat: string, onboardingGoal: unknown) {
  if (serviceFormat === 'vip_support') {
    return {
      title: 'VIP-контур',
      caption: 'Ваш профиль лучше всего раскрывается через личное сопровождение, быстрый контакт и контроль дисциплины.',
    };
  }
  if (serviceFormat === 'logic_review' || onboardingGoal === 'trust_check') {
    return {
      title: 'Проверочный контур',
      caption: 'Клиенту важны логика входа и прозрачный разбор: показывайте доказательства, статистику и причины сигнала.',
    };
  }
  if (serviceFormat === 'distance_report' || onboardingGoal === 'discipline') {
    return {
      title: 'Дистанционный контур',
      caption: 'Фокус на длинной дистанции: флэт, отчётность и спокойная работа без догонов.',
    };
  }
  return {
    title: 'Скоростной контур',
    caption: 'Оптимален быстрый вход по линии: приоритет на уведомления, скорость доставки и короткий маршрут до сигнала.',
  };
}

const MOCK_SECRET_SETTING_KEYS = new Set([
  'VK_ACCESS_TOKEN',
  'TELEGRAM_BOT_TOKEN',
  'PAYMENT_GATEWAY_TOKEN',
  'TELEGRAM_WEBHOOK_SECRET_TOKEN',
  'VK_CALLBACK_CONFIRMATION_CODE',
  'VK_CALLBACK_SECRET',
  'VK_ID_CLIENT_SECRET',
  'YOOKASSA_SECRET_KEY',
  'TEGRO_API_KEY',
  'TEGRO_SECRET_KEY',
  'WEB_PUSH_VAPID_PRIVATE_KEY',
  'GOOGLE_OAUTH_CLIENT_SECRET',
  'GOOGLE_OAUTH_REFRESH_TOKEN',
  'GOOGLE_SERVICE_ACCOUNT_JSON_B64',
  'HTTPS_PROXY',
]);

const DEFAULT_MOCK_SYSTEM_SETTINGS = [
  {
    key: 'MAINTENANCE_MODE',
    value: 'false',
    description: 'Режим обслуживания для временного ограничения пользовательских сценариев.',
  },
  {
    key: 'DISABLE_REGISTRATIONS',
    value: 'false',
    description: 'Закрытый клуб: запрет новых регистраций.',
  },
  {
    key: 'PAUSE_BROADCASTS',
    value: 'false',
    description: 'Экстренная пауза исходящих рассылок.',
  },
  {
    key: 'WELCOME_QUIZ_ENABLED',
    value: 'false',
    description: 'Включает подробный приветственный опрос после шага VK-привязки.',
  },
  {
    key: 'SUBSCRIPTION_PURCHASES_ENABLED',
    value: 'false',
    description: 'Показывает клиентам покупку абонементов и переход к оплате матчей.',
  },
  {
    key: 'REFERRAL_PROGRAM_ENABLED',
    value: 'false',
    description: 'Включает реферальную программу и учет квалифицированных покупок.',
  },
  {
    key: 'REFERRAL_DISCOUNT_ENABLED',
    value: 'true',
    description: 'Включает автоматическую скидку пригласившему за оплаченных рефералов.',
  },
  {
    key: 'REFERRAL_DISCOUNT_STEP_PERCENT',
    value: '5',
    description: 'Процент скидки за одного приглашенного клиента с квалифицированной покупкой.',
  },
  {
    key: 'REFERRAL_DISCOUNT_MAX_PERCENT',
    value: '100',
    description: 'Максимальная автоматическая реферальная скидка на абонемент.',
  },
  {
    key: 'REFERRAL_MATCH_REWARD_ENABLED',
    value: 'false',
    description: 'Начисляет пригласившему матчи после первой квалифицированной покупки реферала.',
  },
  {
    key: 'REFERRAL_MATCH_REWARD_COUNT',
    value: '0',
    description: 'Количество матчей, начисляемых пригласившему за первую покупку реферала.',
  },
  {
    key: 'VK_ACCESS_TOKEN',
    value: '',
    description: 'Токен доступа VK для внешних интеграций.',
  },
  {
    key: 'TELEGRAM_BOT_TOKEN',
    value: '',
    description: 'Токен Telegram-бота для внешних интеграций.',
  },
  {
    key: 'PAYMENT_GATEWAY_TOKEN',
    value: '',
    description: 'Токен платежного шлюза.',
  },
  { key: 'TELEGRAM_WEBHOOK_SECRET_TOKEN', value: '', description: 'Секрет проверки Telegram webhook.' },
  { key: 'TELEGRAM_BOT_USERNAME', value: 'Shamra1_bot', description: 'Username Telegram-бота.' },
  { key: 'TELEGRAM_VIP_CHAT_ID', value: '-100200300400', description: 'ID закрытого Telegram VIP-чата.' },
  { key: 'TELEGRAM_ADMIN_GROUP_CHAT_ID', value: '', description: 'ID Telegram-группы администраторов.' },
  { key: 'SALES_MANAGER_TELEGRAM_ID', value: '', description: 'Telegram ID менеджера продаж.' },
  { key: 'SHAMRAI_ONBOARDING_REPORT_CHAT_ID', value: '', description: 'Telegram chat ID для onboarding-отчетов.' },
  { key: 'VK_CALLBACK_CONFIRMATION_CODE', value: '', description: 'Код подтверждения VK Callback API.' },
  { key: 'VK_CALLBACK_SECRET', value: '', description: 'Секретный ключ VK Callback API.' },
  { key: 'VK_ID_CLIENT_SECRET', value: '', description: 'Client secret для VK ID OAuth.' },
  { key: 'VK_ID_APP_ID', value: '54626979', description: 'App ID для VK ID.' },
  { key: 'VK_ID_REDIRECT_URI', value: 'https://shamra1.pro', description: 'Redirect URI для VK ID.' },
  { key: 'VK_GROUP_ID', value: '239419819', description: 'ID группы VK.' },
  { key: 'VK_API_VERSION', value: '5.199', description: 'Версия VK API.' },
  { key: 'API_BASE_URL', value: 'https://shamra1.pro', description: 'Базовый URL API.' },
  { key: 'FRONTEND_BASE_URL', value: 'https://shamra1.pro/app', description: 'Базовый URL mini app.' },
  { key: 'YOOKASSA_SHOP_ID', value: '', description: 'Shop ID YooKassa.' },
  { key: 'YOOKASSA_SECRET_KEY', value: '', description: 'Секретный ключ YooKassa.' },
  { key: 'YOOKASSA_RETURN_URL', value: 'https://shamra1.pro/app', description: 'Return URL YooKassa.' },
  { key: 'TEGRO_SHOP_ID', value: '', description: 'Shop ID Tegro.' },
  { key: 'TEGRO_API_KEY', value: '', description: 'API key Tegro.' },
  { key: 'TEGRO_SECRET_KEY', value: '', description: 'Secret key Tegro.' },
  { key: 'TEGRO_RETURN_URL', value: 'https://shamra1.pro/app', description: 'Return URL Tegro.' },
  { key: 'TEGRO_API_BASE_URL', value: 'https://tegro.money/api', description: 'Base URL Tegro API.' },
  { key: 'WEB_PUSH_VAPID_PUBLIC_KEY', value: '', description: 'Public VAPID key для Web Push.' },
  { key: 'WEB_PUSH_VAPID_PRIVATE_KEY', value: '', description: 'Private VAPID key для Web Push.' },
  { key: 'WEB_PUSH_VAPID_SUBJECT', value: 'mailto:support@shamra1.pro', description: 'Subject для Web Push.' },
  { key: 'GOOGLE_DRIVE_STATS_ENABLED', value: 'false', description: 'Включение Google Drive stats export.' },
  { key: 'GOOGLE_DRIVE_AUTH_MODE', value: 'auto', description: 'Режим авторизации Google Drive.' },
  { key: 'GOOGLE_DRIVE_STATS_FOLDER_ID', value: '', description: 'Folder ID для выгрузки статистики в Google Drive.' },
  { key: 'GOOGLE_OAUTH_CLIENT_ID', value: '', description: 'Google OAuth client ID.' },
  { key: 'GOOGLE_OAUTH_CLIENT_SECRET', value: '', description: 'Google OAuth client secret.' },
  { key: 'GOOGLE_OAUTH_REFRESH_TOKEN', value: '', description: 'Google OAuth refresh token.' },
  { key: 'GOOGLE_OAUTH_TOKEN_URI', value: 'https://oauth2.googleapis.com/token', description: 'Google OAuth token URI.' },
  { key: 'GOOGLE_SERVICE_ACCOUNT_JSON_B64', value: '', description: 'Service account JSON в base64 для Google Drive.' },
  { key: 'HTTPS_PROXY', value: '', description: 'Proxy URL для интеграций, где он нужен.' },
  {
    key: 'SUPPORT_URL',
    value: '',
    description: 'Публичная ссылка на поддержку.',
  },
  {
    key: 'VIP_CHANNEL_URL',
    value: '',
    description: 'Ссылка на закрытый канал.',
  },
  {
    key: 'THEME_PRIMARY_COLOR',
    value: '#00d2ff',
    description: 'Основной неоновый цвет интерфейса.',
  },
  {
    key: 'THEME_SECONDARY_COLOR',
    value: '#d946ef',
    description: 'Дополнительный неоновый цвет интерфейса.',
  },
  {
    key: 'GLOBAL_PERFORMANCE_MODE',
    value: 'false',
    description: 'Глобальный режим сниженной анимации и эффектов.',
  },
  { key: 'BRAND_LOGO_URL', value: '', description: 'URL логотипа бренда.' },
  { key: 'BRAND_BACKGROUND_URL', value: '', description: 'URL фонового изображения бренда.' },
  { key: 'THEME_GLASS_OPACITY', value: '0.42', description: 'Прозрачность glass-панелей.' },
  { key: 'THEME_GLASS_BLUR_PX', value: '18', description: 'Blur glass-панелей в пикселях.' },
  { key: 'THEME_RADIUS_SCALE', value: '1', description: 'Множитель скруглений интерфейса.' },
  { key: 'THEME_FONT_SCALE', value: '1', description: 'Множитель размера шрифта.' },
  { key: 'THEME_DENSITY', value: 'compact', description: 'Плотность интерфейса.' },
  { key: 'THEME_GLOW_STRENGTH', value: '1', description: 'Интенсивность свечения.' },
];

const DEFAULT_MOCK_MARKETING_WIDGETS = [
  {
    key: 'daily_spin',
    title: 'Daily Spin',
    description: 'Ежедневный бонус: прогноз или персональный промокод.',
    is_enabled: true,
    position: 10,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 24,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'mixed',
    reward_value: 25,
    promo_valid_hours: 24,
    settings_json: { free_bet_weight: 50 },
  },
  {
    key: 'swipe',
    title: 'Swipe',
    description: 'Промокод за совпадение с прогнозом.',
    is_enabled: true,
    position: 20,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 0,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'discount',
    reward_value: 50,
    promo_valid_hours: 24,
    settings_json: {},
  },
  {
    key: 'quiz',
    title: 'Quiz',
    description: 'Аналитический тест с персональным промокодом.',
    is_enabled: true,
    position: 30,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 0,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'discount',
    reward_value: 30,
    promo_valid_hours: 48,
    settings_json: {},
  },
  {
    key: 'pvp',
    title: 'PvP',
    description: 'Голосование Battle of Minds.',
    is_enabled: true,
    position: 40,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 0,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'none',
    reward_value: 0,
    promo_valid_hours: 0,
    settings_json: {},
  },
  {
    key: 'marathon',
    title: 'Marathon',
    description: 'Активный марафон ставок.',
    is_enabled: true,
    position: 50,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 0,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'none',
    reward_value: 0,
    promo_valid_hours: 0,
    settings_json: {},
  },
  {
    key: 'crowd_bet',
    title: 'Crowd Bet',
    description: 'Совместное открытие прогноза.',
    is_enabled: true,
    position: 60,
    audience: 'all',
    starts_at: null,
    ends_at: null,
    cooldown_hours: 0,
    per_user_limit: 0,
    global_daily_limit: 0,
    reward_type: 'none',
    reward_value: 0,
    promo_valid_hours: 0,
    settings_json: {},
  },
];

function getMockPreferences() {
  try {
    const stored = localStorage.getItem(MOCK_PREFS_STORAGE_KEY);
    return {
      ...DEFAULT_MOCK_PREFERENCES,
      ...(stored ? JSON.parse(stored) : {}),
    };
  } catch {
    return DEFAULT_MOCK_PREFERENCES;
  }
}

function saveMockPreferences(preferences: Record<string, any>) {
  const nextPreferences = {
    ...getMockPreferences(),
    ...preferences,
  };
  localStorage.setItem(MOCK_PREFS_STORAGE_KEY, JSON.stringify(nextPreferences));
  return nextPreferences;
}

function readMockSystemSettingsState() {
  try {
    const stored = localStorage.getItem(MOCK_SYSTEM_SETTINGS_STORAGE_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function getMockSystemSettings(options: { revealSecrets?: boolean } = {}) {
  const state = readMockSystemSettingsState();
  return {
    settings: DEFAULT_MOCK_SYSTEM_SETTINGS.map((setting) => {
      const stored = state[setting.key] || {};
      const isSecret = MOCK_SECRET_SETTING_KEYS.has(setting.key);
      const value = isSecret && !options.revealSecrets ? '' : stored.value ?? setting.value;
      return {
        key: setting.key,
        value,
        description: setting.description,
        is_secret: isSecret,
        is_configured: isSecret ? Boolean(stored.is_configured) : Boolean(String(value || '').trim()),
        updated_at: stored.updated_at ?? null,
      };
    }),
  };
}

function saveMockSystemSettings(updates: Array<{ key: string; value: string }>) {
  const state = readMockSystemSettingsState();
  const now = nowIso();
  updates.forEach((item) => {
    const setting = DEFAULT_MOCK_SYSTEM_SETTINGS.find((candidate) => candidate.key === item.key);
    if (!setting) return;
    const isSecret = MOCK_SECRET_SETTING_KEYS.has(setting.key);
    const value = String(item.value ?? '').trim();
    if (isSecret) {
      if (value) {
        state[setting.key] = { value, is_configured: true, updated_at: now };
      }
      return;
    }
    state[setting.key] = {
      value,
      is_configured: Boolean(value),
      updated_at: now,
    };
  });
  localStorage.setItem(MOCK_SYSTEM_SETTINGS_STORAGE_KEY, JSON.stringify(state));
  return getMockSystemSettings();
}

function readMockMarketingWidgetsState() {
  try {
    const stored = localStorage.getItem(MOCK_MARKETING_WIDGETS_STORAGE_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

function getMockMarketingWidgets() {
  const stored = readMockMarketingWidgetsState();
  const widgets = Array.isArray(stored?.widgets) ? stored.widgets : DEFAULT_MOCK_MARKETING_WIDGETS;
  return {
    configured: Boolean(stored?.configured),
    widgets,
  };
}

function saveMockMarketingWidgets(configs: any[]) {
  const now = nowIso();
  const byKey = new Map(DEFAULT_MOCK_MARKETING_WIDGETS.map((widget) => [widget.key, widget]));
  const widgets = configs.map((item) => {
    const base = byKey.get(item.key) || {};
    return {
      ...base,
      ...item,
      updated_at: now,
    };
  });
  const payload = { configured: true, widgets };
  localStorage.setItem(MOCK_MARKETING_WIDGETS_STORAGE_KEY, JSON.stringify(payload));
  return payload;
}

function getMockPublicThemeSettings() {
  const settings = getMockSystemSettings().settings;
  const settingByKey = new Map(settings.map((setting: any) => [setting.key, setting]));
  return {
    primary_color: settingByKey.get('THEME_PRIMARY_COLOR')?.value || '#00d2ff',
    secondary_color: settingByKey.get('THEME_SECONDARY_COLOR')?.value || '#d946ef',
    global_performance_mode: settingByKey.get('GLOBAL_PERFORMANCE_MODE')?.value === 'true',
    welcome_quiz_enabled: settingByKey.get('WELCOME_QUIZ_ENABLED')?.value === 'true',
    subscription_purchases_enabled: settingByKey.get('SUBSCRIPTION_PURCHASES_ENABLED')?.value === 'true',
    brand_logo_url: settingByKey.get('BRAND_LOGO_URL')?.value || '',
    brand_background_url: settingByKey.get('BRAND_BACKGROUND_URL')?.value || '',
    glass_opacity: Number(settingByKey.get('THEME_GLASS_OPACITY')?.value || 0.42),
    glass_blur_px: Number(settingByKey.get('THEME_GLASS_BLUR_PX')?.value || 18),
    radius_scale: Number(settingByKey.get('THEME_RADIUS_SCALE')?.value || 1),
    font_scale: Number(settingByKey.get('THEME_FONT_SCALE')?.value || 1),
    theme_density: settingByKey.get('THEME_DENSITY')?.value || 'compact',
    glow_strength: Number(settingByKey.get('THEME_GLOW_STRENGTH')?.value || 1),
  };
}

function getMockMessageTemplates() {
  let customBodies: Record<string, { body: string; updated_at: string | null }> = {};
  try {
    const stored = localStorage.getItem(MOCK_MESSAGE_TEMPLATES_STORAGE_KEY);
    customBodies = stored ? JSON.parse(stored) : {};
  } catch {
    customBodies = {};
  }

  return DEFAULT_MOCK_MESSAGE_TEMPLATES.map((template) => {
    const custom = customBodies[template.key];
    return {
      ...template,
      body: custom?.body ?? template.body,
      default_body: template.body,
      is_custom: Boolean(custom && custom.body !== template.body),
      updated_by: custom ? 987654321 : null,
      created_at: null,
      updated_at: custom?.updated_at ?? null,
    };
  });
}

function saveMockMessageTemplate(templateKey: string, body: string) {
  const template = DEFAULT_MOCK_MESSAGE_TEMPLATES.find((item) => item.key === templateKey);
  if (!template) throw new Error('Шаблон сообщения не найден');

  let customBodies: Record<string, { body: string; updated_at: string | null }> = {};
  try {
    const stored = localStorage.getItem(MOCK_MESSAGE_TEMPLATES_STORAGE_KEY);
    customBodies = stored ? JSON.parse(stored) : {};
  } catch {
    customBodies = {};
  }
  customBodies[templateKey] = { body, updated_at: nowIso() };
  localStorage.setItem(MOCK_MESSAGE_TEMPLATES_STORAGE_KEY, JSON.stringify(customBodies));
  return getMockMessageTemplates().find((item) => item.key === templateKey);
}

function resetMockMessageTemplate(templateKey: string) {
  const template = DEFAULT_MOCK_MESSAGE_TEMPLATES.find((item) => item.key === templateKey);
  if (!template) throw new Error('Шаблон сообщения не найден');

  let customBodies: Record<string, { body: string; updated_at: string | null }> = {};
  try {
    const stored = localStorage.getItem(MOCK_MESSAGE_TEMPLATES_STORAGE_KEY);
    customBodies = stored ? JSON.parse(stored) : {};
  } catch {
    customBodies = {};
  }
  customBodies[templateKey] = { body: template.body, updated_at: nowIso() };
  localStorage.setItem(MOCK_MESSAGE_TEMPLATES_STORAGE_KEY, JSON.stringify(customBodies));
  return getMockMessageTemplates().find((item) => item.key === templateKey);
}

function readMockBookmakerLinks(body: FormData | null, fallback: any[] = []) {
  const rawValues = body
    ? [...body.getAll('bookmaker_links'), ...body.getAll('bookmaker_links[]')]
    : [];
  if (rawValues.length === 0) return fallback || [];

  const byBookmakerId = new Map<number, { bookmaker_id: number; url: string }>();
  const addCandidate = (candidate: any) => {
    if (!candidate || typeof candidate !== 'object') return;
    const bookmakerId = Number(candidate.bookmaker_id ?? candidate.bookmakerId ?? candidate.id);
    const url = String(candidate.url ?? candidate.link ?? candidate.match_link ?? '').trim();
    if (!Number.isFinite(bookmakerId) || !url) return;
    byBookmakerId.set(bookmakerId, { bookmaker_id: bookmakerId, url });
  };

  rawValues.forEach((rawValue) => {
    const rawText = String(rawValue || '').trim();
    if (!rawText) return;
    try {
      const parsed = JSON.parse(rawText);
      if (Array.isArray(parsed)) {
        parsed.forEach(addCandidate);
      } else if (parsed && typeof parsed === 'object') {
        if ('bookmaker_id' in parsed || 'bookmakerId' in parsed || 'id' in parsed) {
          addCandidate(parsed);
        } else {
          Object.entries(parsed).forEach(([bookmakerId, url]) => addCandidate({ bookmaker_id: bookmakerId, url }));
        }
      }
    } catch {
      // Ignore malformed mock payloads just like empty optional links.
    }
  });

  return Array.from(byBookmakerId.values());
}

function buildMockBet(id: string, overrides: Record<string, any> = {}) {
  const bookmakerIds = overrides.bookmakerIds ?? [1, 4];
  const bookmakers = MOCK_BOOKMAKERS.filter((bookmaker) => bookmakerIds.includes(bookmaker.id));
  const bookmakerLinks = bookmakers.map((bookmaker) => ({
    bookmaker_id: bookmaker.id,
    url: `https://example.com/bookmakers/${bookmaker.code}`,
  }));

  return {
    id,
    event_name: 'Зенит - Спартак',
    coefficient: 1.92,
    bookmaker_id: bookmakers[0]?.id ?? null,
    description: null,
    status: 'pending',
    author_id: 987654321,
    created_at: nowIso(),
    resolved_at: null,
    bookmaker: bookmakers[0] ?? null,
    bookmakers,
    price_stars: null,
    is_unlocked: true,
    is_taken: false,
    guarantee_count: 0,
    supercompensation_count: 0,
    refund_count: 0,
    category: 'prematch',
    live_ends_at: null,
    brain_score: 5,
    api_match_id: null,
    sport_type: 'Футбол',
    outcome: 'П1 с форой 0',
    coupon_image_url: null,
    match_link: 'https://example.com/match',
    bookmaker_links: bookmakerLinks,
    delivery_mode: 'feed',
    auto_send_on_interest: false,
    odds_dropped_to: null,
    odds_drop_notified_at: null,
    ...overrides,
  };
}

function withMockBookmakerLinks(bet: any) {
  if (Array.isArray(bet.bookmaker_links) && bet.bookmaker_links.length > 0) return bet;
  const betBookmakers = Array.isArray(bet.bookmakers) && bet.bookmakers.length
    ? bet.bookmakers
    : MOCK_BOOKMAKERS.filter((bookmaker) => {
        const ids = Array.isArray(bet.bookmakerIds) ? bet.bookmakerIds : [bet.bookmaker_id];
        return ids.includes(bookmaker.id);
      });

  return {
    ...bet,
    bookmaker_links: betBookmakers.map((bookmaker: any) => ({
      bookmaker_id: bookmaker.id,
      url: `https://example.com/bookmakers/${bookmaker.code}`,
    })),
  };
}

function getMockBets() {
  const stored = localStorage.getItem('bet_tma_mock_bets');
  if (stored) {
    try {
      const bets = JSON.parse(stored);
      if (Array.isArray(bets)) return bets.map(withMockBookmakerLinks);
    } catch {
      // Fall back to seeded data.
    }
  }

  const seeded = [
    buildMockBet('mock-bet-1', {
      event_name: 'Зенит - Спартак',
      coefficient: 1.92,
      bookmakerIds: [1, 4],
      sport_type: 'Футбол',
      outcome: 'П1 с форой 0',
    }),
    buildMockBet('mock-bet-2', {
      event_name: 'ЦСКА - Локомотив',
      coefficient: 2.14,
      bookmakerIds: [2, 7],
      sport_type: 'Хоккей',
      outcome: 'ТБ 4.5',
      category: 'live',
      live_ends_at: new Date(Date.now() + 12 * 60 * 1000).toISOString(),
      api_match_id: 'mock-match-1',
    }),
    buildMockBet('mock-bet-history-1', {
      event_name: 'Реал - Атлетико',
      coefficient: 2.12,
      bookmakerIds: [1],
      sport_type: 'Футбол',
      outcome: 'П1',
      status: 'win',
      is_taken: true,
      created_at: daysAgoIso(1, 14),
      resolved_at: daysAgoIso(0, 22),
      delivery_mode: 'feed',
    }),
    buildMockBet('mock-bet-history-2', {
      event_name: 'СКА - Динамо',
      coefficient: 1.86,
      bookmakerIds: [2],
      sport_type: 'Хоккей',
      outcome: 'ТМ 5.5',
      status: 'loss',
      is_taken: true,
      created_at: daysAgoIso(3, 15),
      resolved_at: daysAgoIso(2, 21),
      delivery_mode: 'feed',
    }),
    buildMockBet('mock-bet-history-3', {
      event_name: 'Медведев - Рублев',
      coefficient: 1.74,
      bookmakerIds: [3],
      sport_type: 'Теннис',
      outcome: 'П1',
      status: 'win',
      is_taken: true,
      created_at: daysAgoIso(8, 11),
      resolved_at: daysAgoIso(7, 19),
      delivery_mode: 'feed',
    }),
    buildMockBet('mock-bet-history-4', {
      event_name: 'Барселона - Валенсия',
      coefficient: 2.35,
      bookmakerIds: [4],
      sport_type: 'Баскетбол',
      outcome: 'Ф1 -4.5',
      status: 'refund',
      is_taken: true,
      created_at: daysAgoIso(11, 12),
      resolved_at: daysAgoIso(10, 20),
      delivery_mode: 'feed',
    }),
    buildMockBet('mock-bet-history-5', {
      event_name: 'Интер - Милан',
      coefficient: 2.04,
      bookmakerIds: [7],
      sport_type: 'Футбол',
      outcome: 'Обе забьют',
      status: 'loss',
      is_taken: true,
      created_at: daysAgoIso(34, 16),
      resolved_at: daysAgoIso(33, 23),
      delivery_mode: 'sales_private',
    }),
    buildMockBet('mock-bet-history-6', {
      event_name: 'ПСЖ - Лион',
      coefficient: 1.91,
      bookmakerIds: [1, 2],
      sport_type: 'Футбол',
      outcome: 'ТБ 2.5',
      status: 'win',
      is_taken: true,
      created_at: daysAgoIso(39, 13),
      resolved_at: daysAgoIso(38, 21),
      delivery_mode: 'sales_private',
    }),
  ];
  localStorage.setItem('bet_tma_mock_bets', JSON.stringify(seeded));
  return seeded;
}

function saveMockBets(bets: any[]) {
  localStorage.setItem('bet_tma_mock_bets', JSON.stringify(bets));
}

function buildMockForecastRequest(id: string, overrides: Record<string, any> = {}) {
  const user = getMockUsers().find((item: any) => item.role === 'user') || buildMockUsers()[0];
  const defaultBet = buildMockBet(`mock-private-bet-${id}`, {
    event_name: 'Рубин - Краснодар',
    coefficient: 2.06,
    bookmakerIds: [1, 4],
    sport_type: 'Футбол',
    outcome: 'ТБ 2.5',
    description: 'Линия просела, но модель держит запас по тоталу.',
    delivery_mode: 'sales_private',
  });
  const bet = overrides.bet || defaultBet;

  return {
    id,
    bet_id: bet.id,
    user_id: user.telegram_id,
    status: 'interested',
    delivery_method: null,
    handled_by: null,
    responded_at: nowIso(),
    delivered_at: null,
    balance_before: null,
    balance_after: null,
    no_balance_warning: false,
    created_at: nowIso(),
    updated_at: nowIso(),
    bet,
    user,
    ...overrides,
  };
}

function getMockForecastRequests() {
  const stored = localStorage.getItem('bet_tma_mock_forecast_requests');
  if (stored) {
    try {
      const requests = JSON.parse(stored);
      if (Array.isArray(requests)) {
        return requests.map((request: any) => ({
          ...request,
          bet: request.bet ? withMockBookmakerLinks(request.bet) : request.bet,
        }));
      }
    } catch {
      // Fall back to seeded data.
    }
  }

  const seeded = [
    buildMockForecastRequest('mock-request-1'),
    buildMockForecastRequest('mock-request-paid-set-1', {
      id: 'mock-request-paid-set-1',
      bet_id: 'mock-paid-set-bet-1',
      bet: buildMockBet('mock-paid-set-bet-1', {
        event_name: 'ПЛАТНЫЙ НАБОР',
        coefficient: 3.9,
        bookmakerIds: [1, 4, 2],
        sport_type: 'Футбол',
        outcome: null,
        description: 'Реальный КФ не выше 1.9!\n(Вышлю первым 5-ти написавшим)',
        delivery_mode: 'paid_set',
        price_stars: 1500,
      }),
    }),
    buildMockForecastRequest('mock-request-2', {
      id: 'mock-request-2',
      status: 'manual_sent',
      delivery_method: 'manual',
      delivered_at: nowIso(),
      balance_before: 0,
      balance_after: -1,
      no_balance_warning: true,
    }),
    buildMockForecastRequest('mock-request-3', {
      id: 'mock-request-3',
      status: 'declined',
      responded_at: nowIso(),
    }),
  ];
  localStorage.setItem('bet_tma_mock_forecast_requests', JSON.stringify(seeded));
  return seeded;
}

function saveMockForecastRequests(requests: any[]) {
  localStorage.setItem('bet_tma_mock_forecast_requests', JSON.stringify(requests));
}

function buildMockRecentResults(statuses: Array<'win' | 'loss'>) {
  return statuses.map((status, index) => ({
    bet_id: `mock-history-${status}-${index}`,
    status,
    taken_at: new Date(Date.now() - index * 24 * 60 * 60 * 1000).toISOString(),
  }));
}

function getMockPrivateForecastBets() {
  const betsById = new Map<string, any>();
  getMockForecastRequests().forEach((request: any) => {
    if (request.bet?.id) {
      betsById.set(request.bet.id, request.bet);
    }
  });
  return Array.from(betsById.values());
}

function getMockActivatedForecastBets() {
  return getMockForecastRequests()
    .filter((request: any) => request.status === 'sent' || request.status === 'manual_sent')
    .map((request: any) => ({
      ...request.bet,
      is_taken: true,
      is_unlocked: true,
    }));
}

function getMockTakenBets() {
  const betsById = new Map<string, any>();
  getMockBets()
    .filter((bet: any) => bet.is_taken || bet.status !== 'pending')
    .forEach((bet: any) => betsById.set(bet.id, bet));
  getMockActivatedForecastBets().forEach((bet: any) => betsById.set(bet.id, bet));
  return Array.from(betsById.values()).sort((a: any, b: any) => (
    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  ));
}

const MONTH_LABELS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
];

type PeriodFilter = 'week' | 'month' | 'quarter' | 'all';
type StatsPeriodFilter = PeriodFilter | `${number}-${number}`;

const PERIOD_LABELS: Record<PeriodFilter, string> = {
  week: 'Текущая неделя',
  month: 'Текущий месяц',
  quarter: 'Текущий квартал',
  all: 'Весь период',
};

function normalizeMockPeriod(period: string | null | undefined): StatsPeriodFilter {
  const cleanPeriod = String(period || 'all');
  if (/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(cleanPeriod)) return cleanPeriod as `${number}-${number}`;
  return ['week', 'month', 'quarter', 'all'].includes(cleanPeriod)
    ? (cleanPeriod as PeriodFilter)
    : 'all';
}

function mockPeriodStart(period: StatsPeriodFilter) {
  if (period === 'all') return null;
  if (/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(period)) {
    const [year, month] = period.split('-').map(Number);
    return new Date(year, month - 1, 1);
  }
  const now = new Date();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);

  if (period === 'week') {
    const mondayOffset = (start.getDay() + 6) % 7;
    start.setDate(start.getDate() - mondayOffset);
    return start;
  }
  if (period === 'month') {
    start.setDate(1);
    return start;
  }

  start.setMonth(Math.floor(start.getMonth() / 3) * 3, 1);
  return start;
}

function mockPeriodEnd(period: StatsPeriodFilter) {
  if (!/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(period)) return null;
  const [year, month] = period.split('-').map(Number);
  return new Date(year, month, 1);
}

function mockPeriodLabel(period: StatsPeriodFilter) {
  if (period in PERIOD_LABELS) return PERIOD_LABELS[period as PeriodFilter];
  const [year, month] = period.split('-').map(Number);
  return `${MONTH_LABELS[month - 1]} ${year}`;
}

function filterMockItemsByPeriod(items: any[], period: StatsPeriodFilter) {
  const start = mockPeriodStart(period);
  const end = mockPeriodEnd(period);
  if (!start) return items;
  return items.filter((item) => {
    const resolvedAt = new Date(item.resolved_at).getTime();
    return resolvedAt >= start.getTime() && (!end || resolvedAt < end.getTime());
  });
}

function mockProfitUnits(bet: any) {
  if (bet.status === 'win') return Number(bet.coefficient || 1) - 1;
  if (bet.status === 'loss') return -1;
  return 0;
}

function mockStatItemFromBet(bet: any) {
  if (!['win', 'loss'].includes(bet.status) || !bet.resolved_at) return null;
  const bookmakers = (bet.bookmakers?.length ? bet.bookmakers : bet.bookmaker ? [bet.bookmaker] : [])
    .filter(Boolean)
    .map((bookmaker: any) => ({ id: bookmaker.id, name: bookmaker.name, code: bookmaker.code }));
  return {
    id: bet.id,
    event_name: bet.event_name,
    status: bet.status,
    coefficient: Number(bet.coefficient || 0),
    profit_units: Number(mockProfitUnits(bet).toFixed(2)),
    resolved_at: bet.resolved_at,
    created_at: bet.created_at,
    taken_at: bet.taken_at || bet.created_at,
    delivery_mode: bet.delivery_mode || 'feed',
    source_type: bet.delivery_mode === 'paid_set' ? 'paid_set' : bet.delivery_mode && bet.delivery_mode !== 'feed' ? 'private' : 'feed',
    sport_type: bet.sport_type || null,
    outcome: bet.outcome || null,
    bookmakers,
    bookmaker_names: bookmakers.map((bookmaker: any) => bookmaker.name),
    access_type: bet.access_type || 'paid_match',
    match_charged: bet.match_charged ?? true,
  };
}

function mockSummary(items: any[]) {
  const bets = items.length;
  const wins = items.filter((item) => item.status === 'win').length;
  const losses = items.filter((item) => item.status === 'loss').length;
  const profit = items.reduce((sum, item) => sum + Number(item.profit_units || 0), 0);
  const coefficientSum = items.reduce((sum, item) => sum + Number(item.coefficient || 0), 0);
  let maxWinStreak = 0;
  let maxLossStreak = 0;
  let currentStreak = 0;
  let currentStreakType: 'win' | 'loss' | null = null;
  let lastType: 'win' | 'loss' | null = null;
  let streak = 0;
  [...items].sort((a, b) => new Date(a.resolved_at).getTime() - new Date(b.resolved_at).getTime()).forEach((item) => {
    const type = item.status as 'win' | 'loss';
    streak = type === lastType ? streak + 1 : 1;
    lastType = type;
    if (type === 'win') maxWinStreak = Math.max(maxWinStreak, streak);
    if (type === 'loss') maxLossStreak = Math.max(maxLossStreak, streak);
    currentStreak = streak;
    currentStreakType = type;
  });
  return {
    bets,
    wins,
    losses,
    winrate: bets > 0 ? Number(((wins / bets) * 100).toFixed(2)) : 0,
    roi: bets > 0 ? Number(((profit / bets) * 100).toFixed(2)) : 0,
    profit_units: Number(profit.toFixed(2)),
    average_coefficient: bets > 0 ? Number((coefficientSum / bets).toFixed(2)) : 0,
    max_win_streak: maxWinStreak,
    max_loss_streak: maxLossStreak,
    current_streak: currentStreak,
    current_streak_type: currentStreakType,
  };
}

function mockBreakdown(items: any[], key: 'sport_type' | 'bookmaker_names', emptyLabel: string) {
  const groups = new Map<string, { label: string; items: any[] }>();
  items.forEach((item) => {
    const labels = key === 'bookmaker_names'
      ? (item.bookmaker_names?.length ? item.bookmaker_names : [emptyLabel])
      : [item[key] || emptyLabel];
    labels.forEach((label: string) => {
      const groupKey = String(label).toLowerCase();
      if (!groups.has(groupKey)) groups.set(groupKey, { label, items: [] });
      groups.get(groupKey)!.items.push(item);
    });
  });
  return Array.from(groups.entries())
    .map(([keyValue, group]) => ({ key: keyValue, label: group.label, summary: mockSummary(group.items) }))
    .sort((a, b) => b.summary.bets - a.summary.bets || a.label.localeCompare(b.label));
}

function buildMockPerformancePayload(rawItems: any[], period: StatsPeriodFilter = 'all') {
  const items = rawItems
    .map(mockStatItemFromBet)
    .filter(Boolean)
    .filter((item: any) => filterMockItemsByPeriod([item], period).length > 0)
    .sort((a: any, b: any) => new Date(b.resolved_at).getTime() - new Date(a.resolved_at).getTime());
  const monthMap = new Map<string, Map<string, any[]>>();
  items.forEach((item: any) => {
    const date = new Date(item.resolved_at);
    const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
    const dayKey = `${monthKey}-${String(date.getDate()).padStart(2, '0')}`;
    if (!monthMap.has(monthKey)) monthMap.set(monthKey, new Map());
    const dayMap = monthMap.get(monthKey)!;
    if (!dayMap.has(dayKey)) dayMap.set(dayKey, []);
    dayMap.get(dayKey)!.push(item);
  });
  const timeline = Array.from(monthMap.entries())
    .sort(([left], [right]) => right.localeCompare(left))
    .map(([monthKey, days]) => {
      const [year, month] = monthKey.split('-').map(Number);
      const monthItems = Array.from(days.values()).flat();
      return {
        key: monthKey,
        label: `${MONTH_LABELS[month - 1]} ${year}`,
        summary: mockSummary(monthItems),
        days: Array.from(days.entries())
          .sort(([left], [right]) => right.localeCompare(left))
          .map(([dayKey, dayItems]) => ({
            key: dayKey,
            label: new Date(dayItems[0].resolved_at).toLocaleDateString('ru-RU'),
            summary: mockSummary(dayItems),
            bets: dayItems,
          })),
      };
    });
  const now = new Date();
  const monthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const dayKey = `${monthKey}-${String(now.getDate()).padStart(2, '0')}`;
  const feedItems = items.filter((item: any) => item.source_type === 'feed');
  const privateItems = items.filter((item: any) => item.source_type === 'private');
  const paidSetItems = items.filter((item: any) => item.source_type === 'paid_set');
  return {
    period,
    period_label: mockPeriodLabel(period),
    summary: mockSummary(items),
    source_split: {
      all: mockSummary(items),
      feed: mockSummary(feedItems),
      private: mockSummary(privateItems),
      paid_set: mockSummary(paidSetItems),
    },
    timeline,
    bookmaker_breakdown: mockBreakdown(items, 'bookmaker_names', 'Без БК'),
    sport_breakdown: mockBreakdown(items, 'sport_type', 'Без спорта'),
    default_expanded_month_key: monthKey,
    default_expanded_day_key: dayKey,
  };
}

function mockClientSituation(summary: any) {
  if (!summary.bets) return { code: 'no_data', label: 'Нет расчетов', tone: 'neutral', description: 'Пока нет расчетов.' };
  if (summary.current_streak_type === 'loss' && summary.current_streak >= 3) return { code: 'attention', label: 'Нужно внимание', tone: 'danger', description: 'Идет серия минусов.' };
  if (summary.roi > 15) return { code: 'strong_plus', label: 'Хороший плюс', tone: 'success', description: 'Клиент уверенно в плюсе.' };
  if (summary.roi >= 0) return { code: 'plus', label: 'В плюсе', tone: 'success', description: 'Положительная динамика.' };
  if (summary.roi > -15) return { code: 'drawdown', label: 'Рабочая просадка', tone: 'warning', description: 'Небольшой минус.' };
  return { code: 'deep_drawdown', label: 'Глубокая просадка', tone: 'danger', description: 'ROI ниже -15%.' };
}

function buildMockUsers() {
  const baseUser = getMockUser();
  return [
    {
      ...baseUser,
      telegram_id: 123456789,
      vk_user_id: 'vk-123456789',
      vk_messages_allowed: true,
      web_push_enabled: false,
      username: 'debug_user',
      first_name: 'Иван',
      last_name: 'Подписчик',
      photo_url: MOCK_AVATAR_CYAN,
      vk_photo_url: null,
      role: 'user',
      matches_remaining: 12,
      guarantee_active: false,
      has_active_subscription: true,
      flat_subscription: buildMockFlatSubscription({
        user_id: 123456789,
        status: 'active',
        flat_amount_rub: '10000.00',
        activated_at: nowIso(),
        pending_bets: 1,
        bets: [{
          bet_id: 'mock-bet-1',
          event_name: 'Зенит — Спартак',
          outcome: 'П1',
          taken_at: nowIso(),
          stake_rub: '5000.00',
          stake_flats: '0.500000',
          coefficient: '1.90',
          status: 'pending',
          profit_rub: null,
          profit_flats: null,
        }],
      }),
      subscription_end_date: new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString(),
      bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => [1, 2, 4, 7].includes(bookmaker.id)),
      other_bookmaker_name: null,
      client_group: 'VIP',
      client_tag: 'топ',
      recent_match_results: buildMockRecentResults(['win', 'win', 'win', 'loss', 'win', 'loss', 'loss', 'win', 'win', 'loss']),
    },
    {
      ...baseUser,
      telegram_id: 223344556,
      vk_user_id: null,
      vk_messages_allowed: false,
      web_push_enabled: false,
      username: 'Gold_ForzaJuve',
      first_name: 'Тимур',
      last_name: 'Голдобин',
      photo_url: null,
      vk_photo_url: MOCK_AVATAR_GOLD,
      role: 'user',
      purchased_bets_balance: -3,
      matches_remaining: -3,
      guarantee_active: false,
      has_active_subscription: false,
      flat_subscription: buildMockFlatSubscription({ user_id: 223344556, status: 'pending_setup', flat_amount_rub: null }),
      subscription_end_date: null,
      bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => [1].includes(bookmaker.id)),
      other_bookmaker_name: null,
      client_group: null,
      client_tag: null,
      recent_match_results: buildMockRecentResults(['win', 'win', 'win', 'loss', 'win']),
    },
    {
      ...baseUser,
      telegram_id: 223344557,
      vk_user_id: 'vk-223344557',
      vk_messages_allowed: false,
      web_push_enabled: false,
      username: 'new_client',
      first_name: 'Мария',
      last_name: 'Новикова',
      photo_url: null,
      vk_photo_url: null,
      role: 'user',
      matches_remaining: 0,
      guarantee_active: false,
      has_active_subscription: false,
      flat_subscription: buildMockFlatSubscription({ user_id: 223344557, status: 'closing', flat_amount_rub: '5000.00', pending_bets: 1, profit_rub: '15000.00', profit_flats: '3.000000', remaining_flats: '0.000000' }),
      subscription_end_date: null,
      bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => [3, 12].includes(bookmaker.id)),
      other_bookmaker_name: 'Локальная БК',
      client_group: 'Новые',
      client_tag: 'сомневается',
      recent_match_results: buildMockRecentResults(['loss', 'loss', 'win', 'loss']),
    },
    {
      ...baseUser,
      telegram_id: 334455667,
      vk_user_id: 'vk-334455667',
      vk_messages_allowed: true,
      web_push_enabled: true,
      username: 'guarantee_client',
      first_name: 'Олег',
      last_name: 'Гарантия',
      photo_url: null,
      vk_photo_url: MOCK_AVATAR_GREEN,
      role: 'user',
      matches_remaining: 0,
      guarantee_active: true,
      has_active_subscription: true,
      flat_subscription: buildMockFlatSubscription({ user_id: 334455667, status: 'completed', flat_amount_rub: '15000.00', profit_rub: '45000.00', profit_flats: '3.000000', remaining_flats: '0.000000', completed_at: nowIso() }),
      subscription_end_date: null,
      bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => [1, 6].includes(bookmaker.id)),
      other_bookmaker_name: null,
      client_group: 'Гарантия',
      client_tag: 'важный',
      recent_match_results: buildMockRecentResults(['loss', 'win', 'win', 'loss', 'win', 'win', 'win']),
    },
    {
      ...baseUser,
      telegram_id: 987654321,
      username: 'debug_admin',
      first_name: 'Алексей',
      last_name: 'Админ',
      photo_url: null,
      vk_photo_url: null,
      role: 'admin',
      has_active_subscription: false,
      subscription_end_date: null,
      bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => [1, 4].includes(bookmaker.id)),
    },
  ].map((user) => withMockIdentityFields(user));
}

function getMockUsers() {
  const stored = localStorage.getItem('bet_tma_mock_admin_users');
  if (stored) {
    try {
      const users = JSON.parse(stored);
      if (
        Array.isArray(users) &&
        users.some((user: any) => user.role === 'user') &&
        users.every((user: any) => user.role !== 'user' || (
          'client_group' in user
          && 'client_tag' in user
          && 'recent_match_results' in user
          && 'photo_url' in user
          && 'vk_photo_url' in user
          && 'telegram_connected' in user
          && 'vk_connected' in user
          && 'web_push_enabled' in user
          && 'flat_subscription' in user
        ))
      ) {
        return users;
      }
    } catch {
      // Fall back to seeded users.
    }
  }

  const seeded = buildMockUsers();
  localStorage.setItem('bet_tma_mock_admin_users', JSON.stringify(seeded));
  return seeded;
}

function saveMockUsers(users: any[]) {
  localStorage.setItem('bet_tma_mock_admin_users', JSON.stringify(users));
}

function mockSupportDisplayName(user: any) {
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  if (fullName) return fullName;
  if (user.username) return `@${user.username}`;
  return user.is_web_only ? 'Web/VK клиент' : `ID ${user.telegram_id}`;
}

function buildMockSupportMessage(
  userId: number,
  text: string,
  direction: 'staff' | 'client',
  overrides: Record<string, any> = {},
) {
  const user = getMockUsers().find((item: any) => item.telegram_id === userId);
  const type = direction === 'staff' ? 'support_staff_message' : 'support_client_message';
  return {
    id: Date.now() + Math.floor(Math.random() * 1000),
    user_id: userId,
    text,
    type,
    direction,
    author_label: direction === 'staff' ? 'Shamrai' : mockSupportDisplayName(user || { telegram_id: userId }),
    sender_user_id: direction === 'staff' ? 987654321 : userId,
    sender_role: direction === 'staff' ? 'admin' : 'user',
    data: {
      direction,
      author_label: direction === 'staff' ? 'Shamrai' : mockSupportDisplayName(user || { telegram_id: userId }),
      sender_user_id: direction === 'staff' ? 987654321 : userId,
      sender_role: direction === 'staff' ? 'admin' : 'user',
      event_type: 'support_web_chat',
    },
    created_at: nowIso(),
    ...overrides,
  };
}

function mockChatUserPayload(user: any) {
  return {
    telegram_id: user.telegram_id,
    username: user.username ?? null,
    first_name: user.first_name ?? null,
    last_name: user.last_name ?? null,
    photo_url: user.photo_url ?? null,
    is_web_only: Boolean(user.is_web_only),
    role: user.role || 'user',
    display_name: mockSupportDisplayName(user),
  };
}

function mockConversationId(userId: number) {
  const suffix = Math.abs(userId).toString().padStart(12, '0').slice(-12);
  return `00000000-0000-4000-8000-${suffix}`;
}

function mockSupportMessageToChatMessage(message: any) {
  const messageType = message.message_type || message.chat_type || 'text';
  const payload = {
    ...(message.payload || {}),
    ...(messageType !== 'text' && message.id ? { download_url: `/chat/attachments/${message.id}/download` } : {}),
  };
  return {
    id: message.id,
    conversation_id: mockConversationId(message.user_id),
    sender_user_id: message.sender_user_id ?? (message.direction === 'client' ? message.user_id : 987654321),
    sender_role: message.sender_role ?? (message.direction === 'client' ? 'user' : 'admin'),
    direction: message.direction,
    author_label: message.direction === 'staff' ? 'Shamrai' : message.author_label || 'Клиент',
    type: messageType,
    text: message.text,
    payload,
    client_message_id: message.client_message_id || `mock-${message.id}`,
    reply_to_id: message.reply_to_id ?? null,
    reply_to: message.reply_to ?? null,
    created_at: message.created_at,
    edited_at: null,
    deleted_at: null,
  };
}

function mockConversationFromUser(user: any, latestMessage: any | null, status = 'open') {
  const latestChatMessage = latestMessage ? mockSupportMessageToChatMessage(latestMessage) : null;
  return {
    id: mockConversationId(user.telegram_id),
    kind: 'support',
    key: 'support',
    status,
    owner_user: mockChatUserPayload(user),
    assigned_staff_id: null,
    last_message: latestChatMessage,
    last_message_text: latestMessage?.text ?? null,
    last_message_at: latestMessage?.created_at ?? null,
    unread_count: latestMessage?.direction === 'client' ? 1 : 0,
  };
}

function mockSignalConversation(user: any) {
  return {
    id: 'signals',
    kind: 'signals',
    key: 'signals',
    status: 'open',
    owner_user: mockChatUserPayload(user),
    assigned_staff_id: null,
    last_message: null,
    last_message_text: 'Сигналы и прогнозы',
    last_message_at: null,
    unread_count: 0,
  };
}

function getMockSupportMessages() {
  const stored = localStorage.getItem(MOCK_SUPPORT_MESSAGES_STORAGE_KEY);
  if (stored) {
    try {
      const messages = JSON.parse(stored);
      if (Array.isArray(messages)) return messages;
    } catch {
      // Fall back to seeded support messages.
    }
  }

  const seeded = [
    buildMockSupportMessage(123456789, 'Добрый день, хочу уточнить по закрытому прогнозу.', 'client', {
      id: 9001,
      created_at: new Date(Date.now() - 34 * 60 * 1000).toISOString(),
    }),
    buildMockSupportMessage(123456789, 'Здравствуйте. Да, сейчас проверим линию и подскажем.', 'staff', {
      id: 9002,
      created_at: new Date(Date.now() - 31 * 60 * 1000).toISOString(),
    }),
    buildMockSupportMessage(223344557, 'Я новичок, можно начать с одного матча?', 'client', {
      id: 9003,
      created_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
    }),
  ];
  localStorage.setItem(MOCK_SUPPORT_MESSAGES_STORAGE_KEY, JSON.stringify(seeded));
  return seeded;
}

function saveMockSupportMessages(messages: any[]) {
  localStorage.setItem(MOCK_SUPPORT_MESSAGES_STORAGE_KEY, JSON.stringify(messages));
}

function mockThreadFromUser(user: any, latestMessage: any | null) {
  return {
    user: {
      telegram_id: user.telegram_id,
      username: user.username ?? null,
      first_name: user.first_name ?? null,
      last_name: user.last_name ?? null,
      photo_url: user.photo_url ?? null,
      is_web_only: Boolean(user.is_web_only),
      role: user.role || 'user',
      display_name: mockSupportDisplayName(user),
    },
    last_message: latestMessage,
    last_message_text: latestMessage?.text ?? null,
    last_message_created_at: latestMessage?.created_at ?? null,
    needs_reply: latestMessage?.direction === 'client',
  };
}

let MOCK_PLANS = [
  { id: 1, name: 'Старт +3', duration_days: 0, match_count: 0, entitlement_type: 'flat', target_flats: 3, price: 990, price_stars: 0, currency: 'RUB', is_active: true },
  { id: 2, name: 'Профи +5', duration_days: 0, match_count: 0, entitlement_type: 'flat', target_flats: 5, price: 3990, price_stars: 0, currency: 'RUB', is_active: true },
  { id: 3, name: 'VIP +10', duration_days: 0, match_count: 0, entitlement_type: 'flat', target_flats: 10, price: 7990, price_stars: 0, currency: 'RUB', is_active: true },
];

function readMockPaymentAttempts() {
  try {
    const stored = localStorage.getItem(MOCK_PAYMENT_ATTEMPTS_STORAGE_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function saveMockPaymentAttempts(attempts: Record<string, any>) {
  localStorage.setItem(MOCK_PAYMENT_ATTEMPTS_STORAGE_KEY, JSON.stringify(attempts));
}

function buildMockFlatSubscription(overrides: Record<string, any> = {}) {
  const now = nowIso();
  return {
    id: '11111111-2222-4333-8444-555555555555',
    user_id: 123456789,
    revision: 1,
    status: 'pending_setup',
    flat_amount_rub: null,
    target_flats: '3.00',
    profit_rub: '0.00',
    profit_flats: '0.000000',
    remaining_flats: '3.000000',
    pending_bets: 0,
    activated_at: null,
    completed_at: null,
    created_at: now,
    updated_at: now,
    bets: [],
    credits: [{
      id: 1,
      event_type: 'purchase',
      delta_target_flats: '3.00',
      actor_id: null,
      note: 'Тестовая покупка',
      created_at: now,
    }],
    ...overrides,
  };
}

function getMockFlatSubscription() {
  try {
    const stored = localStorage.getItem(MOCK_FLAT_SUBSCRIPTION_STORAGE_KEY);
    if (stored) return JSON.parse(stored);
  } catch {
    // Rebuild synthetic state below.
  }

  const hasSuccessfulPayment = Object.values(readMockPaymentAttempts()).some((attempt: any) => (
    ['paid', 'success', 'succeeded', 'completed'].includes(String(attempt?.status || '').toLowerCase())
  ));
  return hasSuccessfulPayment ? buildMockFlatSubscription() : null;
}

function saveMockFlatSubscription(subscription: any) {
  localStorage.setItem(MOCK_FLAT_SUBSCRIPTION_STORAGE_KEY, JSON.stringify(subscription));
  return subscription;
}

function createMockPaymentAttempt(provider: 'tegro' | 'yookassa') {
  const attemptId = crypto.randomUUID();
  const attempts = readMockPaymentAttempts();
  attempts[attemptId] = {
    attempt_id: attemptId,
    checkout_state: 'ready',
    status: 'pending',
    requires_flat_setup: false,
    provider,
  };
  saveMockPaymentAttempts(attempts);
  return attemptId;
}

function getMockBookmakerIds() {
  const stored = localStorage.getItem('bet_tma_mock_bookmaker_ids');
  if (!stored) return [1, 2, 4, 7];

  try {
    const ids = JSON.parse(stored);
    return Array.isArray(ids) ? ids.filter((id) => Number.isFinite(id)) : [1, 2, 4, 7];
  } catch {
    return [1, 2, 4, 7];
  }
}

function withMockIdentityFields<T extends { telegram_id: number; vk_user_id: string | null }>(user: T) {
  const identityProviders = [
    ...(user.telegram_id > 0 ? ['telegram'] : []),
    ...(user.vk_user_id ? ['vk'] : []),
  ];
  const missingIdentityProviders = ['telegram', 'vk'].filter((provider) => !identityProviders.includes(provider));
  const webPushEnabled = Boolean((user as any).web_push_enabled);
  return {
    ...user,
    photo_url: (user as any).photo_url ?? null,
    vk_photo_url: (user as any).vk_photo_url ?? null,
    identity_complete: missingIdentityProviders.length === 0,
    identity_providers: identityProviders,
    missing_identity_providers: missingIdentityProviders,
    telegram_connected: user.telegram_id > 0,
    telegram_delivery_enabled: user.telegram_id > 0 && Boolean((user as any).tg_chat_joined),
    vk_connected: Boolean(user.vk_user_id),
    vk_delivery_enabled: Boolean(user.vk_user_id) && Boolean((user as any).vk_messages_allowed),
    web_push_enabled: webPushEnabled,
  };
}

function getMockUser() {
  const mockRole = localStorage.getItem(DEBUG_ROLE_STORAGE_KEY) || 'user';
  const isAdmin = mockRole === 'admin';
  const now = new Date().toISOString();
  const bookmakerIds = getMockBookmakerIds();
  const onboardedOverride = localStorage.getItem('bet_tma_mock_is_onboarded');
  const isOnboarded = onboardedOverride === null ? false : onboardedOverride === 'true';
  const preferences = getMockPreferences();

  return withMockIdentityFields({
    telegram_id: isAdmin ? 987654321 : 123456789,
    username: isAdmin ? 'debug_admin' : 'debug_user',
    first_name: isAdmin ? 'Алексей' : 'Иван',
    last_name: isAdmin ? 'Админ' : 'Подписчик',
    phone: null,
    photo_url: null,
    vk_photo_url: null,
    is_web_only: false,
    role: isAdmin ? 'admin' : 'user',
    stats_display_mode: 'percent',
    bankroll: 50000,
    is_onboarded: isOnboarded,
    experience_level: 'amateur',
    bankroll_size: 'mid',
    favorite_sports: [],
    risk_tolerance: 'balanced',
    primary_bookmaker: 'fonbet',
    vk_user_id: localStorage.getItem('bet_tma_mock_vk_user_id'),
    vk_group_member: localStorage.getItem('bet_tma_mock_vk_group_member') === 'true',
    vk_messages_allowed: localStorage.getItem('bet_tma_mock_vk_messages_allowed') === 'true',
    vk_notifications_allowed: localStorage.getItem('bet_tma_mock_vk_notifications_allowed') === 'true',
    web_push_enabled: localStorage.getItem('bet_tma_mock_web_push_enabled') === 'true',
    currency_preference: 'RUB',
    purchased_bets_balance: 12,
    free_bets_available: 0,
    matches_remaining: 12,
    guarantee_active: false,
    guarantee_opened_from_bet_id: null,
    guarantee_closed_at: null,
    flat_subscription: getMockFlatSubscription(),
    onboarding_goal: 'profit',
    ab_group: null,
    tg_chat_joined: true,
    has_used_shield: false,
    alert_min_coef: preferences.alert_min_coef,
    odds_drop_notifications_enabled: preferences.odds_drop_notifications_enabled,
    is_night_mode: preferences.is_night_mode,
    night_mode_start: preferences.night_mode_start,
    night_mode_end: preferences.night_mode_end,
    preferred_sports: preferences.preferred_sports,
    other_bookmaker_name: localStorage.getItem('bet_tma_mock_other_bookmaker_name'),
    client_group: null,
    client_tag: null,
    created_at: now,
    updated_at: now,
    bookmakers: MOCK_BOOKMAKERS.filter((bookmaker) => bookmakerIds.includes(bookmaker.id)),
    badges: [],
  });
}

function getMockProfileDashboard() {
  const user = getMockUser();
  const preferences = getMockPreferences();
  const selectedBookmakerIds = user.bookmakers.map((bookmaker: any) => bookmaker.id);
  const groupId = Number(import.meta.env.VITE_VK_GROUP_ID || 0) || null;

  return {
    user,
    bookmakers: MOCK_BOOKMAKERS,
    selected_bookmaker_ids: selectedBookmakerIds,
    preferences: {
      ...preferences,
      stats_display_mode: user.stats_display_mode,
    },
    subscription: {
      id: 'mock-active-subscription',
      user_id: user.telegram_id,
      plan_id: 1,
      status: 'active',
      payment_provider: 'mock',
      payment_id: null,
      start_date: daysAgoIso(6, 10),
      end_date: daysAgoIso(-24, 23),
      created_at: daysAgoIso(6, 10),
      plan: {
        id: 1,
        name: 'Демо абонемент',
        duration_days: 30,
        match_count: user.matches_remaining,
        price: 9900,
        price_stars: 0,
        currency: 'RUB',
        is_active: true,
      },
    },
    payments: {
      transactions: [
        {
          id: 'mock-payment-1',
          plan_name: 'Демо абонемент',
          amount: 9900,
          amount_currency: 'RUB',
          amount_stars: null,
          payment_provider: 'mock',
          status: 'paid',
          created_at: daysAgoIso(6, 10),
          start_date: daysAgoIso(6, 10),
          end_date: daysAgoIso(-24, 23),
        },
      ],
      total: 1,
    },
    referral: {
      referral_code: 'DEBUG2026',
      referral_link: 'https://t.me/debug?start=DEBUG2026',
      invited_count: 0,
      purchased_invited_count: 0,
      program_enabled: false,
      discount_enabled: true,
      discount_step_percent: 5,
      discount_max_percent: 100,
      referral_discount_percent: 0,
      match_reward_enabled: false,
      match_reward_count: 0,
    },
    vk_delivery_status: {
      vk_user_id: user.vk_user_id,
      group_id: groupId,
      configured: Boolean(groupId),
      group_member: user.vk_group_member,
      messages_allowed: user.vk_messages_allowed,
      notifications_allowed: user.vk_notifications_allowed,
    },
  };
}

export function mockApiFetch(endpoint: string, options: RequestInit) {
  const requestUrl = new URL(endpoint, 'http://mock.local');
  const queryParams = requestUrl.searchParams;
  endpoint = requestUrl.pathname;

  if (DEBUG_AUTH_ENABLED && endpoint === '/auth/vk/login') {
    const vkUserId = 'vk_mock_741852963';
    localStorage.setItem('bet_tma_mock_vk_user_id', vkUserId);
    return {
      access_token: 'mock_debug_access_token',
      token_type: 'bearer',
      user: withMockIdentityFields({
        ...getMockUser(),
        telegram_id: -1000741852963,
        username: null,
        first_name: 'VK',
        last_name: 'Client',
        is_web_only: true,
        role: 'user',
        tg_chat_joined: false,
        vk_user_id: vkUserId,
      }),
    };
  }
  if (endpoint === '/marketing/wheel-of-fortune') {
    return {
      message: 'Скидка 70% на абонемент',
      promo_code: 'MOCK-CODE-70',
      reward_type: 'discount_70',
      discount_percent: 70
    };
  }

  if (endpoint.match(/^\/admin\/users\/\d+\/bonuses$/)) {
    return [
      {
        id: 9991,
        code: 'MOCK-CODE-70',
        user_id: 1,
        status: 'active',
        created_at: new Date().toISOString(),
        valid_until: new Date(Date.now() + 7 * 86400000).toISOString(),
        reward_type: 'discount_70',
        discount_percent: 70,
        is_used: false,
      }
    ];
  }


  if (endpoint === '/users/me/vk-delivery-status') {
    if (options.method === 'PUT') {
      const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
      if (typeof body.group_member === 'boolean') {
        localStorage.setItem('bet_tma_mock_vk_group_member', String(body.group_member));
      }
      if (typeof body.messages_allowed === 'boolean') {
        localStorage.setItem('bet_tma_mock_vk_messages_allowed', String(body.messages_allowed));
      }
      if (typeof body.notifications_allowed === 'boolean') {
        localStorage.setItem('bet_tma_mock_vk_notifications_allowed', String(body.notifications_allowed));
      }
    }
    return {
      status: 'success',
      vk_user_id: localStorage.getItem('bet_tma_mock_vk_user_id'),
      group_id: Number(import.meta.env.VITE_VK_GROUP_ID || 0) || null,
      configured: Boolean(import.meta.env.VITE_VK_GROUP_ID),
      group_member: localStorage.getItem('bet_tma_mock_vk_group_member') === 'true',
      messages_allowed: localStorage.getItem('bet_tma_mock_vk_messages_allowed') === 'true',
      notifications_allowed: localStorage.getItem('bet_tma_mock_vk_notifications_allowed') === 'true',
    };
  }

  if (DEBUG_AUTH_ENABLED && endpoint === '/auth/telegram-widget') {
    return {
      access_token: 'mock_debug_access_token',
      token_type: 'bearer',
      user: getMockUser(),
    };
  }

  if (DEBUG_AUTH_ENABLED && endpoint === '/auth/telegram/bot-session') {
    return {
      auth_token: 'mock_telegram_bot_auth',
      bot_url: 'https://t.me/Shamra1_bot?start=auth_mock_telegram_bot_auth',
      expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
    };
  }

  if (DEBUG_AUTH_ENABLED && endpoint.startsWith('/auth/telegram/bot-session/')) {
    return {
      status: 'confirmed',
      access_token: 'mock_debug_access_token',
      token_type: 'bearer',
      user: getMockUser(),
    };
  }

  if (!DEBUG_AUTH_ENABLED || getStoredAuthToken() !== 'mock_debug_access_token') {
    return null;
  }

  if (endpoint === '/signals/web-push/public-key') {
    return {
      public_key: import.meta.env.VITE_WEB_PUSH_VAPID_PUBLIC_KEY || '',
      configured: Boolean(import.meta.env.VITE_WEB_PUSH_VAPID_PUBLIC_KEY),
    };
  }
  if (endpoint === '/signals/web-push/subscription') {
    return { status: options.method === 'DELETE' ? 'deleted' : 'saved', configured: false };
  }
  if (endpoint === '/signals/messages' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const message = buildMockSupportMessage(getMockUser().telegram_id, String(body.text || ''), 'client');
    const messages = [...getMockSupportMessages(), message];
    saveMockSupportMessages(messages);
    return message;
  }
  if (endpoint === '/chat/conversations') {
    const user = getMockUser();
    const supportMessages = getMockSupportMessages().filter((message: any) => message.user_id === user.telegram_id);
    const latestSupportMessage = [...supportMessages].sort((left: any, right: any) => (
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
    ))[0] || null;
    return {
      items: [
        mockSignalConversation(user),
        mockConversationFromUser(user, latestSupportMessage),
      ],
      next_before: null,
      has_more: false,
    };
  }
  if (endpoint === '/chat/conversations/signals/messages') {
    const supportTypes = new Set(['support_staff_message', 'support_client_message']);
    const limit = Math.max(1, Number(queryParams.get('limit')) || 50);
    const teaserBookmakers = MOCK_BOOKMAKERS.filter((bookmaker) => [1, 4].includes(bookmaker.id));
    const items = [
      {
        id: 1,
        user_id: getMockUser().telegram_id,
        text: '⚡ Shamrai Web Bot подключен. Здесь будут дублироваться live-сигналы вне Telegram.',
        type: 'system',
        data: {},
        created_at: new Date(Date.now() - 7 * 60 * 1000).toISOString(),
      },
      {
        id: 2,
        user_id: getMockUser().telegram_id,
        text: 'LIVE: Зенит - Спартак, коэффициент 1.92. Проверьте линию в своей БК.',
        type: 'live_signal',
        data: {},
        created_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
      },
      {
        id: 3,
        user_id: getMockUser().telegram_id,
        text: 'Анонс закрытого прогноза: Зенит - Спартак. Линия подходит под ваш профиль. Нажмите «Взять», если хотите получить ставку.',
        type: 'forecast_teaser',
        data: {
          forecast_request_id: 'mock-request-client-training',
          forecast_status: localStorage.getItem('bet_tma_mock_forecast_training_status') || 'announced',
          request_kind: 'forecast',
          sport_type: 'Футбол',
          coefficient: 1.92,
          event_name: 'Зенит - Спартак',
          bookmakers: teaserBookmakers.map((bookmaker) => ({
            id: bookmaker.id,
            name: bookmaker.name,
            code: bookmaker.code,
            url: `https://example.com/bookmakers/${bookmaker.code}`,
          })),
          message_text: [
            'Анонс закрытого прогноза',
            'Матч: Зенит - Спартак',
            'Коэффициент: 1.92',
            'Подходит под ваш профиль и выбранные БК.',
            'Нажмите «Взять», если хотите получить ставку.',
          ].join('\n'),
        },
        created_at: new Date(Date.now() - 90 * 1000).toISOString(),
      },
    ].filter((signal: any) => !supportTypes.has(signal.type)).slice(-limit);
    return { items, next_before_id: null, has_more: false };
  }
  if (endpoint === '/chat/conversations/signals/read' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    return { status: 'ok', last_read_signal_id: body.last_read_signal_id ?? null, last_read_message_id: null };
  }
  const forecastActionMatch = endpoint.match(/^\/signals\/forecast-requests\/([^/]+)\/(take|decline)$/);
  if (forecastActionMatch && options.method === 'POST') {
    const action = forecastActionMatch[2];
    const nextStatus = action === 'take' ? 'interested' : 'declined';
    localStorage.setItem('bet_tma_mock_forecast_training_status', nextStatus);
    return {
      status: nextStatus,
      message: action === 'take'
        ? 'Заявка отправлена Shamrai. Менеджер подготовит полный прогноз.'
        : 'Отказ учтен.',
      forecast_request_id: forecastActionMatch[1],
      action: 'accepted',
      contact: null,
    };
  }
  if (endpoint === '/chat/conversations/support/messages' && (!options.method || options.method === 'GET')) {
    const userId = getMockUser().telegram_id;
    const items = getMockSupportMessages()
      .filter((message: any) => message.user_id === userId)
      .sort((left: any, right: any) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime())
      .map(mockSupportMessageToChatMessage);
    return { items, next_before_id: null, has_more: false };
  }
  if (endpoint === '/chat/conversations/support/messages' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const message = buildMockSupportMessage(getMockUser().telegram_id, String(body.text || ''), 'client', {
      client_message_id: body.client_message_id || `mock-${Date.now()}`,
      reply_to_id: body.reply_to_id ?? null,
    });
    const messages = [...getMockSupportMessages(), message];
    saveMockSupportMessages(messages);
    return mockSupportMessageToChatMessage(message);
  }
  if (endpoint === '/chat/conversations/support/attachments' && options.method === 'POST') {
    const formData = options.body instanceof FormData ? options.body : null;
    const file = formData?.get('file') as File | null;
    const messageType = String(formData?.get('message_type') || 'file');
    const text = String(formData?.get('text') || '');
    const message = buildMockSupportMessage(getMockUser().telegram_id, text, 'client', {
      client_message_id: String(formData?.get('client_message_id') || `mock-${Date.now()}`),
      reply_to_id: Number(formData?.get('reply_to_id')) || null,
      message_type: messageType,
      payload: {
        original_filename: file?.name || 'attachment.bin',
        mime_type: file?.type || 'application/octet-stream',
        size_bytes: file?.size || 1,
      },
    });
    saveMockSupportMessages([...getMockSupportMessages(), message]);
    return mockSupportMessageToChatMessage(message);
  }
  if (endpoint === '/chat/conversations/support/read' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    return { status: 'ok', last_read_message_id: body.last_read_message_id ?? null, last_read_signal_id: null };
  }
  if (endpoint === '/chat/conversations/support/typing' && options.method === 'POST') return { status: 'ok' };
  if (endpoint === '/chat/stream-ticket' && options.method === 'POST') {
    return { ticket: `mock-chat-v2-ticket-${Date.now()}`, expires_in: 30 };
  }
  if (endpoint === '/signals/history') {
    const supportMessages = getMockSupportMessages().filter((message: any) => message.user_id === getMockUser().telegram_id);
    return [
      {
        id: 1,
        user_id: getMockUser().telegram_id,
        text: '⚡ Shamrai Web Bot подключен. Здесь будут дублироваться live-сигналы вне Telegram.',
        type: 'system',
        created_at: new Date(Date.now() - 7 * 60 * 1000).toISOString(),
      },
      {
        id: 2,
        user_id: getMockUser().telegram_id,
        text: 'LIVE: Зенит - Спартак, коэффициент 1.92. Проверьте линию в своей БК.',
        type: 'live_signal',
        created_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
      },
      ...supportMessages,
    ];
  }

  if (endpoint === '/bookmakers') return MOCK_BOOKMAKERS;
  if (endpoint === '/users/me/bookmakers' && (!options.method || options.method === 'GET')) {
    return getMockBookmakerIds();
  }
  if (endpoint === '/users/me/bookmakers' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const ids = Array.isArray(body.bookmaker_ids) ? body.bookmaker_ids : [];
    localStorage.setItem('bet_tma_mock_bookmaker_ids', JSON.stringify(ids));
    if (body.other_bookmaker_name) {
      localStorage.setItem('bet_tma_mock_other_bookmaker_name', body.other_bookmaker_name);
    } else {
      localStorage.removeItem('bet_tma_mock_other_bookmaker_name');
    }
    return { status: 'success', message: 'Mock bookmakers list updated successfully' };
  }
  if (endpoint === '/users/me/onboard/skip') {
    const bookmakerIds = MOCK_BOOKMAKERS.map((bookmaker) => bookmaker.id);
    localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    localStorage.setItem('bet_tma_mock_bookmaker_ids', JSON.stringify(bookmakerIds));
    return {
      ...getMockUser(),
      is_onboarded: true,
      free_bets_available: 0,
      bookmakers: MOCK_BOOKMAKERS,
    };
  }
  if (DEBUG_AUTH_ENABLED && endpoint === '/auth/vk/link') {
    const vkUserId = 'vk_mock_741852963';
    localStorage.setItem('bet_tma_mock_vk_user_id', vkUserId);
    return {
      status: 'success',
      vk_user_id: vkUserId,
      vk_display_name: 'Иван VK',
    };
  }
  if (endpoint === '/users/me/profile-dashboard') return getMockProfileDashboard();
  if (endpoint === '/users/me') return getMockUser();
  if (endpoint === '/users/me/onboard' || /^\/users\/\d+\/onboard$/.test(endpoint)) {
    const linkedVkUserId = localStorage.getItem('bet_tma_mock_vk_user_id');
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    if (body.vk_user_id && body.vk_user_id !== linkedVkUserId) {
      throw new Error('vk_user_id must match the linked VK profile');
    }
    const bookmakerCodes = Array.isArray(body.bookmakers) ? body.bookmakers : [];
    const idsFromCodes = MOCK_BOOKMAKERS
      .filter((bookmaker) => bookmakerCodes.includes(bookmaker.code))
      .map((bookmaker) => bookmaker.id);
    const bookmakerIds = Array.isArray(body.bookmaker_ids) && body.bookmaker_ids.length
      ? body.bookmaker_ids
      : idsFromCodes.length
        ? idsFromCodes
        : getMockBookmakerIds();
    const selectedBookmakers = MOCK_BOOKMAKERS.filter((bookmaker) => bookmakerIds.includes(bookmaker.id));
    const serviceFormat = normalizeMockServiceFormat(body.service_format);
    const vipVerdict = buildMockVipVerdict(serviceFormat, body.onboarding_goal);
    localStorage.setItem('bet_tma_mock_is_onboarded', 'true');
    localStorage.setItem('bet_tma_mock_bookmaker_ids', JSON.stringify(bookmakerIds));
    const user = {
      ...getMockUser(),
      is_onboarded: true,
      onboarding_goal: body.onboarding_goal ?? 'fast_signals',
      experience_level: body.experience_level ?? 'amateur',
      bankroll_size: body.bankroll_size ?? 'mid',
      risk_tolerance: body.risk_tolerance ?? 'balanced',
      primary_bookmaker: selectedBookmakers[0]?.code ?? body.primary_bookmaker ?? 'fonbet',
      vk_user_id: linkedVkUserId,
      other_bookmaker_name: bookmakerCodes.includes('other') ? (body.other_bookmaker_name ?? null) : null,
      currency_preference: body.currency_preference ?? 'RUB',
      purchased_bets_balance: 12,
      free_bets_available: 0,
      bookmakers: selectedBookmakers,
      favorite_sports: Array.isArray(body.favorite_sports) ? body.favorite_sports : [],
      preferred_sports: [
        'Автогонки',
        'Ам. футбол',
        'Бадминтон',
        'Баскетбол',
        'Бейсбол',
        'Бильярд',
        'Бокс',
        'Велоспорт',
        'Вод. поло',
        'Водные виды',
        'Волейбол',
        'Гандбол',
        'Гимнастика',
        'Гольф',
        'Дартс',
        'Другие',
        'Единоборства',
        'Киберспорт',
        'Коньки',
        'Крикет',
        'Л/Атл',
        'Лыжи/Биатлон',
        'Н/Т',
        'Пляж. футб',
        'Регби',
        'Сани/Бобслей',
        'Теннис',
        'Футбол',
        'Футзал',
        'Хоккей',
      ],
      client_group: body.experience_level === 'pro' || body.bankroll_size === 'high'
        ? 'Новый PRO'
        : body.risk_tolerance === 'aggressive'
          ? 'Новый aggressive'
          : 'Новый balanced',
      client_tag: `goal: ${body.onboarding_goal ?? 'fast_signals'}`,
    };
    return {
      status: 'success',
      message: 'Shamrai neural calibration completed',
      recommendation: {
        flat_stake_percent: body.risk_tolerance === 'aggressive' ? 8.5 : body.risk_tolerance === 'cautious' ? 7 : 7.75,
        monthly_profit_percent: body.risk_tolerance === 'aggressive' ? 41.5 : body.risk_tolerance === 'cautious' ? 22.4 : 35,
        missed_profit_percent_24h: body.risk_tolerance === 'aggressive' ? 9.2 : body.risk_tolerance === 'cautious' ? 4.8 : 7.4,
        missed_profit_amount_24h: body.bankroll_size === 'high' ? 11100 : body.bankroll_size === 'micro' ? 2220 : 5550,
        currency: body.currency_preference ?? 'RUB',
        source: 'mock_channel_24h',
        resolved_bets_24h: 7,
        service_format_label: MOCK_SERVICE_FORMAT_LABELS[serviceFormat],
        vip_verdict_title: vipVerdict.title,
        vip_verdict_caption: vipVerdict.caption,
      },
      user,
    };
  }
  if (endpoint === '/users/me/bets') return getMockTakenBets();
  if (endpoint === '/users/me/bets/timeline') {
    const takenBets = getMockTakenBets();
    const payload = buildMockPerformancePayload(takenBets, normalizeMockPeriod(queryParams.get('period')));
    return {
      ...payload,
      excluded_summary: mockSummary([]),
      excluded_bets: [],
    };
  }
  if (endpoint === '/bets/feed-page') {
    const limit = Math.max(1, Number(queryParams.get('limit')) || 20);
    const cursor = Math.max(0, Number(queryParams.get('cursor')) || 0);
    const items = getMockBets().filter((bet: any) => bet.delivery_mode === 'feed');
    const pageItems = items.slice(cursor, cursor + limit);
    const nextCursor = cursor + pageItems.length;
    return {
      items: pageItems,
      next_cursor: nextCursor < items.length ? String(nextCursor) : null,
      has_more: nextCursor < items.length,
      total: items.length,
      filtered_total: items.length,
    };
  }
  if (endpoint === '/bets/feed') return getMockBets().filter((bet: any) => bet.delivery_mode === 'feed');
  if (endpoint === '/admin/bets/pending') {
    return [...getMockBets(), ...getMockPrivateForecastBets()].filter((bet: any) => bet.status === 'pending');
  }
  if (endpoint.startsWith('/admin/bets/') && options.method === 'DELETE') {
    const betId = endpoint.split('/')[3];
    let revokedCount = 0;
    let balanceDeltaTotal = 0;
    const bets = getMockBets().map((bet: any) => (
      bet.id === betId ? { ...bet, status: 'deleted', is_taken: false, is_unlocked: false } : bet
    ));
    saveMockBets(bets);
    const requests = getMockForecastRequests().map((request: any) => {
      if (request.bet_id !== betId && request.bet?.id !== betId) return request;
      const hadAccess = request.status === 'sent' || request.status === 'manual_sent';
      const balanceDelta = hadAccess ? 1 : 0;
      const currentBalance = request.balance_after ?? request.user.matches_remaining ?? 0;
      const nextBalance = currentBalance + balanceDelta;
      if (hadAccess) {
        revokedCount += 1;
        balanceDeltaTotal += balanceDelta;
      }
      return {
        ...request,
        status: 'removed',
        balance_after: hadAccess ? nextBalance : request.balance_after,
        no_balance_warning: false,
        updated_at: nowIso(),
        bet: request.bet
          ? { ...request.bet, status: 'deleted', is_taken: false, is_unlocked: false }
          : request.bet,
        user: hadAccess ? { ...request.user, matches_remaining: nextBalance } : request.user,
      };
    });
    saveMockForecastRequests(requests);
    return {
      status: 'success',
      bet_id: betId,
      revoked_count: revokedCount,
      marked_requests: requests.filter((request: any) => request.bet_id === betId || request.bet?.id === betId).length,
      balance_delta_total: balanceDeltaTotal,
    };
  }
  if (endpoint.match(/^\/admin\/forecast-broadcast\/([^/]+)\/stop$/) && options.method === 'POST') {
    const betId = endpoint.split('/')[3];
    let stoppedRequests = 0;
    let skippedProcessing = 0;
    const requests = getMockForecastRequests().map((request: any) => {
      if (request.bet_id !== betId && request.bet?.id !== betId) return request;
      if (request.status === 'processing') {
        skippedProcessing += 1;
        return request;
      }
      const shouldRemove = ['announced', 'interested', 'declined', 'cancelled'].includes(request.status);
      if (shouldRemove) stoppedRequests += 1;
      return {
        ...request,
        status: shouldRemove ? 'removed' : request.status,
        bet: request.bet ? { ...request.bet, status: 'deleted', auto_send_on_interest: false } : request.bet,
      };
    });
    saveMockForecastRequests(requests);
    return {
      status: 'success',
      bet_id: betId,
      already_stopped: false,
      stopped_requests: stoppedRequests,
      skipped_processing: skippedProcessing,
    };
  }
  if (endpoint.startsWith('/admin/forecast-requests-page') && (!options.method || options.method === 'GET')) {
    const [, queryString = ''] = endpoint.split('?');
    const params = new URLSearchParams(queryString);
    const status = params.get('status');
    const limit = Math.max(1, Number(params.get('limit')) || 20);
    const filteredRequests = status
      ? getMockForecastRequests().filter((request: any) => request.status === status)
      : getMockForecastRequests();
    const items = filteredRequests.slice(0, limit);
    return {
      items,
      next_cursor: filteredRequests.length > items.length ? String(items.length) : null,
      has_more: filteredRequests.length > items.length,
      total: filteredRequests.length,
      filtered_total: filteredRequests.length,
    };
  }
  if (endpoint.startsWith('/admin/forecast-requests') && (!options.method || options.method === 'GET')) {
    const [, queryString = ''] = endpoint.split('?');
    const params = new URLSearchParams(queryString);
    const status = params.get('status');
    const requests = getMockForecastRequests();
    return status ? requests.filter((request: any) => request.status === status) : requests;
  }
  if (endpoint === '/admin/forecast-broadcast') {
    const body = options.body instanceof FormData ? options.body : null;
    const coefficient = body?.get('coefficient') || 2;
    const sportType = body?.get('sport_type') ? String(body.get('sport_type')) : null;
    const selectedBookmakerIds = body
      ? body.getAll('bookmaker_ids')
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value))
      : [];
    const fallbackBookmakerId = Number(body?.get('bookmaker_id'));
    const bookmakerIds = selectedBookmakerIds.length
      ? selectedBookmakerIds
      : Number.isFinite(fallbackBookmakerId)
        ? [fallbackBookmakerId]
        : getMockBookmakerIds().slice(0, 2);
    const teaserBet = buildMockBet(`mock-private-bet-${Date.now()}`, {
      event_name: 'Закрытый прогноз',
      coefficient,
      bookmakerIds,
      sport_type: sportType,
      outcome: null,
      description: null,
      coupon_image_url: null,
      match_link: null,
      delivery_mode: 'sales_private',
    });
    const nextRequest = buildMockForecastRequest(`mock-request-${Date.now()}`, {
      status: 'announced',
      responded_at: null,
      bet_id: teaserBet.id,
      bet: teaserBet,
    });
    saveMockForecastRequests([nextRequest, ...getMockForecastRequests()]);
    return {
      sent: 18,
      failed: 0,
      errors: [],
      total_audience: 18,
      status: 'success',
      bet_id: nextRequest.bet_id,
      delivery: {
        sent: 18,
        failed: 0,
        telegram: { sent: 12, failed: 0 },
        vk_messages: { sent: 6, failed: 0 },
        vk_notifications: { requested: 4, sent: 0, failed: 0 },
      },
    };
  }
  if (endpoint === '/admin/paid-set-broadcast') {
    const body = options.body instanceof FormData ? options.body : null;
    const eventName = String(body?.get('event_name') || 'Платный набор');
    const outcome = String(body?.get('outcome') || '');
    const coefficient = body?.get('coefficient') || 3.9;
    const priceRub = Number(body?.get('price_rub') || 1500);
    const sportType = body?.get('sport_type') ? String(body.get('sport_type')) : null;
    const teaserText = String(body?.get('teaser_text') || 'Реальный КФ не выше 1.9!');
    const couponImage = body?.get('coupon_image');
    const selectedBookmakerIds = body
      ? body.getAll('bookmaker_ids')
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value))
      : [];
    const fallbackBookmakerId = Number(body?.get('bookmaker_id'));
    const bookmakerIds = selectedBookmakerIds.length
      ? selectedBookmakerIds
      : Number.isFinite(fallbackBookmakerId)
        ? [fallbackBookmakerId]
        : getMockBookmakerIds().slice(0, 3);
    const paidSetBet = buildMockBet(`mock-paid-set-bet-${Date.now()}`, {
      event_name: eventName,
      coefficient,
      bookmakerIds,
      sport_type: sportType,
      outcome,
      description: teaserText,
      coupon_image_url: couponImage ? '/static/coupons/mock-paid-set-coupon.png' : null,
      match_link: null,
      delivery_mode: 'paid_set',
      price_stars: priceRub,
    });
    const nextRequest = buildMockForecastRequest(`mock-paid-set-request-${Date.now()}`, {
      status: 'announced',
      responded_at: null,
      bet_id: paidSetBet.id,
      bet: paidSetBet,
    });
    saveMockForecastRequests([nextRequest, ...getMockForecastRequests()]);
    return {
      sent: 18,
      failed: 0,
      errors: [],
      total_audience: 18,
      status: 'success',
      bet_id: nextRequest.bet_id,
      delivery_mode: 'paid_set',
      price_rub: priceRub,
      delivery: {
        sent: 18,
        failed: 0,
        telegram: { sent: 12, failed: 0 },
        vk_messages: { sent: 6, failed: 0 },
        vk_notifications: { requested: 4, sent: 0, failed: 0 },
        web_push: { sent: 18, failed: 0, missing_permission: 0 },
      },
    };
  }
  if (endpoint.startsWith('/admin/forecast-broadcast/') && options.method === 'POST') {
    const match = endpoint.match(/^\/admin\/forecast-broadcast\/([^/]+)\/full-forecast$/);
    if (match) {
      const [, betId] = match;
      const body = options.body instanceof FormData ? options.body : null;
      const currentRequests = getMockForecastRequests();
      const targetRequest = currentRequests.find((request: any) => request.bet_id === betId);
      if (!targetRequest) throw new Error('Закрытый прогноз не найден');
      const couponImage = body?.get('coupon_image');
      const couponImageUrl = couponImage
        ? '/static/coupons/mock-prepared-coupon.png'
        : targetRequest.bet.coupon_image_url;
      const autoSendEnabled = String(body?.get('auto_send_interested') || 'false') === 'true';
      const reannounceNewAudience = String(body?.get('reannounce_new_audience') || 'false') === 'true';
      const selectedBookmakerIds = body
        ? body.getAll('bookmaker_ids')
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value) && value > 0)
        : [];
      const previousBookmakerIds = Array.isArray(targetRequest.bet.bookmakers)
        ? targetRequest.bet.bookmakers.map((bookmaker: any) => Number(bookmaker.id)).filter((value: number) => Number.isFinite(value))
        : [];
      const addedBookmakerIds = selectedBookmakerIds.filter((bookmakerId) => !previousBookmakerIds.includes(bookmakerId));
      const nextSportType = body?.has('sport_type')
        ? String(body.get('sport_type') || '').trim() || null
        : targetRequest.bet.sport_type;

      const preparedBet = {
        ...targetRequest.bet,
        event_name: String(body?.get('event_name') || targetRequest.bet.event_name || 'Закрытый прогноз'),
        outcome: String(body?.get('outcome') || targetRequest.bet.outcome || ''),
        coefficient: body?.get('coefficient') || targetRequest.bet.coefficient,
        sport_type: nextSportType,
        category: String(body?.get('category') || targetRequest.bet.category || 'prematch'),
        description: body?.get('description') ? String(body.get('description')) : null,
        match_link: body?.get('match_link') ? String(body.get('match_link')) : null,
        bookmaker_links: readMockBookmakerLinks(body, targetRequest.bet.bookmaker_links),
        coupon_image_url: couponImageUrl,
        bookmakers: selectedBookmakerIds.length
          ? MOCK_BOOKMAKERS.filter((bookmaker) => selectedBookmakerIds.includes(bookmaker.id))
          : targetRequest.bet.bookmakers,
        bookmaker_id: selectedBookmakerIds[0] || targetRequest.bet.bookmaker_id,
        auto_send_on_interest: autoSendEnabled,
      };

      let sent = 0;
      let failed = 0;
      let autoSendTotal = 0;
      const errors: string[] = [];
      const requests = currentRequests.map((request: any) => (
        request.bet_id === betId
          ? (() => {
              if (!autoSendEnabled || request.status !== 'interested') {
                return { ...request, bet: preparedBet, updated_at: nowIso() };
              }
              autoSendTotal += 1;
              const balanceBefore = request.user.matches_remaining ?? 0;
              if (balanceBefore <= 0) {
                failed += 1;
                errors.push(`ID ${request.user_id}: 0 матчей, оставлено в ручной обработке`);
                return { ...request, bet: preparedBet, updated_at: nowIso() };
              }
              const balanceAfter = balanceBefore - 1;
              sent += 1;
              return {
                ...request,
                status: 'sent',
                delivery_method: 'bot',
                delivered_at: nowIso(),
                balance_before: balanceBefore,
                balance_after: balanceAfter,
                no_balance_warning: balanceBefore <= 0 && !request.user.guarantee_active,
                bet: { ...preparedBet, is_taken: true, is_unlocked: true },
                user: { ...request.user, matches_remaining: balanceAfter },
                updated_at: nowIso(),
              };
            })()
          : request
      ));
      let reannounce = null;
      const nextRequests = [...requests];
      if (reannounceNewAudience && addedBookmakerIds.length > 0) {
        const existingUserIds = new Set(nextRequests.filter((request: any) => request.bet_id === betId).map((request: any) => request.user_id));
        const mockUsers = getMockUsers().filter((user: any) => {
          if (existingUserIds.has(user.telegram_id)) return false;
          return Array.isArray(user.bookmakers) && user.bookmakers.some((bookmaker: any) => addedBookmakerIds.includes(bookmaker.id));
        });
        mockUsers.slice(0, 3).forEach((user: any, index: number) => {
          nextRequests.push(buildMockForecastRequest(`mock-reannounce-${Date.now()}-${index}`, {
            status: 'announced',
            responded_at: null,
            bet_id: betId,
            user_id: user.telegram_id,
            user,
            bet: preparedBet,
          }));
        });
        reannounce = {
          total_audience: Math.min(mockUsers.length, 3),
          sent: Math.min(mockUsers.length, 3),
          failed: 0,
          errors: [],
          delivery: {
            total_audience: Math.min(mockUsers.length, 3),
            sent: Math.min(mockUsers.length, 3),
            failed: 0,
          },
        };
      }
      saveMockForecastRequests(nextRequests);
      const autoSend = autoSendEnabled
        ? {
            status: failed === 0 ? 'success' : sent ? 'partial' : 'failed',
            total: autoSendTotal,
            sent,
            failed,
            errors,
          }
        : null;
      return {
        bet: preparedBet,
        auto_send_enabled: autoSendEnabled,
        auto_send: autoSend,
        reannounce,
      };
    }
  }
  if (endpoint === '/admin/forecast-requests/bulk-send' && options.method === 'POST') {
    const body = options.body instanceof FormData ? options.body : null;
    const selectedRequestIds = body
      ? body.getAll('request_ids').map((value) => String(value))
      : [];
    if (selectedRequestIds.length === 0) throw new Error('Выберите хотя бы одного клиента');

    const currentRequests = getMockForecastRequests();
    const selectedRequests = currentRequests.filter((request: any) => selectedRequestIds.includes(request.id));
    const selectedBetIds = Array.from(new Set(selectedRequests.map((request: any) => request.bet_id)));
    if (selectedBetIds.length > 1) throw new Error('Выберите клиентов из одного закрытого прогноза');

    const couponImage = body?.get('coupon_image');
    const referenceRequest = selectedRequests[0];
    const couponImageUrl = couponImage
      ? '/static/coupons/mock-bulk-coupon.png'
      : referenceRequest?.bet?.coupon_image_url;

    let sent = 0;
    let failed = 0;
    const errors: string[] = [];
    const updatedRequests = currentRequests.map((request: any) => {
      if (!selectedRequestIds.includes(request.id)) return request;
      if (request.status !== 'interested') {
        failed += 1;
        errors.push(`ID ${request.user_id}: заявка уже обработана`);
        return request;
      }

      const balanceBefore = request.user.matches_remaining ?? 0;
      const balanceAfter = balanceBefore - 1;
      const sentBet = {
        ...request.bet,
        event_name: String(body?.get('event_name') || request.bet.event_name || 'Закрытый прогноз'),
        outcome: String(body?.get('outcome') || request.bet.outcome || ''),
        coefficient: body?.get('coefficient') || request.bet.coefficient,
        sport_type: body?.has('sport_type')
          ? String(body.get('sport_type') || '').trim() || null
          : request.bet.sport_type,
        category: String(body?.get('category') || request.bet.category || 'prematch'),
        description: body?.get('description') ? String(body.get('description')) : request.bet.description,
        match_link: body?.get('match_link') ? String(body.get('match_link')) : request.bet.match_link,
        bookmaker_links: readMockBookmakerLinks(body, request.bet.bookmaker_links),
        coupon_image_url: couponImageUrl,
        is_taken: true,
        is_unlocked: true,
      };
      sent += 1;
      return {
        ...request,
        status: 'sent',
        delivery_method: 'bot',
        delivered_at: nowIso(),
        balance_before: balanceBefore,
        balance_after: balanceAfter,
        no_balance_warning: balanceBefore <= 0 && !request.user.guarantee_active,
        bet: sentBet,
        user: { ...request.user, matches_remaining: balanceAfter },
      };
    });

    const missing = selectedRequestIds.length - selectedRequests.length;
    if (missing > 0) {
      failed += missing;
      errors.push(`Не найдено заявок: ${missing}`);
    }

    saveMockForecastRequests(updatedRequests);
    return {
      status: sent && failed === 0 ? 'success' : sent ? 'partial' : 'failed',
      total: selectedRequestIds.length,
      sent,
      failed,
      errors,
    };
  }
  if (endpoint.startsWith('/admin/forecast-requests/') && options.method === 'POST') {
    const match = endpoint.match(/^\/admin\/forecast-requests\/([^/]+)\/(send|send-saved|mark-manual|cancel|remove-client)$/);
    if (match) {
      const [, requestId, action] = match;
      const currentRequests = getMockForecastRequests();
      const targetRequest = currentRequests.find((request: any) => request.id === requestId);
      if (!targetRequest) throw new Error('Заявка не найдена');
      if ((action === 'send' || action === 'send-saved' || action === 'mark-manual') && targetRequest.status !== 'interested') {
        throw new Error('Клиент еще не нажал «Беру» или заявка уже обработана');
      }
      if (action === 'cancel' && ['processing', 'sent', 'manual_sent', 'cancelled', 'removed'].includes(targetRequest.status)) {
        throw new Error('Эту заявку нельзя отменить');
      }
      if (action === 'remove-client' && targetRequest.status === 'processing') {
        throw new Error('Заявка уже обрабатывается, удаление клиента недоступно');
      }

      const isDeliveryAction = action === 'send' || action === 'send-saved' || action === 'mark-manual';
      const isRemoveAction = action === 'remove-client';
      const body = options.body instanceof FormData ? options.body : null;
      const balanceBefore = targetRequest.user.matches_remaining ?? 0;
      const balanceAfter = balanceBefore - 1;
      const removalHadAccess = targetRequest.status === 'sent' || targetRequest.status === 'manual_sent';
      const removalBalance = (targetRequest.balance_after ?? targetRequest.user.matches_remaining ?? 0) + (removalHadAccess ? 1 : 0);
      const nextStatus = action === 'send' || action === 'send-saved' ? 'sent' : action === 'mark-manual' ? 'manual_sent' : isRemoveAction ? 'removed' : 'cancelled';
      const deliveryMethod = action === 'send' || action === 'send-saved' ? 'bot' : action === 'mark-manual' ? 'manual' : targetRequest.delivery_method;
      const sentBet = action === 'send' || action === 'send-saved'
        ? {
            ...targetRequest.bet,
            event_name: String(body?.get('event_name') || targetRequest.bet.event_name || 'Закрытый прогноз'),
            outcome: String(body?.get('outcome') || targetRequest.bet.outcome || ''),
            coefficient: body?.get('coefficient') || targetRequest.bet.coefficient,
            sport_type: body?.has('sport_type')
              ? String(body.get('sport_type') || '').trim() || null
              : targetRequest.bet.sport_type,
            category: String(body?.get('category') || targetRequest.bet.category || 'prematch'),
            description: body?.get('description') ? String(body.get('description')) : targetRequest.bet.description,
            match_link: body?.get('match_link') ? String(body.get('match_link')) : targetRequest.bet.match_link,
            bookmaker_links: readMockBookmakerLinks(body, targetRequest.bet.bookmaker_links),
            coupon_image_url: body?.get('coupon_image') ? '/static/coupons/mock-final-coupon.png' : targetRequest.bet.coupon_image_url,
            is_taken: true,
            is_unlocked: true,
          }
        : { ...targetRequest.bet, is_taken: true, is_unlocked: true };
      const requests = currentRequests.map((request: any) => (
        request.id === requestId
          ? {
              ...request,
              status: nextStatus,
              delivery_method: deliveryMethod,
              delivered_at: isDeliveryAction ? nowIso() : request.delivered_at,
              balance_before: isDeliveryAction ? balanceBefore : request.balance_before,
              balance_after: isDeliveryAction ? balanceAfter : isRemoveAction && removalHadAccess ? removalBalance : request.balance_after,
              no_balance_warning: isDeliveryAction ? balanceBefore <= 0 && !request.user.guarantee_active : isRemoveAction ? false : request.no_balance_warning,
              bet: isDeliveryAction
                ? sentBet
                : isRemoveAction
                  ? { ...request.bet, is_taken: false, is_unlocked: false }
                  : request.bet,
              user: isDeliveryAction
                ? { ...request.user, matches_remaining: balanceAfter }
                : isRemoveAction && removalHadAccess
                  ? { ...request.user, matches_remaining: removalBalance }
                  : request.user,
            }
          : request
      ));
      saveMockForecastRequests(requests);
      return requests.find((request: any) => request.id === requestId) || { status: 'success' };
    }
  }
  if (endpoint === '/bets/stats') {
    const takenBets = getMockTakenBets();
    const total = takenBets.length;
    const won = takenBets.filter((bet: any) => bet.status === 'win').length;
    const lost = takenBets.filter((bet: any) => bet.status === 'loss').length;
    const refunded = takenBets.filter((bet: any) => bet.status === 'refund').length;
    const coefficientSum = takenBets.reduce((sum: number, bet: any) => sum + Number(bet.coefficient || 0), 0);
    const profit = takenBets.reduce((sum: number, bet: any) => {
      if (bet.status === 'win') return sum + Number(bet.coefficient || 1) - 1;
      if (bet.status === 'loss') return sum - 1;
      return sum;
    }, 0);
    const resolved = won + lost;
    return {
      total_bets_taken: total,
      won_bets: won,
      lost_bets: lost,
      refund_bets: refunded,
      net_profit: Number(profit.toFixed(2)),
      winrate: resolved > 0 ? Number(((won / resolved) * 100).toFixed(2)) : 0,
      roi: total > 0 ? Number(((profit / total) * 100).toFixed(2)) : 0,
      average_coefficient: total > 0 ? Number((coefficientSum / total).toFixed(2)) : 0,
    };
  }
  if (requestUrl.pathname === '/stats/global' || endpoint === '/bets/analytics') {
    const period = requestUrl.searchParams.get('period') === 'month' ? 'month' : 'all';
    if (period === 'month') {
      return {
        period,
        period_label: 'Текущий месяц',
        winrate: 66.67,
        roi: 21.33,
        net_profit: 6.4,
        total_bets: 30,
        won_bets: 20,
        lost_bets: 10,
        refund_bets: 0,
        average_coefficient: 1.96,
        chart_points: [
          { month: 'Текущий месяц', profit: 6.4 },
        ],
      };
    }
    return {
      period,
      period_label: 'Весь период',
      winrate: 64,
      roi: 18.7,
      net_profit: 42,
      total_bets: 156,
      won_bets: 100,
      lost_bets: 48,
      refund_bets: 8,
      average_coefficient: 1.94,
      chart_points: [
        { month: 'Янв', profit: 4 },
        { month: 'Фев', profit: 9 },
        { month: 'Мар', profit: 7 },
        { month: 'Апр', profit: 16 },
        { month: 'Май', profit: 22 },
        { month: 'Июн', profit: 28 },
      ],
    };
  }
  if (endpoint === '/admin/dashboard/stats') {
    return {
      active_subscribers: 42,
      channel_roi: 18.7,
      winrate: 64,
      total_bets_issued: getMockBets().length,
      average_coefficient: 1.94,
      author_total_bets_issued: getMockBets().length,
      author_winrate: 64,
      author_average_coefficient: 1.94,
      author_channel_roi: 18.7,
      total_users: 128,
    };
  }
  if (endpoint.startsWith('/admin/stats/author-timeline')) {
    const period = normalizeMockPeriod(queryParams.get('period'));
    return {
      ...buildMockPerformancePayload([...getMockBets(), ...getMockPrivateForecastBets()], period),
      author: {
        telegram_id: 987654321,
        name: 'Debug Admin',
        username: 'debug_admin',
      },
    };
  }
  if (endpoint.startsWith('/admin/stats/shamrai-timeline')) {
    const period = normalizeMockPeriod(queryParams.get('period'));
    return buildMockPerformancePayload([...getMockBets(), ...getMockPrivateForecastBets()], period);
  }
  if (endpoint === '/admin/stats/clients' || endpoint.startsWith('/admin/stats/clients?')) {
    const period = normalizeMockPeriod(queryParams.get('period'));
    const users = getMockUsers().filter((user: any) => user.role === 'user');
    const clients = users.map((user: any, index: number) => {
      const shiftedBets = getMockTakenBets().map((bet: any, betIndex: number) => ({
        ...bet,
        status: index === 1 && betIndex < 3 ? 'loss' : bet.status,
        resolved_at: bet.resolved_at || daysAgoIso(betIndex + index + 1),
      }));
      const payload = buildMockPerformancePayload(shiftedBets, period);
      return {
        telegram_id: user.telegram_id,
        username: user.username,
        first_name: user.first_name,
        last_name: user.last_name,
        photo_url: user.photo_url,
        name: [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username || String(user.telegram_id),
        matches_remaining: user.matches_remaining,
        guarantee_active: user.guarantee_active,
        client_group: user.client_group,
        client_tag: user.client_tag,
        summary: payload.summary,
        source_split: payload.source_split,
        recent_results: payload.timeline.flatMap((month: any) => month.days).flatMap((day: any) => day.bets).slice(0, 5).map((bet: any) => bet.status),
        situation: mockClientSituation(payload.summary),
      };
    });
    const summaryItems = clients.flatMap((client: any) => (
      Array.from({ length: client.summary.bets }, (_, index) => ({
        status: index < client.summary.wins ? 'win' : 'loss',
        profit_units: index < client.summary.wins ? 0.9 : -1,
        coefficient: 1.9,
        resolved_at: daysAgoIso(index),
      }))
    ));
    return {
      period,
      period_label: mockPeriodLabel(period),
      clients_count: users.length,
      active_clients_count: users.filter((user: any) => (user.matches_remaining || user.purchased_bets_balance || 0) > 0 || user.guarantee_active).length,
      active_clients_with_stats_count: clients.filter((client: any) => client.summary.bets > 0).length,
      summary: mockSummary(summaryItems),
      clients,
    };
  }
  if (endpoint === '/admin/stats/drive-export' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const period = normalizeMockPeriod(body.period);
    return {
      id: `mock-drive-${Date.now()}`,
      status: 'completed',
      scope: body.scope || 'all',
      period,
      formats: body.formats || ['xlsx', 'google_sheet'],
      links: [
        {
          title: 'Mock Drive export',
          url: 'https://drive.google.com/',
          id: 'mock-folder',
          format: 'folder',
        },
        {
          title: body.scope === 'clients' ? 'Клиенты - инфа' : 'Шамрай - статистика',
          url: 'https://docs.google.com/spreadsheets/',
          id: 'mock-sheet',
          format: 'google_sheet',
        },
      ],
      error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
  if (endpoint === '/admin/users/drive-export' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    return {
      id: `mock-crm-drive-${Date.now()}`,
      status: 'completed',
      scope: 'crm',
      period: 'all',
      formats: body.formats || ['xlsx', 'google_sheet'],
      filters: {
        q: body.q || '',
        activity: body.activity || 'all',
        group: body.group || '',
        tag: body.tag || '',
      },
      links: [
        {
          title: 'Shamrai Exports',
          url: 'https://drive.google.com/',
          id: 'mock-crm-root',
          format: 'folder',
        },
        {
          title: 'CRM',
          url: 'https://drive.google.com/',
          id: 'mock-crm-folder',
          format: 'folder',
        },
        {
          title: 'CRM по клиентам',
          url: 'https://docs.google.com/spreadsheets/',
          id: 'mock-crm-sheet',
          format: 'google_sheet',
        },
      ],
      error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
  const crmDriveExportMatch = endpoint.match(/^\/admin\/users\/drive-export\/([^/]+)$/);
  if (crmDriveExportMatch) {
    return {
      id: crmDriveExportMatch[1],
      status: 'completed',
      scope: 'crm',
      period: 'all',
      formats: ['xlsx', 'google_sheet'],
      filters: {},
      links: [
        {
          title: 'Shamrai Exports',
          url: 'https://drive.google.com/',
          id: 'mock-crm-root',
          format: 'folder',
        },
        {
          title: 'CRM',
          url: 'https://drive.google.com/',
          id: 'mock-crm-folder',
          format: 'folder',
        },
        {
          title: 'CRM по клиентам',
          url: 'https://docs.google.com/spreadsheets/',
          id: 'mock-crm-sheet',
          format: 'google_sheet',
        },
      ],
      error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
  const driveExportMatch = endpoint.match(/^\/admin\/stats\/drive-export\/([^/]+)$/);
  if (driveExportMatch) {
    return {
      id: driveExportMatch[1],
      status: 'completed',
      scope: 'clients',
      period: 'all',
      formats: ['google_sheet'],
      links: [
        {
          title: 'Mock Drive export',
          url: 'https://drive.google.com/',
          id: 'mock-folder',
          format: 'folder',
        },
        {
          title: 'Клиенты - инфа',
          url: 'https://docs.google.com/spreadsheets/',
          id: 'mock-sheet',
          format: 'google_sheet',
        },
      ],
      error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }
  const clientStatsMatch = endpoint.match(/^\/admin\/stats\/clients\/(-?\d+)$/);
  if (clientStatsMatch) {
    const user = getMockUsers().find((item: any) => String(item.telegram_id) === clientStatsMatch[1]) || getMockUsers()[0];
    const payload = buildMockPerformancePayload(getMockTakenBets(), normalizeMockPeriod(queryParams.get('period')));
    return {
      ...payload,
      user: {
        telegram_id: user.telegram_id,
        username: user.username,
        first_name: user.first_name,
        last_name: user.last_name,
        photo_url: user.photo_url,
        name: [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username || String(user.telegram_id),
        matches_remaining: user.matches_remaining,
        guarantee_active: user.guarantee_active,
        client_group: user.client_group,
        client_tag: user.client_tag,
      },
      situation: mockClientSituation(payload.summary),
      recent_results: payload.timeline.flatMap((month: any) => month.days).flatMap((day: any) => day.bets).slice(0, 5).map((bet: any) => bet.status),
      excluded_summary: mockSummary([]),
      excluded_bets: [],
    };
  }
  if (endpoint === '/users/me/preferences' && (!options.method || options.method === 'GET')) {
    return getMockPreferences();
  }
  if (endpoint === '/users/me/preferences' && options.method === 'PUT') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    return { status: 'success', preferences: saveMockPreferences(body) };
  }
  if (endpoint === '/users/me/referral') {
    return {
      referral_code: 'DEBUG2026',
      referral_link: 'https://t.me/debug?start=DEBUG2026',
      invited_count: 0,
      purchased_invited_count: 0,
      program_enabled: false,
      discount_enabled: true,
      discount_step_percent: 5,
      discount_max_percent: 100,
      referral_discount_percent: 0,
      match_reward_enabled: false,
      match_reward_count: 0,
    };
  }
  if (endpoint === '/users/me/payments') return [];
  if (endpoint === '/subscriptions/my-status') return { status: 'inactive' };
  if (endpoint === '/subscriptions/flats/current/configure' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const current = getMockFlatSubscription() || buildMockFlatSubscription();
    const configured = saveMockFlatSubscription({
      ...current,
      revision: Number(current.revision || 0) + 1,
      status: 'active',
      flat_amount_rub: body.flat_amount_rub,
      activated_at: current.activated_at || nowIso(),
      updated_at: nowIso(),
    });
    const attempts = readMockPaymentAttempts();
    Object.keys(attempts).forEach((attemptId) => {
      attempts[attemptId] = {
        ...attempts[attemptId],
        requires_flat_setup: false,
        flat_subscription: configured,
      };
    });
    saveMockPaymentAttempts(attempts);
    return configured;
  }
  if (endpoint === '/subscriptions/flats/current') {
    const subscription = getMockFlatSubscription();
    // `null` is the mock-router fallthrough sentinel, so wrap the valid nullable API response.
    return subscription === null ? Promise.resolve(null) : subscription;
  }
  if (endpoint === '/subscriptions/plans' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const createdPlan = {
      id: Math.max(0, ...MOCK_PLANS.map((plan) => plan.id)) + 1,
      name: body.name || 'Новый абонемент',
      duration_days: Number(body.duration_days || 0),
      match_count: Number(body.match_count || 0),
      entitlement_type: body.entitlement_type || 'flat',
      target_flats: body.target_flats ?? 3,
      price: Number(body.price || 0),
      price_stars: Number(body.price_stars || 0),
      currency: body.currency || 'RUB',
      is_active: body.is_active !== false,
      is_hidden: body.is_hidden === true,
      allowed_user_ids: Array.isArray(body.allowed_user_ids) ? body.allowed_user_ids : [],
    };
    MOCK_PLANS = [...MOCK_PLANS, createdPlan];
    return createdPlan;
  }
  if (/^\/subscriptions\/plans\/\d+\/allowlist$/.test(endpoint)) {
    const planId = Number(endpoint.split('/')[3]);
    const plan = MOCK_PLANS.find((candidate) => candidate.id === planId) as any;
    if (options.method === 'PUT') {
      const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
      MOCK_PLANS = MOCK_PLANS.map((candidate) => (
        candidate.id === planId ? { ...candidate, allowed_user_ids: body.allowed_user_ids || [] } : candidate
      ));
      return { plan_id: planId, is_hidden: Boolean(plan?.is_hidden), allowed_user_ids: body.allowed_user_ids || [] };
    }
    return { plan_id: planId, is_hidden: Boolean(plan?.is_hidden), allowed_user_ids: plan?.allowed_user_ids || [] };
  }
  if (endpoint.startsWith('/subscriptions/plans/') && options.method === 'PUT') {
    const planId = Number(endpoint.split('/').pop());
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    MOCK_PLANS = MOCK_PLANS.map((plan) => (plan.id === planId ? { ...plan, ...body } : plan));
    return MOCK_PLANS.find((plan) => plan.id === planId) || { status: 'success' };
  }
  if (endpoint.startsWith('/subscriptions/plans/') && options.method === 'DELETE') {
    const planId = Number(endpoint.split('/').pop());
    MOCK_PLANS = MOCK_PLANS.map((plan) => (plan.id === planId ? { ...plan, is_active: false } : plan));
    return { status: 'success' };
  }
  if (endpoint.startsWith('/subscriptions/plans')) return MOCK_PLANS;
  if (endpoint === '/subscriptions/buy' || endpoint === '/subscriptions/assign') {
    return { status: 'success', user: getMockUser() };
  }
  if (endpoint === '/payments/invoice') return { invoice_url: 'https://example.com/mock-invoice' };
  if (endpoint.startsWith('/payments/attempts/')) {
    const attemptId = endpoint.split('/').pop() || '';
    const attempt = readMockPaymentAttempts()[attemptId];
    if (!attempt) throw new Error('Платёжная попытка не найдена');
    return {
      ...attempt,
      flat_subscription: getMockFlatSubscription(),
    };
  }
  if (endpoint.startsWith('/payments/debug/complete-bet/')) return { status: 'success' };
  if (endpoint.startsWith('/payments/promo/validate')) {
    const code = new URLSearchParams(endpoint.split('?')[1] || '').get('code') || 'DEBUG10';
    if (code.toUpperCase().includes('MATCH')) {
      return { code: code.toUpperCase(), reward_type: 'matches', discount_percent: 0, matches_count: 3 };
    }
    return { valid: true, reward_type: 'discount', discount_percent: 10, matches_count: 0, code: 'DEBUG10' };
  }
  if (endpoint === '/payments/promo/redeem') {
    return {
      code: 'MATCH3',
      reward_type: 'matches',
      matches_added: 3,
      balance_before: 12,
      balance_after: 15,
    };
  }
  if (endpoint === '/payments/tegro/create') {
    return { mock: true, attempt_id: createMockPaymentAttempt('tegro') };
  }
  if (endpoint === '/payments/yookassa/create') {
    return { mock: true, attempt_id: createMockPaymentAttempt('yookassa') };
  }
  if (endpoint === '/payments/tegro/debug-complete' || endpoint === '/payments/yookassa/debug-complete') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const attempts = readMockPaymentAttempts();
    if (body.attempt_id && attempts[body.attempt_id]) {
      attempts[body.attempt_id] = {
        ...attempts[body.attempt_id],
        checkout_state: 'ready',
        status: 'succeeded',
        requires_flat_setup: true,
      };
      saveMockPaymentAttempts(attempts);
    }
    saveMockFlatSubscription(buildMockFlatSubscription());
    return { status: 'success' };
  }
  if (endpoint === '/marketing/marathon') return { current_day: 3, streak: 3, reward_available: true };
  if (endpoint === '/marketing/widgets') {
    const payload = getMockMarketingWidgets();
    return {
      configured: payload.configured,
      widgets: payload.configured ? payload.widgets.filter((widget: any) => widget.is_enabled) : [],
    };
  }
  if (endpoint === '/marketing/daily-spin') return { reward: 'Матч в подарок', matches_added: 1 };
  if (endpoint === '/marketing/swipe-candidate') {
    return {
      bet_id: 'mock-bet-1',
      match_name: 'Зенит - Спартак',
      bookmaker_name: 'Фонбет',
      coefficient: 1.92,
      options: ['П1', 'ТБ 2.5'],
    };
  }
  if (endpoint === '/marketing/swipe') return { match: true, discount: 10, promo_code: 'SWIPE10', message: 'Совпадение найдено' };
  if (endpoint === '/marketing/pvp-active') {
    return { id: 1, match_name: 'Зенит - Спартак', option_a: 'П1', option_b: 'ТБ 2.5', votes_a: 24, votes_b: 18, percent_a: 57, percent_b: 43 };
  }
  if (endpoint === '/marketing/pvp-vote') {
    return { id: 1, match_name: 'Зенит - Спартак', option_a: 'П1', option_b: 'ТБ 2.5', votes_a: 25, votes_b: 18, percent_a: 58, percent_b: 42, selected_option: 'П1', message: 'Голос учтен' };
  }
  if (endpoint === '/marketing/quiz-active') {
    return { id: 1, bet_id: 'mock-bet-1', discount_reward: 10, questions: [{ id: 'q1', question: 'Что важнее для value?', options: ['КФ выше рынка', 'Название команды', 'Время матча'] }] };
  }
  if (endpoint === '/marketing/quiz-submit') return { passed: true, score: 1, total: 1, discount: 10, promo_code: 'QUIZ10', message: 'Верно' };
  if (endpoint === '/crowd-bets/active') {
    return { id: 1, bet_id: 'mock-bet-1', target_amount: 5000, current_amount: 3200, status: 'funding', progress_percent: 64, is_participant: false };
  }
  if (endpoint.startsWith('/crowd-bets/') && endpoint.endsWith('/fund')) {
    return {
      crowd_bet: { id: 1, bet_id: 'mock-bet-1', target_amount: 5000, current_amount: 3200, status: 'funding', progress_percent: 64, is_participant: false },
      attempt_id: 'mock-crowd-attempt',
      invoice_url: 'https://example.com/mock-crowd-invoice',
      amount_xtr: 50,
      status: 'invoice_created',
    };
  }
  if (endpoint.startsWith('/bets/match/')) {
    return { home_score: 1, away_score: 0, minute: 67, status: 'live' };
  }
  if (endpoint.endsWith('/notes') && endpoint.startsWith('/bets/')) {
    if (options.method === 'POST') return { status: 'success' };
    return { text: '', emotion_score: 5 };
  }
  if (endpoint.endsWith('/buy-hint') && endpoint.startsWith('/bets/')) {
    return {
      bet_id: endpoint.split('/')[2],
      attempt_id: 'mock-hint-attempt',
      invoice_url: 'https://example.com/mock-hint-invoice',
      price_xtr: 20,
      status: 'invoice_created',
    };
  }
  if (endpoint.endsWith('/hint') && endpoint.startsWith('/bets/')) {
    return { bet_id: endpoint.split('/')[2], paid_xtr: 20, hint: 'Следите за движением линии за 20 минут до матча.', reveal_level: 'analysis_only' };
  }
  if (endpoint.endsWith('/take') && endpoint.startsWith('/bets/')) {
    const betId = endpoint.split('/')[2];
    const bets = getMockBets().map((bet: any) => (bet.id === betId ? { ...bet, is_taken: true } : bet));
    saveMockBets(bets);
    return { status: 'success' };
  }
  if (endpoint.endsWith('/odds-drop') && endpoint.startsWith('/bets/') && options.method === 'PUT') {
    const betId = endpoint.split('/')[2];
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    let updatedBet: any = null;
    const patchBet = (bet: any) => {
      if (bet.id !== betId) return bet;
      updatedBet = {
        ...bet,
        odds_dropped_to: body.odds_dropped_to ? Number(body.odds_dropped_to) : null,
      };
      return updatedBet;
    };
    saveMockBets(getMockBets().map(patchBet));
    saveMockForecastRequests(getMockForecastRequests().map((request: any) => (
      request.bet?.id === betId ? { ...request, bet: patchBet(request.bet) } : request
    )));
    return updatedBet;
  }
  if (endpoint.endsWith('/odds-drop/notify') && endpoint.startsWith('/bets/') && options.method === 'POST') {
    const betId = endpoint.split('/')[2];
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    let updatedBet: any = null;
    const notifiedAt = nowIso();
    const patchBet = (bet: any) => {
      if (bet.id !== betId) return bet;
      updatedBet = {
        ...bet,
        odds_dropped_to: Number(body.odds_dropped_to),
        odds_drop_notified_at: notifiedAt,
      };
      return updatedBet;
    };
    saveMockBets(getMockBets().map(patchBet));
    saveMockForecastRequests(getMockForecastRequests().map((request: any) => (
      request.bet?.id === betId ? { ...request, bet: patchBet(request.bet) } : request
    )));
    return {
      bet: updatedBet,
      total: 3,
      sent: 0,
      queued: 3,
      failed: 0,
      errors: [],
    };
  }
  if (endpoint.endsWith('/resolve') && endpoint.startsWith('/bets/')) {
    const betId = endpoint.split('/')[2];
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    let resolvedBet: any = null;
    const nextStatus = body.status ?? 'win';
    const bets = getMockBets().map((bet: any) => {
      if (bet.id !== betId) return bet;
      resolvedBet = { ...bet, status: nextStatus, resolved_at: nowIso() };
      return resolvedBet;
    });
    saveMockBets(bets);
    const requests = getMockForecastRequests().map((request: any) => {
      if (request.bet?.id !== betId) return request;
      resolvedBet = { ...request.bet, status: nextStatus, resolved_at: nowIso() };
      return { ...request, bet: resolvedBet };
    });
    saveMockForecastRequests(requests);
    return {
      ...(resolvedBet || {}),
      status: nextStatus,
      guarantee_count: nextStatus === 'loss' ? 3 : 0,
      refund_count: nextStatus === 'refund' ? 3 : 0,
    };
  }
  if (endpoint === '/bets' || endpoint === '/bets/with-coupon') {
    const body = options.body instanceof FormData ? options.body : null;
    const selectedBookmakerIds = body
      ? body.getAll('bookmaker_ids')
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value) && value > 0)
      : [];
    const rawFallbackBookmakerId = body?.get('bookmaker_id');
    const fallbackBookmakerId = rawFallbackBookmakerId ? Number(rawFallbackBookmakerId) : null;
    const bookmakerIds = selectedBookmakerIds.length
      ? selectedBookmakerIds
      : fallbackBookmakerId && Number.isFinite(fallbackBookmakerId)
        ? [fallbackBookmakerId]
        : body
          ? []
          : getMockBookmakerIds().slice(0, 2);
    const couponImage = body?.get('coupon_image');

    const nextBet = buildMockBet(`mock-bet-${Date.now()}`, {
      event_name: String(body?.get('event_name') || 'Закрытый прогноз'),
      coefficient: body?.get('coefficient') || 1.88,
      sport_type: body?.get('sport_type') ? String(body.get('sport_type')) : null,
      outcome: body?.get('outcome') ? String(body.get('outcome')) : 'Тестовый исход',
      description: body?.get('description') ? String(body.get('description')) : undefined,
      category: String(body?.get('category') || 'prematch'),
      live_ends_at: body?.get('live_ends_at') ? String(body.get('live_ends_at')) : null,
      price_stars: body?.get('price_stars') ? Number(body.get('price_stars')) : null,
      coupon_image_url: couponImage ? '/static/coupons/mock-feed-coupon.png' : null,
      bookmaker_links: readMockBookmakerLinks(body),
      bookmakerIds,
    });
    const bets = [nextBet, ...getMockBets()];
    saveMockBets(bets);
    return nextBet;
  }
  if (endpoint === '/admin/web-chat/stream-ticket' && options.method === 'POST') {
    return { ticket: `mock-chat-ticket-${Date.now()}`, expires_in: 30 };
  }
  if (endpoint === '/chat/admin/conversations') {
    const status = queryParams.get('status') || 'open';
    const limit = Math.max(1, Number(queryParams.get('limit')) || 50);
    const messages = getMockSupportMessages();
    const latestByUserId = new Map<number, any>();
    [...messages]
      .sort((left: any, right: any) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
      .forEach((message: any) => {
        if (!latestByUserId.has(message.user_id)) latestByUserId.set(message.user_id, message);
      });
    const items = getMockUsers()
      .filter((user: any) => user.role === 'user' && latestByUserId.has(user.telegram_id))
      .map((user: any) => mockConversationFromUser(user, latestByUserId.get(user.telegram_id), status))
      .sort((left: any, right: any) => {
        const leftTime = left.last_message_at ? new Date(left.last_message_at).getTime() : 0;
        const rightTime = right.last_message_at ? new Date(right.last_message_at).getTime() : 0;
        return rightTime - leftTime;
      })
      .slice(0, limit);
    return { items, next_before: null, has_more: false };
  }
  const chatAdminByUserMatch = endpoint.match(/^\/chat\/admin\/conversations\/by-user\/(-?\d+)$/);
  if (chatAdminByUserMatch && options.method === 'POST') {
    const userId = Number(chatAdminByUserMatch[1]);
    const user = getMockUsers().find((item: any) => item.telegram_id === userId);
    if (!user) throw new Error('Клиент не найден');
    const latestMessage = getMockSupportMessages()
      .filter((message: any) => message.user_id === user.telegram_id)
      .sort((left: any, right: any) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())[0] || null;
    return mockConversationFromUser(user, latestMessage);
  }
  const chatAdminMessagesMatch = endpoint.match(/^\/chat\/admin\/conversations\/([^/]+)\/messages$/);
  if (chatAdminMessagesMatch && (!options.method || options.method === 'GET')) {
    const conversationId = chatAdminMessagesMatch[1];
    const user = getMockUsers().find((item: any) => mockConversationId(item.telegram_id) === conversationId);
    const items = getMockSupportMessages()
      .filter((message: any) => message.user_id === user?.telegram_id)
      .sort((left: any, right: any) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime())
      .map(mockSupportMessageToChatMessage);
    return { items, next_before_id: null, has_more: false };
  }
  if (chatAdminMessagesMatch && options.method === 'POST') {
    const conversationId = chatAdminMessagesMatch[1];
    const user = getMockUsers().find((item: any) => mockConversationId(item.telegram_id) === conversationId);
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const message = buildMockSupportMessage(user?.telegram_id || 123456789, String(body.text || ''), 'staff', {
      client_message_id: body.client_message_id || `mock-${Date.now()}`,
      reply_to_id: body.reply_to_id ?? null,
    });
    saveMockSupportMessages([...getMockSupportMessages(), message]);
    return mockSupportMessageToChatMessage(message);
  }
  const chatAdminAttachmentsMatch = endpoint.match(/^\/chat\/admin\/conversations\/([^/]+)\/attachments$/);
  if (chatAdminAttachmentsMatch && options.method === 'POST') {
    const conversationId = chatAdminAttachmentsMatch[1];
    const user = getMockUsers().find((item: any) => mockConversationId(item.telegram_id) === conversationId);
    const formData = options.body instanceof FormData ? options.body : null;
    const file = formData?.get('file') as File | null;
    const messageType = String(formData?.get('message_type') || 'file');
    const message = buildMockSupportMessage(user?.telegram_id || 123456789, String(formData?.get('text') || ''), 'staff', {
      client_message_id: String(formData?.get('client_message_id') || `mock-${Date.now()}`),
      reply_to_id: Number(formData?.get('reply_to_id')) || null,
      message_type: messageType,
      payload: {
        original_filename: file?.name || 'attachment.bin',
        mime_type: file?.type || 'application/octet-stream',
        size_bytes: file?.size || 1,
      },
    });
    saveMockSupportMessages([...getMockSupportMessages(), message]);
    return mockSupportMessageToChatMessage(message);
  }
  const chatAdminReadMatch = endpoint.match(/^\/chat\/admin\/conversations\/([^/]+)\/read$/);
  if (chatAdminReadMatch && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    return { status: 'ok', last_read_message_id: body.last_read_message_id ?? null, last_read_signal_id: null };
  }
  const chatAdminStatusMatch = endpoint.match(/^\/chat\/admin\/conversations\/([^/]+)\/status$/);
  if (chatAdminStatusMatch && options.method === 'POST') {
    const conversationId = chatAdminStatusMatch[1];
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const user = getMockUsers().find((item: any) => mockConversationId(item.telegram_id) === conversationId);
    const latestMessage = getMockSupportMessages()
      .filter((message: any) => message.user_id === user?.telegram_id)
      .sort((left: any, right: any) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())[0] || null;
    return mockConversationFromUser(user || getMockUser(), latestMessage, body.status || 'open');
  }
  const chatAdminTypingMatch = endpoint.match(/^\/chat\/admin\/conversations\/([^/]+)\/typing$/);
  if (chatAdminTypingMatch && options.method === 'POST') return { status: 'ok' };
  if (endpoint === '/admin/web-chat/threads') {
    const cleanQ = (queryParams.get('q') || '').trim().toLowerCase();
    const limit = Math.max(1, Number(queryParams.get('limit')) || 40);
    const messages = getMockSupportMessages();
    const latestByUserId = new Map<number, any>();
    [...messages]
      .sort((left: any, right: any) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
      .forEach((message: any) => {
        if (!latestByUserId.has(message.user_id)) latestByUserId.set(message.user_id, message);
      });

    const users = getMockUsers().filter((user: any) => {
      if (user.role !== 'user') return false;
      if (!cleanQ) return latestByUserId.has(user.telegram_id);
      const searchable = [
        user.telegram_id,
        user.username,
        user.first_name,
        user.last_name,
        user.client_group,
        user.client_tag,
        latestByUserId.get(user.telegram_id)?.text,
      ].join(' ').toLowerCase();
      return searchable.includes(cleanQ);
    });
    const items = users
      .map((user: any) => mockThreadFromUser(user, latestByUserId.get(user.telegram_id) || null))
      .sort((left: any, right: any) => {
        const leftTime = left.last_message_created_at ? new Date(left.last_message_created_at).getTime() : 0;
        const rightTime = right.last_message_created_at ? new Date(right.last_message_created_at).getTime() : 0;
        return rightTime - leftTime;
      })
      .slice(0, limit);
    return { items };
  }
  const adminWebChatMessagesMatch = endpoint.match(/^\/admin\/web-chat\/users\/(-?\d+)\/messages$/);
  if (adminWebChatMessagesMatch && (!options.method || options.method === 'GET')) {
    const userId = Number(adminWebChatMessagesMatch[1]);
    return getMockSupportMessages()
      .filter((message: any) => message.user_id === userId)
      .sort((left: any, right: any) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
  }
  if (adminWebChatMessagesMatch && options.method === 'POST') {
    const userId = Number(adminWebChatMessagesMatch[1]);
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    const message = buildMockSupportMessage(userId, String(body.text || ''), 'staff');
    const messages = [...getMockSupportMessages(), message];
    saveMockSupportMessages(messages);
    return message;
  }
  if (endpoint === '/admin/users-page') {
    const cleanQ = String(queryParams.get('q') || '').trim().toLowerCase();
    const activity = queryParams.get('activity') || 'all';
    const group = queryParams.get('group') || 'all';
    const tag = queryParams.get('tag') || 'all';
    const limit = Math.max(1, Number(queryParams.get('limit')) || 50);
    const items = getMockUsers().filter((user: any) => {
      if (user.role !== 'user') return false;
      const balance = (user.purchased_bets_balance || 0) !== 0
        ? user.purchased_bets_balance
        : user.matches_remaining || 0;
      const searchable = [
        user.first_name,
        user.last_name,
        user.username,
        user.telegram_id,
        user.client_group,
        user.client_tag,
        user.other_bookmaker_name,
      ].join(' ').toLowerCase();
      const matchesSearch = !cleanQ || searchable.includes(cleanQ);
      const matchesActivity = activity === 'all'
        || (activity === 'active' && user.has_active_subscription)
        || (activity === 'empty' && !user.guarantee_active && balance <= 0)
        || (activity === 'guarantee' && user.guarantee_active);
      const matchesGroup = group === 'all' || user.client_group === group;
      const matchesTag = tag === 'all' || user.client_tag === tag;
      return matchesSearch && matchesActivity && matchesGroup && matchesTag;
    });
    return {
      items: items.slice(0, limit),
      next_cursor: null,
      has_more: false,
      total: getMockUsers().filter((user: any) => user.role === 'user').length,
      filtered_total: items.length,
    };
  }
  if (endpoint === '/admin/users') return getMockUsers().filter((user: any) => user.role === 'user');
  if (endpoint === '/admin/admins') return getMockUsers().filter((user: any) => user.role !== 'user');
  if (endpoint === '/admin/custom-emojis') return [];
  if (endpoint === '/admin/custom-emojis/refresh' && options.method === 'POST') return [];
  const adminFlatDetailsMatch = endpoint.match(/^\/admin\/users\/(-?\d+)\/flat-subscription$/);
  if (adminFlatDetailsMatch && (!options.method || options.method === 'GET')) {
    const userId = Number(adminFlatDetailsMatch[1]);
    return getMockUsers().find((user: any) => user.telegram_id === userId)?.flat_subscription ?? null;
  }
  const adminFlatPreviewMatch = endpoint.match(/^\/admin\/users\/(-?\d+)\/flat-subscriptions\/([^/]+)\/flat-amount\/preview$/);
  if (adminFlatPreviewMatch && options.method === 'POST') {
    const userId = Number(adminFlatPreviewMatch[1]);
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const subscription = getMockUsers().find((user: any) => user.telegram_id === userId)?.flat_subscription || buildMockFlatSubscription({ user_id: userId, status: 'active', flat_amount_rub: '10000.00' });
    const nextFlatAmount = Number(body.flat_amount_rub);
    const profitRub = Number(subscription.profit_rub || 0);
    return {
      kind: 'flat_amount',
      flat_subscription_id: subscription.id,
      current_revision: Number(subscription.revision || 1),
      affected_bets: subscription.bets?.length || 0,
      before: {
        flat_amount_rub: subscription.flat_amount_rub,
        profit_rub: subscription.profit_rub,
        profit_flats: subscription.profit_flats,
        status: subscription.status,
      },
      after: {
        flat_amount_rub: body.flat_amount_rub,
        profit_rub: subscription.profit_rub,
        profit_flats: nextFlatAmount > 0 ? (profitRub / nextFlatAmount).toFixed(6) : subscription.profit_flats,
        status: subscription.status,
      },
    };
  }
  const adminFlatApplyMatch = endpoint.match(/^\/admin\/users\/(-?\d+)\/flat-subscriptions\/([^/]+)\/flat-amount$/);
  if (adminFlatApplyMatch && options.method === 'PATCH') {
    const userId = Number(adminFlatApplyMatch[1]);
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const currentSubscription = getMockUsers().find((user: any) => user.telegram_id === userId)?.flat_subscription;
    if (!String(body.note || '').trim()) throw new Error('Укажите причину финансовой корректировки');
    if (Number(body.expected_revision) !== Number(currentSubscription?.revision)) {
      throw new Error('Данные абонемента изменились. Обновите карточку и повторите проверку');
    }
    let updatedSubscription: any = null;
    const users = getMockUsers().map((user: any) => {
      if (user.telegram_id !== userId || !user.flat_subscription) return user;
      const profitRub = Number(user.flat_subscription.profit_rub || 0);
      const flatAmountRub = Number(body.flat_amount_rub);
      updatedSubscription = {
        ...user.flat_subscription,
        revision: Number(user.flat_subscription.revision || 0) + 1,
        flat_amount_rub: body.flat_amount_rub,
        profit_flats: flatAmountRub > 0 ? (profitRub / flatAmountRub).toFixed(6) : user.flat_subscription.profit_flats,
        updated_at: nowIso(),
      };
      return { ...user, flat_subscription: updatedSubscription };
    });
    saveMockUsers(users);
    return updatedSubscription;
  }
  const adminStakePreviewMatch = endpoint.match(/^\/admin\/users\/(-?\d+)\/bets\/([^/]+)\/stake\/preview$/);
  if (adminStakePreviewMatch && options.method === 'POST') {
    const userId = Number(adminStakePreviewMatch[1]);
    const betId = adminStakePreviewMatch[2];
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const subscription = getMockUsers().find((user: any) => user.telegram_id === userId)?.flat_subscription;
    const bet = subscription?.bets?.find((item: any) => item.bet_id === betId);
    const nextStake = Number(body.stake_rub);
    const coefficient = Number(bet?.coefficient || 1);
    const nextProfitRub = bet?.status === 'win' ? nextStake * (coefficient - 1) : bet?.status === 'loss' ? -nextStake : 0;
    const flatAmount = Number(subscription?.flat_amount_rub || 1);
    return {
      kind: 'stake',
      flat_subscription_id: subscription?.id,
      bet_id: betId,
      current_revision: Number(subscription?.revision || 1),
      affected_bets: 1,
      before: {
        stake_rub: bet?.stake_rub || 0,
        profit_rub: subscription?.profit_rub || 0,
        profit_flats: subscription?.profit_flats || 0,
        status: subscription?.status || 'active',
      },
      after: {
        stake_rub: body.stake_rub,
        profit_rub: nextProfitRub.toFixed(2),
        profit_flats: (nextProfitRub / flatAmount).toFixed(6),
        status: subscription?.status || 'active',
      },
    };
  }
  const adminStakeApplyMatch = endpoint.match(/^\/admin\/users\/(-?\d+)\/bets\/([^/]+)\/stake$/);
  if (adminStakeApplyMatch && options.method === 'PATCH') {
    const userId = Number(adminStakeApplyMatch[1]);
    const betId = adminStakeApplyMatch[2];
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const currentSubscription = getMockUsers().find((user: any) => user.telegram_id === userId)?.flat_subscription;
    if (!String(body.note || '').trim()) throw new Error('Укажите причину финансовой корректировки');
    if (Number(body.expected_revision) !== Number(currentSubscription?.revision)) {
      throw new Error('Данные абонемента изменились. Обновите карточку и повторите проверку');
    }
    let updatedSubscription: any = null;
    const users = getMockUsers().map((user: any) => {
      if (user.telegram_id !== userId || !user.flat_subscription) return user;
      updatedSubscription = {
        ...user.flat_subscription,
        revision: Number(user.flat_subscription.revision || 0) + 1,
        bets: (user.flat_subscription.bets || []).map((bet: any) => bet.bet_id === betId ? { ...bet, stake_rub: body.stake_rub } : bet),
        updated_at: nowIso(),
      };
      return { ...user, flat_subscription: updatedSubscription };
    });
    saveMockUsers(users);
    return updatedSubscription;
  }
  if (endpoint.startsWith('/admin/users/') && options.method === 'DELETE') {
    const userId = Number(endpoint.split('/')[3]);
    const users = getMockUsers().filter((user: any) => user.telegram_id !== userId);
    saveMockUsers(users);
    return { status: 'success', deleted_user_id: userId };
  }
  if (endpoint.startsWith('/admin/users/') && options.method === 'PUT') {
    const userId = Number(endpoint.split('/')[3]);
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    const users = getMockUsers().map((user: any) => {
      if (user.telegram_id !== userId) return user;

      const bookmakerIds = Array.isArray(body.bookmaker_ids)
        ? body.bookmaker_ids
        : user.bookmakers.map((bookmaker: any) => bookmaker.id);
      const matchesDelta = Number.isInteger(body.matches_delta) ? body.matches_delta : 0;
      const currentMatches = (user.purchased_bets_balance || 0) !== 0
        ? user.purchased_bets_balance
        : user.matches_remaining || 0;
      const nextMatches = Math.max(0, currentMatches + matchesDelta);
      const nextGuarantee = body.close_guarantee ? false : user.guarantee_active;
      const selectedBookmakers = MOCK_BOOKMAKERS.filter((bookmaker) => bookmakerIds.includes(bookmaker.id));
      const hasOtherBookmaker = selectedBookmakers.some((bookmaker) => bookmaker.code === 'other');

      return {
        ...user,
        stats_display_mode: body.stats_display_mode ?? user.stats_display_mode,
        purchased_bets_balance: nextMatches,
        matches_remaining: nextMatches,
        guarantee_active: nextGuarantee,
        has_active_subscription: nextMatches > 0 || nextGuarantee,
        bookmakers: selectedBookmakers,
        other_bookmaker_name: hasOtherBookmaker ? body.other_bookmaker_name ?? null : null,
        client_group: body.client_group ?? null,
        client_tag: body.client_tag ?? null,
        updated_at: nowIso(),
      };
    });
    saveMockUsers(users);
    return users.find((user: any) => user.telegram_id === userId) || { status: 'success' };
  }
  if (endpoint === '/admin/admins/grant') return { status: 'success', user: getMockUser() };
  if (endpoint.startsWith('/admin/audit-log')) return [];
  if (endpoint === '/settings/theme') return getMockPublicThemeSettings();
  if (endpoint === '/users/me/presence' && options.method === 'POST') return { status: 'ok' };
  if (endpoint === '/admin/monitoring/online') return { online_users: 14 };
  if (endpoint === '/admin/monitoring/parser-status') {
    return { status: 'active', last_sync: nowIso() };
  }
  if (endpoint === '/admin/monitoring/logs') {
    const now = nowIso();
    return {
      logs: [
        `${now} [warning] Parser latency probe: bookmaker feed delayed by 1.4s`,
        `${now} [error] Mock error stream: no persistent error log source configured yet`,
        `${now} [info] Delivery outbox monitor heartbeat completed`,
      ],
    };
  }
  if (endpoint === '/admin/monitoring/summary') {
    const now = nowIso();
    return {
      generated_at: now,
      online: { online_users: 14 },
      health: { api: 'ok', database: 'ok', redis: 'ok' },
      delivery_outbox: { pending: 2, sent: 128, failed: 1, cancelled: 0 },
      payment_reconciliation: {
        generated_at: now,
        window_hours: 48,
        total_attempts_scanned: 24,
        total_issues: 1,
        provider_checks_included: false,
        by_code: { pending_stale: 1 },
        by_severity: { warning: 1 },
      },
      rate_limit: { active_windows: 3, blocked_keys: 0, last_reset_at: now },
      parser: { status: 'active', last_sync: now },
      audit: [
        { id: 3, actor_id: 987654321, action: 'system_settings_updated', created_at: now },
        { id: 2, actor_id: 987654321, action: 'message_template_updated', created_at: now },
      ],
    };
  }
  if (endpoint.startsWith('/admin/monitoring/diagnostic-report')) {
    return {
      summary: {
        generated_at: nowIso(),
        health: { api: 'ok', database: 'ok', redis: 'ok' },
      },
      logs: ['mock sanitized report line'],
    };
  }
  if (endpoint === '/admin/settings/reset-sessions' && options.method === 'POST') {
    return { status: 'success', deleted: 2 };
  }
  if (endpoint === '/admin/settings/integrations/unlock' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    if (!String(body.password || '').trim()) throw new Error('Введите пароль интеграций');
    return {
      ...getMockSystemSettings({ revealSecrets: true }),
      unlock_token: 'mock-integration-unlock-token',
      expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    };
  }
  if (endpoint === '/admin/settings/integrations/diagnostics' && options.method === 'POST') {
    const body = typeof options.body === 'string' ? JSON.parse(options.body || '{}') : {};
    if (!String(body.unlock_token || '').trim()) throw new Error('Сейф интеграций закрыт');
    const requestedGroup = String(body.group || '');
    const mockGroups: Record<string, string[]> = {
      telegram: ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET_TOKEN', 'TELEGRAM_VIP_CHAT_ID'],
      vk: ['VK_ACCESS_TOKEN', 'VK_CALLBACK_SECRET', 'VK_GROUP_ID'],
      payments: ['YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY', 'TEGRO_SHOP_ID', 'TEGRO_API_KEY'],
      webpush: ['WEB_PUSH_VAPID_PUBLIC_KEY', 'WEB_PUSH_VAPID_PRIVATE_KEY'],
      google: ['GOOGLE_DRIVE_AUTH_MODE', 'GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_SERVICE_ACCOUNT_JSON_B64'],
      urls: ['API_BASE_URL', 'FRONTEND_BASE_URL', 'SUPPORT_URL', 'VIP_CHANNEL_URL'],
    };
    const settings = getMockSystemSettings({ revealSecrets: true }).settings;
    const byKey = new Map(settings.map((setting: any) => [setting.key, setting]));
    const groups = Object.entries(mockGroups)
      .filter(([group]) => !requestedGroup || group === requestedGroup)
      .map(([group, keys]) => {
        const checks = keys.map((key) => {
          const setting: any = byKey.get(key);
          const configured = Boolean(setting?.is_configured || String(setting?.value || '').trim());
          return {
            key,
            label: key,
            status: configured ? 'ok' : 'missing',
            configured,
            is_secret: MOCK_SECRET_SETTING_KEYS.has(key),
            message: configured ? 'Значение найдено, секрет не раскрывается.' : 'Значение не задано.',
          };
        });
        const missing = checks.filter(check => check.status === 'missing').length;
        return {
          group,
          status: missing === 0 ? 'ok' : missing === checks.length ? 'missing' : 'warning',
          checks,
        };
      });
    return {
      overall_status: groups.every(group => group.status === 'ok') ? 'ok' : 'warning',
      generated_at: nowIso(),
      groups,
    };
  }
  if (endpoint === '/admin/settings') {
    if (options.method === 'PUT') {
      const body = typeof options.body === 'string' ? JSON.parse(options.body) : [];
      return saveMockSystemSettings(Array.isArray(body) ? body : []);
    }
    return getMockSystemSettings();
  }
  if (endpoint === '/admin/referrals/summary') {
    const settings = getMockSystemSettings().settings;
    const byKey = new Map(settings.map((setting: any) => [setting.key, setting.value]));
    return {
      program_enabled: byKey.get('REFERRAL_PROGRAM_ENABLED') === 'true',
      discount_enabled: byKey.get('REFERRAL_DISCOUNT_ENABLED') !== 'false',
      discount_step_percent: Number(byKey.get('REFERRAL_DISCOUNT_STEP_PERCENT') || 5),
      discount_max_percent: Number(byKey.get('REFERRAL_DISCOUNT_MAX_PERCENT') || 100),
      match_reward_enabled: byKey.get('REFERRAL_MATCH_REWARD_ENABLED') === 'true',
      match_reward_count: Number(byKey.get('REFERRAL_MATCH_REWARD_COUNT') || 0),
      invited_count: 0,
      qualified_purchase_events: 0,
      active_referrers: 0,
      matches_awarded_total: 0,
      held_events: 0,
      rejected_events: 0,
      recent_events: [],
    };
  }
  if (endpoint === '/admin/marketing/widgets') {
    if (options.method === 'PATCH') {
      const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
      return saveMockMarketingWidgets(Array.isArray(body.configs) ? body.configs : []);
    }
    return getMockMarketingWidgets();
  }
  if (endpoint.startsWith('/admin/marketing/risk-queue/')) {
    return { status: endpoint.endsWith('/reject') ? 'rejected' : 'approved' };
  }
  if (endpoint === '/admin/marketing/risk-queue') {
    return { items: [], held_count: 0 };
  }
  if (endpoint.startsWith('/admin/marketing/reward-events')) {
    return { events: [] };
  }
  if (endpoint === '/admin/message-templates') return getMockMessageTemplates();
  if (endpoint.startsWith('/admin/message-templates/')) {
    const match = endpoint.match(/^\/admin\/message-templates\/([^/]+)(?:\/reset)?$/);
    const templateKey = decodeURIComponent(match?.[1] || '');
    if (endpoint.endsWith('/reset') && options.method === 'POST') {
      return resetMockMessageTemplate(templateKey);
    }
    if (options.method === 'PUT') {
      const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
      return saveMockMessageTemplate(templateKey, String(body.body || ''));
    }
  }
  if (endpoint.startsWith('/admin/announcements/audience-count')) return { total_audience: 18, count: 18 };
  if (endpoint === '/admin/announcements' || endpoint === '/admin/broadcast') {
    return {
      sent: 18,
      failed: 0,
      status: 'success',
      delivery: {
        sent: 18,
        failed: 0,
        telegram: { sent: 12, failed: 0 },
        vk_messages: { sent: 6, failed: 0 },
        vk_notifications: { requested: 4, sent: 0, failed: 0 },
      },
    };
  }
  if (endpoint === '/admin/promo/list') return [];
  if (endpoint === '/admin/promo' || endpoint.startsWith('/admin/promo/')) return { status: 'success' };
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(options.method ?? '')) {
    return { status: 'success' };
  }

  return null;
}
