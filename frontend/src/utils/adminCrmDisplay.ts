import { buildProfileAvatarSources } from './profileAvatar';

export interface CrmRecentMatchResult {
  bet_id: string;
  status: 'win' | 'loss';
  taken_at: string | null;
}

export interface CrmBookmakerPreviewItem {
  id: number;
  name: string;
  code: string;
}

export interface CrmDisplayClient {
  telegram_id?: number;
  photo_url?: string | null;
  vk_photo_url?: string | null;
  telegram_connected?: boolean;
  telegram_delivery_enabled?: boolean;
  tg_chat_joined?: boolean;
  vk_user_id?: string | null;
  vk_connected?: boolean;
  vk_delivery_enabled?: boolean;
  vk_messages_allowed?: boolean;
  web_push_enabled?: boolean;
  has_active_subscription?: boolean;
  purchased_bets_balance?: number;
  matches_remaining?: number;
  guarantee_active?: boolean;
}

export type CrmChannelTone = 'ready' | 'warning' | 'missing';
export type CrmPriorityTone = 'danger' | 'warning' | 'success' | 'info' | 'muted';

export interface CrmChannelStatus {
  key: 'telegram' | 'vk' | 'web';
  shortLabel: 'TG' | 'VK' | 'Web';
  label: string;
  detail: string;
  tone: CrmChannelTone;
  ready: boolean;
}

export interface CrmClientPriority {
  label: string;
  detail: string;
  tone: CrmPriorityTone;
}

export function getCrmMatchBalance(user: Pick<CrmDisplayClient, 'purchased_bets_balance' | 'matches_remaining'>) {
  return user.purchased_bets_balance !== undefined && user.purchased_bets_balance !== 0
    ? user.purchased_bets_balance
    : user.matches_remaining || 0;
}

export function getClientAvatarSources(user: Pick<CrmDisplayClient, 'photo_url' | 'vk_photo_url'>) {
  return buildProfileAvatarSources(user);
}

export function getClientChannelStatuses(user: CrmDisplayClient): CrmChannelStatus[] {
  const telegramConnected = user.telegram_connected ?? ((user.telegram_id ?? 0) > 0);
  const telegramReady = user.telegram_delivery_enabled ?? Boolean(user.tg_chat_joined);
  const vkConnected = user.vk_connected ?? Boolean(user.vk_user_id);
  const vkReady = user.vk_delivery_enabled ?? Boolean(user.vk_messages_allowed);
  const webPushReady = Boolean(user.web_push_enabled);

  return [
    telegramConnected && telegramReady
      ? {
          key: 'telegram',
          shortLabel: 'TG',
          label: 'готов',
          detail: 'Telegram: вход и чат готовы',
          tone: 'ready',
          ready: true,
        }
      : telegramConnected
        ? {
            key: 'telegram',
            shortLabel: 'TG',
            label: 'нет чата',
            detail: 'Telegram: вход есть, чат не подтвержден',
            tone: 'warning',
            ready: false,
          }
        : {
            key: 'telegram',
            shortLabel: 'TG',
            label: 'нет входа',
            detail: 'Telegram: клиент не авторизован',
            tone: 'missing',
            ready: false,
          },
    vkConnected && vkReady
      ? {
          key: 'vk',
          shortLabel: 'VK',
          label: 'готов',
          detail: 'VK: профиль связан, сообщения разрешены',
          tone: 'ready',
          ready: true,
        }
      : vkConnected
        ? {
            key: 'vk',
            shortLabel: 'VK',
            label: 'нет разрешения',
            detail: 'VK: профиль связан, сообщения не разрешены',
            tone: 'warning',
            ready: false,
          }
        : {
            key: 'vk',
            shortLabel: 'VK',
            label: 'не связан',
            detail: 'VK: профиль не связан',
            tone: 'missing',
            ready: false,
          },
    webPushReady
      ? {
          key: 'web',
          shortLabel: 'Web',
          label: 'push включен',
          detail: 'Web Push: уведомления включены',
          tone: 'ready',
          ready: true,
        }
      : {
          key: 'web',
          shortLabel: 'Web',
          label: 'нет push',
          detail: 'Web Push: уведомления не включены',
          tone: 'missing',
          ready: false,
        },
  ];
}

export function getClientPriority(user: CrmDisplayClient): CrmClientPriority {
  const balance = getCrmMatchBalance(user);
  if (balance < 0) {
    return {
      label: 'Долг',
      detail: `${balance} матч.`,
      tone: 'danger',
    };
  }

  if (user.guarantee_active) {
    return {
      label: 'Гарантия',
      detail: 'закрыть исход',
      tone: 'warning',
    };
  }

  if (user.has_active_subscription || balance > 0) {
    return {
      label: 'Активен',
      detail: `${balance} матч.`,
      tone: 'success',
    };
  }

  const hasReadyChannel = Boolean(user.telegram_delivery_enabled || user.vk_delivery_enabled || user.web_push_enabled)
    || getClientChannelStatuses(user).some((status) => status.ready);
  if (hasReadyChannel) {
    return {
      label: 'Связаться',
      detail: 'канал готов',
      tone: 'info',
    };
  }

  return {
    label: 'Демо',
    detail: 'нет каналов',
    tone: 'muted',
  };
}

function pluralRu(value: number, one: string, few: string, many: string) {
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function getCurrentStreak(results: CrmRecentMatchResult[]) {
  const currentStatus = results[0]?.status;
  if (!currentStatus) return null;

  let count = 0;
  for (const result of results) {
    if (result.status !== currentStatus) break;
    count += 1;
  }

  const noun = currentStatus === 'win'
    ? pluralRu(count, 'победа', 'победы', 'побед')
    : pluralRu(count, 'неудача', 'неудачи', 'неудач');

  return {
    count,
    status: currentStatus,
    label: `${count} ${noun} подряд`,
  };
}

export function getClientRecentMatchSummary(results: CrmRecentMatchResult[]) {
  const wins = results.filter((result) => result.status === 'win').length;
  const losses = results.length - wins;
  const streak = getCurrentStreak(results);

  if (!results.length) {
    return {
      headline: '0 матчей',
      splitLabel: 'нет истории',
      wins,
      losses,
      streak,
    };
  }

  return {
    headline: streak?.label ?? `${results.length} ${pluralRu(results.length, 'матч', 'матча', 'матчей')}`,
    splitLabel: `${wins} ${pluralRu(wins, 'победа', 'победы', 'побед')} / ${losses} ${pluralRu(losses, 'неудача', 'неудачи', 'неудач')}`,
    wins,
    losses,
    streak,
  };
}

export function getStableMatchSegments(results: CrmRecentMatchResult[], segmentCount = 10) {
  const chronologicalResults = [...results].reverse();
  return Array.from({ length: segmentCount }, (_, index) => chronologicalResults[index] ?? null);
}

export function getCrmBookmakerPreview(
  bookmakers: CrmBookmakerPreviewItem[],
  otherBookmakerName: string | null | undefined,
  limit = 3,
) {
  const visibleBookmakers = bookmakers.slice(0, limit);
  const extraCount = Math.max(0, bookmakers.length - visibleBookmakers.length);
  const otherLabel = otherBookmakerName?.trim() || null;

  return {
    visibleBookmakers,
    extraCount,
    otherLabel,
  };
}
