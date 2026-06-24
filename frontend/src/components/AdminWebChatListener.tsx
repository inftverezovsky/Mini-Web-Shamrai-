import { useEffect, useRef } from 'react';

import { ChatConversationResponse, ChatMessageResponse, ChatReadUpdatedEvent } from '../schemas/schemas';
import { apiFetch, buildApiWebSocketUrl } from '../utils/api';
import { MOCK_DEBUG_AUTH_TOKEN, getStoredAuthToken } from '../utils/authStorage';
import { notifyInfo } from '../utils/notify';
import { playIncomingSupportSound, unlockIncomingSignalSound } from '../utils/signalAudio';
import { hasTelegramLaunchParams, isTelegramMiniApp } from '../utils/telegramSdk';
import {
  isServiceWorkerNotificationMessage,
  rememberWebNotificationEvent,
} from '../utils/webNotificationEvents';
import { rememberRecentId } from '../utils/realtimeLimits';

interface AdminWebChatListenerProps {
  enabled: boolean;
}

interface SupportChatStreamTicketResponse {
  ticket: string;
  expires_in: number;
}

export interface AdminWebChatMessageEventPayload {
  event: 'chat.message.created';
  message: ChatMessageResponse;
  conversation: ChatConversationResponse;
}

export const ADMIN_WEB_CHAT_MESSAGE_EVENT = 'shamrai:admin-web-chat-message';
export const ADMIN_WEB_CHAT_CONVERSATION_EVENT = 'shamrai:admin-web-chat-conversation';
export const ADMIN_WEB_CHAT_STATUS_EVENT = 'shamrai:admin-web-chat-status';
export const ADMIN_WEB_CHAT_READ_EVENT = 'shamrai:admin-web-chat-read';
export const ADMIN_WEB_CHAT_TYPING_EVENT = 'shamrai:admin-web-chat-typing';

function supportNoticeText(message: ChatMessageResponse) {
  const fallback = message.type === 'image'
    ? 'Клиент прислал скриншот.'
    : message.type === 'voice'
      ? 'Клиент прислал голосовое сообщение.'
      : message.type === 'file'
        ? 'Клиент прислал файл.'
        : 'Новое сообщение клиента.';
  const firstLine = (message.text || '').split('\n').map((line) => line.trim()).find(Boolean) || fallback;
  return firstLine.length > 260 ? `${firstLine.slice(0, 257)}...` : firstLine;
}

export default function AdminWebChatListener({ enabled }: AdminWebChatListenerProps) {
  const seenMessageIdsRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!enabled || isTelegramMiniApp() || hasTelegramLaunchParams()) return;

    let disposed = false;
    const unlock = () => {
      void unlockIncomingSignalSound().then((unlocked) => {
        if (!unlocked || disposed) return;
        window.removeEventListener('pointerdown', unlock);
        window.removeEventListener('keydown', unlock);
        window.removeEventListener('touchstart', unlock);
      });
    };

    window.addEventListener('pointerdown', unlock, { passive: true });
    window.addEventListener('keydown', unlock);
    window.addEventListener('touchstart', unlock, { passive: true });

    return () => {
      disposed = true;
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
      window.removeEventListener('touchstart', unlock);
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || isTelegramMiniApp() || hasTelegramLaunchParams()) return;
    const token = getStoredAuthToken();
    if (token === MOCK_DEBUG_AUTH_TOKEN) return;

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let closedByUnmount = false;
    let reconnectAttempt = 0;

    const emitStatus = (state: 'connecting' | 'online' | 'offline') => {
      window.dispatchEvent(new CustomEvent(ADMIN_WEB_CHAT_STATUS_EVENT, { detail: { state } }));
    };

    const emitMessage = (payload: AdminWebChatMessageEventPayload) => {
      if (!payload?.message?.id || seenMessageIdsRef.current.has(payload.message.id)) return;
      rememberRecentId(seenMessageIdsRef.current, payload.message.id);
      window.dispatchEvent(new CustomEvent(ADMIN_WEB_CHAT_MESSAGE_EVENT, { detail: payload }));
      if (payload.message.direction !== 'client') return;
      const title = payload.conversation?.owner_user?.display_name || 'Клиент Shamrai';
      const body = supportNoticeText(payload.message);
      if (!rememberWebNotificationEvent({
        id: String(payload.message.id),
        title,
        body,
        type: 'support_client_message',
        url: `/app?open=admin-web-chat&conversation_id=${payload.conversation?.id || ''}`,
        source: 'websocket',
      })) return;
      notifyInfo(body, title);
      void playIncomingSupportSound();
    };

    const scheduleReconnect = () => {
      if (closedByUnmount) return;
      const baseDelay = Math.min(30_000, 1000 * (2 ** Math.min(reconnectAttempt, 5)));
      const jitter = Math.floor(Math.random() * 600);
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(connect, baseDelay + jitter);
    };

    const connect = async () => {
      emitStatus('connecting');
      let ticket: string;
      try {
        const ticketResponse = await apiFetch<SupportChatStreamTicketResponse>('/chat/stream-ticket', {
          method: 'POST',
        });
        ticket = ticketResponse.ticket;
      } catch {
        emitStatus('offline');
        scheduleReconnect();
        return;
      }
      if (closedByUnmount) return;

      socket = new WebSocket(buildApiWebSocketUrl('/api/chat/stream', { ticket }));

      socket.onopen = () => {
        reconnectAttempt = 0;
        emitStatus('online');
        if (pingTimer) window.clearInterval(pingTimer);
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
        }, 25000);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.event === 'pong') return;
          if (payload?.event === 'chat.message.created') emitMessage(payload as AdminWebChatMessageEventPayload);
          if (payload?.event === 'chat.conversation.updated') {
            window.dispatchEvent(new CustomEvent(ADMIN_WEB_CHAT_CONVERSATION_EVENT, { detail: payload.conversation }));
          }
          if (payload?.event === 'chat.read.updated') {
            window.dispatchEvent(new CustomEvent(ADMIN_WEB_CHAT_READ_EVENT, { detail: payload as ChatReadUpdatedEvent }));
          }
          if (payload?.event === 'chat.typing.updated') {
            window.dispatchEvent(new CustomEvent(ADMIN_WEB_CHAT_TYPING_EVENT, { detail: payload }));
          }
        } catch {
          // Ignore malformed stream frames.
        }
      };

      socket.onclose = () => {
        if (pingTimer) window.clearInterval(pingTimer);
        emitStatus('offline');
        scheduleReconnect();
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    void connect();

    return () => {
      closedByUnmount = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (pingTimer) window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || isTelegramMiniApp() || hasTelegramLaunchParams() || !('serviceWorker' in navigator)) return;

    const handleServiceWorkerMessage = (event: MessageEvent) => {
      if (!isServiceWorkerNotificationMessage(event.data)) return;
      const payload = event.data.payload;
      if (!rememberWebNotificationEvent(payload)) return;
      notifyInfo(payload.body, payload.title);
      void playIncomingSupportSound();
    };

    navigator.serviceWorker.addEventListener('message', handleServiceWorkerMessage);
    return () => {
      navigator.serviceWorker.removeEventListener('message', handleServiceWorkerMessage);
    };
  }, [enabled]);

  return null;
}
