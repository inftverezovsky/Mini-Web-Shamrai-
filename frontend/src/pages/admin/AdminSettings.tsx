import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { apiFetch, downloadApiFile } from '../../utils/api';
import type { MessageTemplateResponse } from '../../schemas/schemas';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';
import SmoothCollapse from '../../components/SmoothCollapse';
import {
  BRAND_BACKGROUND_URL_KEY,
  BRAND_LOGO_URL_KEY,
  DEFAULT_THEME_SETTINGS,
  GLOBAL_PERFORMANCE_MODE_KEY,
  PUBLIC_THEME_QUERY_KEY,
  THEME_DENSITY_KEY,
  THEME_FONT_SCALE_KEY,
  THEME_GLASS_BLUR_PX_KEY,
  THEME_GLASS_OPACITY_KEY,
  THEME_GLOW_STRENGTH_KEY,
  THEME_PRIMARY_COLOR_KEY,
  THEME_RADIUS_SCALE_KEY,
  THEME_SECONDARY_COLOR_KEY,
  normalizeHexColor,
} from '../../features/settings/themeSettings';
import {
  createCollapsedSectionState,
  setAllSectionsOpen,
  toggleSection,
  type SettingsAccordionState,
} from '../../features/settings/settingsAccordion';
import {
  Activity,
  AlertTriangle,
  BellRing,
  Braces,
  Check,
  ChevronDown,
  Clock3,
  Cloud,
  CreditCard,
  Database,
  Download,
  Eye,
  EyeOff,
  Globe2,
  Info,
  KeyRound,
  Link2,
  LockKeyhole,
  Loader2,
  MessageCircle,
  MonitorSmartphone,
  Palette,
  Plug,
  Power,
  RotateCcw,
  Save,
  Search,
  Send,
  Settings2,
  ShieldAlert,
  Sparkles,
  ToggleLeft,
  type LucideIcon,
} from 'lucide-react';

type ChannelGroupId = 'telegram' | 'vk' | 'site';
type SettingsTabId = 'texts' | 'switches' | 'integrations' | 'theme' | 'monitoring';

interface ChannelGroupConfig {
  id: ChannelGroupId;
  title: string;
  subtitle: string;
  badge: string;
  Icon: LucideIcon;
  templateKeys: string[];
}

interface SettingsTabConfig {
  id: SettingsTabId;
  title: string;
  subtitle: string;
  badge: string;
  Icon: LucideIcon;
}

type SwitchSettingKey = 'MAINTENANCE_MODE' | 'DISABLE_REGISTRATIONS' | 'PAUSE_BROADCASTS';
type SecretSettingKey = string;
type SystemSettingKey = string;

interface AdminSettingResponse {
  key: SystemSettingKey;
  value: string;
  description: string | null;
  is_secret: boolean;
  is_configured: boolean;
  updated_at: string | null;
}

interface AdminSettingsResponse {
  settings: AdminSettingResponse[];
}

interface IntegrationUnlockResponse extends AdminSettingsResponse {
  unlock_token: string;
  expires_at: string;
}

interface ResetSessionsResponse {
  status: string;
  deleted: number;
}

type AdminSettingsFormValues = Record<SystemSettingKey, string | boolean>;

interface SwitchSettingConfig {
  key: SwitchSettingKey;
  title: string;
  description: string;
  badge: string;
  tone: 'cyan' | 'amber';
}

type IntegrationFieldKind = 'secret' | 'text' | 'url' | 'boolean';

interface IntegrationFieldConfig {
  key: string;
  label: string;
  description: string;
  kind: IntegrationFieldKind;
  placeholder?: string;
}

interface IntegrationGroupConfig {
  id: string;
  title: string;
  subtitle: string;
  Icon: LucideIcon;
  fields: IntegrationFieldConfig[];
}

interface ThemeColorFieldConfig {
  key: typeof THEME_PRIMARY_COLOR_KEY | typeof THEME_SECONDARY_COLOR_KEY;
  label: string;
  description: string;
  fallback: string;
}

interface ThemeTextFieldConfig {
  key: typeof BRAND_LOGO_URL_KEY | typeof BRAND_BACKGROUND_URL_KEY;
  label: string;
  description: string;
  placeholder: string;
}

interface ThemeNumberFieldConfig {
  key:
    | typeof THEME_GLASS_OPACITY_KEY
    | typeof THEME_GLASS_BLUR_PX_KEY
    | typeof THEME_RADIUS_SCALE_KEY
    | typeof THEME_FONT_SCALE_KEY
    | typeof THEME_GLOW_STRENGTH_KEY;
  label: string;
  description: string;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}

interface OnlineMonitoringResponse {
  online_users: number;
}

interface ParserStatusResponse {
  status: 'active' | 'error';
  last_sync: string;
}

interface MonitoringLogsResponse {
  logs: string[];
}

interface MonitoringSummaryResponse {
  generated_at: string;
  online: { online_users: number };
  health: Record<string, string | null>;
  delivery_outbox: Record<string, any>;
  payment_reconciliation?: {
    generated_at: string;
    window_hours: number;
    total_attempts_scanned: number;
    total_issues: number;
    provider_checks_included: boolean;
    by_code: Record<string, number>;
    by_severity: Record<string, number>;
  };
  rate_limit: Record<string, any>;
  parser: { status: string; last_sync: string };
  audit: Array<Record<string, any>>;
}

interface IntegrationUnlockRequest {
  password: string;
}

interface IntegrationDiagnosticsResponse {
  overall_status: 'ok' | 'warning' | 'error' | 'missing';
  generated_at: string;
  groups: Array<{
    group: string;
    status: 'ok' | 'warning' | 'error' | 'missing';
    checks: Array<{
      key: string;
      label: string;
      status: 'ok' | 'warning' | 'error' | 'missing';
      configured: boolean;
      is_secret: boolean;
      message: string;
    }>;
  }>;
}

type TemplateEditorToken =
  | { type: 'text'; value: string }
  | { type: 'variable'; raw: string; key: string; label: string; example: string }
  | { type: 'markup'; raw: string };

const ADMIN_SETTINGS_QUERY_KEY = ['admin-settings'] as const;

const SETTINGS_TABS: SettingsTabConfig[] = [
  {
    id: 'texts',
    title: 'Тексты сообщений',
    subtitle: 'Telegram, VK, сайт',
    badge: 'тексты',
    Icon: MessageCircle,
  },
  {
    id: 'switches',
    title: 'Системные рубильники',
    subtitle: 'Флаги и режимы',
    badge: 'режимы',
    Icon: ToggleLeft,
  },
  {
    id: 'integrations',
    title: 'Интеграции',
    subtitle: 'Внешние сервисы',
    badge: 'api',
    Icon: Plug,
  },
  {
    id: 'theme',
    title: 'Визуал',
    subtitle: 'Тема и оформление',
    badge: 'ui',
    Icon: Palette,
  },
  {
    id: 'monitoring',
    title: 'Мониторинг',
    subtitle: 'Состояние системы',
    badge: 'live',
    Icon: Activity,
  },
];

const SWITCH_SETTINGS: SwitchSettingConfig[] = [
  {
    key: 'MAINTENANCE_MODE',
    title: 'Режим обслуживания',
    description: 'Временно ограничивает пользовательские сценарии во время работ.',
    badge: 'service',
    tone: 'cyan',
  },
  {
    key: 'DISABLE_REGISTRATIONS',
    title: 'Закрытый клуб: Запрет регистраций',
    description: 'Новые пользователи не смогут пройти регистрацию, текущие клиенты останутся внутри.',
    badge: 'club',
    tone: 'cyan',
  },
];

const INTEGRATION_GROUPS: IntegrationGroupConfig[] = [
  {
    id: 'telegram',
    title: 'Telegram',
    subtitle: 'бот, webhook и служебные чаты',
    Icon: Send,
    fields: [
      { key: 'TELEGRAM_BOT_TOKEN', label: 'Telegram Bot Token', description: 'Токен рабочего Telegram-бота.', kind: 'secret', placeholder: 'Введите токен бота' },
      { key: 'TELEGRAM_WEBHOOK_SECRET_TOKEN', label: 'Webhook Secret Token', description: 'Секрет проверки Telegram webhook.', kind: 'secret', placeholder: 'Введите webhook secret' },
      { key: 'TELEGRAM_BOT_USERNAME', label: 'Bot Username', description: 'Username бота без обязательного @.', kind: 'text', placeholder: 'Shamra1_bot' },
      { key: 'TELEGRAM_VIP_CHAT_ID', label: 'VIP Chat ID', description: 'ID закрытого VIP-чата или канала.', kind: 'text', placeholder: '-100...' },
      { key: 'TELEGRAM_ADMIN_GROUP_CHAT_ID', label: 'Admin Group Chat ID', description: 'ID группы для админ-уведомлений.', kind: 'text', placeholder: '-100...' },
      { key: 'SALES_MANAGER_TELEGRAM_ID', label: 'Sales Manager ID', description: 'Telegram ID менеджера продаж.', kind: 'text', placeholder: '123456789' },
      { key: 'SHAMRAI_ONBOARDING_REPORT_CHAT_ID', label: 'Onboarding Report Chat ID', description: 'Куда отправлять onboarding-отчеты.', kind: 'text', placeholder: '-100...' },
    ],
  },
  {
    id: 'vk',
    title: 'VK и VK ID',
    subtitle: 'доставка, callback и OAuth',
    Icon: Plug,
    fields: [
      { key: 'VK_ACCESS_TOKEN', label: 'VK Group Access Token', description: 'Токен группы VK для сообщений и рассылок.', kind: 'secret', placeholder: 'Введите токен группы' },
      { key: 'VK_CALLBACK_CONFIRMATION_CODE', label: 'Callback Confirmation Code', description: 'Код подтверждения VK Callback API.', kind: 'secret', placeholder: 'Код подтверждения' },
      { key: 'VK_CALLBACK_SECRET', label: 'Callback Secret', description: 'Секретный ключ VK Callback API.', kind: 'secret', placeholder: 'Секрет callback' },
      { key: 'VK_ID_CLIENT_SECRET', label: 'VK ID Client Secret', description: 'Client secret для VK ID OAuth.', kind: 'secret', placeholder: 'Client secret' },
      { key: 'VK_ID_APP_ID', label: 'VK ID App ID', description: 'Идентификатор приложения VK ID.', kind: 'text', placeholder: '54626979' },
      { key: 'VK_ID_REDIRECT_URI', label: 'VK ID Redirect URI', description: 'Redirect URI для авторизации VK ID.', kind: 'url', placeholder: 'https://shamra1.pro' },
      { key: 'VK_GROUP_ID', label: 'VK Group ID', description: 'ID сообщества VK.', kind: 'text', placeholder: '239419819' },
      { key: 'VK_API_VERSION', label: 'VK API Version', description: 'Версия VK API для запросов.', kind: 'text', placeholder: '5.199' },
    ],
  },
  {
    id: 'payments',
    title: 'Платежи',
    subtitle: 'YooKassa, Tegro и возвраты',
    Icon: CreditCard,
    fields: [
      { key: 'YOOKASSA_SHOP_ID', label: 'YooKassa Shop ID', description: 'ID магазина YooKassa.', kind: 'text', placeholder: 'shop_id' },
      { key: 'YOOKASSA_SECRET_KEY', label: 'YooKassa Secret Key', description: 'Секретный ключ YooKassa.', kind: 'secret', placeholder: 'Введите secret key' },
      { key: 'YOOKASSA_RETURN_URL', label: 'YooKassa Return URL', description: 'Куда возвращать клиента после оплаты.', kind: 'url', placeholder: 'https://shamra1.pro/app' },
      { key: 'TEGRO_SHOP_ID', label: 'Tegro Shop ID', description: 'ID магазина Tegro.', kind: 'text', placeholder: 'shop_id' },
      { key: 'TEGRO_API_KEY', label: 'Tegro API Key', description: 'API key Tegro.', kind: 'secret', placeholder: 'Введите API key' },
      { key: 'TEGRO_SECRET_KEY', label: 'Tegro Secret Key', description: 'Secret key Tegro.', kind: 'secret', placeholder: 'Введите secret key' },
      { key: 'TEGRO_RETURN_URL', label: 'Tegro Return URL', description: 'Куда возвращать клиента после Tegro.', kind: 'url', placeholder: 'https://shamra1.pro/app' },
      { key: 'TEGRO_API_BASE_URL', label: 'Tegro API Base URL', description: 'Базовый URL API Tegro.', kind: 'url', placeholder: 'https://tegro.money/api' },
      { key: 'PAYMENT_GATEWAY_TOKEN', label: 'Legacy Gateway Token', description: 'Резервный legacy-токен платежного шлюза.', kind: 'secret', placeholder: 'Legacy token' },
    ],
  },
  {
    id: 'webpush',
    title: 'Web Push',
    subtitle: 'VAPID ключи уведомлений',
    Icon: BellRing,
    fields: [
      { key: 'WEB_PUSH_VAPID_PUBLIC_KEY', label: 'VAPID Public Key', description: 'Публичный ключ web push.', kind: 'text', placeholder: 'Public key' },
      { key: 'WEB_PUSH_VAPID_PRIVATE_KEY', label: 'VAPID Private Key', description: 'Приватный ключ web push.', kind: 'secret', placeholder: 'Private key' },
      { key: 'WEB_PUSH_VAPID_SUBJECT', label: 'VAPID Subject', description: 'Контакт subject для push-сервиса.', kind: 'text', placeholder: 'mailto:support@shamra1.pro' },
    ],
  },
  {
    id: 'google',
    title: 'Google Drive',
    subtitle: 'экспорт статистики и OAuth',
    Icon: Cloud,
    fields: [
      { key: 'GOOGLE_DRIVE_STATS_ENABLED', label: 'Drive Stats Enabled', description: 'Включить экспорт статистики в Google Drive.', kind: 'boolean' },
      { key: 'GOOGLE_DRIVE_AUTH_MODE', label: 'Drive Auth Mode', description: 'Режим авторизации Google Drive.', kind: 'text', placeholder: 'auto' },
      { key: 'GOOGLE_DRIVE_STATS_FOLDER_ID', label: 'Stats Folder ID', description: 'ID папки для выгрузок статистики.', kind: 'text', placeholder: 'folder_id' },
      { key: 'GOOGLE_OAUTH_CLIENT_ID', label: 'OAuth Client ID', description: 'Google OAuth client ID.', kind: 'text', placeholder: 'client_id' },
      { key: 'GOOGLE_OAUTH_CLIENT_SECRET', label: 'OAuth Client Secret', description: 'Google OAuth client secret.', kind: 'secret', placeholder: 'client_secret' },
      { key: 'GOOGLE_OAUTH_REFRESH_TOKEN', label: 'OAuth Refresh Token', description: 'Refresh token для Google OAuth.', kind: 'secret', placeholder: 'refresh_token' },
      { key: 'GOOGLE_OAUTH_TOKEN_URI', label: 'OAuth Token URI', description: 'Token URI Google OAuth.', kind: 'url', placeholder: 'https://oauth2.googleapis.com/token' },
      { key: 'GOOGLE_SERVICE_ACCOUNT_JSON_B64', label: 'Service Account JSON B64', description: 'Service account JSON, закодированный в base64.', kind: 'secret', placeholder: 'base64 json' },
    ],
  },
  {
    id: 'urls',
    title: 'URL и сеть',
    subtitle: 'публичные ссылки, поддержка и proxy',
    Icon: Globe2,
    fields: [
      { key: 'API_BASE_URL', label: 'API Base URL', description: 'Публичный базовый URL API.', kind: 'url', placeholder: 'https://shamra1.pro' },
      { key: 'FRONTEND_BASE_URL', label: 'Frontend Base URL', description: 'Публичный URL mini app.', kind: 'url', placeholder: 'https://shamra1.pro/app' },
      { key: 'SUPPORT_URL', label: 'Ссылка на поддержку', description: 'Куда отправлять клиента за ручной помощью.', kind: 'url', placeholder: 'https://...' },
      { key: 'VIP_CHANNEL_URL', label: 'Ссылка на закрытый канал', description: 'Инвайт или публичная ссылка VIP-канала.', kind: 'url', placeholder: 'https://...' },
      { key: 'HTTPS_PROXY', label: 'HTTPS Proxy', description: 'Proxy URL для сервисов, которым он нужен.', kind: 'secret', placeholder: 'https://user:pass@host:port' },
    ],
  },
];

