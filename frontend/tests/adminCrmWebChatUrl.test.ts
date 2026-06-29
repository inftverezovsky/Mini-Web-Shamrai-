import { describe, expect, it } from 'vitest';
import { adminWebChatConversationUrl, adminWebChatUserUrl } from '../src/utils/adminWebChatNavigation';

describe('admin web chat navigation urls', () => {
  it('opens a regular Telegram client in the internal admin web chat', () => {
    expect(adminWebChatUserUrl(1442066982))
      .toBe('/app?open=admin-web-chat&user_id=1442066982');
  });

  it('keeps web-only client ids routable through the same chat entrypoint', () => {
    expect(adminWebChatUserUrl(-10001))
      .toBe('/app?open=admin-web-chat&user_id=-10001');
  });

  it('opens an already ensured conversation directly', () => {
    expect(adminWebChatConversationUrl('support:1442066982'))
      .toBe('/app?open=admin-web-chat&conversation_id=support%3A1442066982');
  });
});
