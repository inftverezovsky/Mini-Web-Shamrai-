import type { ChatConversationResponse } from '../schemas/schemas';

export const ADMIN_WEB_CHAT_OPEN_EVENT = 'shamrai:admin-web-chat-open';

export interface AdminWebChatOpenDetail {
  conversation?: ChatConversationResponse | null;
  conversationId?: string | null;
  userId?: number | null;
}

export function adminWebChatUserUrl(userId: number) {
  return `/app?open=admin-web-chat&user_id=${encodeURIComponent(String(userId))}`;
}

export function adminWebChatConversationUrl(conversationId: string) {
  return `/app?open=admin-web-chat&conversation_id=${encodeURIComponent(conversationId)}`;
}

export function dispatchAdminWebChatOpen(detail: AdminWebChatOpenDetail) {
  window.dispatchEvent(new CustomEvent<AdminWebChatOpenDetail>(ADMIN_WEB_CHAT_OPEN_EVENT, { detail }));
}
