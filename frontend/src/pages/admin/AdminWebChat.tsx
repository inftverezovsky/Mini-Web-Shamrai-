import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  BellRing,
  ArrowLeft,
  Loader2,
  Lock,
  MessageCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  Unlock,
  WifiOff,
  X,
} from 'lucide-react';

import {
  ChatConversationListResponse,
  ChatConversationResponse,
  ChatConversationStatus,
  ChatMessagePageResponse,
  ChatMessageResponse,
  ChatReadUpdatedEvent,
} from '../../schemas/schemas';
import { apiFetch, downloadApiFile } from '../../utils/api';
import { notifyError, notifySuccess } from '../../utils/notify';
import { registerWebPushSubscription } from '../../utils/webPush';
import MessageComposer, { ChatComposerAttachment } from '../../features/chat/MessageComposer';
import SupportMessageBubble, { SupportMessageView } from '../../features/chat/SupportMessageBubble';
import { limitRecent } from '../../utils/realtimeLimits';
import {
  ADMIN_WEB_CHAT_CONVERSATION_EVENT,
  ADMIN_WEB_CHAT_MESSAGE_EVENT,
  ADMIN_WEB_CHAT_READ_EVENT,
  ADMIN_WEB_CHAT_STATUS_EVENT,
  ADMIN_WEB_CHAT_TYPING_EVENT,
  AdminWebChatMessageEventPayload,
} from '../../components/AdminWebChatListener';
import { applyReadReceiptToMessages } from '../../features/chat/readReceipts';

const ADMIN_CHAT_MESSAGES_PAGE_LIMIT = 100;
const ADMIN_CHAT_CONVERSATION_LIMIT = 80;
const ADMIN_CHAT_HISTORY_ITEM_LIMIT = 500;

