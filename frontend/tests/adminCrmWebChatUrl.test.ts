import { describe, expect, it } from 'vitest';
import { getAdminCrmWebChatUrl } from '../src/pages/admin/AdminCRM';

describe('getAdminCrmWebChatUrl', () => {
  it('opens a regular Telegram client in the internal admin web chat', () => {
    expect(getAdminCrmWebChatUrl({ telegram_id: 1442066982 }))
      .toBe('/app?open=admin-web-chat&user_id=1442066982');
  });

  it('keeps web-only client ids routable through the same chat entrypoint', () => {
    expect(getAdminCrmWebChatUrl({ telegram_id: -10001 }))
      .toBe('/app?open=admin-web-chat&user_id=-10001');
  });
});
