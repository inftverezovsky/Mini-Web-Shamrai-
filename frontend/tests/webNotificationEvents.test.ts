import { beforeEach, describe, expect, it } from 'vitest';

import {
  rememberWebNotificationEvent,
  resetWebNotificationDedupeForTests,
  type WebNotificationEvent,
} from '../src/utils/webNotificationEvents';

describe('web notification event dedupe', () => {
  beforeEach(() => {
    resetWebNotificationDedupeForTests();
  });

  it('handles a notification only once across websocket and service worker sources', () => {
    const baseEvent: WebNotificationEvent = {
      id: 'signal-77',
      title: 'Личный бот Shamrai',
      body: 'Новый сигнал',
      type: 'forecast_full',
      url: '/app?open=web-bot-chat',
      source: 'websocket',
    };

    expect(rememberWebNotificationEvent(baseEvent, 1_000)).toBe(true);
    expect(rememberWebNotificationEvent({ ...baseEvent, source: 'service-worker' }, 1_100)).toBe(false);
  });

  it('allows a repeated event after the dedupe window expires', () => {
    const event: WebNotificationEvent = {
      id: 'support-5',
      title: 'Shamrai написал в чат',
      body: 'Ответили в поддержке',
      type: 'support_staff_message',
      url: '/app?open=web-chat&conversation=support',
      source: 'service-worker',
    };

    expect(rememberWebNotificationEvent(event, 1_000)).toBe(true);
    expect(rememberWebNotificationEvent(event, 1_000 + 121_000)).toBe(true);
  });
});