function messageTime(value?: string | null) {
  if (!value) return '';
  try {
    return new Intl.DateTimeFormat('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value));
  } catch {
    return '';
  }
}

function messageDayKey(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function messageDateLabel(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (messageDayKey(value) === messageDayKey(today.toISOString())) return 'Сегодня';
  if (messageDayKey(value) === messageDayKey(yesterday.toISOString())) return 'Вчера';
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

function isMessageListNearBottom(element: HTMLDivElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 120;
}

function mergeConversation(
  conversations: ChatConversationResponse[],
  nextConversation: ChatConversationResponse,
) {
  const byId = new Map<string, ChatConversationResponse>();
  conversations.forEach((conversation) => {
    if (conversation.id) byId.set(conversation.id, conversation);
  });
  if (nextConversation.id) byId.set(nextConversation.id, nextConversation);
  return Array.from(byId.values()).sort((left, right) => {
    const leftTime = left.last_message_at ? new Date(left.last_message_at).getTime() : 0;
    const rightTime = right.last_message_at ? new Date(right.last_message_at).getTime() : 0;
    return rightTime - leftTime;
  }).slice(0, ADMIN_CHAT_CONVERSATION_LIMIT);
}

function appendMessage(messages: SupportMessageView[], nextMessage: SupportMessageView) {
  if (messages.some((message) => message.id > 0 && message.id === nextMessage.id)) return messages;
  const withoutOptimisticDuplicate = messages.filter((message) => message.client_message_id !== nextMessage.client_message_id || message.id > 0);
  return limitRecent([...withoutOptimisticDuplicate, nextMessage].sort((left, right) => {
    const leftTime = new Date(left.created_at).getTime();
    const rightTime = new Date(right.created_at).getTime();
    return (leftTime - rightTime) || (left.id - right.id);
  }), ADMIN_CHAT_HISTORY_ITEM_LIMIT);
}

function matchesSearch(conversation: ChatConversationResponse, cleanSearch: string) {
  if (!cleanSearch) return true;
  const user = conversation.owner_user;
  const searchable = [
    user.telegram_id,
    user.username,
    user.first_name,
    user.last_name,
    user.display_name,
    conversation.last_message_text,
  ].join(' ').toLowerCase();
  return searchable.includes(cleanSearch);
}

function textPreview(value?: string | null) {
  const clean = (value || '').trim();
  return clean || 'Новый диалог';
}

function clientDisplayName(user: AdminChatClientSearchResult) {
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  return user.display_name || fullName || (user.username ? `@${user.username}` : user.is_web_only ? 'Web/VK клиент' : `ID ${user.telegram_id}`);
}

function messagePreview(message: ChatMessageResponse | SupportMessageView) {
  if (message.text?.trim()) return message.text;
  if (message.type === 'image') return 'Скриншот';
  if (message.type === 'voice') return 'Голосовое сообщение';
  if (message.type === 'file') return String(message.payload?.original_filename || 'Файл');
  return 'Новое сообщение';
}

type ChatReplyTarget = NonNullable<ChatMessageResponse['reply_to']>;

interface AdminChatClientSearchResult {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  is_web_only: boolean;
  role: string;
  display_name?: string;
  client_group?: string | null;
  client_tag?: string | null;
}

interface AdminChatClientSearchResponse {
  items: AdminChatClientSearchResult[];
  has_more: boolean;
  next_cursor: string | null;
  total: number;
  filtered_total: number;
}

function replyTargetFromMessage(message: ChatMessageResponse | SupportMessageView): ChatReplyTarget {
  return {
    id: message.id,
    author_label: message.author_label,
    type: message.type,
    text: messagePreview(message),
    payload: message.payload || {},
  };
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

function makeOptimisticStaffMessage(
  conversationId: string,
  text: string,
  clientMessageId: string,
  attachment?: ChatComposerAttachment,
  replyTo?: ChatReplyTarget | null,
): SupportMessageView {
  const now = new Date().toISOString();
  return {
    id: -Date.now(),
    conversation_id: conversationId,
    sender_user_id: null,
    sender_role: 'admin',
    direction: 'staff',
    author_label: 'Shamrai',
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

function syncConversationForStatus(
  conversations: ChatConversationResponse[],
  nextConversation: ChatConversationResponse,
  statusFilter: ChatConversationStatus,
) {
  if (!nextConversation.id) return conversations;
  if (nextConversation.status !== statusFilter) {
    return conversations.filter((conversation) => conversation.id !== nextConversation.id);
  }
  return mergeConversation(conversations, nextConversation);
}

interface VirtualConversationListProps {
  conversations: ChatConversationResponse[];
  selectedConversationId: string | null;
  loading: boolean;
  onSelectConversation: (conversationId: string | null) => void;
}

function VirtualConversationList({
  conversations,
  selectedConversationId,
  loading,
  onSelectConversation,
}: VirtualConversationListProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: conversations.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 126,
    overscan: 4,
    getItemKey: (index) => conversations[index]?.id || conversations[index]?.owner_user.telegram_id || index,
  });

  return (
    <div ref={parentRef} className="admin-chat-conversation-list min-h-[240px] flex-1 overflow-y-auto p-2 xl:min-h-0">
      {loading && (
        <div className="flex items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>Загружаем...</span>
        </div>
      )}

      {!loading && conversations.length === 0 && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-center text-xs font-bold text-slate-500">
          Диалогов пока нет.
        </div>
      )}

      {conversations.length > 0 && (
        <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const conversation = conversations[virtualItem.index];
            if (!conversation) return null;
            const active = selectedConversationId === conversation.id;
            const needsReply = conversation.last_message && 'direction' in conversation.last_message
              ? conversation.last_message.direction === 'client'
              : false;

            return (
              <div
                key={conversation.id || conversation.owner_user.telegram_id}
                ref={virtualizer.measureElement}
                data-index={virtualItem.index}
                className="admin-chat-conversation-row absolute left-0 top-0 w-full pb-2"
                style={{ transform: `translateY(${virtualItem.start}px)` }}
              >
                <button
                  type="button"
                  onClick={() => onSelectConversation(conversation.id)}
                  className={`w-full rounded-2xl border p-3 text-left transition-all active:scale-[0.99] ${
                    active
                      ? 'border-cyan-300/35 bg-cyan-300/12 text-white'
                      : 'border-white/10 bg-white/[0.035] text-slate-200 hover:border-cyan-300/20'
                  }`}
                >
                  <div className="flex min-w-0 items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-black">{conversation.owner_user.display_name}</p>
                      <p className="mt-0.5 truncate text-[10px] font-bold text-slate-500">
                        {conversation.owner_user.username ? `@${conversation.owner_user.username}` : `ID ${conversation.owner_user.telegram_id}`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {conversation.unread_count > 0 && (
                        <span className="grid h-5 min-w-5 place-items-center rounded-full bg-rose-400 px-1.5 text-[10px] font-black text-white">
                          {conversation.unread_count}
                        </span>
                      )}
                      {needsReply && (
                        <span className="rounded-lg border border-amber-300/25 bg-amber-300/12 px-2 py-1 text-[9px] font-black uppercase tracking-[0.1em] text-amber-200">
                          ответ
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="mt-2 line-clamp-2 text-xs font-semibold leading-relaxed text-slate-400">
                    {textPreview(conversation.last_message_text)}
                  </p>
                  <p className="mt-2 text-[9px] font-black uppercase tracking-[0.12em] text-white/35">
                    {messageTime(conversation.last_message_at)}
                  </p>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface VirtualAdminMessageListProps {
  active: boolean;
  messages: SupportMessageView[];
  selectedConversation: ChatConversationResponse | null;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetry: (message: SupportMessageView) => void;
  onReply: (message: SupportMessageView) => void;
  onCopy: (message: SupportMessageView) => void;
  onDownload: (message: SupportMessageView) => void;
}

function VirtualAdminMessageList({
  active,
  messages,
  selectedConversation,
  loading,
  error,
  hasMore,
  loadingMore,
  onLoadMore,
  onRetry,
  onReply,
  onCopy,
  onDownload,
}: VirtualAdminMessageListProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const previousScrollRef = useRef<{
    firstKey?: string;
    lastKey?: string;
    scrollHeight: number;
    scrollTop: number;
    wasNearBottom: boolean;
  } | null>(null);
  const unreadStartIndex = selectedConversation?.unread_count
    ? Math.max(0, messages.length - selectedConversation.unread_count)
    : -1;
  const decoratedMessages = useMemo(() => messages.map((message, index) => {
    const previous = messages[index - 1];
    const showDateSeparator = Boolean(
      message.created_at && messageDayKey(message.created_at) !== messageDayKey(previous?.created_at),
    );
    return {
      message,
      showDateSeparator,
      showUnreadDivider: unreadStartIndex === index,
    };
  }), [messages, unreadStartIndex]);
  const virtualizer = useVirtualizer({
    count: decoratedMessages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 118,
    overscan: 3,
    getItemKey: (index) => `${decoratedMessages[index]?.message.client_message_id}:${decoratedMessages[index]?.message.id}`,
  });

  const requestOlderMessages = useCallback(() => {
    if (!hasMore || loadingMore) return;
    onLoadMore();
  }, [hasMore, loadingMore, onLoadMore]);

  const handleScroll = useCallback(() => {
    const scrollElement = parentRef.current;
    if (!scrollElement || scrollElement.scrollTop > 80) return;
    if (messages.length > 0) {
      const nearBottom = isMessageListNearBottom(scrollElement);
      setShowJumpToBottom(!nearBottom);
      previousScrollRef.current = {
        firstKey: `${messages[0]?.client_message_id}:${messages[0]?.id}`,
        lastKey: `${messages[messages.length - 1]?.client_message_id}:${messages[messages.length - 1]?.id}`,
        scrollHeight: scrollElement.scrollHeight,
        scrollTop: scrollElement.scrollTop,
        wasNearBottom: nearBottom,
      };
    }
    requestOlderMessages();
  }, [messages, requestOlderMessages]);

  const scrollToBottom = useCallback(() => {
    const scrollElement = parentRef.current;
    if (!scrollElement) return;
    scrollElement.scrollTo({ top: scrollElement.scrollHeight, behavior: 'smooth' });
    setShowJumpToBottom(false);
    previousScrollRef.current = {
      firstKey: `${messages[0]?.client_message_id}:${messages[0]?.id}`,
      lastKey: `${messages[messages.length - 1]?.client_message_id}:${messages[messages.length - 1]?.id}`,
      scrollHeight: scrollElement.scrollHeight,
      scrollTop: scrollElement.scrollTop,
      wasNearBottom: true,
    };
  }, [messages]);

  useEffect(() => {
    const scrollElement = parentRef.current;
    if (!scrollElement) return undefined;
    if (!active) return undefined;
    if (messages.length === 0) {
      previousScrollRef.current = null;
      return undefined;
    }

    const firstKey = `${messages[0]?.client_message_id}:${messages[0]?.id}`;
    const lastKey = `${messages[messages.length - 1]?.client_message_id}:${messages[messages.length - 1]?.id}`;
    const previousScroll = previousScrollRef.current;
    const prependedMessages = Boolean(
      previousScroll
      && previousScroll.firstKey !== firstKey
      && previousScroll.lastKey === lastKey,
    );
    const shouldStickToBottom = !previousScroll || (
      previousScroll.lastKey !== lastKey && previousScroll.wasNearBottom
    );

    const frame = window.requestAnimationFrame(() => {
      if (prependedMessages && previousScroll) {
        const scrollDelta = scrollElement.scrollHeight - previousScroll.scrollHeight;
        scrollElement.scrollTop = previousScroll.scrollTop + scrollDelta;
      } else if (shouldStickToBottom) {
        scrollElement.scrollTo({ top: scrollElement.scrollHeight, behavior: 'auto' });
      }
      const nearBottom = isMessageListNearBottom(scrollElement);
      setShowJumpToBottom(!nearBottom);
      previousScrollRef.current = {
        firstKey,
        lastKey,
        scrollHeight: scrollElement.scrollHeight,
        scrollTop: scrollElement.scrollTop,
        wasNearBottom: nearBottom,
      };
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, messages]);

  return (
    <div
      ref={parentRef}
      onScroll={handleScroll}
      className="admin-chat-message-list chat-cover-backdrop relative min-h-0 flex-1 overflow-y-auto px-4 py-4"
    >
      {(hasMore || loadingMore) && selectedConversation && (
        <div className="mb-3 flex justify-center">
          <button
            type="button"
            onClick={requestOlderMessages}
            disabled={loadingMore}
            className="min-h-[36px] rounded-xl border border-white/10 bg-slate-950/70 px-3 py-2 text-xs font-black text-slate-200 transition hover:border-cyan-300/25 hover:bg-white/[0.07] disabled:cursor-wait disabled:opacity-60"
          >
            {loadingMore ? 'Загружаем историю...' : 'Показать раннюю историю'}
          </button>
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>Синхронизация...</span>
        </div>
      )}

      {!selectedConversation && !loading && (
        <div className="grid min-h-[260px] place-items-center text-center">
          <div className="space-y-2 text-slate-500">
            <MessageCircle className="mx-auto h-8 w-8 text-cyan-200/60" />
            <p className="text-sm font-black text-white">Чат Shamrai</p>
          </div>
        </div>
      )}

      {selectedConversation && !loading && messages.length === 0 && (
        <div className="rounded-2xl border border-white/10 bg-slate-950/70 p-4 text-center text-xs font-bold text-slate-300 shadow-lg shadow-black/20 backdrop-blur-md">
          {error || 'История пуста.'}
        </div>
      )}

      {messages.length > 0 && (
        <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const decoratedMessage = decoratedMessages[virtualItem.index];
            const message = decoratedMessage?.message;
            if (!message) return null;

            return (
              <div
                key={`${message.client_message_id}:${message.id}`}
                ref={virtualizer.measureElement}
                data-index={virtualItem.index}
                className="admin-chat-message-row absolute left-0 top-0 w-full pb-3"
                style={{ transform: `translateY(${virtualItem.start}px)` }}
              >
                {decoratedMessage.showDateSeparator && (
                  <div className="mb-3 flex justify-center">
                    <span className="rounded-full border border-white/10 bg-slate-950/60 px-3 py-1 text-[10px] font-black uppercase tracking-[0.12em] text-slate-400 backdrop-blur-md">
                      {messageDateLabel(message.created_at)}
                    </span>
                  </div>
                )}
                {decoratedMessage.showUnreadDivider && (
                  <div className="mb-3 flex items-center gap-3">
                    <span className="h-px flex-1 bg-cyan-200/25" />
                    <span className="rounded-full border border-cyan-200/25 bg-cyan-300/10 px-3 py-1 text-[10px] font-black uppercase tracking-[0.12em] text-cyan-100">
                      Новые
                    </span>
                    <span className="h-px flex-1 bg-cyan-200/25" />
                  </div>
                )}
                <SupportMessageBubble
                  message={message}
                  ownerLabel={selectedConversation?.owner_user.display_name || 'Клиент'}
                  staffSide="right"
                  onRetry={onRetry}
                  onReply={onReply}
                  onCopy={onCopy}
                  onDownload={onDownload}
                />
              </div>
            );
          })}
        </div>
      )}

      {showJumpToBottom && messages.length > 0 && (
        <button
          type="button"
          onClick={scrollToBottom}
          className="sticky bottom-3 z-10 mx-auto mt-2 flex min-h-[34px] items-center justify-center rounded-full border border-cyan-200/25 bg-slate-950/80 px-4 py-2 text-[11px] font-black text-cyan-100 shadow-lg shadow-cyan-500/10 backdrop-blur-md transition hover:border-cyan-200/45 hover:bg-cyan-300/12"
        >
          К новым
        </button>
      )}
    </div>
  );
}

interface AdminWebChatProps {
  active?: boolean;
}

export default function AdminWebChat({ active = true }: AdminWebChatProps) {
  const [conversations, setConversations] = useState<ChatConversationResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<ChatConversationStatus>('open');
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<SupportMessageView[]>([]);
  const [messagesNextBeforeId, setMessagesNextBeforeId] = useState<number | null>(null);
  const [messagesHasMore, setMessagesHasMore] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [messageSearchTerm, setMessageSearchTerm] = useState('');
  const [clientSearchResults, setClientSearchResults] = useState<AdminChatClientSearchResult[]>([]);
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('');
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesLoadingMore, setMessagesLoadingMore] = useState(false);
  const [clientSearchLoading, setClientSearchLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [startDialogBusyUserId, setStartDialogBusyUserId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [replyTarget, setReplyTarget] = useState<ChatReplyTarget | null>(null);
  const [typingText, setTypingText] = useState('');
  const [streamState, setStreamState] = useState<'connecting' | 'online' | 'offline'>('connecting');
  const selectedConversationIdRef = useRef<string | null>(null);
  const typingClearTimerRef = useRef<number | undefined>();
  const lastTypingSentAtRef = useRef(0);

  const initialConversationId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('conversation_id') || null;
  }, []);
  const initialUserId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const rawUserId = params.get('user_id');
    if (!rawUserId) return null;
    const userId = Number(rawUserId);
    return Number.isSafeInteger(userId) ? userId : null;
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearchTerm(searchTerm.trim().toLowerCase()), 250);
    return () => window.clearTimeout(timer);
  }, [searchTerm]);

  useEffect(() => {
    const cleanSearch = debouncedSearchTerm.trim();
    if (cleanSearch.length < 2) {
      setClientSearchResults([]);
      setClientSearchLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    setClientSearchLoading(true);
    const params = new URLSearchParams({
      limit: '8',
      q: cleanSearch,
    });
    void apiFetch<AdminChatClientSearchResponse>(`/admin/users-page?${params.toString()}`, { signal: controller.signal })
      .then((response) => {
        const existingUserIds = new Set(conversations.map((conversation) => conversation.owner_user.telegram_id));
        setClientSearchResults(response.items.filter((user) => !existingUserIds.has(user.telegram_id)));
      })
      .catch((error: any) => {
        if (error?.name !== 'AbortError') setClientSearchResults([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setClientSearchLoading(false);
      });
    return () => controller.abort();
  }, [conversations, debouncedSearchTerm]);

  useEffect(() => {
    selectedConversationIdRef.current = selectedConversationId;
  }, [selectedConversationId]);

  useEffect(() => () => {
    if (typingClearTimerRef.current !== undefined) {
      window.clearTimeout(typingClearTimerRef.current);
    }
  }, []);

  useEffect(() => {
    setReplyTarget(null);
    setMessageSearchTerm('');
    setTypingText('');
  }, [selectedConversationId]);

  const visibleConversations = useMemo(
    () => conversations.filter((conversation) => matchesSearch(conversation, debouncedSearchTerm)),
    [conversations, debouncedSearchTerm],
  );

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedConversationId) || null,
    [selectedConversationId, conversations],
  );

  const visibleMessages = useMemo(() => {
    const cleanSearch = messageSearchTerm.trim().toLowerCase();
    if (!cleanSearch) return messages;
    return messages.filter((message) => [
      message.text,
      message.author_label,
      message.payload?.original_filename,
      message.reply_to?.text,
      message.reply_to?.author_label,
    ].join(' ').toLowerCase().includes(cleanSearch));
  }, [messageSearchTerm, messages]);

  const clearConversationUnread = useCallback((conversationId: string) => {
    setConversations((current) => current.map((conversation) => (
      conversation.id === conversationId && conversation.unread_count !== 0
        ? { ...conversation, unread_count: 0 }
        : conversation
    )));
  }, []);

  const loadConversations = useCallback(async () => {
    setThreadsLoading(true);
    try {
      let ensuredConversation: ChatConversationResponse | null = null;
      if (initialUserId !== null) {
        ensuredConversation = await apiFetch<ChatConversationResponse>(
          `/chat/admin/conversations/by-user/${encodeURIComponent(String(initialUserId))}`,
          { method: 'POST' },
        );
      }
      const params = new URLSearchParams({ limit: '80', status: statusFilter });
      const response = await apiFetch<ChatConversationListResponse>(`/chat/admin/conversations?${params.toString()}`);
      const items = ensuredConversation
        ? mergeConversation(response.items, ensuredConversation)
        : response.items.slice(0, ADMIN_CHAT_CONVERSATION_LIMIT);
      setConversations(items);
      setSelectedConversationId((current) => {
        if (initialUserId !== null && ensuredConversation?.id) return ensuredConversation.id;
        if (current && items.some((conversation) => conversation.id === current)) return current;
        if (initialConversationId && items.some((conversation) => conversation.id === initialConversationId)) return initialConversationId;
        return items[0]?.id ?? null;
      });
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось загрузить чаты клиентов');
    } finally {
      setThreadsLoading(false);
    }
  }, [initialConversationId, initialUserId, statusFilter]);

  useEffect(() => {
    if (!active) return;
    void loadConversations();
  }, [active, loadConversations]);

  useEffect(() => {
    if (!active) return;
    if (!selectedConversationId) {
      setMessages([]);
      setMessagesError(null);
      setMessagesNextBeforeId(null);
      setMessagesHasMore(false);
      setMessagesLoadingMore(false);
      return;
    }

    const conversationId = selectedConversationId;
    let cancelled = false;
    async function loadMessages() {
      setMessagesLoading(true);
      setMessagesError(null);
      setMessagesNextBeforeId(null);
      setMessagesHasMore(false);
      setMessagesLoadingMore(false);
      setMessages([]);
      try {
        const response = await apiFetch<ChatMessagePageResponse>(
          `/chat/admin/conversations/${conversationId}/messages?limit=${ADMIN_CHAT_MESSAGES_PAGE_LIMIT}`,
        );
        if (!cancelled) {
          setMessages(limitRecent(
            response.items.map((message) => ({ ...message, delivery_state: 'sent' as const })),
            ADMIN_CHAT_HISTORY_ITEM_LIMIT,
          ));
          setMessagesNextBeforeId(response.next_before_id);
          setMessagesHasMore(response.has_more);
          const lastMessage = response.items[response.items.length - 1];
          if (lastMessage) {
            void apiFetch(`/chat/admin/conversations/${conversationId}/read`, {
              method: 'POST',
              body: JSON.stringify({ last_read_message_id: lastMessage.id }),
            })
              .then(() => clearConversationUnread(conversationId))
              .catch(() => undefined);
          }
        }
      } catch (error: any) {
        if (!cancelled) {
          const message = error?.message || 'Не удалось загрузить переписку';
          setMessagesError(message);
          notifyError(message);
        }
      } finally {
        if (!cancelled) setMessagesLoading(false);
      }
    }

    void loadMessages();
    return () => {
      cancelled = true;
    };
  }, [active, clearConversationUnread, selectedConversationId]);

  const loadOlderMessages = useCallback(async () => {
    if (!selectedConversationId || !messagesNextBeforeId || messagesLoadingMore) return;
    const conversationId = selectedConversationId;
    setMessagesLoadingMore(true);
    try {
      const params = new URLSearchParams({
        before_id: String(messagesNextBeforeId),
        limit: String(ADMIN_CHAT_MESSAGES_PAGE_LIMIT),
      });
      const response = await apiFetch<ChatMessagePageResponse>(
        `/chat/admin/conversations/${conversationId}/messages?${params.toString()}`,
      );
      if (selectedConversationIdRef.current !== conversationId) return;
      const olderMessages = response.items.map((message) => ({ ...message, delivery_state: 'sent' as const }));
      setMessages((current) => olderMessages.reduce(
        (nextMessages, message) => appendMessage(nextMessages, message),
        current,
      ));
      setMessagesNextBeforeId(response.next_before_id);
      setMessagesHasMore(response.has_more);
      setMessagesError(null);
    } catch (error: any) {
      if (selectedConversationIdRef.current !== conversationId) return;
      const message = error?.message || 'Не удалось загрузить раннюю историю';
      setMessagesError(message);
      notifyError(message);
    } finally {
      if (selectedConversationIdRef.current === conversationId) {
        setMessagesLoadingMore(false);
      }
    }
  }, [messagesLoadingMore, messagesNextBeforeId, selectedConversationId]);

  useEffect(() => {
    if (!active) return;
    const handleMessage = (event: Event) => {
      const payload = (event as CustomEvent<AdminWebChatMessageEventPayload>).detail;
      if (!payload?.message || !payload.conversation) return;
      const conversationId = selectedConversationId;
      setConversations((current) => syncConversationForStatus(current, payload.conversation, statusFilter));
      setMessages((current) => (
        payload.conversation.id === conversationId
          ? appendMessage(current, { ...payload.message, delivery_state: 'sent' })
          : current
      ));
      if (conversationId && payload.conversation.id === conversationId) {
        void apiFetch(`/chat/admin/conversations/${conversationId}/read`, {
          method: 'POST',
          body: JSON.stringify({ last_read_message_id: payload.message.id }),
        })
          .then(() => clearConversationUnread(conversationId))
          .catch(() => undefined);
      }
    };
    const handleConversation = (event: Event) => {
      const conversation = (event as CustomEvent<ChatConversationResponse>).detail;
      if (!conversation?.id) return;
      setConversations((current) => syncConversationForStatus(current, conversation, statusFilter));
    };
    const handleStatus = (event: Event) => {
      const state = (event as CustomEvent<{ state?: 'connecting' | 'online' | 'offline' }>).detail?.state;
      if (state) setStreamState(state);
    };
    const handleRead = (event: Event) => {
      const payload = (event as CustomEvent<ChatReadUpdatedEvent>).detail;
      if (!payload?.conversation_id || payload.conversation_id !== selectedConversationId) return;
      setMessages((current) => applyReadReceiptToMessages(current, payload));
    };
    const handleTyping = (event: Event) => {
      const payload = (event as CustomEvent<{
        conversation_id?: string;
        direction?: 'staff' | 'client';
        is_typing?: boolean;
      }>).detail;
      if (!payload?.is_typing || payload.direction !== 'client' || payload.conversation_id !== selectedConversationId) return;
      if (typingClearTimerRef.current !== undefined) {
        window.clearTimeout(typingClearTimerRef.current);
      }
      setTypingText('Клиент печатает...');
      typingClearTimerRef.current = window.setTimeout(() => {
        typingClearTimerRef.current = undefined;
        setTypingText('');
      }, 4500);
    };

    window.addEventListener(ADMIN_WEB_CHAT_MESSAGE_EVENT, handleMessage);
    window.addEventListener(ADMIN_WEB_CHAT_CONVERSATION_EVENT, handleConversation);
    window.addEventListener(ADMIN_WEB_CHAT_STATUS_EVENT, handleStatus);
    window.addEventListener(ADMIN_WEB_CHAT_READ_EVENT, handleRead);
    window.addEventListener(ADMIN_WEB_CHAT_TYPING_EVENT, handleTyping);
    return () => {
      window.removeEventListener(ADMIN_WEB_CHAT_MESSAGE_EVENT, handleMessage);
      window.removeEventListener(ADMIN_WEB_CHAT_CONVERSATION_EVENT, handleConversation);
      window.removeEventListener(ADMIN_WEB_CHAT_STATUS_EVENT, handleStatus);
      window.removeEventListener(ADMIN_WEB_CHAT_READ_EVENT, handleRead);
      window.removeEventListener(ADMIN_WEB_CHAT_TYPING_EVENT, handleTyping);
    };
  }, [active, clearConversationUnread, selectedConversationId, statusFilter]);

  const updateConversationAfterStaffReply = useCallback((response: ChatMessageResponse) => {
    if (!selectedConversation) return;
    const updatedConversation: ChatConversationResponse = {
      ...selectedConversation,
      status: 'closed',
      last_message: response,
      last_message_text: messagePreview(response),
      last_message_at: response.created_at,
      unread_count: 0,
    };
    setConversations((current) => {
      if (statusFilter === 'closed') {
        return syncConversationForStatus(current, updatedConversation, statusFilter);
      }
      return [updatedConversation];
    });
    if (statusFilter !== 'closed') setStatusFilter('closed');
  }, [selectedConversation, statusFilter]);

  const sendAdminText = useCallback(async (
    text: string,
    clientMessageId: string = crypto.randomUUID(),
    replyTo: ChatReplyTarget | null = replyTarget,
  ) => {
    const cleanText = text.trim();
    if (!selectedConversationId || !selectedConversation || !cleanText) return false;

    setSending(true);
    setMessages((current) => appendMessage(current, makeOptimisticStaffMessage(selectedConversationId, cleanText, clientMessageId, undefined, replyTo)));
    try {
      const response = await apiFetch<ChatMessageResponse>(`/chat/admin/conversations/${selectedConversationId}/messages`, {
        method: 'POST',
        body: JSON.stringify({
          client_message_id: clientMessageId,
          text: cleanText,
          ...(replyTo?.id ? { reply_to_id: replyTo.id } : {}),
        }),
      });
      setMessages((current) => appendMessage(current, { ...response, delivery_state: 'sent' }));
      updateConversationAfterStaffReply(response);
      setDraft('');
      setReplyTarget(null);
      notifySuccess('Сообщение отправлено клиенту, диалог закрыт', 'Чат Shamrai');
      return true;
    } catch (error: any) {
      setMessages((current) => current.map((message) => (
        message.client_message_id === clientMessageId
          ? { ...message, delivery_state: 'failed' }
          : message
      )));
      notifyError(error?.message || 'Не удалось отправить сообщение');
      return false;
    } finally {
      setSending(false);
    }
  }, [replyTarget, selectedConversation, selectedConversationId, updateConversationAfterStaffReply]);

  const sendAdminAttachment = useCallback(async (
    attachment: ChatComposerAttachment,
    text: string,
    clientMessageId: string = crypto.randomUUID(),
    replyTo: ChatReplyTarget | null = replyTarget,
  ) => {
    const cleanText = text.trim();
    if (!selectedConversationId || !selectedConversation) return false;

    setSending(true);
    setMessages((current) => appendMessage(
      current,
      makeOptimisticStaffMessage(selectedConversationId, cleanText, clientMessageId, attachment, replyTo),
    ));

    const formData = new FormData();
    formData.append('client_message_id', clientMessageId);
    formData.append('message_type', attachment.messageType);
    formData.append('file', attachment.file);
    if (cleanText) formData.append('text', cleanText);
    if (replyTo?.id) formData.append('reply_to_id', String(replyTo.id));
    if (attachment.durationMs) formData.append('duration_ms', String(Math.round(attachment.durationMs)));

    try {
      const response = await apiFetch<ChatMessageResponse>(`/chat/admin/conversations/${selectedConversationId}/attachments`, {
        method: 'POST',
        body: formData,
      });
      setMessages((current) => appendMessage(current, { ...response, delivery_state: 'sent' }));
      updateConversationAfterStaffReply(response);
      URL.revokeObjectURL(attachment.previewUrl);
      setReplyTarget(null);
      notifySuccess('Вложение отправлено клиенту, диалог закрыт', 'Чат Shamrai');
      return true;
    } catch (error: any) {
      const retryAttachment = {
        ...attachment,
        previewUrl: URL.createObjectURL(attachment.file),
      };
      setMessages((current) => current.map((message) => (
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
      setSending(false);
    }
  }, [replyTarget, selectedConversation, selectedConversationId, updateConversationAfterStaffReply]);

  const handleRetry = useCallback((message: SupportMessageView) => {
    if (sending) return;
    if (message.retry_attachment) {
      void sendAdminAttachment(message.retry_attachment, message.text || '', message.client_message_id, message.reply_to || null);
      return;
    }
    if (message.text) void sendAdminText(message.text, message.client_message_id, message.reply_to || null);
  }, [sendAdminAttachment, sendAdminText, sending]);

  const handleReply = useCallback((message: SupportMessageView) => {
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

  const sendAdminTyping = useCallback(() => {
    if (!selectedConversationId) return;
    const now = Date.now();
    if (now - lastTypingSentAtRef.current < 2500) return;
    lastTypingSentAtRef.current = now;
    void apiFetch(`/chat/admin/conversations/${selectedConversationId}/typing`, {
      method: 'POST',
      body: JSON.stringify({ is_typing: true }),
    }).catch(() => undefined);
  }, [selectedConversationId]);

  const startDialogWithClient = useCallback(async (userId: number) => {
    setStartDialogBusyUserId(userId);
    try {
      const conversation = await apiFetch<ChatConversationResponse>(
        `/chat/admin/conversations/by-user/${encodeURIComponent(String(userId))}`,
        { method: 'POST' },
      );
      setConversations((current) => mergeConversation(current, conversation));
      setSelectedConversationId(conversation.id ?? null);
      setStatusFilter(conversation.status);
      setSearchTerm('');
      setClientSearchResults([]);
      notifySuccess('Диалог с клиентом открыт', 'Чаты');
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось открыть диалог с клиентом');
    } finally {
      setStartDialogBusyUserId(null);
    }
  }, []);

  const handleEnablePush = async () => {
    setPushBusy(true);
    try {
      const response = await registerWebPushSubscription();
      if (response.status === 'subscribed') {
        notifySuccess('Push-уведомления для чатов включены');
      } else {
        notifyError(response.message || 'Не удалось включить push-уведомления');
      }
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось включить push-уведомления');
    } finally {
      setPushBusy(false);
    }
  };

  const handleStatusToggle = async () => {
    if (!selectedConversationId || !selectedConversation) return;
    const nextStatus: ChatConversationStatus = selectedConversation.status === 'closed' ? 'open' : 'closed';
    setStatusBusy(true);
    try {
      const response = await apiFetch<ChatConversationResponse>(`/chat/admin/conversations/${selectedConversationId}/status`, {
        method: 'POST',
        body: JSON.stringify({ status: nextStatus }),
      });
      setConversations((current) => mergeConversation(current, response));
      notifySuccess(nextStatus === 'closed' ? 'Диалог закрыт' : 'Диалог переоткрыт', 'Чаты');
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось изменить статус диалога');
    } finally {
      setStatusBusy(false);
    }
  };

  return (
    <div className="admin-web-chat-shell grid min-h-[calc(100dvh-8.5rem)] min-w-0 gap-3 pb-8 xl:h-[calc(100dvh-10rem)] xl:min-h-0 xl:grid-cols-[minmax(270px,360px)_minmax(0,1fr)] xl:overflow-hidden xl:pb-0">
      <section className={`${selectedConversationId ? 'hidden xl:flex' : 'flex'} min-h-[320px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-slate-950/45 shadow-glass backdrop-blur-xl xl:min-h-0`}>
        <div className="border-b border-white/10 px-3 py-3">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-base font-black text-white">
                <MessageCircle className="h-5 w-5 text-cyan-200" />
                Чаты
              </h2>
              <div className="mt-1 flex items-center gap-1.5 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
                {streamState === 'offline' ? <WifiOff className="h-3 w-3 text-rose-300" /> : <ShieldCheck className="h-3 w-3 text-emerald-300" />}
                <span>{streamState}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={handleEnablePush}
              disabled={pushBusy}
              title="Включить push-уведомления для чатов"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-100 transition hover:bg-cyan-300/16 disabled:cursor-wait disabled:opacity-55"
            >
              {pushBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <BellRing className="h-4 w-4" />}
            </button>
          </div>

          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {(['open', 'closed'] as const).map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => setStatusFilter(status)}
                className={`rounded-xl border px-3 py-2 text-xs font-black transition ${
                  statusFilter === status
                    ? 'border-cyan-300/35 bg-cyan-300/12 text-white'
                    : 'border-white/10 bg-white/[0.04] text-slate-400 hover:text-slate-200'
                }`}
              >
                {status === 'open' ? 'Открытые' : 'Закрытые'}
              </button>
            ))}
          </div>

          <label className="mt-3 grid grid-cols-[auto_1fr] items-center gap-2 rounded-xl border border-white/10 bg-slate-950/65 px-3 py-2">
            <Search className="h-4 w-4 text-slate-500" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Клиент, ID, username"
              className="min-w-0 bg-transparent text-sm font-semibold text-white placeholder:text-slate-600 focus:outline-none"
            />
          </label>
        </div>

        <VirtualConversationList
          conversations={visibleConversations}
          selectedConversationId={selectedConversationId}
          loading={threadsLoading}
          onSelectConversation={setSelectedConversationId}
        />
        {(clientSearchLoading || clientSearchResults.length > 0) && (
          <div className="border-t border-white/10 p-2">
            <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-2">
              <div className="mb-2 flex items-center justify-between gap-2 px-1">
                <p className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
                  Клиенты
                </p>
                {clientSearchLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-200" />}
              </div>
              <div className="space-y-2">
                {clientSearchResults.map((user) => (
                  <button
                    key={user.telegram_id}
                    type="button"
                    onClick={() => void startDialogWithClient(user.telegram_id)}
                    disabled={startDialogBusyUserId === user.telegram_id}
                    className="flex w-full min-w-0 items-center justify-between gap-3 rounded-xl border border-white/10 bg-slate-950/45 px-3 py-2 text-left transition hover:border-cyan-300/25 hover:bg-white/[0.06] disabled:cursor-wait disabled:opacity-60"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-black text-white">{clientDisplayName(user)}</span>
                      <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-500">
                        {user.username ? `@${user.username}` : `ID ${user.telegram_id}`}
                      </span>
                    </span>
                    {startDialogBusyUserId === user.telegram_id ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-cyan-200" />
                    ) : (
                      <MessageCircle className="h-4 w-4 shrink-0 text-cyan-200" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </section>

      <section className={`${selectedConversationId ? 'flex' : 'hidden xl:flex'} min-h-[520px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-slate-950/45 shadow-glass backdrop-blur-xl xl:min-h-0`}>
        <div className="flex min-w-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {selectedConversation && (
              <button
                type="button"
                onClick={() => setSelectedConversationId(null)}
                title="К списку чатов"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.055] text-slate-200 transition hover:bg-white/[0.09] xl:hidden"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <div className="min-w-0">
            <p className="truncate text-base font-black text-white">
              {selectedConversation?.owner_user.display_name || 'Выберите диалог'}
            </p>
            {selectedConversation && (
              <p className="mt-0.5 truncate text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
                {selectedConversation.owner_user.username ? `@${selectedConversation.owner_user.username}` : `ID ${selectedConversation.owner_user.telegram_id}`}
              </p>
            )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {selectedConversation && (
              <button
                type="button"
                onClick={handleStatusToggle}
                disabled={statusBusy}
                title={selectedConversation.status === 'closed' ? 'Переоткрыть диалог' : 'Закрыть диалог'}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.055] text-slate-200 transition hover:bg-white/[0.09] disabled:cursor-wait disabled:opacity-55"
              >
                {statusBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : selectedConversation.status === 'closed' ? <Unlock className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
              </button>
            )}
            <button
              type="button"
              onClick={() => void loadConversations()}
              disabled={threadsLoading}
              title="Обновить список чатов"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.055] text-slate-200 transition hover:bg-white/[0.09] disabled:cursor-wait disabled:opacity-55"
            >
              {threadsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {selectedConversation && (
          <div className="border-b border-white/10 bg-slate-950/30 px-4 py-2">
            <label className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded-xl border border-white/10 bg-slate-950/55 px-3 py-2">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={messageSearchTerm}
                onChange={(event) => setMessageSearchTerm(event.target.value)}
                placeholder="Поиск в диалоге"
                className="min-w-0 bg-transparent text-sm font-semibold text-white placeholder:text-slate-600 focus:outline-none"
              />
              {messageSearchTerm.trim() && (
                <button
                  type="button"
                  onClick={() => setMessageSearchTerm('')}
                  title="Очистить поиск"
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-white/[0.08] hover:text-white"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </label>
          </div>
        )}

        <VirtualAdminMessageList
          active={active}
          messages={visibleMessages}
          selectedConversation={selectedConversation}
          loading={messagesLoading}
          error={messagesError || (messageSearchTerm.trim() ? 'Ничего не найдено.' : null)}
          hasMore={messagesHasMore}
          loadingMore={messagesLoadingMore}
          onLoadMore={() => void loadOlderMessages()}
          onRetry={handleRetry}
          onReply={handleReply}
          onCopy={handleCopy}
          onDownload={handleDownload}
        />

        {typingText && (
          <p className="mx-3 mt-2 rounded-xl border border-cyan-300/15 bg-cyan-300/10 px-3 py-2 text-[11px] font-bold text-cyan-100">
            {typingText}
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
          disabled={!selectedConversation}
          sending={sending}
          placeholder={selectedConversation ? 'Ответить от имени Shamrai...' : 'Выберите клиента'}
          submitTitle="Отправить сообщение клиенту"
          onSendText={sendAdminText}
          onSendAttachment={sendAdminAttachment}
          onError={notifyError}
          active={active}
          onTyping={sendAdminTyping}
        />
      </section>
    </div>
  );
}