const INTEGRATION_FIELDS = INTEGRATION_GROUPS.flatMap(group => group.fields);
const INTEGRATION_SECRET_FIELDS = INTEGRATION_FIELDS.filter(field => field.kind === 'secret');
const INTEGRATION_SECRET_KEYS = INTEGRATION_SECRET_FIELDS.map(field => field.key);
const DEFAULT_INTEGRATION_VALUES = INTEGRATION_FIELDS.reduce<Record<string, string | boolean>>((acc, field) => {
  acc[field.key] = field.kind === 'boolean' ? false : '';
  return acc;
}, {});

const INTEGRATION_FIELD_HELP: Record<string, string> = {
  TELEGRAM_BOT_TOKEN: 'Ключ доступа к Telegram Bot API. Нужен, чтобы бот отправлял сообщения, уведомления, счета и служебные ответы пользователям.',
  TELEGRAM_WEBHOOK_SECRET_TOKEN: 'Секретная метка webhook-запросов Telegram. Помогает принимать события только от вашего бота, а чужие запросы отсеивать.',
  TELEGRAM_BOT_USERNAME: 'Публичное имя Telegram-бота. Используется в ссылках, кнопках перехода и подсказках для пользователя.',
  TELEGRAM_VIP_CHAT_ID: 'ID закрытого VIP-чата или канала. Нужен для инвайтов, проверки доступа и доставки закрытых материалов.',
  TELEGRAM_ADMIN_GROUP_CHAT_ID: 'ID служебной админ-группы. Сюда бот отправляет важные уведомления по пользователям, оплатам и ошибкам.',
  SALES_MANAGER_TELEGRAM_ID: 'Telegram ID менеджера продаж. Нужен для ручных продаж, персональных уведомлений и передачи лидов ответственному человеку.',
  SHAMRAI_ONBOARDING_REPORT_CHAT_ID: 'Чат для onboarding-отчетов. Сюда уходят анкеты и события новых пользователей после первичного входа.',

  VK_ACCESS_TOKEN: 'Токен сообщества VK. Нужен для отправки сообщений, рассылок, обработки диалогов и синхронизации разрешений на доставку.',
  VK_CALLBACK_CONFIRMATION_CODE: 'Код подтверждения VK Callback API. Используется один раз или при перепривязке callback URL в кабинете VK.',
  VK_CALLBACK_SECRET: 'Секретный ключ VK Callback API. Нужен, чтобы backend доверял только событиям, пришедшим из вашего сообщества VK.',
  VK_ID_CLIENT_SECRET: 'Client secret приложения VK ID. Используется при OAuth-авторизации для обмена кода входа на токены.',
  VK_ID_APP_ID: 'ID приложения VK ID. Связывает авторизацию пользователей с нужным VK-приложением.',
  VK_ID_REDIRECT_URI: 'Адрес возврата после VK ID OAuth. Должен совпадать с redirect URI, указанным в настройках VK.',
  VK_GROUP_ID: 'ID сообщества VK. Используется для проверки callback-событий и правильной доставки сообщений от имени группы.',
  VK_API_VERSION: 'Версия VK API для запросов. Обычно оставляют текущую стабильную версию, чтобы методы VK отвечали ожидаемым форматом.',

  YOOKASSA_SHOP_ID: 'ID магазина YooKassa. Нужен для создания платежей и связывания оплат с вашим магазином.',
  YOOKASSA_SECRET_KEY: 'Секретный ключ YooKassa. Используется backend для создания платежей и проверки статусов оплаты.',
  YOOKASSA_RETURN_URL: 'Страница, куда YooKassa возвращает клиента после оплаты. Обычно это mini app или страница результата.',
  TEGRO_SHOP_ID: 'ID магазина Tegro. Нужен платежному API, чтобы понимать, для какого магазина создается платеж.',
  TEGRO_API_KEY: 'API key Tegro. Используется для авторизованных запросов к платежному шлюзу Tegro.',
  TEGRO_SECRET_KEY: 'Secret key Tegro. Нужен для подписи и проверки платежных операций.',
  TEGRO_RETURN_URL: 'Страница возврата клиента после оплаты через Tegro. Должна вести обратно в приложение или на страницу статуса.',
  TEGRO_API_BASE_URL: 'Базовый URL API Tegro. Меняйте только если Tegro выдал другой endpoint или нужен тестовый контур.',
  PAYMENT_GATEWAY_TOKEN: 'Резервный токен старого платежного шлюза. Нужен только если legacy-интеграция еще используется в проекте.',

  WEB_PUSH_VAPID_PUBLIC_KEY: 'Публичный VAPID-ключ для web push. Передается браузеру, чтобы он мог подписаться на push-уведомления.',
  WEB_PUSH_VAPID_PRIVATE_KEY: 'Приватный VAPID-ключ для web push. Нужен серверу для отправки push-уведомлений подписанным браузерам.',
  WEB_PUSH_VAPID_SUBJECT: 'Контакт отправителя push-уведомлений. Обычно mailto или URL, который видит push-сервис как владельца ключей.',

  GOOGLE_DRIVE_STATS_ENABLED: 'Включает экспорт статистики в Google Drive. Полезно для отчетов, таблиц и автоматической выгрузки аналитики.',
  GOOGLE_DRIVE_AUTH_MODE: 'Режим авторизации Google Drive. Определяет, использовать OAuth, service account или автоматический выбор.',
  GOOGLE_DRIVE_STATS_FOLDER_ID: 'ID папки Google Drive для выгрузок. Именно туда будут складываться отчеты и файлы статистики.',
  GOOGLE_OAUTH_CLIENT_ID: 'Google OAuth Client ID. Идентификатор OAuth-приложения для подключения Google Drive от имени аккаунта.',
  GOOGLE_OAUTH_CLIENT_SECRET: 'Google OAuth Client Secret. Секрет OAuth-приложения, нужен для обновления и получения access token.',
  GOOGLE_OAUTH_REFRESH_TOKEN: 'Refresh token Google OAuth. Позволяет backend получать новые access token без повторного ручного входа.',
  GOOGLE_OAUTH_TOKEN_URI: 'URL выдачи токенов Google OAuth. Обычно стандартный endpoint Google, менять нужно редко.',
  GOOGLE_SERVICE_ACCOUNT_JSON_B64: 'JSON service account в base64. Используется для серверного доступа к Google Drive без личного OAuth-входа.',

  API_BASE_URL: 'Публичный базовый URL backend API. Используется в callback, ссылках, внешних интеграциях и клиентских запросах.',
  FRONTEND_BASE_URL: 'Публичный URL frontend/mini app. Нужен для ссылок возврата, кнопок открытия приложения и внешних переходов.',
  SUPPORT_URL: 'Ссылка на поддержку. По ней пользователя отправляют за ручной помощью, оплатами, доступом или вопросами.',
  VIP_CHANNEL_URL: 'Ссылка на закрытый канал или чат. Используется в интерфейсе и сообщениях для перехода в VIP-зону.',
  HTTPS_PROXY: 'Proxy для исходящих HTTPS-запросов. Используйте только для сервисов, которым нужен обход сетевых ограничений.',
};

const THEME_COLOR_FIELDS: ThemeColorFieldConfig[] = [
  {
    key: THEME_PRIMARY_COLOR_KEY,
    label: 'Primary',
    description: 'Основной неон для активных элементов, свечения и акцентов.',
    fallback: DEFAULT_THEME_SETTINGS.primary_color,
  },
  {
    key: THEME_SECONDARY_COLOR_KEY,
    label: 'Secondary',
    description: 'Дополнительный цвет для вторичных свечений и декоративных акцентов.',
    fallback: DEFAULT_THEME_SETTINGS.secondary_color,
  },
];

const THEME_TEXT_FIELDS: ThemeTextFieldConfig[] = [
  {
    key: BRAND_LOGO_URL_KEY,
    label: 'Logo URL',
    description: 'Ссылка на логотип, который можно использовать в шапках и бренд-блоках.',
    placeholder: 'https://cdn.example.com/logo.png',
  },
  {
    key: BRAND_BACKGROUND_URL_KEY,
    label: 'Background URL',
    description: 'Ссылка на фоновое изображение бренда для ambient-слоя.',
    placeholder: 'https://cdn.example.com/background.webp',
  },
];

const THEME_NUMBER_FIELDS: ThemeNumberFieldConfig[] = [
  {
    key: THEME_GLASS_OPACITY_KEY,
    label: 'Glass opacity',
    description: 'Прозрачность стеклянных панелей.',
    min: 0.15,
    max: 0.9,
    step: 0.01,
  },
  {
    key: THEME_GLASS_BLUR_PX_KEY,
    label: 'Glass blur',
    description: 'Сила blur-эффекта стекла.',
    min: 0,
    max: 36,
    step: 1,
    suffix: 'px',
  },
  {
    key: THEME_RADIUS_SCALE_KEY,
    label: 'Radius scale',
    description: 'Множитель скруглений интерфейса.',
    min: 0.75,
    max: 1.5,
    step: 0.05,
  },
  {
    key: THEME_FONT_SCALE_KEY,
    label: 'Font scale',
    description: 'Множитель размера текста в интерфейсе.',
    min: 0.85,
    max: 1.2,
    step: 0.01,
  },
  {
    key: THEME_GLOW_STRENGTH_KEY,
    label: 'Glow strength',
    description: 'Интенсивность неонового свечения.',
    min: 0,
    max: 1.6,
    step: 0.05,
  },
];

const THEME_PRESETS = [
  { id: 'classic', label: 'Classic', primary: '#00d2ff', secondary: '#d946ef' },
  { id: 'mint', label: 'Mint', primary: '#34d399', secondary: '#22d3ee' },
  { id: 'gold', label: 'Gold', primary: '#facc15', secondary: '#38bdf8' },
  { id: 'rose', label: 'Rose', primary: '#fb7185', secondary: '#a78bfa' },
];

const TAB_SECTION_IDS: Record<SettingsTabId, string[]> = {
  texts: ['texts-telegram', 'texts-vk', 'texts-site', 'texts-editor', 'texts-preview', 'texts-variables'],
  switches: ['switches-access', 'switches-broadcasts', 'switches-sessions', 'switches-audit'],
  integrations: INTEGRATION_GROUPS.map(group => `integrations-${group.id}`),
  theme: ['theme-colors', 'theme-brand', 'theme-effects', 'theme-preview'],
  monitoring: ['monitoring-overview', 'monitoring-health', 'monitoring-delivery', 'monitoring-logs', 'monitoring-audit'],
};

const DEFAULT_ADMIN_SETTINGS_VALUES: AdminSettingsFormValues = {
  ...DEFAULT_INTEGRATION_VALUES,
  MAINTENANCE_MODE: false,
  DISABLE_REGISTRATIONS: false,
  PAUSE_BROADCASTS: false,
  THEME_PRIMARY_COLOR: DEFAULT_THEME_SETTINGS.primary_color,
  THEME_SECONDARY_COLOR: DEFAULT_THEME_SETTINGS.secondary_color,
  GLOBAL_PERFORMANCE_MODE: DEFAULT_THEME_SETTINGS.global_performance_mode,
  BRAND_LOGO_URL: DEFAULT_THEME_SETTINGS.brand_logo_url,
  BRAND_BACKGROUND_URL: DEFAULT_THEME_SETTINGS.brand_background_url,
  THEME_GLASS_OPACITY: String(DEFAULT_THEME_SETTINGS.glass_opacity),
  THEME_GLASS_BLUR_PX: String(DEFAULT_THEME_SETTINGS.glass_blur_px),
  THEME_RADIUS_SCALE: String(DEFAULT_THEME_SETTINGS.radius_scale),
  THEME_FONT_SCALE: String(DEFAULT_THEME_SETTINGS.font_scale),
  THEME_DENSITY: DEFAULT_THEME_SETTINGS.theme_density,
  THEME_GLOW_STRENGTH: String(DEFAULT_THEME_SETTINGS.glow_strength),
};

