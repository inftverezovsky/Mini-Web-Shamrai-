import type { UserResponse } from '../schemas/schemas';

export type IdentityProvider = 'telegram' | 'vk';

export interface MissingIdentityAction {
  provider: IdentityProvider;
  title: string;
  description: string;
  buttonLabel: string;
}

export interface AuthProviderEnvironment {
  runsInTelegramMiniApp?: boolean;
  hasTelegramLaunchParams?: boolean;
  runsInVkApp?: boolean;
  vkReady?: boolean;
  vkOriginCompatible?: boolean;
  telegramAvailable?: boolean;
}

type IdentityAccessUser = Pick<
  UserResponse,
  | 'telegram_id'
  | 'is_web_only'
  | 'identity_providers'
  | 'missing_identity_providers'
  | 'vk_user_id'
  | 'vk_messages_allowed'
>;

export function hasTelegramIdentity(user?: IdentityAccessUser | null): boolean {
  return Boolean(
    user
    && (
      user.identity_providers.includes('telegram')
      || (!user.is_web_only && user.telegram_id > 0)
    ),
  );
}

export function hasVkIdentity(user?: IdentityAccessUser | null): boolean {
  return Boolean(
    user
    && (
      user.identity_providers.includes('vk')
      || user.vk_user_id
    ),
  );
}

export function canEnterCabinet(user?: IdentityAccessUser | null): boolean {
  return hasTelegramIdentity(user) || hasVkIdentity(user);
}

export function isVkDeliveryReady(user?: IdentityAccessUser | null): boolean {
  return Boolean(user?.vk_user_id && user.vk_messages_allowed);
}

export function getMissingIdentityActions(user?: IdentityAccessUser | null): MissingIdentityAction[] {
  if (!user) return [];

  const missing = new Set(user.missing_identity_providers || []);
  const needsTelegram = missing.has('telegram') || !hasTelegramIdentity(user);
  const needsVk = missing.has('vk') || !hasVkIdentity(user);
  const actions: MissingIdentityAction[] = [];

  if (needsTelegram) {
    actions.push({
      provider: 'telegram',
      title: 'Подключите Telegram',
      description: 'Telegram нужен для входа через бота, быстрых уведомлений и восстановления кабинета.',
      buttonLabel: 'Подключить Telegram',
    });
  }

  if (needsVk) {
    actions.push({
      provider: 'vk',
      title: 'Подключите VK ID',
      description: 'VK ID поможет получать сообщения VK и не потерять профиль при смене устройства.',
      buttonLabel: 'Подключить VK',
    });
  }

  return actions;
}

export function pickPrimaryAuthProvider(environment: AuthProviderEnvironment): IdentityProvider {
  if (environment.runsInTelegramMiniApp || environment.hasTelegramLaunchParams) {
    return 'telegram';
  }

  if (environment.telegramAvailable !== false) {
    return 'telegram';
  }

  if (
    environment.runsInVkApp
    && environment.vkReady
    && environment.vkOriginCompatible
  ) {
    return 'vk';
  }

  if (environment.vkReady && environment.vkOriginCompatible) {
    return 'vk';
  }

  return 'telegram';
}
