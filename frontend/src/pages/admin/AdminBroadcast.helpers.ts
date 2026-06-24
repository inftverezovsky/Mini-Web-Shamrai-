import { BetResponse, BookmakerLink, BookmakerResponse, ForecastRequestResponse } from '../../schemas/schemas';

// Pure types and helpers shared by the AdminBroadcast view.
export type BroadcastMode = 'announcement' | 'forecast' | 'requests';
export type BetCategory = 'prematch' | 'live';
export type FullForecastMode = 'prepare' | 'edit' | 'send' | 'bulkSend';
export type ForecastRequestStatus = ForecastRequestResponse['status'];

export interface DeliveryResult {
  sent: number;
  queued?: number;
  failed: number;
  telegram?: {
    sent: number;
    failed: number;
  };
  vkMessages?: {
    sent: number;
    failed: number;
  };
  webPush?: {
    sent: number;
    failed: number;
    missing_permission?: number;
  };
}

export interface ForecastBulkSendResult {
  status: 'success' | 'partial' | 'failed';
  total: number;
  sent: number;
  queued?: number;
  failed: number;
  errors: string[];
}

export interface ForecastTeaserDeliveryResult {
  total_audience: number;
  sent: number;
  failed: number;
  errors: string[];
  delivery?: {
    total_audience?: number;
    sent?: number;
    failed?: number;
    telegram?: DeliveryResult['telegram'];
    vk_messages?: DeliveryResult['vkMessages'];
    web_push?: DeliveryResult['webPush'];
  };
}

export interface ForecastBroadcastFullResult {
  bet: BetResponse;
  auto_send_enabled: boolean;
  auto_send: ForecastBulkSendResult | null;
  reannounce: ForecastTeaserDeliveryResult | null;
}

export interface ForecastBroadcastStopResult {
  status: string;
  bet_id: string;
  already_stopped: boolean;
  stopped_requests: number;
  skipped_processing: number;
}

export interface ForecastRequestGroup {
  key: string;
  eventName: string;
  bet: BetResponse;
  requests: ForecastRequestResponse[];
}

export const FORECAST_REQUEST_TABS: Array<{ status: ForecastRequestStatus; label: string }> = [
  { status: 'interested', label: 'Ожидают' },
  { status: 'announced', label: 'Анонсировано' },
  { status: 'processing', label: 'Обработка' },
  { status: 'sent', label: 'Отправлено' },
  { status: 'manual_sent', label: 'Взяли вручную' },
  { status: 'declined', label: 'Отказались' },
  { status: 'removed', label: 'Удалены' },
];

const STOPPABLE_FORECAST_REQUEST_STATUSES = new Set<ForecastRequestStatus>([
  'announced',
  'interested',
  'declined',
  'cancelled',
]);

export function getForecastRequestUserName(request: ForecastRequestResponse) {
  const user = request.user;
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  if (user.username) return fullName ? `${fullName} (@${user.username})` : `@${user.username}`;
  return fullName || (user.is_web_only ? 'Web/VK клиент' : `ID ${user.telegram_id}`);
}

export function getForecastRequestUserIdLabel(request: ForecastRequestResponse) {
  return request.user.is_web_only ? 'Web/VK клиент' : `ID ${request.user.telegram_id}`;
}

export function forecastRequestWebChatUrl(request: ForecastRequestResponse) {
  return `/app?open=admin-web-chat&user_id=${encodeURIComponent(String(request.user.telegram_id))}`;
}

export function forecastRequestTelegramDialogUrl(request: ForecastRequestResponse) {
  const username = (request.user.username || '').trim().replace(/^@/, '');
  if (/^[A-Za-z0-9_]{5,32}$/.test(username)) return `https://t.me/${username}`;
  if (request.user.telegram_id > 0) return `tg://user?id=${request.user.telegram_id}`;
  return '';
}

export function forecastRequestVkDialogUrl(request: ForecastRequestResponse) {
  const rawVkUserId = (request.user.vk_user_id || '').trim();
  const match = rawVkUserId.match(/^(?:vk[-_]?|id)?(\d+)$/i);
  if (!match) return '';
  return `https://vk.com/im?sel=${match[1]}`;
}

export function requestHasFullForecastAccess(request: ForecastRequestResponse) {
  return isPaidSetRequest(request) || request.user.matches_remaining > 0 || request.user.guarantee_active;
}

export function requestDeliveryBlocked(request: ForecastRequestResponse) {
  return !isPaidSetRequest(request) && !requestHasFullForecastAccess(request);
}

export function canProcessForecastRequest(request: ForecastRequestResponse) {
  return request.status === 'interested' && requestHasFullForecastAccess(request);
}

