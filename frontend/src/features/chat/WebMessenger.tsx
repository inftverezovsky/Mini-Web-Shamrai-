import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bot,
  Headphones,
  MessageCircle,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  WifiOff,
  X,
} from 'lucide-react';

import {
  ChatConversationListResponse,
  ChatConversationResponse,
  ChatMessagePageResponse,
  ChatMessageResponse,
  ChatReadUpdatedEvent,
  ChatSignalMessagePageResponse,
  ChatSignalMessageResponse,
} from '../../schemas/schemas';
import { WEB_SIGNAL_EVENT, WEB_SIGNAL_STATUS_EVENT } from '../../components/WebSignalListener';
import { apiFetch, buildApiWebSocketUrl, downloadApiFile } from '../../utils/api';
import { MOCK_DEBUG_AUTH_TOKEN, getStoredAuthToken } from '../../utils/authStorage';
import { useDataProcessorWorker } from '../../hooks/useDataProcessorWorker';
import { useThrottledCallback, useThrottledEventBuffer } from '../../hooks/useThrottledEvents';
import { notifyError, notifyInfo, notifySuccess } from '../../utils/notify';
import { unlockIncomingSignalSound, playIncomingSupportSound } from '../../utils/signalAudio';
import { isTelegramMiniApp } from '../../utils/telegramSdk';
import { rememberWebNotificationEvent } from '../../utils/webNotificationEvents';
import { limitRecent, rememberRecentId } from '../../utils/realtimeLimits';
import {
  dataWorkerThresholdForProfile,
  supportsWebSocketRuntime,
  type PerformanceProfileName,
} from '../../utils/performancePolicy';
import MessageComposer, { ChatComposerAttachment } from './MessageComposer';
import MessageList from './MessageList';
import { applyReadReceiptToMessages } from './readReceipts';
import SignalMessageCard, { signalActionNotice } from './SignalMessageCard';
import SupportMessageBubble, { SupportMessageView } from './SupportMessageBubble';

type ActiveConversationKey = 'signals' | 'support';
type ForecastSignalAction = 'take' | 'decline';
const CHAT_HISTORY_ITEM_LIMIT = 500;

function currentWorkerThreshold() {
  if (typeof document === 'undefined') return dataWorkerThresholdForProfile('full');
  const profile = document.documentElement.dataset.performanceProfile as PerformanceProfileName | undefined;
  return dataWorkerThresholdForProfile(profile === 'balanced' || profile === 'lowPower' ? profile : 'full');
}

interface ForecastSignalActionResponse {
  status: string;
  message: string;
  forecast_request_id: string;
  action?: 'accepted' | 'contact_required' | 'inactive';
  contact?: {
    draft_text: string;
    channel: 'web' | 'telegram' | 'vk';
    url?: string | null;
  } | null;
}

interface ChatStreamTicketResponse {
  ticket: string;
  expires_in: number;
}

interface ChatStreamMessageCreatedEvent {
  event: 'chat.message.created';
  conversation_id: string;
  message: ChatMessageResponse;
  conversation?: ChatConversationResponse;
}

interface ChatStreamConversationUpdatedEvent {
  event: 'chat.conversation.updated';
  conversation_id: string;
  conversation: ChatConversationResponse;
}

interface ChatTypingUpdatedEvent {
  event: 'chat.typing.updated';
  conversation_id: string;
  sender_user_id: number;
  sender_role: string;
  direction: 'staff' | 'client';
  is_typing: boolean;
}

type ChatReplyTarget = NonNullable<ChatMessageResponse['reply_to']>;

function replyTargetFromMessage(message: ChatMessageResponse): ChatReplyTarget {
  return {
    id: message.id,
    author_label: message.author_label,
    type: message.type,
    text: supportMessagePreview(message),
    payload: message.payload || {},
  };
}

function mergeSupportMessages(
  currentMessages: SupportMessageView[],
  incomingMessages: SupportMessageView[],
  limit = CHAT_HISTORY_ITEM_LIMIT,
) {
  const byClientId = new Map<string, SupportMessageView>();
  const byId = new Map<number, SupportMessageView>();

  currentMessages.forEach((message) => {
    byClientId.set(message.client_message_id, message);
    if (message.id > 0) byId.set(message.id, message);
  });

  incomingMessages.forEach((message) => {
    const existingById = message.id > 0 ? byId.get(message.id) : undefined;
    const existingByClientId = byClientId.get(message.client_message_id);
    const merged = {
      ...(existingByClientId || existingById || {}),
      ...message,
      delivery_state: message.delivery_state || 'sent',
      read_at: message.read_at || existingByClientId?.read_at || existingById?.read_at || null,
    };
    byClientId.set(merged.client_message_id, merged);
    if (merged.id > 0) byId.set(merged.id, merged);
  });

  return limitRecent(Array.from(byClientId.values()).sort((left, right) => {
    const timeDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    return timeDelta || left.id - right.id;
  }), limit);
}

