import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BellRing,
  Loader2,
  Lock,
  MessageCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  Unlock,
  WifiOff,
} from 'lucide-react';

import {
  ChatConversationListResponse,
  ChatConversationResponse,
  ChatConversationStatus,
  ChatMessagePageResponse,
  ChatMessageResponse,
} from '../../schemas/schemas';
import { apiFetch } from '../../utils/api';
import { notifyError, notifySuccess } from '../../utils/notify';
import { registerWebPushSubscription } from '../../utils/webPush';
import MessageComposer, { ChatComposerAttachment } from '../../features/chat/MessageComposer';
import SupportMessageBubble, { SupportMessageView } from '../../features/chat/SupportMessageBubble';
import {
  ADMIN_WEB_CHAT_CONVERSATION_EVENT,
  ADMIN_WEB_CHAT_MESSAGE_EVENT,
  ADMIN_WEB_CHAT_STATUS_EVENT,
  AdminWebChatMessageEventPayload,
} from '../../components/AdminWebChatListener';

const ADMIN_CHAT_MESSAGES_PAGE_LIMIT = 100;

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
  });
}

function appendMessage(messages: SupportMessageView[], nextMessage: SupportMessageView) {
  if (messages.some((message) => message.id > 0 && message.id === nextMessage.id)) return messages;
  const withoutOptimisticDuplicate = messages.filter((message) => message.client_message_id !== nextMessage.client_message_id || message.id > 0);
  return [...withoutOptimisticDuplicate, nextMessage].sort((left, right) => {
    const leftTime = new Date(left.created_at).getTime();
    const rightTime = new Date(right.created_at).getTime();
    return (leftTime - rightTime) || (left.id - right.id);
  });
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

function messagePreview(message: ChatMessageResponse | SupportMessageView) {
  if (message.text?.trim()) return message.text;
  if (message.type === 'image') return 'Скриншот';
  if (message.type === 'voice') return 'Голосовое сообщение';
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

function makeOptimisticStaffMessage(
  conversationId: string,
  text: string,
  clientMessageId: string,
  attachment?: ChatComposerAttachment,
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
    reply_to_id: null,
    created_at: now,
    edited_at: null,
    deleted_at: null,
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

export default function AdminWebChat() {
  const [conversations, setConversations] = useState<ChatConversationResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<ChatConversationStatus>('open');
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<SupportMessageView[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('');
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [streamState, setStreamState] = useState<'connecting' | 'online' | 'offline'>('connecting');

  const initialConversationId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('conversation_id') || null;
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearchTerm(searchTerm.trim().toLowerCase()), 250);
    return () => window.clearTimeout(timer);
  }, [searchTerm]);

  const visibleConversations = useMemo(
    () => conversations.filter((conversation) => matchesSearch(conversation, debouncedSearchTerm)),
    [conversations, debouncedSearchTerm],
  );

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedConversationId) || null,
    [selectedConversationId, conversations],
  );

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
      const params = new URLSearchParams({ limit: '80', status: statusFilter });
      const response = await apiFetch<ChatConversationListResponse>(`/chat/admin/conversations?${params.toString()}`);
      setConversations(response.items);
      setSelectedConversationId((current) => {
        if (current && response.items.some((conversation) => conversation.id === current)) return current;
        if (initialConversationId && response.items.some((conversation) => conversation.id === initialConversationId)) return initialConversationId;
        return response.items[0]?.id ?? null;
      });
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось загрузить чаты клиентов');
    } finally {
      setThreadsLoading(false);
    }
  }, [initialConversationId, statusFilter]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (!selectedConversationId) {
      setMessages([]);
      setMessagesError(null);
      return;
    }

    const conversationId = selectedConversationId;
    let cancelled = false;
    async function loadMessages() {
      setMessagesLoading(true);
      setMessagesError(null);
      setMessages([]);
      try {
        const response = await apiFetch<ChatMessagePageResponse>(
          `/chat/admin/conversations/${conversationId}/messages?limit=${ADMIN_CHAT_MESSAGES_PAGE_LIMIT}`,
        );
        if (!cancelled) {
          setMessages(response.items.map((message) => ({ ...message, delivery_state: 'sent' as const })));
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
  }, [clearConversationUnread, selectedConversationId]);

  useEffect(() => {
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

    window.addEventListener(ADMIN_WEB_CHAT_MESSAGE_EVENT, handleMessage);
    window.addEventListener(ADMIN_WEB_CHAT_CONVERSATION_EVENT, handleConversation);
    window.addEventListener(ADMIN_WEB_CHAT_STATUS_EVENT, handleStatus);
    return () => {
      window.removeEventListener(ADMIN_WEB_CHAT_MESSAGE_EVENT, handleMessage);
      window.removeEventListener(ADMIN_WEB_CHAT_CONVERSATION_EVENT, handleConversation);
      window.removeEventListener(ADMIN_WEB_CHAT_STATUS_EVENT, handleStatus);
    };
  }, [clearConversationUnread, selectedConversationId, statusFilter]);

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

  const sendAdminText = useCallback(async (text: string, clientMessageId: string = crypto.randomUUID()) => {
    const cleanText = text.trim();
    if (!selectedConversationId || !selectedConversation || !cleanText) return false;

    setSending(true);
    setMessages((current) => appendMessage(current, makeOptimisticStaffMessage(selectedConversationId, cleanText, clientMessageId)));
    try {
      const response = await apiFetch<ChatMessageResponse>(`/chat/admin/conversations/${selectedConversationId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ client_message_id: clientMessageId, text: cleanText }),
      });
      setMessages((current) => appendMessage(current, { ...response, delivery_state: 'sent' }));
      updateConversationAfterStaffReply(response);
      setDraft('');
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
  }, [selectedConversation, selectedConversationId, updateConversationAfterStaffReply]);

  const sendAdminAttachment = useCallback(async (
    attachment: ChatComposerAttachment,
    text: string,
    clientMessageId: string = crypto.randomUUID(),
  ) => {
    const cleanText = text.trim();
    if (!selectedConversationId || !selectedConversation) return false;

    setSending(true);
    setMessages((current) => appendMessage(
      current,
      makeOptimisticStaffMessage(selectedConversationId, cleanText, clientMessageId, attachment),
    ));

    const formData = new FormData();
    formData.append('client_message_id', clientMessageId);
    formData.append('message_type', attachment.messageType);
    formData.append('file', attachment.file);
    if (cleanText) formData.append('text', cleanText);
    if (attachment.durationMs) formData.append('duration_ms', String(Math.round(attachment.durationMs)));

    try {
      const response = await apiFetch<ChatMessageResponse>(`/chat/admin/conversations/${selectedConversationId}/attachments`, {
        method: 'POST',
        body: formData,
      });
      setMessages((current) => appendMessage(current, { ...response, delivery_state: 'sent' }));
      updateConversationAfterStaffReply(response);
      URL.revokeObjectURL(attachment.previewUrl);
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
  }, [selectedConversation, selectedConversationId, updateConversationAfterStaffReply]);

  const handleRetry = useCallback((message: SupportMessageView) => {
    if (sending) return;
    if (message.retry_attachment) {
      void sendAdminAttachment(message.retry_attachment, message.text || '', message.client_message_id);
      return;
    }
    if (message.text) void sendAdminText(message.text, message.client_message_id);
  }, [sendAdminAttachment, sendAdminText, sending]);

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
    <div className="grid min-h-[calc(100dvh-8.5rem)] min-w-0 gap-3 pb-8 xl:grid-cols-[minmax(270px,360px)_minmax(0,1fr)]">
      <section className="min-h-[320px] overflow-hidden rounded-2xl border border-white/10 bg-slate-950/45 shadow-glass backdrop-blur-xl">
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

        <div className="max-h-[calc(100dvh-20rem)] min-h-[240px] space-y-2 overflow-y-auto p-2">
          {threadsLoading && (
            <div className="flex items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Загружаем...</span>
            </div>
          )}

          {!threadsLoading && visibleConversations.length === 0 && (
            <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-center text-xs font-bold text-slate-500">
              Диалогов пока нет.
            </div>
          )}

          {visibleConversations.map((conversation) => {
            const active = selectedConversationId === conversation.id;
            const needsReply = conversation.last_message && 'direction' in conversation.last_message
              ? conversation.last_message.direction === 'client'
              : false;
            return (
              <button
                key={conversation.id || conversation.owner_user.telegram_id}
                type="button"
                onClick={() => setSelectedConversationId(conversation.id)}
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
            );
          })}
        </div>
      </section>

      <section className="flex min-h-[520px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-slate-950/45 shadow-glass backdrop-blur-xl">
        <div className="flex min-w-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
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

        <div
          className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-slate-950 px-4 py-4"
          style={{
            backgroundImage: "linear-gradient(180deg, rgba(2, 6, 23, 0.74), rgba(2, 6, 23, 0.86)), url('/images/admin-chat-bg.jpg')",
            backgroundPosition: 'center',
            backgroundSize: 'cover',
          }}
        >
          {messagesLoading && (
            <div className="flex items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Синхронизация...</span>
            </div>
          )}

          {!selectedConversation && !messagesLoading && (
            <div className="grid min-h-[260px] place-items-center text-center">
              <div className="space-y-2 text-slate-500">
                <MessageCircle className="mx-auto h-8 w-8 text-cyan-200/60" />
                <p className="text-sm font-black text-white">Чат Shamrai</p>
              </div>
            </div>
          )}

          {selectedConversation && !messagesLoading && messages.length === 0 && (
            <div className="rounded-2xl border border-white/10 bg-slate-950/70 p-4 text-center text-xs font-bold text-slate-300 shadow-lg shadow-black/20 backdrop-blur-md">
              {messagesError || 'История пуста.'}
            </div>
          )}

          {messages.map((message) => (
            <SupportMessageBubble
              key={`${message.client_message_id}:${message.id}`}
              message={message}
              ownerLabel={selectedConversation?.owner_user.display_name || 'Клиент'}
              staffSide="right"
              onRetry={handleRetry}
            />
          ))}
        </div>

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
        />
      </section>
    </div>
  );
}