export function canRemoveForecastRequest(request: ForecastRequestResponse) {
  return request.status !== 'removed' && request.status !== 'processing';
}

export function forecastGroupIsStopped(group: ForecastRequestGroup) {
  return (
    !group.bet.auto_send_on_interest
    && !group.requests.some((request) => STOPPABLE_FORECAST_REQUEST_STATUSES.has(request.status))
  );
}

export function isPaidSetBet(bet: BetResponse | null | undefined) {
  return bet?.delivery_mode === 'paid_set';
}

export function isPaidSetRequest(request: ForecastRequestResponse) {
  return isPaidSetBet(request.bet);
}

export function getDeliveryMethodLabel(deliveryMethod?: string | null) {
  if (deliveryMethod === 'vk_bot') return 'VK + бот';
  if (deliveryMethod === 'vk') return 'VK';
  if (deliveryMethod === 'web') return 'web';
  if (deliveryMethod === 'manual') return 'вручную';
  return 'бот';
}

export function getBetBookmakers(bet: BetResponse | null | undefined): BookmakerResponse[] {
  if (!bet) return [];
  if (bet.bookmakers?.length) return bet.bookmakers;
  return bet.bookmaker ? [bet.bookmaker] : [];
}

export function bookmakerLinksToState(links: BookmakerLink[] | null | undefined): Record<number, string> {
  return (links || []).reduce<Record<number, string>>((acc, link) => {
    if (link.bookmaker_id && link.url) {
      acc[link.bookmaker_id] = link.url;
    }
    return acc;
  }, {});
}

export function normalizeMatchUrlForPreview(rawUrl: string): string {
  const url = rawUrl.trim().replace(/\u200b|\u200c|\u200d|\ufeff/g, '');
  if (!url || url.startsWith('//') || /[<>\\\s]/.test(url)) return '';

  let withScheme = url;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(withScheme)) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(withScheme)) return '';
    withScheme = `https://${withScheme}`;
  }

  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return '';
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
  if (parsed.username || parsed.password) return '';
  const hostname = parsed.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (!hostname || !/[a-z0-9]/i.test(hostname)) return '';
  if (hostname.toLowerCase() === 'localhost') {
    const scheme = parsed.protocol.slice(0, -1).toLowerCase();
    return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
  }
  if (hostname.includes(':')) {
    const scheme = parsed.protocol.slice(0, -1).toLowerCase();
    return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
  }
  if (!hostname.includes('.')) return '';
  const labels = hostname.split('.');
  if (!labels.every((label) => /^[A-Za-z0-9-]{1,63}$/.test(label) && !label.startsWith('-') && !label.endsWith('-'))) {
    return '';
  }

  const scheme = parsed.protocol.slice(0, -1).toLowerCase();
  return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
}

export function bookmakerLinkError(rawUrl: string): string | null {
  if (!rawUrl.trim()) return null;
  return normalizeMatchUrlForPreview(rawUrl) ? null : 'Введите корректную ссылку: домен или URL http/https';
}

export function betHasSavedFullForecast(bet: BetResponse | null | undefined): boolean {
  if (!bet || bet.status === 'deleted' || isPaidSetBet(bet)) return false;
  const eventName = (bet.event_name || '').trim();
  const hasEventName = Boolean(eventName && eventName !== 'Закрытый прогноз');
  const hasOutcome = Boolean((bet.outcome || '').trim());
  const hasCoefficient = Number(bet.coefficient || 0) > 0;
  const hasCoupon = Boolean((bet.coupon_image_url || '').trim());
  return hasEventName && hasOutcome && hasCoefficient && hasCoupon;
}

export function formatRequestDate(value: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function getForecastRequestGroupKey(request: ForecastRequestResponse) {
  return request.bet_id || request.bet.id || request.bet.event_name || request.id;
}

export function getForecastRequestEventName(request: ForecastRequestResponse) {
  if (isPaidSetRequest(request)) return request.bet.event_name?.trim() || 'Платный набор';
  return request.bet.event_name?.trim() || 'Закрытый прогноз';
}

export function formatForecastRequestCount(count: number) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  const noun = mod10 === 1 && mod100 !== 11
    ? 'заявка'
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
      ? 'заявки'
      : 'заявок';
  return `${count} ${noun}`;
}

export function mergeForecastRequestPages(
  current: ForecastRequestResponse[],
  incoming: ForecastRequestResponse[],
) {
  const byId = new Map<string, ForecastRequestResponse>();
  current.forEach((request) => byId.set(request.id, request));
  incoming.forEach((request) => byId.set(request.id, request));
  return Array.from(byId.values());
}