const CHANNEL_GROUPS: ChannelGroupConfig[] = [
  {
    id: 'telegram',
    title: 'Telegram',
    subtitle: 'бот и личные уведомления',
    badge: 'TG',
    Icon: Send,
    templateKeys: [
      'telegram_welcome',
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
  {
    id: 'vk',
    title: 'VK',
    subtitle: 'сообщения сообщества',
    badge: 'VK',
    Icon: MessageCircle,
    templateKeys: [
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
  {
    id: 'site',
    title: 'Сайт',
    subtitle: 'чат и web-уведомления',
    badge: 'WEB',
    Icon: MonitorSmartphone,
    templateKeys: [
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
];

const GROUP_TONE: Record<ChannelGroupId, {
  header: string;
  marker: string;
  activeCard: string;
}> = {
  telegram: {
    header: 'border-cyan-300/20 bg-cyan-300/[0.075] text-cyan-100',
    marker: 'border-cyan-300/25 bg-cyan-300/15 text-cyan-100',
    activeCard: 'border-cyan-300/45 bg-cyan-300/[0.14] shadow-neon-cyan',
  },
  vk: {
    header: 'border-[#4f8cff]/25 bg-[#4f8cff]/[0.095] text-blue-100',
    marker: 'border-[#4f8cff]/30 bg-[#4f8cff]/15 text-blue-100',
    activeCard: 'border-[#4f8cff]/45 bg-[#4f8cff]/[0.14] shadow-[0_0_22px_rgba(79,140,255,0.13)]',
  },
  site: {
    header: 'border-emerald-300/20 bg-emerald-300/[0.075] text-emerald-100',
    marker: 'border-emerald-300/25 bg-emerald-300/15 text-emerald-100',
    activeCard: 'border-emerald-300/45 bg-emerald-300/[0.14] shadow-[0_0_22px_rgba(52,211,153,0.13)]',
  },
};

function formatTemplateDate(value: string | null) {
  if (!value) return 'не меняли';
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function htmlToPlainText(value: string) {
  const withLineBreaks = value
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(?:p|div|li|h[1-6])>/gi, '\n');
  return withLineBreaks
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function buildPreview(template: MessageTemplateResponse | null, body: string) {
  if (!template) return '';
  const examples = new Map(template.variables.map(variable => [variable.key, variable.example || variable.label]));
  const rendered = body.replace(/{{\s*([a-zA-Z0-9_]+)\s*}}/g, (_match, key: string) => {
    return examples.get(key) || '';
  });
  return htmlToPlainText(rendered);
}

function sanitizeEditableText(value: string) {
  return value.replace(/[{}<>]/g, '');
}

function parseTemplateEditorTokens(
  template: MessageTemplateResponse | null,
  body: string,
): TemplateEditorToken[] {
  const variableMeta = new Map(
    (template?.variables || []).map(variable => [variable.key, variable]),
  );
  const tokens: TemplateEditorToken[] = [];
  const tokenPattern = /({{\s*([a-zA-Z0-9_]+)\s*}}|<\/?[^>]+>)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  const pushText = (value: string) => {
    if (!value) return;
    tokens.push({ type: 'text', value });
  };

  while ((match = tokenPattern.exec(body)) !== null) {
    pushText(body.slice(cursor, match.index));
    const raw = match[0];
    const variableKey = match[2];
    if (variableKey) {
      const meta = variableMeta.get(variableKey);
      tokens.push({
        type: 'variable',
        raw,
        key: variableKey,
        label: meta?.label || variableKey,
        example: meta?.example || '',
      });
    } else {
      tokens.push({ type: 'markup', raw });
    }
    cursor = match.index + raw.length;
  }

  pushText(body.slice(cursor));
  return tokens;
}

function serializeTemplateEditorTokens(tokens: TemplateEditorToken[]) {
  return tokens.map(token => {
    if (token.type === 'text') return token.value;
    return token.raw;
  }).join('');
}

function rowsForEditableText(value: string) {
  const lineCount = value.split('\n').length;
  const lengthRows = Math.ceil(value.length / 58);
  return Math.min(9, Math.max(2, lineCount, lengthRows));
}

function truthySettingValue(value: string | boolean | null | undefined) {
  if (typeof value === 'boolean') return value;
  return ['1', 'true', 'yes', 'on', 'да'].includes(String(value || '').trim().toLowerCase());
}

function settingResponseMap(data?: AdminSettingsResponse) {
  return new Map<SystemSettingKey, AdminSettingResponse>(
    (data?.settings || []).map(setting => [setting.key, setting]),
  );
}

function formValuesFromSettings(data?: AdminSettingsResponse): AdminSettingsFormValues {
  const settingsByKey = settingResponseMap(data);
  const values: AdminSettingsFormValues = {
    ...DEFAULT_ADMIN_SETTINGS_VALUES,
    MAINTENANCE_MODE: truthySettingValue(settingsByKey.get('MAINTENANCE_MODE')?.value),
    DISABLE_REGISTRATIONS: truthySettingValue(settingsByKey.get('DISABLE_REGISTRATIONS')?.value),
    PAUSE_BROADCASTS: truthySettingValue(settingsByKey.get('PAUSE_BROADCASTS')?.value),
    THEME_PRIMARY_COLOR: normalizeHexColor(
      settingsByKey.get(THEME_PRIMARY_COLOR_KEY)?.value,
      DEFAULT_THEME_SETTINGS.primary_color,
    ),
    THEME_SECONDARY_COLOR: normalizeHexColor(
      settingsByKey.get(THEME_SECONDARY_COLOR_KEY)?.value,
      DEFAULT_THEME_SETTINGS.secondary_color,
    ),
    GLOBAL_PERFORMANCE_MODE: truthySettingValue(settingsByKey.get(GLOBAL_PERFORMANCE_MODE_KEY)?.value),
    BRAND_LOGO_URL: settingsByKey.get(BRAND_LOGO_URL_KEY)?.value || DEFAULT_THEME_SETTINGS.brand_logo_url,
    BRAND_BACKGROUND_URL: settingsByKey.get(BRAND_BACKGROUND_URL_KEY)?.value || DEFAULT_THEME_SETTINGS.brand_background_url,
    THEME_GLASS_OPACITY: settingsByKey.get(THEME_GLASS_OPACITY_KEY)?.value || String(DEFAULT_THEME_SETTINGS.glass_opacity),
    THEME_GLASS_BLUR_PX: settingsByKey.get(THEME_GLASS_BLUR_PX_KEY)?.value || String(DEFAULT_THEME_SETTINGS.glass_blur_px),
    THEME_RADIUS_SCALE: settingsByKey.get(THEME_RADIUS_SCALE_KEY)?.value || String(DEFAULT_THEME_SETTINGS.radius_scale),
    THEME_FONT_SCALE: settingsByKey.get(THEME_FONT_SCALE_KEY)?.value || String(DEFAULT_THEME_SETTINGS.font_scale),
    THEME_DENSITY: settingsByKey.get(THEME_DENSITY_KEY)?.value || DEFAULT_THEME_SETTINGS.theme_density,
    THEME_GLOW_STRENGTH: settingsByKey.get(THEME_GLOW_STRENGTH_KEY)?.value || String(DEFAULT_THEME_SETTINGS.glow_strength),
  };

  INTEGRATION_FIELDS.forEach(field => {
    const rawValue = settingsByKey.get(field.key)?.value;
    values[field.key] = field.kind === 'boolean' ? truthySettingValue(rawValue) : rawValue || '';
  });

  return values;
}

function buildSettingsPayload(values: AdminSettingsFormValues) {
  const payload: Array<{ key: SystemSettingKey; value: string }> = [
    {
      key: 'MAINTENANCE_MODE',
      value: truthySettingValue(values.MAINTENANCE_MODE) ? 'true' : 'false',
    },
    {
      key: 'DISABLE_REGISTRATIONS',
      value: truthySettingValue(values.DISABLE_REGISTRATIONS) ? 'true' : 'false',
    },
    {
      key: 'PAUSE_BROADCASTS',
      value: truthySettingValue(values.PAUSE_BROADCASTS) ? 'true' : 'false',
    },
    {
      key: THEME_PRIMARY_COLOR_KEY,
      value: normalizeHexColor(values[THEME_PRIMARY_COLOR_KEY], DEFAULT_THEME_SETTINGS.primary_color),
    },
    {
      key: THEME_SECONDARY_COLOR_KEY,
      value: normalizeHexColor(values[THEME_SECONDARY_COLOR_KEY], DEFAULT_THEME_SETTINGS.secondary_color),
    },
    {
      key: GLOBAL_PERFORMANCE_MODE_KEY,
      value: truthySettingValue(values[GLOBAL_PERFORMANCE_MODE_KEY]) ? 'true' : 'false',
    },
    {
      key: BRAND_LOGO_URL_KEY,
      value: String(values[BRAND_LOGO_URL_KEY] || '').trim(),
    },
    {
      key: BRAND_BACKGROUND_URL_KEY,
      value: String(values[BRAND_BACKGROUND_URL_KEY] || '').trim(),
    },
    {
      key: THEME_GLASS_OPACITY_KEY,
      value: String(values[THEME_GLASS_OPACITY_KEY] || DEFAULT_THEME_SETTINGS.glass_opacity).trim(),
    },
    {
      key: THEME_GLASS_BLUR_PX_KEY,
      value: String(values[THEME_GLASS_BLUR_PX_KEY] || DEFAULT_THEME_SETTINGS.glass_blur_px).trim(),
    },
    {
      key: THEME_RADIUS_SCALE_KEY,
      value: String(values[THEME_RADIUS_SCALE_KEY] || DEFAULT_THEME_SETTINGS.radius_scale).trim(),
    },
    {
      key: THEME_FONT_SCALE_KEY,
      value: String(values[THEME_FONT_SCALE_KEY] || DEFAULT_THEME_SETTINGS.font_scale).trim(),
    },
    {
      key: THEME_DENSITY_KEY,
      value: String(values[THEME_DENSITY_KEY] || DEFAULT_THEME_SETTINGS.theme_density).trim(),
    },
    {
      key: THEME_GLOW_STRENGTH_KEY,
      value: String(values[THEME_GLOW_STRENGTH_KEY] || DEFAULT_THEME_SETTINGS.glow_strength).trim(),
    },
  ];

  INTEGRATION_FIELDS.forEach(field => {
    const rawValue = values[field.key];
    const value = field.kind === 'boolean'
      ? (truthySettingValue(rawValue) ? 'true' : 'false')
      : String(rawValue || '').trim();
    if (field.kind !== 'secret' || value) {
      payload.push({ key: field.key, value });
    }
  });

  return payload;
}

function IntegrationFieldHelp({ text }: { text: string }) {
  return (
    <span className="group/help relative inline-flex shrink-0 items-center">
      <button
        type="button"
        aria-label={`Подсказка: ${text}`}
        title={text}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
        className="flex h-5 w-5 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-400 transition-all hover:border-cyan-300/35 hover:bg-cyan-300/10 hover:text-cyan-100 focus:border-cyan-300/45 focus:bg-cyan-300/10 focus:text-cyan-100 focus:outline-none focus:ring-2 focus:ring-cyan-300/10"
      >
        <Info className="h-3 w-3" />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-50 mt-2 w-72 max-w-[calc(100vw-3rem)] translate-y-1 rounded-2xl border border-cyan-300/20 bg-slate-950/95 px-3 py-2 text-left text-[11px] font-semibold leading-relaxed text-slate-100 opacity-0 shadow-[0_18px_48px_rgba(2,6,23,0.48)] backdrop-blur-xl transition-all duration-150 group-hover/help:translate-y-0 group-hover/help:opacity-100 group-focus-within/help:translate-y-0 group-focus-within/help:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

interface SettingsAccordionSectionProps {
  id: string;
  title: string;
  subtitle: string;
  badge?: string;
  open: boolean;
  onToggle: () => void;
  Icon: LucideIcon;
  tone?: 'cyan' | 'emerald' | 'amber' | 'rose' | 'violet';
  dirty?: boolean;
  status?: React.ReactNode;
  rightActions?: React.ReactNode;
  children: React.ReactNode;
}

function SettingsAccordionSection({
  id,
  title,
  subtitle,
  badge,
  open,
  onToggle,
  Icon,
  tone = 'cyan',
  dirty = false,
  status,
  rightActions,
  children,
}: SettingsAccordionSectionProps) {
  const toneClass = {
    cyan: 'border-cyan-300/25 bg-cyan-300/[0.075] text-cyan-100',
    emerald: 'border-emerald-300/25 bg-emerald-300/[0.075] text-emerald-100',
    amber: 'border-amber-300/25 bg-amber-300/[0.085] text-amber-100',
    rose: 'border-rose-300/25 bg-rose-300/[0.085] text-rose-100',
    violet: 'border-violet-300/25 bg-violet-300/[0.085] text-violet-100',
  }[tone];

  return (
    <section className="overflow-visible rounded-2xl border border-white/10 bg-white/[0.025] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${id}-content`}
        data-settings-accordion-toggle={id}
        onClick={onToggle}
        className={`smooth-pressable flex w-full items-center gap-3 rounded-2xl border px-3 py-3 text-left transition-all ${toneClass}`}
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-black/20">
          <Icon className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="truncate text-xs font-black uppercase tracking-wider text-white">{title}</span>
            {badge && (
              <span className="shrink-0 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider">
                {badge}
              </span>
            )}
            {dirty && (
              <span className="shrink-0 rounded-lg border border-amber-300/25 bg-amber-300/10 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-amber-100">
                изменено
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-400">{subtitle}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {status}
          {rightActions}
          <ChevronDown className={`h-4 w-4 text-slate-300 transition-transform ${open ? 'rotate-180' : ''}`} />
        </span>
      </button>
      <SmoothCollapse open={open}>
        <div id={`${id}-content`} className="p-3 sm:p-4">
          {children}
        </div>
      </SmoothCollapse>
    </section>
  );
}

export default function AdminSettings() {
  const [templates, setTemplates] = useState<MessageTemplateResponse[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [activeKey, setActiveKey] = useState<string>('');
  const [activeTab, setActiveTab] = useState<SettingsTabId>('texts');
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<ChannelGroupId, boolean>>({
    telegram: false,
    vk: false,
    site: false,
  });
  const editorPanelRef = useRef<HTMLElement | null>(null);
  const editorScrollFrameRef = useRef<number | undefined>();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [resettingKey, setResettingKey] = useState<string | null>(null);
  const [visibleSecrets, setVisibleSecrets] = useState<Record<SecretSettingKey, boolean>>(() => (
    Object.fromEntries(INTEGRATION_SECRET_KEYS.map(key => [key, false]))
  ));
  const [integrationsUnlocked, setIntegrationsUnlocked] = useState(false);
  const [integrationPassword, setIntegrationPassword] = useState('');
  const [integrationUnlockToken, setIntegrationUnlockToken] = useState('');
  const [integrationDiagnostics, setIntegrationDiagnostics] = useState<Record<string, IntegrationDiagnosticsResponse['groups'][number]>>({});
  const [openSections, setOpenSections] = useState<Record<SettingsTabId, SettingsAccordionState>>(() => ({
    texts: createCollapsedSectionState(TAB_SECTION_IDS.texts),
    switches: createCollapsedSectionState(TAB_SECTION_IDS.switches),
    integrations: createCollapsedSectionState(TAB_SECTION_IDS.integrations),
    theme: createCollapsedSectionState(TAB_SECTION_IDS.theme),
    monitoring: createCollapsedSectionState(TAB_SECTION_IDS.monitoring),
  }));
  const queryClient = useQueryClient();
  const settingsForm = useForm<AdminSettingsFormValues>({
    defaultValues: DEFAULT_ADMIN_SETTINGS_VALUES,
    mode: 'onChange',
  });
  const settingsQuery = useQuery({
    queryKey: ADMIN_SETTINGS_QUERY_KEY,
    queryFn: () => apiFetch<AdminSettingsResponse>('/admin/settings'),
    staleTime: 60_000,
  });
  const unlockIntegrationsMutation = useMutation({
    mutationFn: (payload: IntegrationUnlockRequest) => apiFetch<IntegrationUnlockResponse>('/admin/settings/integrations/unlock', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    onSuccess: (data) => {
      const unlockedValues = formValuesFromSettings(data);
      Object.entries(unlockedValues).forEach(([key, value]) => {
        settingsForm.setValue(key, value, {
          shouldDirty: false,
          shouldTouch: false,
          shouldValidate: false,
        });
      });
      setIntegrationsUnlocked(true);
      setIntegrationUnlockToken(data.unlock_token);
      setIntegrationPassword('');
      notifySuccess('Интеграции разблокированы');
    },
    onError: (err: any) => {
      notifyError(err.message || 'Неверный пароль интеграций');
    },
  });
  const saveSettingsMutation = useMutation({
    mutationFn: (values: AdminSettingsFormValues) => apiFetch<AdminSettingsResponse>('/admin/settings', {
      method: 'PUT',
      body: JSON.stringify(buildSettingsPayload(values)),
    }),
    onSuccess: (data) => {
      queryClient.setQueryData(ADMIN_SETTINGS_QUERY_KEY, data);
      void queryClient.invalidateQueries({ queryKey: PUBLIC_THEME_QUERY_KEY });
      const nextValues = formValuesFromSettings(data);
      if (integrationsUnlocked) {
        const currentValues = settingsForm.getValues();
        INTEGRATION_SECRET_KEYS.forEach(key => {
          if (!nextValues[key] && currentValues[key]) nextValues[key] = currentValues[key];
        });
      }
      settingsForm.reset(nextValues);
      notifySuccess('Настройки сохранены');
    },
    onError: (err: any) => {
      notifyError(err.message || 'Не удалось сохранить настройки');
    },
  });
  const resetSessionsMutation = useMutation({
    mutationFn: () => apiFetch<ResetSessionsResponse>('/admin/settings/reset-sessions', {
      method: 'POST',
    }),
    onSuccess: (data) => {
      notifySuccess(`Сессии сброшены: ${data.deleted}`);
    },
    onError: (err: any) => {
      notifyError(err.message || 'Не удалось сбросить сессии');
    },
  });
  const onlineQuery = useQuery({
    queryKey: ['admin-monitoring-online'],
    queryFn: () => apiFetch<OnlineMonitoringResponse>('/admin/monitoring/online'),
    enabled: activeTab === 'monitoring',
    refetchInterval: activeTab === 'monitoring' ? 7_000 : false,
  });
  const parserStatusQuery = useQuery({
    queryKey: ['admin-monitoring-parser-status'],
    queryFn: () => apiFetch<ParserStatusResponse>('/admin/monitoring/parser-status'),
    enabled: activeTab === 'monitoring',
    refetchInterval: activeTab === 'monitoring' ? 10_000 : false,
  });
  const monitoringLogsQuery = useQuery({
    queryKey: ['admin-monitoring-logs'],
    queryFn: () => apiFetch<MonitoringLogsResponse>('/admin/monitoring/logs'),
    enabled: activeTab === 'monitoring',
    refetchInterval: activeTab === 'monitoring' ? 10_000 : false,
  });
  const monitoringSummaryQuery = useQuery({
    queryKey: ['admin-monitoring-summary'],
    queryFn: () => apiFetch<MonitoringSummaryResponse>('/admin/monitoring/summary'),
    enabled: activeTab === 'monitoring',
    refetchInterval: activeTab === 'monitoring' ? 10_000 : false,
  });
  const integrationDiagnosticsMutation = useMutation({
    mutationFn: (groupId: string) => apiFetch<IntegrationDiagnosticsResponse>('/admin/settings/integrations/diagnostics', {
      method: 'POST',
      body: JSON.stringify({
        unlock_token: integrationUnlockToken,
        group: groupId,
      }),
    }),
    onSuccess: (data) => {
      setIntegrationDiagnostics(current => {
        const next = { ...current };
        data.groups.forEach(group => {
          next[group.group] = group;
        });
        return next;
      });
      notifySuccess('Диагностика интеграции обновлена');
    },
    onError: (err: any) => {
      notifyError(err.message || 'Не удалось проверить интеграцию');
    },
  });

  const loadTemplates = async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await apiFetch<MessageTemplateResponse[]>('/admin/message-templates');
      setTemplates(data);
      setDrafts(data.reduce<Record<string, string>>((acc, template) => {
        acc[template.key] = template.body;
        return acc;
      }, {}));
      setActiveKey(current => current || data[0]?.key || '');
    } catch (err: any) {
      setLoadError(err.message || 'Не удалось загрузить шаблоны');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadTemplates();
  }, []);

  useEffect(() => {
    if (settingsQuery.data) {
      const nextValues = formValuesFromSettings(settingsQuery.data);
      if (integrationsUnlocked) {
        const currentValues = settingsForm.getValues();
        INTEGRATION_SECRET_KEYS.forEach(key => {
          if (!nextValues[key] && currentValues[key]) nextValues[key] = currentValues[key];
        });
      }
      settingsForm.reset(nextValues);
    }
  }, [integrationsUnlocked, settingsForm, settingsQuery.data]);

  useEffect(() => () => {
    if (editorScrollFrameRef.current !== undefined) window.cancelAnimationFrame(editorScrollFrameRef.current);
  }, []);

  const sectionIsOpen = (tab: SettingsTabId, sectionId: string) => Boolean(openSections[tab]?.[sectionId]);

  const toggleAccordionSection = (tab: SettingsTabId, sectionId: string) => {
    setOpenSections(current => ({
      ...current,
      [tab]: toggleSection(current[tab] || createCollapsedSectionState(TAB_SECTION_IDS[tab]), sectionId),
    }));
  };

  const setActiveTabSectionsOpen = (open: boolean) => {
    if (activeTab === 'texts') {
      setExpandedGroups({
        telegram: open,
        vk: open,
        site: open,
      });
    }
    setOpenSections(current => ({
      ...current,
      [activeTab]: setAllSectionsOpen(TAB_SECTION_IDS[activeTab], open),
    }));
  };

  const renderSectionControls = () => (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        onClick={() => setActiveTabSectionsOpen(true)}
        className="smooth-pressable rounded-xl border border-cyan-300/20 bg-cyan-300/[0.08] px-3 py-2 text-[9px] font-black uppercase tracking-wider text-cyan-100 transition-all hover:bg-cyan-300/[0.14]"
      >
        Раскрыть все
      </button>
      <button
        type="button"
        onClick={() => setActiveTabSectionsOpen(false)}
        className="smooth-pressable rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[9px] font-black uppercase tracking-wider text-slate-300 transition-all hover:border-white/20 hover:bg-white/[0.07]"
      >
        Свернуть все
      </button>
    </div>
  );

  const templateByKey = useMemo(() => (
    new Map(templates.map(template => [template.key, template]))
  ), [templates]);

  const groupedTemplates = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return CHANNEL_GROUPS.map(group => {
      const groupTemplates = group.templateKeys
        .map(templateKey => templateByKey.get(templateKey))
        .filter((template): template is MessageTemplateResponse => Boolean(template));

      const visibleTemplates = query
        ? groupTemplates.filter(template => (
          template.title.toLowerCase().includes(query)
          || template.description.toLowerCase().includes(query)
          || template.key.toLowerCase().includes(query)
        ))
        : groupTemplates;

      return {
        ...group,
        templates: visibleTemplates,
        total: groupTemplates.length,
      };
    });
  }, [searchTerm, templateByKey]);

  const activeTemplate = useMemo(() => (
    templates.find(template => template.key === activeKey) || templates[0] || null
  ), [activeKey, templates]);
  const customTemplateCount = useMemo(() => (
    templates.filter(template => template.is_custom).length
  ), [templates]);
  const dirtyTemplateCount = useMemo(() => (
    templates.filter(template => (drafts[template.key] ?? template.body) !== template.body).length
  ), [drafts, templates]);
  const activeBody = activeTemplate ? drafts[activeTemplate.key] ?? activeTemplate.body : '';
  const activePreview = useMemo(() => buildPreview(activeTemplate, activeBody), [activeBody, activeTemplate]);
  const editorTokens = useMemo(
    () => parseTemplateEditorTokens(activeTemplate, activeBody),
    [activeBody, activeTemplate],
  );
  const isDirty = Boolean(activeTemplate && activeBody !== activeTemplate.body);
  const busy = Boolean(activeTemplate && (savingKey === activeTemplate.key || resettingKey === activeTemplate.key));
  const adminSettingsByKey = useMemo(() => settingResponseMap(settingsQuery.data), [settingsQuery.data]);
  const settingsFormDirty = settingsForm.formState.isDirty;
  const settingsFormValid = settingsForm.formState.isValid;
  const systemSettingsStatusText = settingsQuery.isLoading
    ? 'загрузка'
    : settingsQuery.isError
      ? 'ошибка'
      : settingsFormDirty
        ? 'есть правки'
        : 'сохранено';
  const settingsStatusText = activeTab === 'texts'
    ? loading
      ? 'загрузка'
      : loadError
        ? 'ошибка'
        : `${customTemplateCount}/${templates.length} изменено`
    : systemSettingsStatusText;

  const setActiveBody = (value: string) => {
    if (!activeTemplate) return;
    setDrafts(current => ({ ...current, [activeTemplate.key]: value }));
  };

  const toggleGroup = (groupId: ChannelGroupId) => {
    setExpandedGroups(current => ({ ...current, [groupId]: !current[groupId] }));
  };

  const handleSelectTemplate = (templateKey: string) => {
    setActiveKey(templateKey);
    if (typeof window === 'undefined' || !window.matchMedia('(max-width: 1023px)').matches) return;

    if (editorScrollFrameRef.current !== undefined) window.cancelAnimationFrame(editorScrollFrameRef.current);
    editorScrollFrameRef.current = window.requestAnimationFrame(() => {
      editorScrollFrameRef.current = undefined;
      editorPanelRef.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
    });
  };

  const handleTextTokenChange = (tokenIndex: number, value: string) => {
    if (!activeTemplate) return;
    const nextTokens = parseTemplateEditorTokens(activeTemplate, activeBody).map((token, index) => (
      index === tokenIndex && token.type === 'text'
        ? { ...token, value: sanitizeEditableText(value) }
        : token
    ));
    setActiveBody(serializeTemplateEditorTokens(nextTokens));
  };

  const replaceTemplateInState = (template: MessageTemplateResponse) => {
    setTemplates(current => current.map(item => (item.key === template.key ? template : item)));
    setDrafts(current => ({ ...current, [template.key]: template.body }));
  };

  const handleSave = async () => {
    if (!activeTemplate) return;
    if (!activeBody.trim()) {
      notifyError('Текст шаблона не может быть пустым');
      return;
    }
    try {
      setSavingKey(activeTemplate.key);
      const saved = await apiFetch<MessageTemplateResponse>(`/admin/message-templates/${encodeURIComponent(activeTemplate.key)}`, {
        method: 'PUT',
        body: JSON.stringify({ body: activeBody }),
      });
      replaceTemplateInState(saved);
      notifySuccess('Шаблон сохранен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить шаблон');
    } finally {
      setSavingKey(null);
    }
  };

  const renderTemplateActions = (
    template: MessageTemplateResponse,
    className: string,
  ) => (
    <div className={className}>
      <button
        type="button"
        onClick={handleReset}
        disabled={busy}
        className="flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-3 text-[10px] font-black uppercase tracking-wider text-slate-300 transition-all hover:border-amber-300/35 hover:text-amber-100 active:scale-[0.99] disabled:opacity-50"
      >
        {resettingKey === template.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
        <span>Сброс</span>
      </button>
      <button
        type="button"
        onClick={handleSave}
        disabled={busy || !isDirty}
        className="flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border border-emerald-300/25 bg-emerald-400/15 px-3 text-[10px] font-black uppercase tracking-wider text-emerald-100 transition-all hover:bg-emerald-400/20 active:scale-[0.99] disabled:opacity-50"
      >
        {savingKey === template.key ? <Loader2 className="h-4 w-4 animate-spin" /> : isDirty ? <Save className="h-4 w-4" /> : <Check className="h-4 w-4" />}
        <span>{isDirty ? 'Сохранить' : 'Сохранено'}</span>
      </button>
    </div>
  );

  const handleReset = async () => {
    if (!activeTemplate) return;
    const confirmed = await confirmDestructive({
      title: 'Сбросить шаблон',
      message: `Вернуть стандартный текст для «${activeTemplate.title}»?`,
      confirmLabel: 'Сбросить',
      tone: 'warning',
    });
    if (!confirmed) return;

    try {
      setResettingKey(activeTemplate.key);
      const reset = await apiFetch<MessageTemplateResponse>(`/admin/message-templates/${encodeURIComponent(activeTemplate.key)}/reset`, {
        method: 'POST',
      });
      replaceTemplateInState(reset);
      notifySuccess('Шаблон сброшен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сбросить шаблон');
    } finally {
      setResettingKey(null);
    }
  };

  const handleSettingsSubmit = (values: AdminSettingsFormValues) => {
    saveSettingsMutation.mutate(values);
  };

  const handleUnlockIntegrations = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const password = integrationPassword.trim();
    if (!password) {
      notifyError('Введите пароль интеграций');
      return;
    }
    unlockIntegrationsMutation.mutate({ password });
  };

  const toggleSecretVisibility = (key: SecretSettingKey) => {
    setVisibleSecrets(current => ({ ...current, [key]: !current[key] }));
  };

  const handleDownloadMonitoringLog = async () => {
    const timestamp = new Date().toISOString();
    try {
      await downloadApiFile(
        '/admin/monitoring/diagnostic-report?format=txt',
        `shamrai-monitoring-${timestamp.replace(/[:.]/g, '-')}.txt`,
      );
      return;
    } catch (err: any) {
      if (!import.meta.env.DEV) {
        notifyError(err.message || 'Не удалось скачать диагностический отчет');
        return;
      }
    }

    const summary = monitoringSummaryQuery.data;
    const onlineUsers = summary?.online?.online_users ?? onlineQuery.data?.online_users ?? 0;
    const parserStatus = summary?.parser?.status || parserStatusQuery.data?.status || 'unknown';
    const parserLastSync = summary?.parser?.last_sync || parserStatusQuery.data?.last_sync || 'unknown';
    const logs = monitoringLogsQuery.data?.logs || [];
    const body = [
      'Shamrai Analytics Hub sanitized diagnostic report',
      `created_at=${timestamp}`,
      `online_users=${onlineUsers}`,
      `parser_status=${parserStatus}`,
      `parser_last_sync=${parserLastSync}`,
      '',
      'health:',
      JSON.stringify(summary?.health || {}, null, 2),
      '',
      'delivery_outbox:',
      JSON.stringify(summary?.delivery_outbox || {}, null, 2),
      '',
      'payment_reconciliation:',
      JSON.stringify(summary?.payment_reconciliation || {}, null, 2),
      '',
      'rate_limit:',
      JSON.stringify(summary?.rate_limit || {}, null, 2),
      '',
      'audit:',
      JSON.stringify(summary?.audit || [], null, 2),
      '',
      'logs:',
      ...(logs.length ? logs : ['<empty>']),
      '',
    ].join('\n');
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `shamrai-monitoring-${timestamp.replace(/[:.]/g, '-')}.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const handleResetUserSessions = async () => {
    const confirmed = await confirmDestructive({
      title: 'Сбросить все сессии пользователей',
      message: 'Будут очищены временные auth-сессии в Redis. Пользователям потребуется войти заново.',
      confirmLabel: 'Сбросить',
      tone: 'danger',
    });
    if (!confirmed) return;
    resetSessionsMutation.mutate();
  };

  const handleRunIntegrationDiagnostics = (groupId: string) => {
    if (!integrationUnlockToken) {
      notifyError('Сначала откройте сейф интеграций');
      return;
    }
    integrationDiagnosticsMutation.mutate(groupId);
  };

  const handleApplyThemePreset = (preset: (typeof THEME_PRESETS)[number]) => {
    settingsForm.setValue(THEME_PRIMARY_COLOR_KEY, preset.primary, {
      shouldDirty: true,
      shouldTouch: true,
      shouldValidate: true,
    });
    settingsForm.setValue(THEME_SECONDARY_COLOR_KEY, preset.secondary, {
      shouldDirty: true,
      shouldTouch: true,
      shouldValidate: true,
    });
  };

  const renderStatusBadge = (status?: string, label?: string) => {
    const normalizedStatus = status || 'missing';
    const className = normalizedStatus === 'ok' || normalizedStatus === 'active'
      ? 'border-emerald-300/25 bg-emerald-300/10 text-emerald-100'
      : normalizedStatus === 'warning'
        ? 'border-amber-300/25 bg-amber-300/10 text-amber-100'
        : normalizedStatus === 'error'
          ? 'border-rose-300/25 bg-rose-300/10 text-rose-100'
          : 'border-white/10 bg-black/20 text-slate-300';

    return (
      <span className={`shrink-0 rounded-lg border px-2 py-1 text-[8px] font-black uppercase tracking-wider ${className}`}>
        {label || normalizedStatus}
      </span>
    );
  };

  const renderMetricCard = (
    label: string,
    value: React.ReactNode,
    description: string,
    tone: 'cyan' | 'emerald' | 'amber' | 'rose' | 'violet' = 'cyan',
  ) => {
    const toneClass = {
      cyan: 'border-cyan-300/20 bg-cyan-300/[0.075] text-cyan-100',
      emerald: 'border-emerald-300/20 bg-emerald-300/[0.075] text-emerald-100',
      amber: 'border-amber-300/20 bg-amber-300/[0.085] text-amber-100',
      rose: 'border-rose-300/20 bg-rose-300/[0.085] text-rose-100',
      violet: 'border-violet-300/20 bg-violet-300/[0.085] text-violet-100',
    }[tone];

    return (
      <div className={`rounded-2xl border p-4 ${toneClass}`}>
        <p className="text-[9px] font-black uppercase tracking-[0.18em] text-slate-300">{label}</p>
        <div className="mt-2 text-2xl font-black text-white tabular-nums">{value}</div>
        <p className="mt-1 text-[10px] font-semibold leading-relaxed text-slate-400">{description}</p>
      </div>
    );
  };

  const renderSettingsQueryState = (tab: SettingsTabConfig) => {
    const StateIcon = tab.Icon;
    if (settingsQuery.isLoading) {
      return (
        <section
          id={`settings-panel-${tab.id}`}
          role="tabpanel"
          aria-labelledby={`settings-tab-${tab.id}`}
          className="flex min-h-[280px] items-center justify-center rounded-3xl border border-white/10 bg-slate-950/42 shadow-glass backdrop-blur-xl"
        >
          <Loader2 className="h-7 w-7 animate-spin text-cyan-300" />
        </section>
      );
    }

    if (settingsQuery.isError) {
      return (
        <section
          id={`settings-panel-${tab.id}`}
          role="tabpanel"
          aria-labelledby={`settings-tab-${tab.id}`}
          className="space-y-3 rounded-2xl border border-rose-500/25 bg-rose-500/10 p-5 text-center text-xs text-rose-100"
        >
          <StateIcon className="mx-auto h-7 w-7 text-rose-300" />
          <p className="font-bold">Не удалось загрузить настройки</p>
          <button
            type="button"
            onClick={() => void settingsQuery.refetch()}
            className="rounded-xl border border-rose-300/30 bg-rose-300/10 px-4 py-2 font-black uppercase tracking-wider text-rose-50"
          >
            Повторить
          </button>
        </section>
      );
    }

    return null;
  };

  const renderSettingsSaveFooter = () => (
    <div className="flex flex-col gap-3 border-t border-white/10 pt-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
        {settingsFormDirty ? 'Есть несохраненные изменения' : 'Все изменения сохранены'}
      </div>
      <button
        type="submit"
        disabled={!settingsFormDirty || !settingsFormValid || saveSettingsMutation.isPending || settingsQuery.isLoading}
        className="flex min-h-[44px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/[0.13] px-4 text-[10px] font-black uppercase tracking-wider text-cyan-50 shadow-neon-cyan transition-all hover:bg-cyan-300/[0.18] active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {saveSettingsMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        <span>Сохранить изменения</span>
      </button>
    </div>
  );

  const renderSwitchToggle = (config: SwitchSettingConfig) => {
    const enabled = truthySettingValue(settingsForm.watch(config.key));
    const activeClass = config.tone === 'amber'
      ? 'border-amber-300/35 bg-amber-300/[0.12] shadow-[0_0_24px_rgba(251,191,36,0.13)]'
      : 'border-cyan-300/35 bg-cyan-300/[0.12] shadow-neon-cyan';

    return (
      <label
        key={config.key}
        className={`group flex cursor-pointer items-center gap-4 rounded-2xl border p-4 transition-all ${
          enabled ? activeClass : 'border-white/10 bg-white/[0.035] hover:border-white/20'
        }`}
      >
        <input
          type="checkbox"
          {...settingsForm.register(config.key)}
          className="sr-only"
        />
        <span className={`relative h-7 w-12 shrink-0 rounded-full border transition-all ${
          enabled
            ? 'border-cyan-200/45 bg-cyan-300/30'
            : 'border-white/10 bg-black/30'
        }`}>
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow-lg transition-transform ${
            enabled ? 'translate-x-5' : 'translate-x-1'
          }`} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-black text-white">{config.title}</span>
            <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-cyan-100">
              {config.badge}
            </span>
          </span>
          <span className="mt-1 block text-xs font-semibold leading-relaxed text-slate-400">
            {config.description}
          </span>
        </span>
      </label>
    );
  };

  const renderSwitchesTab = () => {
    const state = renderSettingsQueryState(SETTINGS_TABS[1]);
    if (state) return state;

    const broadcastsPaused = truthySettingValue(settingsForm.watch('PAUSE_BROADCASTS'));
    const maintenanceMode = truthySettingValue(settingsForm.watch('MAINTENANCE_MODE'));
    const registrationsDisabled = truthySettingValue(settingsForm.watch('DISABLE_REGISTRATIONS'));

    return (
      <form
        id="settings-panel-switches"
        role="tabpanel"
        aria-labelledby="settings-tab-switches"
        onSubmit={settingsForm.handleSubmit(handleSettingsSubmit)}
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <SettingsAccordionSection
          id="switches-access"
          title="Доступ и режимы"
          subtitle="Maintenance mode и закрытый клуб для новых регистраций"
          badge={`${Number(maintenanceMode) + Number(registrationsDisabled)}/2 on`}
          Icon={ToggleLeft}
          open={sectionIsOpen('switches', 'switches-access')}
          onToggle={() => toggleAccordionSection('switches', 'switches-access')}
          dirty={settingsFormDirty}
        >
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {SWITCH_SETTINGS.map(renderSwitchToggle)}
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="switches-broadcasts"
          title="Рассылки"
          subtitle="Пауза массовых исходящих отправок и delivery outbox"
          badge={broadcastsPaused ? 'paused' : 'active'}
          Icon={BellRing}
          tone={broadcastsPaused ? 'amber' : 'cyan'}
          open={sectionIsOpen('switches', 'switches-broadcasts')}
          onToggle={() => toggleAccordionSection('switches', 'switches-broadcasts')}
          dirty={settingsFormDirty}
          status={renderStatusBadge(broadcastsPaused ? 'warning' : 'ok', broadcastsPaused ? 'пауза' : 'активно')}
        >
          <button
            type="button"
            onClick={() => settingsForm.setValue('PAUSE_BROADCASTS', !broadcastsPaused, {
              shouldDirty: true,
              shouldTouch: true,
            })}
            className={`smooth-pressable flex min-h-[116px] w-full items-center gap-4 rounded-2xl border p-4 text-left transition-all ${
              broadcastsPaused
                ? 'border-amber-300/45 bg-amber-300/[0.14] text-amber-50 shadow-[0_0_28px_rgba(251,191,36,0.13)]'
                : 'border-white/10 bg-white/[0.035] text-slate-300 hover:border-amber-300/25 hover:bg-amber-300/[0.07]'
            }`}
          >
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-amber-300/25 bg-amber-300/10 text-amber-100">
              <Power className="h-5 w-5" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-black text-white">Экстренная пауза рассылок</span>
              <span className="mt-1 block text-xs font-semibold leading-relaxed text-slate-400">
                {broadcastsPaused
                  ? 'После сохранения новые массовые отправки и delivery-очередь будут блокироваться.'
                  : 'Остановить исходящие массовые отправки одним рубильником.'}
              </span>
            </span>
          </button>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="switches-sessions"
          title="Сессии и безопасность"
          subtitle="Опасные операции с auth-сессиями пользователей"
          badge="danger"
          Icon={ShieldAlert}
          tone="rose"
          open={sectionIsOpen('switches', 'switches-sessions')}
          onToggle={() => toggleAccordionSection('switches', 'switches-sessions')}
        >
          <button
            type="button"
            onClick={() => void handleResetUserSessions()}
            disabled={resetSessionsMutation.isPending}
            className="smooth-pressable flex min-h-[116px] w-full items-center gap-4 rounded-2xl border border-rose-400/35 bg-rose-500/[0.12] p-4 text-left text-rose-50 shadow-[0_0_28px_rgba(244,63,94,0.13)] transition-all hover:bg-rose-500/[0.17] active:scale-[0.99] disabled:opacity-60"
          >
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-rose-300/30 bg-rose-300/10 text-rose-100">
              {resetSessionsMutation.isPending ? <Loader2 className="h-5 w-5 animate-spin" /> : <ShieldAlert className="h-5 w-5" />}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-black text-white">Сбросить все сессии пользователей</span>
              <span className="mt-1 block text-xs font-semibold leading-relaxed text-rose-100/75">
                Опасное действие: очистка временных auth-сессий в Redis. Пользователям потребуется войти заново.
              </span>
            </span>
          </button>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="switches-audit"
          title="Аудит действий"
          subtitle="Быстрый снимок текущих флагов перед сохранением"
          badge="read"
          Icon={Activity}
          tone="violet"
          open={sectionIsOpen('switches', 'switches-audit')}
          onToggle={() => toggleAccordionSection('switches', 'switches-audit')}
        >
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {renderMetricCard('Maintenance', maintenanceMode ? 'ON' : 'OFF', 'Ограничение пользовательских сценариев.', maintenanceMode ? 'amber' : 'emerald')}
            {renderMetricCard('Регистрации', registrationsDisabled ? 'Закрыты' : 'Открыты', 'Контроль входа новых пользователей.', registrationsDisabled ? 'amber' : 'emerald')}
            {renderMetricCard('Рассылки', broadcastsPaused ? 'Пауза' : 'Активны', 'Контроль массовой доставки.', broadcastsPaused ? 'amber' : 'emerald')}
          </div>
        </SettingsAccordionSection>

        {renderSettingsSaveFooter()}
      </form>
    );
  };

  const renderIntegrationField = (field: IntegrationFieldConfig) => {
    const configured = Boolean(adminSettingsByKey.get(field.key)?.is_configured || settingsForm.watch(field.key));
    const visible = visibleSecrets[field.key];
    const isSecret = field.kind === 'secret';
    const isBoolean = field.kind === 'boolean';
    const FieldIcon = isSecret ? KeyRound : field.kind === 'url' ? Link2 : Database;
    const helpText = INTEGRATION_FIELD_HELP[field.key] || field.description;

    if (isBoolean) {
      const enabled = truthySettingValue(settingsForm.watch(field.key));
      return (
        <label
          key={field.key}
          className={`group flex min-h-[118px] cursor-pointer items-center gap-4 rounded-2xl border p-4 transition-all ${
            enabled
              ? 'border-emerald-300/35 bg-emerald-300/[0.12] shadow-[0_0_24px_rgba(52,211,153,0.13)]'
              : 'border-white/10 bg-white/[0.035] hover:border-white/20'
          }`}
        >
          <input type="checkbox" {...settingsForm.register(field.key)} className="sr-only" />
          <span className={`relative h-7 w-12 shrink-0 rounded-full border transition-all ${
            enabled ? 'border-emerald-200/45 bg-emerald-300/30' : 'border-white/10 bg-black/30'
          }`}>
            <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow-lg transition-transform ${
              enabled ? 'translate-x-5' : 'translate-x-1'
            }`} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className="block text-xs font-black text-white">{field.label}</span>
              <IntegrationFieldHelp text={helpText} />
            </span>
            <span className="mt-1 block text-[10px] font-semibold leading-relaxed text-slate-500">{field.description}</span>
          </span>
        </label>
      );
    }

    return (
      <label key={field.key} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <span className="mb-3 flex items-start gap-3">
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${
            isSecret
              ? 'border-cyan-300/20 bg-cyan-300/10 text-cyan-100'
              : field.kind === 'url'
                ? 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100'
                : 'border-violet-300/20 bg-violet-300/10 text-violet-100'
          }`}>
            <FieldIcon className="h-4 w-4" />
          </span>
          <span className="min-w-0">
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className="block text-xs font-black text-white">{field.label}</span>
              <IntegrationFieldHelp text={helpText} />
            </span>
            <span className="mt-1 block text-[10px] font-semibold leading-relaxed text-slate-500">{field.description}</span>
          </span>
        </span>
        <span className="relative block">
          <input
            type={isSecret && !visible ? 'password' : field.kind === 'url' ? 'url' : 'text'}
            autoComplete={isSecret ? 'new-password' : 'off'}
            placeholder={configured ? 'Значение загружено' : field.placeholder || 'Введите значение'}
            {...settingsForm.register(field.key)}
            className={`w-full rounded-2xl border border-white/10 bg-white/5 py-3 text-[16px] font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:ring-2 sm:text-sm ${
              isSecret
                ? 'pl-3 pr-11 focus:border-cyan-300/45 focus:ring-cyan-300/10'
                : field.kind === 'url'
                  ? 'px-3 focus:border-emerald-300/45 focus:ring-emerald-300/10'
                  : 'px-3 focus:border-violet-300/45 focus:ring-violet-300/10'
            }`}
          />
          {isSecret && (
            <button
              type="button"
              onClick={() => toggleSecretVisibility(field.key)}
              aria-label={visible ? 'Скрыть значение' : 'Показать значение'}
              className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-xl border border-white/10 bg-black/20 text-slate-300 transition-all hover:border-cyan-300/30 hover:text-cyan-100"
            >
              {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          )}
        </span>
        <span className="mt-2 block text-[10px] font-black uppercase tracking-wider text-slate-500">
          {configured ? (isSecret ? 'секрет задан' : 'значение задано') : (isSecret ? 'секрет не задан' : 'значение не задано')}
        </span>
      </label>
    );
  };

  const renderIntegrationsTab = () => {
    if (!integrationsUnlocked) {
      return (
        <section
          id="settings-panel-integrations"
          role="tabpanel"
          aria-labelledby="settings-tab-integrations"
          className="rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
        >
          <form onSubmit={handleUnlockIntegrations} className="mx-auto flex min-h-[320px] max-w-xl flex-col items-center justify-center gap-4 text-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-3xl border border-cyan-300/25 bg-cyan-300/10 text-cyan-100 shadow-neon-cyan">
              <LockKeyhole className="h-7 w-7" />
            </span>
            <div>
              <h3 className="text-lg font-black text-white">Интеграции под паролем</h3>
              <p className="mt-2 text-xs font-semibold leading-relaxed text-slate-400">
                Вкладка содержит токены, webhook-секреты, платежные ключи и внешние ссылки проекта.
              </p>
            </div>
            <div className="flex w-full flex-col gap-2 sm:flex-row">
              <input
                type="password"
                value={integrationPassword}
                onChange={(event) => setIntegrationPassword(event.target.value)}
                placeholder="Введите пароль"
                autoComplete="current-password"
                className="min-h-[46px] flex-1 rounded-2xl border border-white/10 bg-white/5 px-4 text-center text-[16px] font-black text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/45 focus:ring-2 focus:ring-cyan-300/10 sm:text-sm"
              />
              <button
                type="submit"
                disabled={unlockIntegrationsMutation.isPending}
                className="flex min-h-[46px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/[0.13] px-5 text-[10px] font-black uppercase tracking-wider text-cyan-50 shadow-neon-cyan transition-all hover:bg-cyan-300/[0.18] active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {unlockIntegrationsMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                <span>Открыть</span>
              </button>
            </div>
          </form>
        </section>
      );
    }

    return (
      <form
        id="settings-panel-integrations"
        role="tabpanel"
        aria-labelledby="settings-tab-integrations"
        onSubmit={settingsForm.handleSubmit(handleSettingsSubmit)}
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <div className="flex flex-col gap-3 rounded-2xl border border-cyan-300/15 bg-cyan-300/[0.06] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-black text-white">Сейф интеграций открыт</p>
            <p className="mt-1 text-[10px] font-semibold leading-relaxed text-cyan-100/75">
              Значения подтянуты из сохраненных настроек или runtime env сервера.
            </p>
          </div>
          <span className="shrink-0 rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-[9px] font-black uppercase tracking-wider text-cyan-100">
            {INTEGRATION_FIELDS.length} fields
          </span>
        </div>

        {INTEGRATION_GROUPS.map(group => {
          const groupDiagnostic = integrationDiagnostics[group.id];
          const configuredCount = group.fields.filter(field => (
            Boolean(adminSettingsByKey.get(field.key)?.is_configured || settingsForm.watch(field.key))
          )).length;
          const groupStatus = groupDiagnostic?.status || (
            configuredCount === 0
              ? 'missing'
              : configuredCount === group.fields.length
                ? 'ok'
                : 'warning'
          );

          return (
            <SettingsAccordionSection
              key={group.id}
              id={`integrations-${group.id}`}
              title={group.title}
              subtitle={group.subtitle}
              badge={`${configuredCount}/${group.fields.length}`}
              Icon={group.Icon}
              tone={groupStatus === 'error' ? 'rose' : groupStatus === 'warning' ? 'amber' : groupStatus === 'ok' ? 'emerald' : 'cyan'}
              open={sectionIsOpen('integrations', `integrations-${group.id}`)}
              onToggle={() => toggleAccordionSection('integrations', `integrations-${group.id}`)}
              dirty={settingsFormDirty}
              status={renderStatusBadge(groupStatus)}
            >
              <div className="mb-3 flex flex-col gap-3 rounded-2xl border border-white/10 bg-black/20 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-xs font-black text-white">Read-only диагностика</p>
                  <p className="mt-1 text-[10px] font-semibold leading-relaxed text-slate-500">
                    Проверяет наличие обязательных ключей и базовые URL-форматы без отправки сообщений, платежей и внешних действий.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleRunIntegrationDiagnostics(group.id)}
                  disabled={integrationDiagnosticsMutation.isPending}
                  className="smooth-pressable flex min-h-[40px] shrink-0 items-center justify-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/[0.1] px-3 text-[9px] font-black uppercase tracking-wider text-cyan-50 transition-all hover:bg-cyan-300/[0.16] active:scale-[0.98] disabled:opacity-50"
                >
                  {integrationDiagnosticsMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
                  <span>Проверить</span>
                </button>
              </div>

              {groupDiagnostic && (
                <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                  {groupDiagnostic.checks.map(check => (
                    <div key={`${group.id}-${check.key}`} className="flex min-w-0 items-start justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-[10px] font-black text-white">{check.label}</p>
                        <p className="mt-1 line-clamp-2 text-[9px] font-semibold text-slate-500">{check.message}</p>
                      </div>
                      {renderStatusBadge(check.status)}
                    </div>
                  ))}
                </div>
              )}

              <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                {group.fields.map(renderIntegrationField)}
              </div>
            </SettingsAccordionSection>
          );
        })}

        {renderSettingsSaveFooter()}
      </form>
    );
  };

  const renderThemeColorField = (field: ThemeColorFieldConfig) => {
    const currentValue = String(settingsForm.watch(field.key) || field.fallback);
    const safeColor = normalizeHexColor(currentValue, field.fallback);
    const error = settingsForm.formState.errors[field.key];

    return (
      <div key={field.key} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <div className="mb-4 flex items-start gap-3">
          <span
            className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/10 shadow-[0_0_24px_rgba(255,255,255,0.12)]"
            style={{ backgroundColor: safeColor }}
          >
            <Palette className="h-5 w-5 text-white drop-shadow" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-black text-white">{field.label}</p>
            <p className="mt-1 text-xs font-semibold leading-relaxed text-slate-400">{field.description}</p>
          </div>
        </div>

        <div className="grid grid-cols-[56px_minmax(0,1fr)] items-center gap-3">
          <input
            type="color"
            value={safeColor}
            onChange={(event) => settingsForm.setValue(field.key, event.target.value, {
              shouldDirty: true,
              shouldTouch: true,
              shouldValidate: true,
            })}
            className="h-12 w-14 cursor-pointer rounded-2xl border border-white/10 bg-white/5 p-1"
            aria-label={`Выбрать цвет ${field.label}`}
          />
          <input
            {...settingsForm.register(field.key, {
              pattern: {
                value: /^#[0-9a-fA-F]{6}$/,
                message: 'HEX формат #RRGGBB',
              },
            })}
            placeholder={field.fallback}
            spellCheck={false}
            className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-3 text-[16px] font-semibold uppercase text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/45 focus:ring-2 focus:ring-cyan-300/10 sm:text-sm"
          />
        </div>
        {error?.message && (
          <p className="mt-2 text-[10px] font-bold uppercase tracking-wider text-rose-200">
            {String(error.message)}
          </p>
        )}
      </div>
    );
  };

  const renderThemeTextField = (field: ThemeTextFieldConfig) => (
    <label key={field.key} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
      <span className="mb-3 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-emerald-300/20 bg-emerald-300/10 text-emerald-100">
          <Link2 className="h-4 w-4" />
        </span>
        <span className="min-w-0">
          <span className="block text-xs font-black text-white">{field.label}</span>
          <span className="mt-1 block text-[10px] font-semibold leading-relaxed text-slate-500">{field.description}</span>
        </span>
      </span>
      <input
        type="url"
        {...settingsForm.register(field.key)}
        placeholder={field.placeholder}
        className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-3 text-[16px] font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-emerald-300/45 focus:ring-2 focus:ring-emerald-300/10 sm:text-sm"
      />
    </label>
  );

  const renderThemeNumberField = (field: ThemeNumberFieldConfig) => {
    const fallback = {
      [THEME_GLASS_OPACITY_KEY]: DEFAULT_THEME_SETTINGS.glass_opacity,
      [THEME_GLASS_BLUR_PX_KEY]: DEFAULT_THEME_SETTINGS.glass_blur_px,
      [THEME_RADIUS_SCALE_KEY]: DEFAULT_THEME_SETTINGS.radius_scale,
      [THEME_FONT_SCALE_KEY]: DEFAULT_THEME_SETTINGS.font_scale,
      [THEME_GLOW_STRENGTH_KEY]: DEFAULT_THEME_SETTINGS.glow_strength,
    }[field.key];
    const rawValue = settingsForm.watch(field.key);
    const numberValue = Number(rawValue || fallback);
    const safeValue = Number.isFinite(numberValue)
      ? Math.min(field.max, Math.max(field.min, numberValue))
      : fallback;
    const setValue = (value: string) => {
      settingsForm.setValue(field.key, value, {
        shouldDirty: true,
        shouldTouch: true,
        shouldValidate: true,
      });
    };

    return (
      <label key={field.key} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <span className="mb-3 flex min-w-0 items-start justify-between gap-3">
          <span className="min-w-0">
            <span className="block text-xs font-black text-white">{field.label}</span>
            <span className="mt-1 block text-[10px] font-semibold leading-relaxed text-slate-500">{field.description}</span>
          </span>
          <span className="shrink-0 rounded-xl border border-white/10 bg-black/20 px-2 py-1 text-[9px] font-black uppercase tracking-wider text-cyan-100">
            {safeValue}{field.suffix || ''}
          </span>
        </span>
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={safeValue}
          onChange={(event) => setValue(event.target.value)}
          className="w-full accent-cyan-300"
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={String(rawValue ?? fallback)}
          onChange={(event) => setValue(event.target.value)}
          className="mt-3 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-[16px] font-semibold text-white outline-none transition-all focus:border-cyan-300/45 focus:ring-2 focus:ring-cyan-300/10 sm:text-sm"
        />
      </label>
    );
  };

  const renderThemeTab = () => {
    const state = renderSettingsQueryState(SETTINGS_TABS[3]);
    if (state) return state;

    const performanceMode = truthySettingValue(settingsForm.watch(GLOBAL_PERFORMANCE_MODE_KEY));
    const density = String(settingsForm.watch(THEME_DENSITY_KEY) || DEFAULT_THEME_SETTINGS.theme_density);
    const primaryColor = normalizeHexColor(settingsForm.watch(THEME_PRIMARY_COLOR_KEY), DEFAULT_THEME_SETTINGS.primary_color);
    const secondaryColor = normalizeHexColor(settingsForm.watch(THEME_SECONDARY_COLOR_KEY), DEFAULT_THEME_SETTINGS.secondary_color);
    const logoUrl = String(settingsForm.watch(BRAND_LOGO_URL_KEY) || '').trim();
    const backgroundUrl = String(settingsForm.watch(BRAND_BACKGROUND_URL_KEY) || '').trim();

    return (
      <form
        id="settings-panel-theme"
        role="tabpanel"
        aria-labelledby="settings-tab-theme"
        onSubmit={settingsForm.handleSubmit(handleSettingsSubmit)}
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <SettingsAccordionSection
          id="theme-colors"
          title="Цвета и пресеты"
          subtitle="Основной и дополнительный неон интерфейса"
          badge="colors"
          Icon={Palette}
          open={sectionIsOpen('theme', 'theme-colors')}
          onToggle={() => toggleAccordionSection('theme', 'theme-colors')}
          dirty={settingsFormDirty}
        >
          <div className="mb-3 flex flex-wrap gap-2">
            {THEME_PRESETS.map(preset => (
              <button
                key={preset.id}
                type="button"
                onClick={() => handleApplyThemePreset(preset)}
                className="smooth-pressable flex min-h-[38px] items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-[9px] font-black uppercase tracking-wider text-slate-200 transition-all hover:border-cyan-300/30 hover:bg-cyan-300/[0.08]"
              >
                <span className="h-4 w-4 rounded-full border border-white/20" style={{ backgroundColor: preset.primary }} />
                <span className="h-4 w-4 rounded-full border border-white/20" style={{ backgroundColor: preset.secondary }} />
                <span>{preset.label}</span>
              </button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {THEME_COLOR_FIELDS.map(renderThemeColorField)}
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="theme-brand"
          title="Бренд-кит"
          subtitle="Логотип и фон задаются только URL-полями"
          badge="url"
          Icon={Globe2}
          tone="emerald"
          open={sectionIsOpen('theme', 'theme-brand')}
          onToggle={() => toggleAccordionSection('theme', 'theme-brand')}
          dirty={settingsFormDirty}
        >
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {THEME_TEXT_FIELDS.map(renderThemeTextField)}
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="theme-effects"
          title="Стекло и производительность"
          subtitle="Opacity, blur, радиусы, шрифт, плотность и glow"
          badge={performanceMode ? 'perf on' : density}
          Icon={MonitorSmartphone}
          tone={performanceMode ? 'emerald' : 'cyan'}
          open={sectionIsOpen('theme', 'theme-effects')}
          onToggle={() => toggleAccordionSection('theme', 'theme-effects')}
          dirty={settingsFormDirty}
          status={renderStatusBadge(performanceMode ? 'ok' : 'missing', performanceMode ? 'perf' : 'fx')}
        >
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {THEME_NUMBER_FIELDS.map(renderThemeNumberField)}
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
              <p className="text-xs font-black text-white">Плотность интерфейса</p>
              <p className="mt-1 text-[10px] font-semibold leading-relaxed text-slate-500">
                Управляет общей компактностью рабочих экранов.
              </p>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {(['compact', 'cozy', 'comfortable'] as const).map(item => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => settingsForm.setValue(THEME_DENSITY_KEY, item, {
                      shouldDirty: true,
                      shouldTouch: true,
                    })}
                    className={`min-h-[38px] rounded-xl border px-2 text-[9px] font-black uppercase tracking-wider transition-all ${
                      density === item
                        ? 'border-cyan-300/35 bg-cyan-300/[0.13] text-white shadow-neon-cyan'
                        : 'border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20'
                    }`}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>

            <label className={`group flex cursor-pointer items-center gap-4 rounded-2xl border p-4 transition-all ${
              performanceMode
                ? 'border-emerald-300/35 bg-emerald-300/[0.12] shadow-[0_0_24px_rgba(52,211,153,0.13)]'
                : 'border-white/10 bg-white/[0.035] hover:border-white/20'
            }`}>
              <input
                type="checkbox"
                {...settingsForm.register(GLOBAL_PERFORMANCE_MODE_KEY)}
                className="sr-only"
              />
              <span className={`relative h-7 w-12 shrink-0 rounded-full border transition-all ${
                performanceMode
                  ? 'border-emerald-200/45 bg-emerald-300/30'
                  : 'border-white/10 bg-black/30'
              }`}>
                <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow-lg transition-transform ${
                  performanceMode ? 'translate-x-5' : 'translate-x-1'
                }`} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-black text-white">Глобальный Performance Mode</span>
                  <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-emerald-100">
                    effects
                  </span>
                </span>
                <span className="mt-1 block text-xs font-semibold leading-relaxed text-slate-400">
                  Отключает тяжелые ambient/glass/nav-анимации и снижает стоимость переходов для всего приложения.
                </span>
              </span>
            </label>
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="theme-preview"
          title="Live preview"
          subtitle="Как текущие значения будут выглядеть после сохранения"
          badge="preview"
          Icon={Sparkles}
          tone="violet"
          open={sectionIsOpen('theme', 'theme-preview')}
          onToggle={() => toggleAccordionSection('theme', 'theme-preview')}
        >
          <div
            className="relative overflow-hidden rounded-3xl border border-white/10 bg-black/30 p-5"
            style={{
              backgroundImage: backgroundUrl ? `linear-gradient(rgba(2,6,23,0.72), rgba(2,6,23,0.72)), url("${backgroundUrl.replace(/"/g, '%22')}")` : undefined,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            }}
          >
            <div className="flex flex-wrap items-center gap-3">
              <span
                className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/20 bg-white/10 text-xs font-black text-white shadow-neon-cyan"
                style={{ boxShadow: `0 0 24px ${primaryColor}44` }}
              >
                {logoUrl ? <img src={logoUrl} alt="" className="h-full w-full rounded-2xl object-cover" /> : 'S'}
              </span>
              <span className="h-8 w-8 rounded-full border border-white/20" style={{ backgroundColor: primaryColor }} />
              <span className="h-8 w-8 rounded-full border border-white/20" style={{ backgroundColor: secondaryColor }} />
              <div className="min-w-0">
                <p className="text-xs font-black text-white">Shamrai Analytics Hub</p>
                <p className="mt-1 text-[10px] font-semibold text-slate-300">
                  После сохранения тема применится через CSS variables без перезагрузки страницы.
                </p>
              </div>
            </div>
          </div>
        </SettingsAccordionSection>

        {renderSettingsSaveFooter()}
      </form>
    );
  };

  const renderMonitoringTab = () => {
    const summary = monitoringSummaryQuery.data;
    const onlineUsers = summary?.online?.online_users ?? onlineQuery.data?.online_users ?? 0;
    const parserStatus = summary?.parser?.status || parserStatusQuery.data?.status || 'error';
    const parserActive = parserStatus === 'active' && !parserStatusQuery.isError;
    const parserLastSyncRaw = summary?.parser?.last_sync || parserStatusQuery.data?.last_sync;
    const parserLastSync = parserLastSyncRaw
      ? new Date(parserLastSyncRaw).toLocaleString('ru-RU')
      : 'нет данных';
    const logs = monitoringLogsQuery.data?.logs || [];
    const healthEntries = Object.entries(summary?.health || {});
    const deliveryEntries = Object.entries(summary?.delivery_outbox || {});
    const paymentAudit = summary?.payment_reconciliation;
    const paymentIssueCount = paymentAudit?.total_issues ?? 0;
    const paymentAuditStatus = paymentIssueCount > 0 ? 'warning' : 'ok';
    const paymentAuditCodes = Object.entries(paymentAudit?.by_code || {});
    const rateLimitEntries = Object.entries(summary?.rate_limit || {});
    const auditEntries = summary?.audit || [];
    const formatMonitoringValue = (value: unknown) => {
      if (value === null || value === undefined || value === '') return 'none';
      if (typeof value === 'object') return JSON.stringify(value);
      return String(value);
    };

    return (
      <section
        id="settings-panel-monitoring"
        role="tabpanel"
        aria-labelledby="settings-tab-monitoring"
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <SettingsAccordionSection
          id="monitoring-overview"
          title="Онлайн и parser"
          subtitle="Redis presence и синтетический статус парсера"
          badge={monitoringSummaryQuery.isFetching ? 'sync' : 'live'}
          Icon={Activity}
          open={sectionIsOpen('monitoring', 'monitoring-overview')}
          onToggle={() => toggleAccordionSection('monitoring', 'monitoring-overview')}
          status={renderStatusBadge(parserActive ? 'active' : 'error')}
        >
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(280px,420px)]">
            <div className="rounded-3xl border border-cyan-300/20 bg-cyan-300/[0.075] p-5 shadow-neon-cyan">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-100">Онлайн статистика</p>
                  <div className="mt-3 text-5xl font-black leading-none text-white tabular-nums sm:text-6xl">
                    {onlineQuery.isLoading && !summary ? '...' : onlineUsers}
                  </div>
                  <p className="mt-2 text-xs font-semibold text-cyan-100/75">
                    активных пользователей по Redis heartbeat
                  </p>
                </div>
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/10 text-cyan-100">
                  {onlineQuery.isFetching || monitoringSummaryQuery.isFetching ? <Loader2 className="h-5 w-5 animate-spin" /> : <Activity className="h-5 w-5" />}
                </span>
              </div>
            </div>

            <div className="rounded-3xl border border-white/10 bg-white/[0.035] p-5">
              <div className="flex items-start gap-4">
                <span className={`mt-1 h-4 w-4 shrink-0 rounded-full ${
                  parserActive ? 'animate-pulse bg-green-500 shadow-[0_0_18px_rgba(34,197,94,0.45)]' : 'bg-rose-500 shadow-[0_0_18px_rgba(244,63,94,0.35)]'
                }`} />
                <div className="min-w-0">
                  <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Статус парсера</p>
                  <h3 className="mt-2 text-xl font-black text-white">
                    {parserActive ? 'Active' : 'Error'}
                  </h3>
                  <p className="mt-2 flex items-center gap-2 text-xs font-semibold text-slate-400">
                    <Clock3 className="h-4 w-4 text-slate-500" />
                    <span>{parserLastSync}</span>
                  </p>
                </div>
              </div>
            </div>
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="monitoring-health"
          title="Health и rate-limit"
          subtitle="API, база данных и лимиты безопасности"
          badge="health"
          Icon={Database}
          tone="emerald"
          open={sectionIsOpen('monitoring', 'monitoring-health')}
          onToggle={() => toggleAccordionSection('monitoring', 'monitoring-health')}
        >
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
            <div className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.035] p-4">
              <p className="text-xs font-black text-white">Системное здоровье</p>
              {(healthEntries.length ? healthEntries : [['api', 'loading']]).map(([key, value]) => (
                <div key={key} className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-black/20 px-3 py-2">
                  <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">{key}</span>
                  {renderStatusBadge(String(value || 'none'))}
                </div>
              ))}
            </div>
            <div className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.035] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-black text-white">Payment audit</p>
                  <p className="mt-1 text-[10px] font-semibold text-slate-500">
                    {paymentAudit?.total_attempts_scanned ?? 0} attempts / {paymentAudit?.window_hours ?? 48}h
                  </p>
                </div>
                {renderStatusBadge(paymentAuditStatus)}
              </div>
              <div className="rounded-xl border border-white/[0.08] bg-black/20 px-3 py-2">
                <p className="text-2xl font-black text-white tabular-nums">{paymentIssueCount}</p>
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">issues</p>
              </div>
              {paymentAuditCodes.length > 0 && (
                <div className="space-y-1">
                  {paymentAuditCodes.slice(0, 3).map(([key, value]) => (
                    <div key={key} className="flex min-w-0 items-center justify-between gap-3 text-[10px] font-semibold text-slate-300">
                      <span className="truncate">{key}</span>
                      <span className="tabular-nums text-amber-200">{value}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.035] p-4">
              <p className="text-xs font-black text-white">Rate-limit metrics</p>
              {(rateLimitEntries.length ? rateLimitEntries : [['status', 'empty']]).map(([key, value]) => (
                <div key={key} className="flex min-w-0 items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-black/20 px-3 py-2">
                  <span className="truncate text-[10px] font-black uppercase tracking-wider text-slate-400">{key}</span>
                  <span className="max-w-[60%] truncate text-right text-[10px] font-semibold text-slate-200">{formatMonitoringValue(value)}</span>
                </div>
              ))}
            </div>
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="monitoring-delivery"
          title="Delivery outbox"
          subtitle="Метрики очереди исходящей доставки"
          badge={`${deliveryEntries.length} метрик`}
          Icon={BellRing}
          tone="amber"
          open={sectionIsOpen('monitoring', 'monitoring-delivery')}
          onToggle={() => toggleAccordionSection('monitoring', 'monitoring-delivery')}
        >
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(deliveryEntries.length ? deliveryEntries : [['status', 'empty']]).map(([key, value]) => (
              <div key={key} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
                <p className="truncate text-[10px] font-black uppercase tracking-wider text-slate-400">{key}</p>
                <p className="mt-2 truncate text-xl font-black text-white tabular-nums">{formatMonitoringValue(value)}</p>
              </div>
            ))}
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="monitoring-logs"
          title="Recent backend logs"
          subtitle="Последние строки логов без секретов и env-значений"
          badge={monitoringLogsQuery.isFetching ? 'sync' : `${logs.length} lines`}
          Icon={Download}
          tone="violet"
          open={sectionIsOpen('monitoring', 'monitoring-logs')}
          onToggle={() => toggleAccordionSection('monitoring', 'monitoring-logs')}
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-[10px] font-semibold leading-relaxed text-slate-500">
              Этот файл можно прислать для разбора ошибки: отчет не содержит токены, пароли, DB URL и `.env`.
            </p>
            <button
              type="button"
                onClick={() => void handleDownloadMonitoringLog()}
              className="smooth-pressable flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/[0.1] px-3 text-[9px] font-black uppercase tracking-wider text-cyan-50 transition-all hover:bg-cyan-300/[0.16] active:scale-[0.98]"
            >
              <Download className="h-4 w-4" />
              <span>Скачать отчет</span>
            </button>
          </div>
          <div className="max-h-[320px] overflow-y-auto rounded-2xl border border-white/10 bg-[#020617] p-4 font-mono text-[11px] leading-relaxed text-emerald-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
            {logs.length ? logs.map((line, index) => (
              <div key={`${line}-${index}`} className="break-words">
                <span className="text-cyan-300">$</span> {line}
              </div>
            )) : (
              <div className="text-slate-500">
                {monitoringLogsQuery.isLoading ? 'Загрузка логов...' : 'Логи пока пустые'}
              </div>
            )}
          </div>
        </SettingsAccordionSection>

        <SettingsAccordionSection
          id="monitoring-audit"
          title="Audit trail"
          subtitle="Последние админские действия из backend-аудита"
          badge={`${auditEntries.length} rows`}
          Icon={ShieldAlert}
          tone="rose"
          open={sectionIsOpen('monitoring', 'monitoring-audit')}
          onToggle={() => toggleAccordionSection('monitoring', 'monitoring-audit')}
        >
          <div className="space-y-2">
            {auditEntries.length ? auditEntries.map((entry, index) => (
              <div key={`${entry.id || index}-${entry.action || 'audit'}`} className="grid grid-cols-1 gap-2 rounded-2xl border border-white/10 bg-white/[0.035] p-3 text-[10px] font-semibold text-slate-300 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <p className="truncate font-black text-white">{formatMonitoringValue(entry.action)}</p>
                  <p className="mt-1 truncate text-slate-500">actor: {formatMonitoringValue(entry.actor_id)}</p>
                </div>
                <span className="text-slate-500">{formatMonitoringValue(entry.created_at)}</span>
              </div>
            )) : (
              <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-[10px] font-semibold text-slate-500">
                Аудит пока пуст или еще не загружен.
              </div>
            )}
          </div>
        </SettingsAccordionSection>
      </section>
    );
  };

  const renderTextTabState = () => {
    if (loading) {
      return (
        <div
          id="settings-panel-texts"
          role="tabpanel"
          aria-labelledby="settings-tab-texts"
          className="flex min-h-[44vh] items-center justify-center rounded-3xl border border-white/10 bg-slate-950/42 shadow-glass backdrop-blur-xl"
        >
          <Loader2 className="h-7 w-7 animate-spin text-cyan-300" />
        </div>
      );
    }

    if (loadError) {
      return (
        <div
          id="settings-panel-texts"
          role="tabpanel"
          aria-labelledby="settings-tab-texts"
          className="space-y-3 rounded-2xl border border-rose-500/25 bg-rose-500/10 p-5 text-center text-xs text-rose-100"
        >
          <AlertTriangle className="mx-auto h-7 w-7 text-rose-300" />
          <p className="font-bold">{loadError}</p>
          <button
            type="button"
            onClick={() => void loadTemplates()}
            className="rounded-xl border border-rose-300/30 bg-rose-300/10 px-4 py-2 font-black uppercase tracking-wider text-rose-50"
          >
            Повторить
          </button>
        </div>
      );
    }

    return null;
  };

  const renderActiveTab = () => {
    switch (activeTab) {
      case 'switches':
        return renderSwitchesTab();
      case 'integrations':
        return renderIntegrationsTab();
      case 'theme':
        return renderThemeTab();
      case 'monitoring':
        return renderMonitoringTab();
      case 'texts':
      default:
        return renderTextTabState();
    }
  };

  return (
    <div className="min-w-0 max-w-full space-y-4 overflow-x-hidden pb-[calc(env(safe-area-inset-bottom,0px)+5rem)] animate-slide-up sm:space-y-5">
      <div className="min-w-0 rounded-3xl border border-white/10 bg-slate-950/42 p-3 shadow-glass backdrop-blur-xl sm:p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="flex items-center text-lg font-black uppercase tracking-wider text-white">
            <Settings2 className="mr-2 h-5 w-5 text-cyan-300" />
            Настройки
            </h2>
            <p className="mt-0.5 text-[10px] font-bold uppercase tracking-widest text-slate-400">
              Разделы панели
            </p>
          </div>
          <div className="flex w-full items-center justify-center gap-2 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-[10px] font-black uppercase tracking-wider text-cyan-100 sm:w-auto">
            <Sparkles className="h-4 w-4 text-cyan-200" />
            <span>{settingsStatusText}</span>
          </div>
        </div>

        <div className="relative mt-4 min-w-0 max-w-full">
          <div
            role="tablist"
            aria-label="Подвкладки настроек"
            className="grid w-full min-w-0 grid-cols-2 gap-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5"
          >
          {SETTINGS_TABS.map(tab => {
            const TabIcon = tab.Icon;
            const active = activeTab === tab.id;
            const statusText = tab.id === 'texts'
              ? dirtyTemplateCount > 0
                ? `${dirtyTemplateCount} черновик`
                : loading
                  ? 'загрузка'
                  : loadError
                    ? 'ошибка'
                    : `${customTemplateCount}/${templates.length}`
              : tab.badge;

            return (
              <button
                key={tab.id}
                id={`settings-tab-${tab.id}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`settings-panel-${tab.id}`}
                onClick={() => setActiveTab(tab.id)}
                className={`smooth-pressable grid min-h-[78px] min-w-0 grid-cols-[2.25rem_minmax(0,1fr)] items-center gap-x-2 gap-y-1 rounded-2xl border p-2 text-left transition-all sm:min-h-[72px] sm:grid-cols-[2.25rem_minmax(0,1fr)_auto] sm:p-2.5 ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/[0.13] text-white shadow-neon-cyan'
                    : 'border-white/10 bg-white/5 text-slate-300 backdrop-blur hover:border-white/20 hover:bg-white/[0.08]'
                }`}
              >
                <span className={`row-span-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/15 text-cyan-100'
                    : 'border-white/10 bg-black/20 text-slate-400'
                }`}>
                  <TabIcon className="h-4 w-4" />
                </span>
                <span className="min-w-0 self-end">
                  <span className="block text-[11px] font-black leading-tight text-white">
                    {tab.title}
                  </span>
                  <span className="mt-0.5 block text-[10px] font-bold leading-tight text-slate-400">
                    {tab.subtitle}
                  </span>
                </span>
                <span className="col-start-2 w-fit max-w-full self-start rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black leading-none text-cyan-100 sm:col-start-auto sm:row-span-2 sm:self-center">
                  {statusText}
                </span>
              </button>
            );
          })}
          </div>
        </div>

        <div className="mt-2 flex justify-end">
          {renderSectionControls()}
        </div>
      </div>

      {renderActiveTab()}

      {activeTab === 'texts' && !loading && !loadError && (
      <div
        id="settings-panel-texts"
        role="tabpanel"
        aria-labelledby="settings-tab-texts"
        className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]"
      >
        <aside className="space-y-3 rounded-3xl border border-white/10 bg-[#050b16]/88 p-3 shadow-glass backdrop-blur-xl sm:p-4 xl:sticky xl:top-4 xl:self-start">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-black uppercase tracking-wider text-white">Шаблоны</p>
              <p className="mt-0.5 truncate text-[10px] font-bold text-slate-500">
                Выберите текст и редактируйте ниже
              </p>
            </div>
            <span className="shrink-0 rounded-xl border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[9px] font-black uppercase tracking-wider text-slate-300">
              {templates.length}
            </span>
          </div>

          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Найти шаблон"
              className="w-full rounded-2xl border border-white/10 bg-slate-900/70 py-2.5 pl-9 pr-3 text-[16px] font-bold text-white placeholder-slate-600 outline-none transition-all focus:border-cyan-300/45 sm:text-xs"
            />
          </div>

          <div className="max-h-[34dvh] space-y-2.5 overflow-y-auto pr-1 overscroll-contain lg:max-h-none lg:overflow-visible lg:pr-0 xl:max-h-[calc(100dvh-220px)] xl:overflow-y-auto xl:pr-1">
            {groupedTemplates.map(group => {
              const tone = GROUP_TONE[group.id];
              const hasSearch = Boolean(searchTerm.trim());
              const open = hasSearch ? group.templates.length > 0 : expandedGroups[group.id];
              const dirtyCount = group.templates.filter(template => (
                (drafts[template.key] ?? template.body) !== template.body
              )).length;
              const customCount = group.templates.filter(template => template.is_custom).length;
              const GroupIcon = group.Icon;

              return (
                <div key={group.id} className="overflow-hidden rounded-2xl border border-white/[0.08] bg-black/20">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.id)}
                    aria-expanded={open}
                    className={`smooth-pressable flex w-full items-center gap-3 border px-3 py-3 text-left transition-all ${tone.header}`}
                  >
                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-[10px] font-black ${tone.marker}`}>
                      <GroupIcon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-xs font-black uppercase tracking-wider text-white">
                          {group.title}
                        </span>
                        <span className={`shrink-0 rounded-lg border px-1.5 py-0.5 text-[8px] font-black uppercase tracking-wider ${tone.marker}`}>
                          {group.badge}
                        </span>
                      </span>
                      <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-400">
                        {group.subtitle}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-slate-300">
                        {group.templates.length}/{group.total}
                      </span>
                      {(dirtyCount > 0 || customCount > 0) && (
                        <span className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-amber-100">
                          {dirtyCount || customCount}
                        </span>
                      )}
                      <ChevronDown className={`h-4 w-4 text-slate-300 transition-transform ${open ? 'rotate-180' : ''}`} />
                    </span>
                  </button>

                  <SmoothCollapse open={open}>
                    <div className="space-y-2 p-2">
                      {group.templates.length === 0 ? (
                        <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-3 text-[10px] font-bold text-slate-500">
                          Ничего не найдено
                        </div>
                      ) : group.templates.map(template => {
                        const active = activeTemplate?.key === template.key;
                        const dirty = (drafts[template.key] ?? template.body) !== template.body;
                        return (
                          <button
                            key={`${group.id}-${template.key}`}
                            type="button"
                            onClick={() => handleSelectTemplate(template.key)}
                            className={`w-full rounded-xl border p-3 text-left transition-all active:scale-[0.99] ${
                              active
                                ? tone.activeCard
                                : 'border-white/[0.08] bg-[#07101d]/78 hover:border-white/[0.16] hover:bg-[#0b1728]'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p className="truncate text-xs font-black text-white">{template.title}</p>
                                <p className="mt-1 line-clamp-2 text-[10px] font-semibold leading-relaxed text-slate-400">
                                  {template.description}
                                </p>
                              </div>
                              <span className={`shrink-0 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider ${
                                dirty
                                  ? 'bg-amber-400/15 text-amber-200'
                                  : template.is_custom
                                    ? 'bg-emerald-400/15 text-emerald-200'
                                    : 'bg-slate-800 text-slate-400'
                              }`}>
                                {dirty ? 'черновик' : template.is_custom ? 'свой' : 'база'}
                              </span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </SmoothCollapse>
                </div>
              );
            })}
          </div>
        </aside>

        {activeTemplate && (
          <section ref={editorPanelRef} className="scroll-mt-4 grid min-w-0 grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4">
              <SettingsAccordionSection
                id="texts-editor"
                title={activeTemplate.title}
                subtitle={`Обновлено: ${formatTemplateDate(activeTemplate.updated_at)}`}
                badge={isDirty ? 'черновик' : 'готово'}
                Icon={Braces}
                tone={isDirty ? 'amber' : 'cyan'}
                open={sectionIsOpen('texts', 'texts-editor')}
                onToggle={() => toggleAccordionSection('texts', 'texts-editor')}
                dirty={isDirty}
                status={renderStatusBadge(isDirty ? 'warning' : 'ok', isDirty ? 'правки' : 'ok')}
              >
                <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <h3 className="text-base font-black leading-snug text-white sm:truncate">{activeTemplate.title}</h3>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      <span className="flex items-center gap-1">
                        <Clock3 className="h-3.5 w-3.5" />
                        {formatTemplateDate(activeTemplate.updated_at)}
                      </span>
                      {isDirty && <span className="text-amber-200">есть несохраненные правки</span>}
                    </div>
                  </div>

                  {renderTemplateActions(activeTemplate, 'grid grid-cols-2 gap-2 sm:flex')}
                </div>

                <div className="space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] font-black uppercase tracking-wider text-slate-400">
                    <span>Текст сообщения</span>
                    <span className="rounded-lg border border-cyan-300/15 bg-cyan-300/[0.07] px-2 py-1 text-cyan-100">
                      {activeTemplate.variables.length} поля защищены
                    </span>
                  </div>
                  <div className="flex flex-wrap items-start gap-2 rounded-2xl border border-cyan-300/20 bg-[#07111f] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_18px_48px_rgba(0,0,0,0.25)]">
                    {editorTokens.map((token, index) => {
                      if (token.type === 'markup') return null;
                      if (token.type === 'variable') {
                        return (
                          <div
                            key={`${token.type}-${index}-${token.key}`}
                            className="inline-flex max-w-full items-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/[0.11] px-3 py-2 text-[11px] font-black text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.08)]"
                          >
                            <Braces className="h-3.5 w-3.5 shrink-0 text-cyan-200" />
                            <span className="truncate">{token.label}</span>
                            {token.example && (
                              <span className="hidden max-w-[140px] truncate rounded-lg bg-black/20 px-2 py-1 text-[9px] font-bold text-cyan-100/70 sm:inline">
                                {token.example}
                              </span>
                            )}
                          </div>
                        );
                      }
                      if (!token.value.trim()) return null;
                      const compactText = !token.value.includes('\n') && token.value.trim().length <= 18;
                      if (compactText) {
                        return (
                          <input
                            key={`${token.type}-${index}`}
                            value={token.value}
                            onChange={(event) => handleTextTokenChange(index, event.target.value)}
                            spellCheck={false}
                            aria-label={`Редактируемый текст ${index + 1}`}
                            style={{ '--editor-token-width': `${Math.max(4, Math.min(22, token.value.length + 2))}ch` } as React.CSSProperties}
                            className="min-h-[42px] w-full max-w-full rounded-xl border border-slate-600/70 bg-[#0b1728] px-3 text-[16px] font-semibold text-[#e8f3ff] shadow-inner outline-none transition-all [color-scheme:dark] selection:bg-cyan-300/25 focus:border-cyan-300/65 focus:bg-[#0d1b30] focus:ring-2 focus:ring-cyan-300/15 sm:w-[var(--editor-token-width)] sm:text-[13px]"
                          />
                        );
                      }
                      return (
                        <textarea
                          key={`${token.type}-${index}`}
                          value={token.value}
                          onChange={(event) => handleTextTokenChange(index, event.target.value)}
                          rows={rowsForEditableText(token.value)}
                          spellCheck={false}
                          aria-label={`Редактируемый текст ${index + 1}`}
                          className="w-full basis-full resize-y rounded-xl border border-slate-600/70 bg-[#0b1728] px-3.5 py-3 text-[16px] font-semibold leading-relaxed text-[#e8f3ff] shadow-inner outline-none transition-all [color-scheme:dark] selection:bg-cyan-300/25 placeholder:text-slate-500 focus:border-cyan-300/65 focus:bg-[#0d1b30] focus:ring-2 focus:ring-cyan-300/15 sm:text-[13px]"
                        />
                      );
                    })}
                  </div>
                </div>

                {renderTemplateActions(activeTemplate, 'mt-3 grid grid-cols-2 gap-2 border-t border-white/10 pt-3 sm:hidden')}
              </SettingsAccordionSection>

              <SettingsAccordionSection
                id="texts-variables"
                title="Переменные шаблона"
                subtitle="Защищенные поля, которые подставляет система"
                badge={`${activeTemplate.variables.length} fields`}
                Icon={Braces}
                tone="violet"
                open={sectionIsOpen('texts', 'texts-variables')}
                onToggle={() => toggleAccordionSection('texts', 'texts-variables')}
              >
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {activeTemplate.variables.map(variable => (
                    <div
                      key={variable.key}
                      className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2"
                    >
                      <p className="truncate text-[10px] font-black text-cyan-100">{variable.label}</p>
                      {variable.example && (
                        <p className="mt-1 truncate text-[9px] font-semibold text-slate-500">{variable.example}</p>
                      )}
                    </div>
                  ))}
                </div>
              </SettingsAccordionSection>
            </div>

            <div className="space-y-4">
              <SettingsAccordionSection
                id="texts-preview"
                title="Предпросмотр"
                subtitle="Итоговый текст с примерными значениями"
                badge="итог"
                Icon={MessageCircle}
                tone="emerald"
                open={sectionIsOpen('texts', 'texts-preview')}
                onToggle={() => toggleAccordionSection('texts', 'texts-preview')}
              >
                <pre className="max-h-[42dvh] whitespace-pre-wrap break-words rounded-2xl border border-white/10 bg-[#07111f] p-3 text-[12px] font-semibold leading-relaxed text-slate-100 sm:max-h-[520px] sm:p-4">
                  {activePreview || 'Пустой предпросмотр'}
                </pre>
              </SettingsAccordionSection>
            </div>
          </section>
        )}
      </div>
      )}
    </div>
  );
}