function mergeConversations(
  currentConversations: ChatConversationResponse[],
  incomingConversations: ChatConversationResponse[],
) {
  const byKey = new Map(currentConversations.map((conversation) => [conversation.key, conversation]));
  incomingConversations.forEach((conversation) => {
    byKey.set(conversation.key, conversation);
  });
  return Array.from(byKey.values());
}

function conversationTitle(conversation?: ChatConversationResponse | null, key?: ActiveConversationKey) {
  const conversationKey = conversation?.key || key;
  return conversationKey === 'signals' ? 'Личный бот Shamrai' : 'Поддержка Shamrai';
}

function lastMessagePreview(conversation?: ChatConversationResponse | null, key?: ActiveConversationKey) {
  if (!conversation?.last_message_text) {
    return (conversation?.key || key) === 'support' ? 'Напишите Shamrai вручную' : 'Сигналы и прогнозы';
  }
  return conversation.last_message_text;
}

function supportMessagePreview(message: ChatMessageResponse) {
  if (message.text?.trim()) return message.text;
  if (message.type === 'image') return 'Скриншот';
  if (message.type === 'voice') return 'Голосовое сообщение';
  if (message.type === 'file') return String(message.payload?.original_filename || 'Файл');
  return 'Новое сообщение';
}

function attachmentPayload(attachment: ChatComposerAttachment) {
  return {
    url: attachment.previewUrl,
    mime_type: attachment.file.type,
    size_bytes: attachment.file.size,
    original_filename: attachment.file.name,
    ...(attachment.durationMs ? { duration_ms: attachment.durationMs } : {}),
  };
}

function makeOptimisticMessage(
  text: string,
  clientMessageId: string,
  attachment?: ChatComposerAttachment,
  replyTo?: ChatReplyTarget | null,
): SupportMessageView {
  const now = new Date().toISOString();
  return {
    id: -Date.now(),
    conversation_id: 'support',
    sender_user_id: null,
    sender_role: 'user',
    direction: 'client',
    author_label: 'Вы',
    type: attachment?.messageType || 'text',
    text,
    payload: attachment ? attachmentPayload(attachment) : {},
    client_message_id: clientMessageId,
    reply_to_id: replyTo?.id || null,
    reply_to: replyTo || null,
    created_at: now,
    edited_at: null,
    deleted_at: null,
    read_at: null,
    delivery_state: 'sending',
    retry_attachment: attachment,
  };
}

function ErrorRetryCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <>
      <WifiOff className="mx-auto h-6 w-6 text-rose-300" />
      <p className="mt-2 text-sm font-black text-white">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mx-auto mt-3 inline-flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        <span>Повторить</span>
      </button>
    </>
  );
}

interface WebMessengerProps {
  active?: boolean;
}

