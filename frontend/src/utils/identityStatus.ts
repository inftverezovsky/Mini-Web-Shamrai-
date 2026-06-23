import type { UserResponse } from '../schemas/schemas';

type TelegramIdentityUser = Pick<
  UserResponse,
  'identity_providers' | 'is_web_only' | 'telegram_id' | 'tg_chat_joined' | 'username'
>;

export interface TelegramIdentityStatus {
  linked: boolean;
  badge: string;
  detail: string;
  deliveryDetail: string | null;
}

function telegramDisplayName(user: TelegramIdentityUser) {
  if (user.username) return `@${user.username}`;
  if (user.telegram_id > 0) return `ID ${user.telegram_id}`;
  return 'профиль Telegram';
}

export function getTelegramIdentityStatus(user?: TelegramIdentityUser | null): TelegramIdentityStatus {
  const linked = Boolean(
    user
    && (
      user.identity_providers.includes('telegram')
      || (!user.is_web_only && user.telegram_id > 0)
    ),
  );

  if (!user || !linked) {
    return {
      linked: false,
      badge: 'можно подключить',
      detail: 'Telegram еще не привязан к этому кабинету.',
      deliveryDetail: null,
    };
  }

  return {
    linked: true,
    badge: 'Готово',
    detail: `Telegram привязан: ${telegramDisplayName(user)}`,
    deliveryDetail: user.tg_chat_joined
      ? 'Telegram-бот и чат подтверждены.'
      : 'Авторизация Telegram активна; чат/бот можно подтвердить отдельно.',
  };
}
