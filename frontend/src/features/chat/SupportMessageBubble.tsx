import { Bot, Check, Clock3, RefreshCw, User } from 'lucide-react';

import { ChatMessageResponse } from '../../schemas/schemas';

export type ChatDeliveryState = 'sent' | 'sending' | 'failed';

export interface SupportMessageView extends ChatMessageResponse {
  delivery_state?: ChatDeliveryState;
}

interface SupportMessageBubbleProps {
  message: SupportMessageView;
  ownerLabel?: string;
  onRetry?: (message: SupportMessageView) => void;
}

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

function renderTextWithLinks(text: string) {
  const urlPattern = /(https?:\/\/[^\s]+)/g;
  return text.split(urlPattern).map((part, index) => {
    if (!part.match(urlPattern)) return <span key={`${part}:${index}`}>{part}</span>;
    const cleanUrl = part.replace(/[),.;!?]+$/, '');
    const suffix = part.slice(cleanUrl.length);
    return (
      <span key={`${part}:${index}`}>
        <a
          href={cleanUrl}
          target="_blank"
          rel="noreferrer"
          className="break-all font-black text-cyan-100 underline decoration-cyan-200/45 underline-offset-4"
        >
          {cleanUrl}
        </a>
        {suffix}
      </span>
    );
  });
}

export default function SupportMessageBubble({
  message,
  ownerLabel = 'Клиент',
  onRetry,
}: SupportMessageBubbleProps) {
  const staff = message.direction === 'staff';
  const deliveryState = message.delivery_state || 'sent';

  return (
    <div className={`flex items-start gap-3 ${staff ? '' : 'justify-end'}`}>
      {staff && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 text-cyan-100">
          <Bot className="h-4 w-4" />
        </div>
      )}

      <div className={`min-w-0 max-w-[86%] rounded-2xl border px-3.5 py-3 ${
        staff
          ? 'rounded-bl-md border-cyan-300/25 bg-cyan-300/12 text-cyan-50'
          : 'rounded-br-md border-fuchsia-300/20 bg-fuchsia-300/10 text-fuchsia-50'
      } ${deliveryState === 'failed' ? 'border-rose-300/35 bg-rose-400/10' : ''}`}>
        <p className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/45">
          {staff ? 'Shamrai' : ownerLabel}
        </p>
        <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
          {renderTextWithLinks(message.text || '')}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
          <span>{messageTime(message.created_at)}</span>
          {deliveryState === 'sending' && (
            <span className="inline-flex items-center gap-1 text-cyan-100/70">
              <Clock3 className="h-3 w-3" />
              sending
            </span>
          )}
          {deliveryState === 'sent' && (
            <span className="inline-flex items-center gap-1">
              <Check className="h-3 w-3" />
              sent
            </span>
          )}
          {deliveryState === 'failed' && onRetry && (
            <button
              type="button"
              onClick={() => onRetry(message)}
              className="inline-flex items-center gap-1 rounded-lg border border-rose-200/25 bg-rose-300/10 px-2 py-1 text-rose-100 transition hover:bg-rose-300/18"
            >
              <RefreshCw className="h-3 w-3" />
              retry
            </button>
          )}
        </div>
      </div>

      {!staff && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 text-fuchsia-100">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  );
}
