import type { FlatSubscriptionStatus } from '../schemas/schemas';

export type FlatSubscriptionUiAction = 'buy' | 'configure' | 'take' | 'renew' | 'none';
export type FlatSubscriptionQueue =
  | 'flat_pending_setup'
  | 'flat_active'
  | 'flat_closing'
  | 'flat_completed'
  | 'flat_cancelled';

interface FlatSubscriptionLike {
  status: FlatSubscriptionStatus;
  flat_amount_rub: string | number | null;
  pending_bets?: number;
}

interface FlatSubscriptionOwner {
  flat_subscription?: FlatSubscriptionLike | null;
}

export interface FlatSubscriptionUiState {
  key: 'none' | 'pending_setup' | 'waiting_legacy' | FlatSubscriptionStatus;
  title: string;
  description: string;
  action: FlatSubscriptionUiAction;
  tone: 'slate' | 'amber' | 'cyan' | 'emerald';
}

export function flatSubscriptionUiState(
  subscription: FlatSubscriptionLike | null | undefined,
  hasLegacyAccess: boolean,
): FlatSubscriptionUiState {
  if (!subscription) {
    return {
      key: 'none',
      title: 'Нужен абонемент',
      description: 'Выберите цель в флетах, чтобы открыть премиум-прогнозы.',
      action: 'buy',
      tone: 'slate',
    };
  }

  if (subscription.status === 'pending_setup' && subscription.flat_amount_rub == null) {
    return {
      key: 'pending_setup',
      title: 'Настройте размер флета',
      description: 'Оплата получена. Укажите обычную сумму одной ставки, чтобы активировать абонемент.',
      action: 'configure',
      tone: 'amber',
    };
  }

  if (subscription.status === 'pending_setup') {
    return {
      key: 'waiting_legacy',
      title: hasLegacyAccess ? 'Ожидает завершения старого абонемента' : 'Активация ожидается',
      description: hasLegacyAccess
        ? 'Флет сохранён. Новый абонемент включится после старых матчей, открытых ставок и гарантии.'
        : 'Флет сохранён. Система завершает активацию абонемента.',
      action: 'none',
      tone: 'cyan',
    };
  }

  if (subscription.status === 'active') {
    return {
      key: 'active',
      title: 'Абонемент активен',
      description: 'Можно брать прогнозы. Каждая ставка учитывается по фактической сумме.',
      action: 'take',
      tone: 'emerald',
    };
  }

  if (subscription.status === 'closing') {
    return {
      key: 'closing',
      title: 'Абонемент закрывается',
      description: `Цель достигнута. Новые ставки закрыты до расчёта ${subscription.pending_bets || 0} открытых ставок.`,
      action: 'none',
      tone: 'amber',
    };
  }

  if (subscription.status === 'completed') {
    return {
      key: 'completed',
      title: 'Цель достигнута',
      description: 'Абонемент завершён. Можно выбрать новую цель и продолжить дистанцию.',
      action: 'renew',
      tone: 'emerald',
    };
  }

  return {
    key: 'cancelled',
    title: 'Абонемент отменён',
    description: 'Этот абонемент больше не выдаёт прогнозы. Выберите новую цель или обратитесь в поддержку.',
    action: 'renew',
    tone: 'slate',
  };
}

export function matchesFlatSubscriptionQueue(user: FlatSubscriptionOwner, queue: FlatSubscriptionQueue) {
  return user.flat_subscription?.status === queue.replace('flat_', '');
}
