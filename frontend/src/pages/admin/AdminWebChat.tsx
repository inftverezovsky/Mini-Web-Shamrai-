import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
  BellRing,
  Bot,
  Loader2,
  Lock,
  MessageCircle,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Unlock,
  User,
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
import {
  ADMIN_WEB_CHAT_CONVERSATION_EVENT,
  ADMIN_WEB_CHAT_MESSAGE_EVENT,
  ADMIN_WEB_CHAT_STATUS_EVENT,
  AdminWebChatMessageEventPayload,
} from '../../components/AdminWebChatListener';

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

function appendMessage(messages: ChatMessageResponse[], nextMessage: ChatMessageResponse) {
  if (messages.some((message) => message.id === nextMessage.id)) return messages;
  return [...messages, nextMessage].sort((left, right) => {
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

export default function AdminWebChat() {
  const [conversations, setConversations] = useState<ChatConversationResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<ChatConversationStatus>('open');
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageResponse[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('');
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
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
      return;
    }

    const conversationId = selectedConversationId;
    let cancelled = false;
    async function loadMessages() {
      setMessagesLoading(true);
      try {
        const response = await apiFetch<ChatMessagePageResponse>(
          `/chat/admin/conversations/${conversationId}/messages?limit=160`,
        );
        if (!cancelled) {
          setMessages(response.items);
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
        if (!cancelled) notifyError(error?.message || 'Не удалось загрузить переписку');
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
      setConversations((current) => mergeConversation(current, payload.conversation));
      setMessages((current) => (
        payload.conversation.id === conversationId
          ? appendMessage(current, payload.message)
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
      setConversations((current) => mergeConversation(current, conversation));
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
  }, [clearConversationUnread, selectedConversationId]);

  const handleSend = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!selectedConversationId || !selectedConversation || !text) return;

    setSending(true);
    const clientMessageId = crypto.randomUUID();
    try {
      const response = await apiFetch<ChatMessageResponse>(`/chat/admin/conversations/${selectedConversationId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ client_message_id: clientMessageId, text }),
      });
      setMessages((current) => appendMessage(current, response));
      setConversations((current) => mergeConversation(current, {
        ...selectedConversation,
        status: 'open',
        last_message: response,
        last_message_text: response.text,
        last_message_at: response.created_at,
        unread_count: 0,
      }));
      setDraft('');
      notifySuccess('Сообщение отправлено клиенту', 'Чат Shamrai');
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось отправить сообщение');
    } finally {
      setSending(false);
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    if (!draft.trim() || sending) return;
    void handleSend();
  };

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
    <div className="grid min-h-[calc(100dvh-8.5rem)] gap-3 pb-8 lg:grid-cols-[minmax(270px,360px)_minmax(0,1fr)]">
      <section className="min-h-[320px] overflow-hidden rounded-2xl border border-white/10 bg-slate-950/45 shadow-glass backdrop-blur-xl">
        <div className="border-b border-white/10 px-3 py-3">
          <div className="flex items-center justify-between gap-2">
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

          <div className="mt-3 grid grid-cols-2 gap-2">
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
                <div className="flex items-start justify-between gap-2">
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
        <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
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

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
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
            <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-center text-xs font-bold text-slate-500">
              История пуста.
            </div>
          )}

          {messages.map((message) => {
            const staff = message.direction === 'staff';
            return (
              <div key={message.id} className={`flex items-start gap-3 ${staff ? 'justify-end' : ''}`}>
                {!staff && (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 text-fuchsia-100">
                    <User className="h-4 w-4" />
                  </div>
                )}
                <div className={`max-w-[82%] rounded-2xl border px-3.5 py-3 ${
                  staff
                    ? 'rounded-br-md border-cyan-300/25 bg-cyan-300/12 text-cyan-50'
                    : 'rounded-bl-md border-fuchsia-300/20 bg-fuchsia-300/10 text-fuchsia-50'
                }`}>
                  <p className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/45">
                    {staff ? 'Shamrai' : selectedConversation?.owner_user.display_name || 'Клиент'}
                  </p>
                  <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">{message.text}</p>
                  <p className="mt-2 text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
                    {messageTime(message.created_at)}
                  </p>
                </div>
                {staff && (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 text-cyan-100">
                    <Bot className="h-4 w-4" />
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <form onSubmit={(event) => void handleSend(event)} className="border-t border-white/10 bg-slate-950/55 px-3 py-3">
          <div className="grid grid-cols-[1fr_auto] items-end gap-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              disabled={!selectedConversation || sending}
              maxLength={4000}
              rows={2}
              placeholder={selectedConversation ? 'Ответить от имени Shamrai...' : 'Выберите клиента'}
              className="min-h-[48px] max-h-32 w-full resize-none rounded-2xl border border-white/10 bg-slate-950/75 px-3 py-2.5 text-sm font-semibold text-white placeholder:text-slate-600 focus:border-cyan-300/45 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!selectedConversation || sending || !draft.trim()}
              title="Отправить сообщение клиенту"
              className="flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/15 text-cyan-50 transition-all hover:bg-cyan-300/24 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45"
            >
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
