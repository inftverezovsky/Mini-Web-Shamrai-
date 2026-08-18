import { describe, expect, it } from 'vitest';

import {
  flatSubscriptionUiState,
  matchesFlatSubscriptionQueue,
} from '../src/utils/flatSubscriptionUi';

const base = {
  flat_amount_rub: '10000.00',
  pending_bets: 0,
};

describe('flat subscription UI states', () => {
  it('distinguishes setup from a configured subscription waiting for legacy access', () => {
    expect(flatSubscriptionUiState({ ...base, status: 'pending_setup', flat_amount_rub: null }, false)).toMatchObject({
      key: 'pending_setup',
      title: 'Настройте размер флета',
      action: 'configure',
    });
    expect(flatSubscriptionUiState({ ...base, status: 'pending_setup' }, true)).toMatchObject({
      key: 'waiting_legacy',
      title: 'Ожидает завершения старого абонемента',
      action: 'none',
    });
  });

  it.each([
    ['active', 'Абонемент активен', 'take'],
    ['closing', 'Абонемент закрывается', 'none'],
    ['completed', 'Цель достигнута', 'renew'],
    ['cancelled', 'Абонемент отменён', 'renew'],
  ] as const)('maps %s to a dedicated user state', (status, title, action) => {
    expect(flatSubscriptionUiState({ ...base, status }, false)).toMatchObject({ title, action });
  });

  it('supports dedicated CRM queues without grouping all flat subscriptions as active', () => {
    const users = [
      { flat_subscription: { ...base, status: 'pending_setup' as const } },
      { flat_subscription: { ...base, status: 'active' as const } },
      { flat_subscription: { ...base, status: 'closing' as const } },
      { flat_subscription: { ...base, status: 'completed' as const } },
    ];

    expect(users.filter((user) => matchesFlatSubscriptionQueue(user, 'flat_pending_setup'))).toHaveLength(1);
    expect(users.filter((user) => matchesFlatSubscriptionQueue(user, 'flat_closing'))).toHaveLength(1);
    expect(users.filter((user) => matchesFlatSubscriptionQueue(user, 'flat_completed'))).toHaveLength(1);
  });
});
