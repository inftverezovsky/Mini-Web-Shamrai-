import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { apiFetch } from '../../utils/api';
import type { MessageTemplateResponse } from '../../schemas/schemas';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';
import SmoothCollapse from '../../components/SmoothCollapse';
import {
  DEFAULT_THEME_SETTINGS,
  GLOBAL_PERFORMANCE_MODE_KEY,
  PUBLIC_THEME_QUERY_KEY,
  THEME_PRIMARY_COLOR_KEY,
  THEME_SECONDARY_COLOR_KEY,
  normalizeHexColor,
} from '../../features/settings/themeSettings';
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

interface IntegrationUnlockRequest {
  password: string;
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

const DEFAULT_ADMIN_SETTINGS_VALUES: AdminSettingsFormValues = {
  ...DEFAULT_INTEGRATION_VALUES,
  MAINTENANCE_MODE: false,
  DISABLE_REGISTRATIONS: false,
  PAUSE_BROADCASTS: false,
  THEME_PRIMARY_COLOR: DEFAULT_THEME_SETTINGS.primary_color,
  THEME_SECONDARY_COLOR: DEFAULT_THEME_SETTINGS.secondary_color,
  GLOBAL_PERFORMANCE_MODE: DEFAULT_THEME_SETTINGS.global_performance_mode,
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
    mutationFn: (payload: IntegrationUnlockRequest) => apiFetch<AdminSettingsResponse>('/admin/settings/integrations/unlock', {
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

  const handleDownloadMonitoringLog = () => {
    const timestamp = new Date().toISOString();
    const onlineUsers = onlineQuery.data?.online_users ?? 0;
    const parserStatus = parserStatusQuery.data?.status || 'unknown';
    const parserLastSync = parserStatusQuery.data?.last_sync || 'unknown';
    const logs = monitoringLogsQuery.data?.logs || [];
    const body = [
      'Shamrai Analytics Hub monitoring log',
      `created_at=${timestamp}`,
      `online_users=${onlineUsers}`,
      `parser_status=${parserStatus}`,
      `parser_last_sync=${parserLastSync}`,
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

    return (
      <form
        id="settings-panel-switches"
        role="tabpanel"
        aria-labelledby="settings-tab-switches"
        onSubmit={settingsForm.handleSubmit(handleSettingsSubmit)}
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {SWITCH_SETTINGS.map(renderSwitchToggle)}
        </div>

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(260px,360px)]">
          <button
            type="button"
            onClick={() => settingsForm.setValue('PAUSE_BROADCASTS', !broadcastsPaused, {
              shouldDirty: true,
              shouldTouch: true,
            })}
            className={`smooth-pressable flex min-h-[116px] items-center gap-4 rounded-2xl border p-4 text-left transition-all ${
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
                  ? 'Рассылки поставлены на паузу до следующего сохранения.'
                  : 'Остановить исходящие массовые отправки одним рубильником.'}
              </span>
            </span>
          </button>

          <button
            type="button"
            onClick={() => void handleResetUserSessions()}
            disabled={resetSessionsMutation.isPending}
            className="smooth-pressable flex min-h-[116px] items-center gap-4 rounded-2xl border border-rose-400/35 bg-rose-500/[0.12] p-4 text-left text-rose-50 shadow-[0_0_28px_rgba(244,63,94,0.13)] transition-all hover:bg-rose-500/[0.17] active:scale-[0.99] disabled:opacity-60"
          >
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-rose-300/30 bg-rose-300/10 text-rose-100">
              {resetSessionsMutation.isPending ? <Loader2 className="h-5 w-5 animate-spin" /> : <ShieldAlert className="h-5 w-5" />}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-black text-white">Сбросить все сессии пользователей</span>
              <span className="mt-1 block text-xs font-semibold leading-relaxed text-rose-100/75">
                Опасное действие: очистка временных auth-сессий в Redis.
              </span>
            </span>
          </button>
        </div>

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
            <span className="block text-xs font-black text-white">{field.label}</span>
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
            <span className="block text-xs font-black text-white">{field.label}</span>
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
          const GroupIcon = group.Icon;
          return (
            <section key={group.id} className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.025] p-3 sm:p-4">
              <div className="flex items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-black/20 text-cyan-100">
                  <GroupIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <h3 className="text-sm font-black text-white">{group.title}</h3>
                  <p className="mt-1 text-[10px] font-semibold leading-relaxed text-slate-500">{group.subtitle}</p>
                </div>
              </div>
              <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                {group.fields.map(renderIntegrationField)}
              </div>
            </section>
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

  const renderThemeTab = () => {
    const state = renderSettingsQueryState(SETTINGS_TABS[3]);
    if (state) return state;

    const performanceMode = truthySettingValue(settingsForm.watch(GLOBAL_PERFORMANCE_MODE_KEY));

    return (
      <form
        id="settings-panel-theme"
        role="tabpanel"
        aria-labelledby="settings-tab-theme"
        onSubmit={settingsForm.handleSubmit(handleSettingsSubmit)}
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {THEME_COLOR_FIELDS.map(renderThemeColorField)}
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

        <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="h-8 w-8 rounded-full border border-white/20 shadow-neon-cyan"
              style={{ backgroundColor: normalizeHexColor(settingsForm.watch(THEME_PRIMARY_COLOR_KEY), DEFAULT_THEME_SETTINGS.primary_color) }}
            />
            <span
              className="h-8 w-8 rounded-full border border-white/20 shadow-[0_0_18px_rgba(217,70,239,0.22)]"
              style={{ backgroundColor: normalizeHexColor(settingsForm.watch(THEME_SECONDARY_COLOR_KEY), DEFAULT_THEME_SETTINGS.secondary_color) }}
            />
            <div className="min-w-0">
              <p className="text-xs font-black text-white">Живой предпросмотр темы</p>
              <p className="mt-1 text-[10px] font-semibold text-slate-500">
                После сохранения цвета применятся к CSS variables без перезагрузки страницы.
              </p>
            </div>
          </div>
        </div>

        {renderSettingsSaveFooter()}
      </form>
    );
  };

  const renderMonitoringTab = () => {
    const onlineUsers = onlineQuery.data?.online_users ?? 0;
    const parserStatus = parserStatusQuery.data?.status || 'error';
    const parserActive = parserStatus === 'active' && !parserStatusQuery.isError;
    const parserLastSync = parserStatusQuery.data?.last_sync
      ? new Date(parserStatusQuery.data.last_sync).toLocaleString('ru-RU')
      : 'нет данных';
    const logs = monitoringLogsQuery.data?.logs || [];

    return (
      <section
        id="settings-panel-monitoring"
        role="tabpanel"
        aria-labelledby="settings-tab-monitoring"
        className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/42 p-4 shadow-glass backdrop-blur-xl sm:p-5"
      >
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(280px,420px)]">
          <div className="rounded-3xl border border-cyan-300/20 bg-cyan-300/[0.075] p-5 shadow-neon-cyan">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-100">Онлайн статистика</p>
                <div className="mt-3 text-5xl font-black leading-none text-white tabular-nums sm:text-6xl">
                  {onlineQuery.isLoading ? '...' : onlineUsers}
                </div>
                <p className="mt-2 text-xs font-semibold text-cyan-100/75">
                  активных пользователей по Redis heartbeat
                </p>
              </div>
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/10 text-cyan-100">
                {onlineQuery.isFetching ? <Loader2 className="h-5 w-5 animate-spin" /> : <Activity className="h-5 w-5" />}
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

        <div className="rounded-3xl border border-white/10 bg-white/[0.035] p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-black text-white">Живой лог ошибок</p>
              <p className="mt-1 text-[10px] font-semibold text-slate-500">Последние события мониторинга</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={handleDownloadMonitoringLog}
                className="smooth-pressable flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/[0.1] px-3 text-[9px] font-black uppercase tracking-wider text-cyan-50 transition-all hover:bg-cyan-300/[0.16] active:scale-[0.98]"
              >
                <Download className="h-4 w-4" />
                <span>Скачать лог</span>
              </button>
              <span className="rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-[9px] font-black uppercase tracking-wider text-slate-300">
                {monitoringLogsQuery.isFetching ? 'sync' : `${logs.length} lines`}
              </span>
            </div>
          </div>
          <div className="max-h-[320px] overflow-y-auto rounded-2xl border border-white/10 bg-[#020617] p-4 font-mono text-[11px] leading-relaxed text-emerald-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
            {logs.length ? (
              logs.map((line, index) => (
                <div key={`${line}-${index}`} className="break-words">
                  <span className="text-cyan-300">$</span> {line}
                </div>
              ))
            ) : (
              <div className="text-slate-500">
                {monitoringLogsQuery.isLoading ? 'Загрузка логов...' : 'Логи пока пустые'}
              </div>
            )}
          </div>
        </div>
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
            className="flex w-full min-w-0 snap-x snap-mandatory flex-row gap-3 overflow-x-auto overflow-y-hidden overscroll-x-contain scroll-smooth scrollbar-hide pb-2 pr-5 [-webkit-overflow-scrolling:touch]"
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
                className={`smooth-pressable flex min-h-[52px] min-w-[min(82vw,210px)] flex-shrink-0 snap-start items-center gap-3 whitespace-nowrap rounded-2xl border px-3 py-2 text-left transition-all sm:min-w-[240px] ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/[0.13] text-white shadow-neon-cyan'
                    : 'border-white/10 bg-white/5 text-slate-300 backdrop-blur hover:border-white/20 hover:bg-white/[0.08]'
                }`}
              >
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/15 text-cyan-100'
                    : 'border-white/10 bg-black/20 text-slate-400'
                }`}>
                  <TabIcon className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-black uppercase tracking-wider">
                    {tab.title}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-400">
                    {tab.subtitle}
                  </span>
                </span>
                <span className="shrink-0 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-cyan-100">
                  {statusText}
                </span>
              </button>
            );
          })}
          </div>
          <div className="pointer-events-none absolute right-0 top-0 h-[calc(100%-0.5rem)] w-8 rounded-r-2xl bg-gradient-to-l from-slate-950/80 to-transparent" aria-hidden="true" />
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
              const open = hasSearch || expandedGroups[group.id];
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
            <div className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/38 p-3 shadow-glass backdrop-blur-xl sm:p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
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

              <div className="space-y-2">
                <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-wider text-slate-400">
                  <Braces className="h-4 w-4 text-cyan-200" />
                  <span>Поля</span>
                </div>
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
              </div>

              {renderTemplateActions(activeTemplate, 'grid grid-cols-2 gap-2 border-t border-white/10 pt-3 sm:hidden')}
            </div>

            <div className="space-y-4">
              <div className="rounded-3xl border border-white/10 bg-slate-950/45 p-3 shadow-glass backdrop-blur-xl sm:p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">Предпросмотр</p>
                  <span className="rounded-lg bg-slate-800 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-slate-400">
                    итог
                  </span>
                </div>
                <pre className="max-h-[42dvh] whitespace-pre-wrap break-words rounded-2xl border border-white/10 bg-[#07111f] p-3 text-[12px] font-semibold leading-relaxed text-slate-100 sm:max-h-[520px] sm:p-4">
                  {activePreview || 'Пустой предпросмотр'}
                </pre>
              </div>
            </div>
          </section>
        )}
      </div>
      )}
    </div>
  );
}