export default function WebMessenger({ active = true }: WebMessengerProps) {
  const [conversations, setConversations] = useState<ChatConversationResponse[]>([]);
  const [activeConversation, setActiveConversation] = useState<ActiveConversationKey>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('conversation') === 'support' ? 'support' : 'signals';
  });
  const [signals, setSignalsState] = useState<ChatSignalMessageResponse[]>([]);
  const [supportMessages, setSupportMessages] = useState<SupportMessageView[]>([]);
  const [signalsNextBeforeId, setSignalsNextBeforeId] = useState<number | null>(null);
  const [signalsHasMore, setSignalsHasMore] = useState(false);
  const [signalsLoadingMore, setSignalsLoadingMore] = useState(false);
  const [supportNextBeforeId, setSupportNextBeforeId] = useState<number | null>(null);
  const [supportHasMore, setSupportHasMore] = useState(false);
  const [supportLoadingMore, setSupportLoadingMore] = useState(false);
  const [loadingSignals, setLoadingSignals] = useState(true);
  const [loadingSupport, setLoadingSupport] = useState(true);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<'connecting' | 'online' | 'offline'>('connecting');
  const [supportStreamState, setSupportStreamState] = useState<'connecting' | 'online' | 'offline'>('connecting');
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [replyTarget, setReplyTarget] = useState<ChatReplyTarget | null>(null);
  const [supportSearch, setSupportSearch] = useState('');
  const [supportTypingText, setSupportTypingText] = useState('');
  const [sendingClientIds, setSendingClientIds] = useState<Set<string>>(new Set());
  const seenSignalIdsRef = useRef<Set<number>>(new Set());
  const seenSupportMessageIdsRef = useRef<Set<number>>(new Set());
  const signalsRef = useRef<ChatSignalMessageResponse[]>([]);
  const signalMergeQueueRef = useRef<Promise<void>>(Promise.resolve());
  const typingClearTimerRef = useRef<number | undefined>();
  const lastTypingSentAtRef = useRef(0);
  const { mergeSignals: mergeSignalsOffThread } = useDataProcessorWorker();

  const isTma = isTelegramMiniApp();

  const setSignals = useCallback((
    updater: ChatSignalMessageResponse[] | ((current: ChatSignalMessageResponse[]) => ChatSignalMessageResponse[]),
  ) => {
    setSignalsState((current) => {
      const nextSignals = limitRecent(
        typeof updater === 'function' ? updater(current) : updater,
        CHAT_HISTORY_ITEM_LIMIT,
      );
      signalsRef.current = nextSignals;
      return nextSignals;
    });
  }, []);

  useEffect(() => () => {
    if (typingClearTimerRef.current !== undefined) {
      window.clearTimeout(typingClearTimerRef.current);
    }
  }, []);

  const mergeSignalsIntoState = useCallback((incomingSignals: ChatSignalMessageResponse[]) => {
    if (incomingSignals.length === 0) return signalMergeQueueRef.current;

    signalMergeQueueRef.current = signalMergeQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        const nextSignals = await mergeSignalsOffThread(
          signalsRef.current,
          incomingSignals,
          CHAT_HISTORY_ITEM_LIMIT,
          currentWorkerThreshold(),
        );
        setSignals(nextSignals);
      });

    return signalMergeQueueRef.current;
  }, [mergeSignalsOffThread, setSignals]);

  const supportConversation = useMemo(
    () => conversations.find((conversation) => conversation.key === 'support') || null,
    [conversations],
  );
  const signalsConversation = useMemo(
    () => conversations.find((conversation) => conversation.key === 'signals') || null,
    [conversations],
  );

  const clearConversationUnread = useCallback((key: ActiveConversationKey) => {
    setConversations((current) => current.map((conversation) => (
      conversation.key === key && conversation.unread_count !== 0
        ? { ...conversation, unread_count: 0 }
        : conversation
    )));
  }, []);

  const loadConversations = useCallback(async () => {
    try {
      const response = await apiFetch<ChatConversationListResponse>('/chat/conversations');
      setConversations(response.items);
      setConversationError(null);
    } catch (error) {
      setConversationError('Не удалось обновить список диалогов.');
      throw error;
    }
  }, []);

  const loadSignalMessages = useCallback(async () => {
    setLoadingSignals(true);
    try {
      const response = await apiFetch<ChatSignalMessagePageResponse>('/chat/conversations/signals/messages?limit=100');
      const nextSignals = response.items;
      nextSignals.forEach((signal) => rememberRecentId(seenSignalIdsRef.current, signal.id));
      await mergeSignalsIntoState(nextSignals);
      setSignalsNextBeforeId(response.next_before_id);
      setSignalsHasMore(response.has_more);
      setSignalsError(null);
    } catch {
      setSignalsError('Не удалось загрузить сообщения личного бота.');
    } finally {
      setLoadingSignals(false);
    }
  }, [mergeSignalsIntoState]);

  const loadSupportMessages = useCallback(async () => {
    setLoadingSupport(true);
    try {
      const response = await apiFetch<ChatMessagePageResponse>('/chat/conversations/support/messages?limit=100');
      const nextMessages = response.items.map((message) => ({ ...message, delivery_state: 'sent' as const }));
      nextMessages.forEach((message) => rememberRecentId(seenSupportMessageIdsRef.current, message.id));
      setSupportMessages((current) => mergeSupportMessages(current, nextMessages));
      setSupportNextBeforeId(response.next_before_id);
      setSupportHasMore(response.has_more);
      setSupportError(null);
    } catch {
      setSupportError('Не удалось загрузить историю поддержки.');
    } finally {
      setLoadingSupport(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([loadConversations(), loadSignalMessages(), loadSupportMessages()]);
  }, [loadConversations, loadSignalMessages, loadSupportMessages]);

  const loadOlderSignalMessages = useCallback(async () => {
    if (!signalsNextBeforeId || signalsLoadingMore) return;
    setSignalsLoadingMore(true);
    try {
      const params = new URLSearchParams({
        before_id: String(signalsNextBeforeId),
        limit: '100',
      });
      const response = await apiFetch<ChatSignalMessagePageResponse>(`/chat/conversations/signals/messages?${params.toString()}`);
      response.items.forEach((signal) => rememberRecentId(seenSignalIdsRef.current, signal.id));
      await mergeSignalsIntoState(response.items);
      setSignalsNextBeforeId(response.next_before_id);
      setSignalsHasMore(response.has_more);
      setSignalsError(null);
    } catch {
      setSignalsError('Не удалось загрузить ранние сообщения личного бота.');
    } finally {
      setSignalsLoadingMore(false);
    }
  }, [mergeSignalsIntoState, signalsLoadingMore, signalsNextBeforeId]);

  const loadOlderSupportMessages = useCallback(async () => {
    if (!supportNextBeforeId || supportLoadingMore) return;
    setSupportLoadingMore(true);
    try {
      const params = new URLSearchParams({
        before_id: String(supportNextBeforeId),
        limit: '100',
      });
      const response = await apiFetch<ChatMessagePageResponse>(`/chat/conversations/support/messages?${params.toString()}`);
      const olderMessages = response.items.map((message) => ({ ...message, delivery_state: 'sent' as const }));
      olderMessages.forEach((message) => rememberRecentId(seenSupportMessageIdsRef.current, message.id));
      setSupportMessages((current) => mergeSupportMessages(current, olderMessages));
      setSupportNextBeforeId(response.next_before_id);
      setSupportHasMore(response.has_more);
      setSupportError(null);
    } catch {
      setSupportError('Не удалось загрузить раннюю историю поддержки.');
    } finally {
      setSupportLoadingMore(false);
    }
  }, [supportLoadingMore, supportNextBeforeId]);

  const { enqueue: enqueueSignalStreamMessage, clear: clearSignalStreamMessages } = useThrottledEventBuffer<ChatSignalMessageResponse>((incomingSignals) => {
    void mergeSignalsIntoState(incomingSignals);
    void loadConversations().catch(() => undefined);
  }, 400);

  const { enqueue: enqueueSupportStreamMessage, clear: clearSupportStreamMessages } = useThrottledEventBuffer<SupportMessageView>((incomingMessages) => {
    setSupportMessages((current) => mergeSupportMessages(current, incomingMessages));
  }, 400);

  const { enqueue: enqueueStreamConversation, clear: clearStreamConversations } = useThrottledEventBuffer<ChatConversationResponse>((incomingConversations) => {
    setConversations((current) => mergeConversations(current, incomingConversations));
  }, 400);

  const refreshConversationsThrottled = useThrottledCallback(() => {
    void loadConversations().catch(() => undefined);
  }, 500);

  useEffect(() => {
    if (active) return;
    clearSignalStreamMessages();
    clearSupportStreamMessages();
    clearStreamConversations();
    if (typingClearTimerRef.current !== undefined) {
      window.clearTimeout(typingClearTimerRef.current);
      typingClearTimerRef.current = undefined;
    }
    setSupportTypingText('');
  }, [active, clearSignalStreamMessages, clearStreamConversations, clearSupportStreamMessages]);

  useEffect(() => {
    if (!active || isTma) return;
    void refreshAll();
  }, [active, isTma, refreshAll]);

  useEffect(() => {
    if (!active || isTma) return;
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
  }, [active, isTma]);

  useEffect(() => {
    if (!active || isTma) return;

    const handleStatus = (event: Event) => {
      const state = (event as CustomEvent<{ state?: 'connecting' | 'online' | 'offline' }>).detail?.state;
      if (state) setStreamState(state);
    };
    const handleSignal = (event: Event) => {
      const signal = (event as CustomEvent<ChatSignalMessageResponse>).detail;
      if (!signal || seenSignalIdsRef.current.has(signal.id)) return;
      if (signal.type.startsWith('support_')) return;
      rememberRecentId(seenSignalIdsRef.current, signal.id);
      enqueueSignalStreamMessage(signal);
    };

    window.addEventListener(WEB_SIGNAL_STATUS_EVENT, handleStatus);
    window.addEventListener(WEB_SIGNAL_EVENT, handleSignal as EventListener);
    return () => {
      window.removeEventListener(WEB_SIGNAL_STATUS_EVENT, handleStatus);
      window.removeEventListener(WEB_SIGNAL_EVENT, handleSignal as EventListener);
    };
  }, [active, enqueueSignalStreamMessage, isTma]);

  useEffect(() => {
    if (!active || isTma) return;
    if (!supportsWebSocketRuntime()) {
      setSupportStreamState('offline');
      return;
    }
    const token = getStoredAuthToken();
    if (token === MOCK_DEBUG_AUTH_TOKEN) {
      setSupportStreamState('online');
      return;
    }

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let closedByUnmount = false;
    let reconnectAttempt = 0;

    const scheduleReconnect = () => {
      if (closedByUnmount) return;
      const baseDelay = Math.min(30_000, 1000 * (2 ** Math.min(reconnectAttempt, 5)));
      const jitter = Math.floor(Math.random() * 600);
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(connect, baseDelay + jitter);
    };

    const handleMessageCreated = (payload: ChatStreamMessageCreatedEvent) => {
      if (!payload.message || seenSupportMessageIdsRef.current.has(payload.message.id)) return;
      rememberRecentId(seenSupportMessageIdsRef.current, payload.message.id);
      const nextMessage = { ...payload.message, delivery_state: 'sent' as const };
      enqueueSupportStreamMessage(nextMessage);
      if (payload.conversation) {
        enqueueStreamConversation(payload.conversation);
      } else {
        refreshConversationsThrottled();
      }
      if (payload.message.direction === 'staff') {
        const body = supportMessagePreview(payload.message).slice(0, 260);
        if (rememberWebNotificationEvent({
          id: String(payload.message.id),
          title: 'Shamrai написал в чат',
          body,
          type: 'support_staff_message',
          url: '/app?open=web-chat&conversation=support',
          source: 'websocket',
        })) {
          notifyInfo(body, 'Shamrai написал в чат');
          void playIncomingSupportSound();
        }
      }
    };

    const handleConversationUpdated = (payload: ChatStreamConversationUpdatedEvent) => {
      if (!payload.conversation) return;
      enqueueStreamConversation(payload.conversation);
    };

    const handleReadUpdated = (payload: ChatReadUpdatedEvent) => {
      if (!payload?.conversation_id) return;
      setSupportMessages((current) => applyReadReceiptToMessages(current, payload));
    };

    const handleTypingUpdated = (payload: ChatTypingUpdatedEvent) => {
      if (!payload?.is_typing || payload.direction !== 'staff') return;
      if (typingClearTimerRef.current !== undefined) {
        window.clearTimeout(typingClearTimerRef.current);
      }
      setSupportTypingText('Shamrai печатает...');
      typingClearTimerRef.current = window.setTimeout(() => {
        typingClearTimerRef.current = undefined;
        setSupportTypingText('');
      }, 4500);
    };

    async function connect() {
      setSupportStreamState('connecting');
      let ticket: string;
      try {
        const ticketResponse = await apiFetch<ChatStreamTicketResponse>('/chat/stream-ticket', { method: 'POST' });
        ticket = ticketResponse.ticket;
      } catch {
        setSupportStreamState('offline');
        scheduleReconnect();
        return;
      }
      if (closedByUnmount) return;

      socket = new WebSocket(buildApiWebSocketUrl('/api/chat/stream', { ticket }));
      socket.onopen = () => {
        reconnectAttempt = 0;
        setSupportStreamState('online');
        void refreshAll();
        if (pingTimer) window.clearInterval(pingTimer);
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
        }, 25000);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.event === 'pong') return;
          if (payload?.event === 'chat.message.created') handleMessageCreated(payload as ChatStreamMessageCreatedEvent);
          if (payload?.event === 'chat.conversation.updated') handleConversationUpdated(payload as ChatStreamConversationUpdatedEvent);
          if (payload?.event === 'chat.read.updated') handleReadUpdated(payload as ChatReadUpdatedEvent);
          if (payload?.event === 'chat.typing.updated') handleTypingUpdated(payload as ChatTypingUpdatedEvent);
        } catch {
          // Ignore malformed stream frames.
        }
      };

      socket.onclose = () => {
        if (pingTimer) window.clearInterval(pingTimer);
        setSupportStreamState('offline');
        scheduleReconnect();
      };

      socket.onerror = () => {
        socket?.close();
      };
    }

    void connect();

    return () => {
      closedByUnmount = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (pingTimer) window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [
    active,
    enqueueStreamConversation,
    enqueueSupportStreamMessage,
    isTma,
    refreshAll,
    refreshConversationsThrottled,
  ]);

  const updateSignalForecastStatus = useCallback((requestId: string, status: string) => {
    setSignals((current) => current.map((signal) => {
      if (signal.data?.forecast_request_id !== requestId) return signal;
      return {
        ...signal,
        data: {
          ...signal.data,
          forecast_status: status,
        },
      };
    }));
  }, [setSignals]);

  const openSupportDraft = useCallback((draftText: string) => {
    setActiveConversation('support');
    setDraft(draftText);
    window.setTimeout(() => {
      document.getElementById('web-bot-chat')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
  }, []);

  const answerForecastRequest = useCallback(async (signal: ChatSignalMessageResponse, action: ForecastSignalAction) => {
    const requestId = signal.data?.forecast_request_id;
    if (!requestId) return;

    const busyKey = `${requestId}:${action}`;
    setActionBusy(busyKey);
    void unlockIncomingSignalSound();
    try {
      const response = await apiFetch<ForecastSignalActionResponse>(
        `/signals/forecast-requests/${requestId}/${action}`,
        { method: 'POST' },
      );
      updateSignalForecastStatus(requestId, response.status);
      if (response.action === 'inactive') {
        const notice = signalActionNotice(signal, action, response.message);
        notifyInfo(notice.message, notice.title);
        return;
      }
      if (action === 'take' && response.action === 'contact_required' && response.contact?.draft_text) {
        openSupportDraft(response.contact.draft_text);
        notifyInfo(response.message, signalActionNotice(signal, action, response.message).title);
        return;
      }
      const notice = signalActionNotice(signal, action, response.message);
      if (action === 'take') {
        notifySuccess(notice.message, notice.title);
      } else {
        notifyInfo(notice.message, notice.title);
      }
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось обработать заявку');
    } finally {
      setActionBusy((current) => (current === busyKey ? null : current));
    }
  }, [openSupportDraft, updateSignalForecastStatus]);

  const sendSupportText = useCallback(async (
    text: string,
    clientMessageId: string = crypto.randomUUID(),
    replyTo: ChatReplyTarget | null = replyTarget,
  ) => {
    const cleanText = text.trim();
    if (!cleanText) {
      notifyError('Введите сообщение для Shamrai');
      return false;
    }

    const optimisticMessage = makeOptimisticMessage(cleanText, clientMessageId, undefined, replyTo);
    setSendingClientIds((current) => new Set(current).add(clientMessageId));
    setSupportMessages((current) => mergeSupportMessages(current, [optimisticMessage]));
    void unlockIncomingSignalSound();

    try {
      const response = await apiFetch<ChatMessageResponse>('/chat/conversations/support/messages', {
        method: 'POST',
        body: JSON.stringify({
          client_message_id: clientMessageId,
          text: cleanText,
          ...(replyTo?.id ? { reply_to_id: replyTo.id } : {}),
        }),
      });
      rememberRecentId(seenSupportMessageIdsRef.current, response.id);
      setSupportMessages((current) => mergeSupportMessages(
        current.filter((message) => message.client_message_id !== clientMessageId || message.id > 0),
        [{ ...response, delivery_state: 'sent' }],
      ));
      setDraft('');
      setReplyTarget(null);
      void loadConversations().catch(() => undefined);
      return true;
    } catch (error: any) {
      setSupportMessages((current) => current.map((message) => (
        message.client_message_id === clientMessageId
          ? { ...message, delivery_state: 'failed' }
          : message
      )));
      notifyError(error?.message || 'Не удалось отправить сообщение');
      return false;
    } finally {
      setSendingClientIds((current) => {
        const next = new Set(current);
        next.delete(clientMessageId);
        return next;
      });
    }
  }, [loadConversations, replyTarget]);

  const sendSupportAttachment = useCallback(async (
    attachment: ChatComposerAttachment,
    text: string,
    clientMessageId: string = crypto.randomUUID(),
    replyTo: ChatReplyTarget | null = replyTarget,
  ) => {
    const cleanText = text.trim();
    const optimisticMessage = makeOptimisticMessage(cleanText, clientMessageId, attachment, replyTo);
    setSendingClientIds((current) => new Set(current).add(clientMessageId));
    setSupportMessages((current) => mergeSupportMessages(current, [optimisticMessage]));
    void unlockIncomingSignalSound();

    const formData = new FormData();
    formData.append('client_message_id', clientMessageId);
    formData.append('message_type', attachment.messageType);
    formData.append('file', attachment.file);
    if (cleanText) formData.append('text', cleanText);
    if (replyTo?.id) formData.append('reply_to_id', String(replyTo.id));
    if (attachment.durationMs) formData.append('duration_ms', String(Math.round(attachment.durationMs)));

    try {
      const response = await apiFetch<ChatMessageResponse>('/chat/conversations/support/attachments', {
        method: 'POST',
        body: formData,
      });
      rememberRecentId(seenSupportMessageIdsRef.current, response.id);
      setSupportMessages((current) => mergeSupportMessages(
        current.filter((message) => message.client_message_id !== clientMessageId || message.id > 0),
        [{ ...response, delivery_state: 'sent' }],
      ));
      URL.revokeObjectURL(attachment.previewUrl);
      setReplyTarget(null);
      void loadConversations().catch(() => undefined);
      return true;
    } catch (error: any) {
      const retryAttachment = {
        ...attachment,
        previewUrl: URL.createObjectURL(attachment.file),
      };
      setSupportMessages((current) => current.map((message) => (
        message.client_message_id === clientMessageId
          ? {
            ...message,
            payload: attachmentPayload(retryAttachment),
            delivery_state: 'failed',
            retry_attachment: retryAttachment,
          }
          : message
      )));
      notifyError(error?.message || 'Не удалось отправить вложение');
      return true;
    } finally {
      setSendingClientIds((current) => {
        const next = new Set(current);
        next.delete(clientMessageId);
        return next;
      });
    }
  }, [loadConversations, replyTarget]);

  const handleRetry = useCallback((message: SupportMessageView) => {
    if (sendingClientIds.has(message.client_message_id)) return;
    if (message.retry_attachment) {
      void sendSupportAttachment(message.retry_attachment, message.text || '', message.client_message_id, message.reply_to || null);
      return;
    }
    if (message.text) void sendSupportText(message.text, message.client_message_id, message.reply_to || null);
  }, [sendSupportAttachment, sendSupportText, sendingClientIds]);

  const handleReply = useCallback((message: SupportMessageView) => {
    setActiveConversation('support');
    setReplyTarget(replyTargetFromMessage(message));
  }, []);

  const handleCopy = useCallback((message: SupportMessageView) => {
    const text = message.text?.trim();
    if (!text) return;
    void navigator.clipboard.writeText(text)
      .then(() => notifySuccess('Текст скопирован'))
      .catch(() => notifyError('Не удалось скопировать текст'));
  }, []);

  const handleDownload = useCallback((message: SupportMessageView) => {
    const downloadUrl = typeof message.payload?.download_url === 'string' ? message.payload.download_url : '';
    if (!downloadUrl) {
      notifyError('Вложение пока недоступно для скачивания');
      return;
    }
    const filename = String(message.payload?.original_filename || `chat-attachment-${message.id}`);
    void downloadApiFile(downloadUrl, filename).catch((error: any) => {
      notifyError(error?.message || 'Не удалось скачать вложение');
    });
  }, []);

  const sendSupportTyping = useCallback(() => {
    if (activeConversation !== 'support') return;
    const now = Date.now();
    if (now - lastTypingSentAtRef.current < 2500) return;
    lastTypingSentAtRef.current = now;
    void apiFetch('/chat/conversations/support/typing', {
      method: 'POST',
      body: JSON.stringify({ is_typing: true }),
    }).catch(() => undefined);
  }, [activeConversation]);

  const markActiveRead = useCallback(() => {
    if (activeConversation === 'signals') {
      const lastSignal = signals[signals.length - 1];
      if (!lastSignal) return;
      void apiFetch('/chat/conversations/signals/read', {
        method: 'POST',
        body: JSON.stringify({ last_read_signal_id: lastSignal.id }),
      })
        .then(() => clearConversationUnread('signals'))
        .catch(() => undefined);
      return;
    }
    const sentMessages = supportMessages.filter((message) => message.id > 0);
    const lastMessage = sentMessages[sentMessages.length - 1];
    if (!lastMessage) return;
    void apiFetch('/chat/conversations/support/read', {
      method: 'POST',
      body: JSON.stringify({ last_read_message_id: lastMessage.id }),
    })
      .then(() => clearConversationUnread('support'))
      .catch(() => undefined);
  }, [activeConversation, clearConversationUnread, signals, supportMessages]);

  useEffect(() => {
    if (!active) return;
    markActiveRead();
  }, [active, markActiveRead]);

  const handleForecastAction = useCallback((nextSignal: ChatSignalMessageResponse, action: ForecastSignalAction) => {
    void answerForecastRequest(nextSignal, action);
  }, [answerForecastRequest]);

  const signalUnreadStartIndex = signalsConversation?.unread_count
    ? Math.max(0, signals.length - signalsConversation.unread_count)
    : -1;
  const signalItems = useMemo(() => signals.map((signal, index) => {
    const requestId = signal.data?.forecast_request_id;
    const signalBusy = requestId && actionBusy?.startsWith(`${requestId}:`) ? actionBusy : null;
    return {
      key: `signal:${signal.id}`,
      createdAt: signal.created_at,
      unreadDivider: signalUnreadStartIndex === index,
      element: (
        <SignalMessageCard
          signal={signal}
          index={index}
          actionBusy={signalBusy}
          onForecastAction={handleForecastAction}
        />
      ),
    };
  }), [actionBusy, handleForecastAction, signalUnreadStartIndex, signals]);

  const visibleSupportMessages = useMemo(() => {
    const cleanSearch = supportSearch.trim().toLowerCase();
    if (!cleanSearch) return supportMessages;
    return supportMessages.filter((message) => [
      message.text,
      message.author_label,
      message.payload?.original_filename,
      message.reply_to?.text,
      message.reply_to?.author_label,
    ].join(' ').toLowerCase().includes(cleanSearch));
  }, [supportMessages, supportSearch]);

  const supportUnreadStartIndex = !supportSearch.trim() && supportConversation?.unread_count
    ? Math.max(0, visibleSupportMessages.length - supportConversation.unread_count)
    : -1;
  const supportItems = useMemo(() => visibleSupportMessages.map((message, index) => ({
    key: `support:${message.client_message_id}:${message.id}`,
    createdAt: message.created_at,
    unreadDivider: supportUnreadStartIndex === index,
    element: (
      <SupportMessageBubble
        message={message}
        ownerLabel={message.direction === 'client' ? 'Вы' : undefined}
        onRetry={handleRetry}
        onReply={handleReply}
        onCopy={handleCopy}
        onDownload={handleDownload}
      />
    ),
  })), [handleCopy, handleDownload, handleReply, handleRetry, supportUnreadStartIndex, visibleSupportMessages]);

  if (isTma) return null;

  const activeSummary = activeConversation === 'signals' ? signalsConversation : supportConversation;
  const activeTitle = conversationTitle(activeSummary, activeConversation);
  const statusLabel = activeConversation === 'signals'
    ? (streamState === 'online' ? 'online' : streamState === 'connecting' ? 'sync' : 'offline')
    : (supportStreamState === 'online' ? 'online' : supportStreamState === 'connecting' ? 'sync' : 'offline');
  const supportClosed = supportConversation?.status === 'closed';

  return (
    <section
      id="web-bot-chat"
      className="web-bot-chat mb-0 flex min-w-0 flex-col overflow-hidden rounded-3xl border border-white/10 bg-white/[0.045] shadow-glass backdrop-blur-xl"
    >
      <div className="border-b border-white/10 bg-slate-950/35 px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2 sm:gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
              {activeConversation === 'signals' ? <Bot className="h-5 w-5" /> : <Headphones className="h-5 w-5" />}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-black text-white">{activeTitle}</p>
              <div className="mt-1 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
                <Radio className={`h-3 w-3 ${statusLabel === 'online' ? 'text-emerald-300' : 'text-slate-500'}`} />
                <span>{statusLabel}</span>
              </div>
            </div>
          </div>

          <div className="flex min-h-[34px] max-w-full shrink-0 items-center gap-2 rounded-2xl border border-emerald-300/15 bg-emerald-300/10 px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.12em] text-emerald-100">
            <ShieldCheck className="h-3.5 w-3.5" />
            <span className="whitespace-nowrap">Web канал</span>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(['signals', 'support'] as const).map((key) => {
            const conversation = key === 'signals' ? signalsConversation : supportConversation;
            const active = activeConversation === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setActiveConversation(key)}
                className={`min-w-0 rounded-2xl border px-3 py-2 text-left transition-all active:scale-[0.99] ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/12 text-white'
                    : 'border-white/10 bg-white/[0.035] text-slate-300 hover:border-cyan-300/20'
                }`}
              >
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-xs font-black">{key === 'signals' ? 'Личный бот' : 'Поддержка'}</span>
                  {Boolean(conversation?.unread_count) && (
                    <span className="grid h-5 min-w-5 place-items-center rounded-full bg-rose-400 px-1.5 text-[10px] font-black text-white">
                      {conversation?.unread_count}
                    </span>
                  )}
                </div>
                <p className="mt-1 line-clamp-1 text-[10px] font-semibold text-slate-500">
                  {lastMessagePreview(conversation, key)}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {activeConversation === 'signals' ? (
        <MessageList
          items={signalItems}
          loading={loadingSignals}
          active={active}
          hasMore={signalsHasMore}
          loadingMore={signalsLoadingMore}
          loadMoreLabel="Показать ранние сигналы"
          onLoadMore={() => void loadOlderSignalMessages()}
          empty={(
            <div className="grid min-h-[92px] w-full max-w-[34rem] place-items-center rounded-2xl border border-white/10 bg-slate-950/35 p-4 text-center">
              {signalsError ? (
                <ErrorRetryCard message={signalsError} onRetry={() => void loadSignalMessages()} />
              ) : (
                <div className="grid place-items-center">
                  <ShieldCheck className="mx-auto h-6 w-6 text-emerald-300" />
                  <p className="mt-2 text-sm font-black text-white">Канал готов</p>
                </div>
              )}
            </div>
          )}
        />
      ) : (
        <>
          <div className="border-b border-white/10 bg-slate-950/30 px-3 py-2">
            <label className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded-xl border border-white/10 bg-slate-950/55 px-3 py-2">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={supportSearch}
                onChange={(event) => setSupportSearch(event.target.value)}
                placeholder="Поиск по истории поддержки"
                className="min-w-0 bg-transparent text-sm font-semibold text-white placeholder:text-slate-600 focus:outline-none"
              />
              {supportSearch.trim() && (
                <button
                  type="button"
                  onClick={() => setSupportSearch('')}
                  title="Очистить поиск"
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-white/[0.08] hover:text-white"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </label>
          </div>
          <MessageList
            items={supportItems}
            loading={loadingSupport}
            active={active}
            hasMore={supportHasMore}
            loadingMore={supportLoadingMore}
            loadMoreLabel="Показать раннюю историю"
            onLoadMore={() => void loadOlderSupportMessages()}
            empty={(
              <div className="grid min-h-[92px] w-full max-w-[34rem] place-items-center rounded-2xl border border-white/10 bg-slate-950/35 p-4 text-center">
                {supportError || conversationError ? (
                  <ErrorRetryCard message={supportError || conversationError || ''} onRetry={() => void refreshAll()} />
                ) : supportSearch.trim() ? (
                  <div className="grid place-items-center">
                    <Search className="mx-auto h-6 w-6 text-slate-400" />
                    <p className="mt-2 text-sm font-black text-white">Ничего не найдено</p>
                  </div>
                ) : (
                  <div className="grid place-items-center">
                    <MessageCircle className="mx-auto h-6 w-6 text-cyan-200" />
                    <p className="mt-2 text-sm font-black text-white">История поддержки пуста</p>
                  </div>
                )}
              </div>
            )}
          />
        </>
      )}

      {activeConversation === 'support' && (
        <>
          {supportClosed && (
            <p className="mx-3 mt-3 rounded-xl border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-[11px] font-bold text-amber-100">
              Диалог закрыт. Новое сообщение снова откроет обращение.
            </p>
          )}
          {supportTypingText && (
            <p className="mx-3 mt-2 rounded-xl border border-cyan-300/15 bg-cyan-300/10 px-3 py-2 text-[11px] font-bold text-cyan-100">
              {supportTypingText}
            </p>
          )}
          {replyTarget && (
            <div className="mx-3 mt-2 flex min-w-0 items-center gap-3 rounded-xl border border-cyan-300/20 bg-cyan-300/10 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-[10px] font-black uppercase tracking-[0.12em] text-cyan-100/75">
                  Ответ на {replyTarget.author_label}
                </p>
                <p className="mt-1 truncate text-xs font-bold text-white/75">{replyTarget.text || 'Сообщение'}</p>
              </div>
              <button
                type="button"
                onClick={() => setReplyTarget(null)}
                title="Отменить ответ"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-white/65 transition hover:bg-white/[0.08] hover:text-white"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <MessageComposer
            draft={draft}
            onDraftChange={setDraft}
            sending={sendingClientIds.size > 0}
            active={active}
            placeholder="Написать Shamrai..."
            submitTitle="Отправить сообщение Shamrai"
            onSendText={sendSupportText}
            onSendAttachment={sendSupportAttachment}
            onError={notifyError}
            onTyping={sendSupportTyping}
          />
        </>
      )}

      {(activeConversation === 'signals' ? streamState : supportStreamState) === 'offline' && (
        <div className="flex items-center gap-2 border-t border-white/10 bg-slate-950/45 px-4 py-2 text-[11px] font-bold text-slate-400">
          <WifiOff className="h-3.5 w-3.5 text-rose-300" />
          <span className="min-w-0">Переподключение...</span>
        </div>
      )}
    </section>
  );
}
