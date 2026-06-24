import { memo, useMemo } from 'react';
import {
  Bot,
  Check,
  CheckCheck,
  Clock3,
  Copy,
  Download,
  FileText,
  Image as ImageIcon,
  Mic,
  RefreshCw,
  Reply,
  User,
} from 'lucide-react';

import { ChatMessageResponse } from '../../schemas/schemas';
import { API_BASE_URL } from '../../utils/api';
import type { ChatComposerAttachment } from './MessageComposer';

export type ChatDeliveryState = 'sent' | 'sending' | 'failed';

export interface SupportMessageView extends ChatMessageResponse {
  delivery_state?: ChatDeliveryState;
  retry_attachment?: ChatComposerAttachment;
}

interface SupportMessageBubbleProps {
  message: SupportMessageView;
  ownerLabel?: string;
  onRetry?: (message: SupportMessageView) => void;
  onReply?: (message: SupportMessageView) => void;
  onCopy?: (message: SupportMessageView) => void;
  onDownload?: (message: SupportMessageView) => void;
  staffSide?: 'left' | 'right';
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

function resolveMediaUrl(path?: unknown) {
  const cleanPath = typeof path === 'string' ? path.trim() : '';
  if (!cleanPath) return '';
  if (cleanPath.startsWith('blob:') || cleanPath.startsWith('data:')) return cleanPath;
  if (cleanPath.startsWith('http://') || cleanPath.startsWith('https://')) return cleanPath;
  const normalizedPath = cleanPath.startsWith('/') ? cleanPath : `/${cleanPath}`;
  if (normalizedPath.startsWith('/static')) return `${API_BASE_URL}${normalizedPath}`;
  return normalizedPath;
}

function formatBytes(value?: unknown) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${bytes} Б`;
}

function formatDuration(value?: unknown) {
  const durationMs = Number(value || 0);
  if (!Number.isFinite(durationMs) || durationMs <= 0) return '';
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function messagePreview(message: Pick<SupportMessageView, 'type' | 'text' | 'payload'>) {
  if (message.text?.trim()) return message.text.trim();
  if (message.type === 'image') return 'Скриншот';
  if (message.type === 'voice') return 'Голосовое сообщение';
  if (message.type === 'file') return String(message.payload?.original_filename || 'Файл');
  return 'Сообщение';
}

function downloadFilename(message: SupportMessageView) {
  const filename = String(message.payload?.original_filename || '').trim();
  if (filename) return filename;
  if (message.type === 'image') return `chat-image-${message.id}.png`;
  if (message.type === 'voice') return `chat-voice-${message.id}.webm`;
  return `chat-file-${message.id}`;
}

function renderReplyPreview(message: SupportMessageView) {
  const reply = message.reply_to;
  if (!reply) return null;
  return (
    <div className="mb-2 rounded-xl border-l-2 border-cyan-200/50 bg-black/18 px-3 py-2">
      <p className="text-[10px] font-black uppercase tracking-[0.12em] text-cyan-100/70">
        Ответ на {reply.author_label}
      </p>
      <p className="mt-1 line-clamp-2 text-xs font-bold leading-relaxed text-white/65">
        {messagePreview(reply)}
      </p>
    </div>
  );
}

function renderMessageBody(message: SupportMessageView) {
  const mediaUrl = resolveMediaUrl(message.payload?.url);
  const caption = message.text || '';

  if (message.type === 'image') {
    return (
      <div className="space-y-2">
        {mediaUrl ? (
          <a href={mediaUrl} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border border-white/10 bg-black/20">
            <img
              src={mediaUrl}
              alt={caption || 'Скриншот'}
              className="max-h-72 w-full object-contain"
              loading="lazy"
              decoding="async"
            />
          </a>
        ) : (
          <div className="flex min-h-[96px] items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/45">
            <ImageIcon className="h-5 w-5" />
          </div>
        )}
        {caption && (
          <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
            {renderTextWithLinks(caption)}
          </p>
        )}
        <p className="text-[10px] font-black uppercase tracking-[0.12em] text-white/35">
          {formatBytes(message.payload?.size_bytes) || 'image'}
        </p>
      </div>
    );
  }

  if (message.type === 'voice') {
    return (
      <div className="space-y-2">
        <div className="rounded-xl border border-white/10 bg-black/20 p-2">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/45">
            <Mic className="h-3.5 w-3.5" />
            <span>{formatDuration(message.payload?.duration_ms) || 'voice'}</span>
          </div>
          {mediaUrl ? (
            <audio controls preload="metadata" src={mediaUrl} className="h-9 w-full min-w-0" />
          ) : (
            <p className="text-xs font-bold text-white/45">Аудио недоступно</p>
          )}
        </div>
        {caption && (
          <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
            {renderTextWithLinks(caption)}
          </p>
        )}
      </div>
    );
  }

  if (message.type === 'file') {
    const filename = String(message.payload?.original_filename || 'Файл');
    const mimeType = String(message.payload?.mime_type || 'application/octet-stream');
    return (
      <div className="flex min-w-0 items-center gap-3 rounded-xl border border-white/10 bg-black/20 p-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-cyan-200/20 bg-cyan-300/10 text-cyan-100">
          <FileText className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-black text-white">{filename}</p>
          <p className="mt-1 truncate text-[10px] font-black uppercase tracking-[0.12em] text-white/35">
            {formatBytes(message.payload?.size_bytes) || 'file'} · {mimeType}
          </p>
        </div>
      </div>
    );
  }

  return (
    <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
      {renderTextWithLinks(caption)}
    </p>
  );
}

function SupportMessageBubble({
  message,
  ownerLabel = 'Клиент',
  onRetry,
  onReply,
  onCopy,
  onDownload,
  staffSide = 'left',
}: SupportMessageBubbleProps) {
  const staff = message.direction === 'staff';
  const deliveryState = message.delivery_state || 'sent';
  const alignRight = staff ? staffSide === 'right' : staffSide === 'left';
  const label = staff ? 'Shamrai' : ownerLabel;
  const accentClass = staff
    ? 'border-cyan-300/25 bg-cyan-300/12 text-cyan-50'
    : 'border-fuchsia-300/20 bg-fuchsia-300/10 text-fuchsia-50';
  const cornerClass = alignRight ? 'rounded-br-md' : 'rounded-bl-md';
  const avatarClass = staff ? 'text-cyan-100' : 'text-fuchsia-100';
  const AvatarIcon = staff ? Bot : User;
  const showDeliveryReceipt = alignRight;
  const receiptRead = Boolean(message.read_at);
  const messageBody = useMemo(() => renderMessageBody(message), [message]);
  const canDownload = Boolean(message.payload?.download_url);
  const canCopy = Boolean(message.text?.trim());

  return (
    <div className={`flex min-w-0 items-start gap-2 sm:gap-3 ${alignRight ? 'justify-end' : ''}`}>
      {!alignRight && (
        <div className={`mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 ${avatarClass}`}>
          <AvatarIcon className="h-4 w-4" />
        </div>
      )}

      <div className={`min-w-0 max-w-[calc(100%_-_2.5rem)] rounded-2xl border px-3 py-3 sm:max-w-[86%] sm:px-3.5 ${cornerClass} ${accentClass} ${deliveryState === 'failed' ? 'border-rose-300/35 bg-rose-400/10' : ''}`}>
        <p className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/45">
          {label}
        </p>
        {renderReplyPreview(message)}
        {messageBody}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
            <span>{messageTime(message.created_at)}</span>
            {showDeliveryReceipt && deliveryState === 'sending' && (
              <span className="inline-flex items-center text-cyan-100/70" title="Отправляем" aria-label="Отправляем">
                <Clock3 className="h-3 w-3" />
              </span>
            )}
            {showDeliveryReceipt && deliveryState === 'sent' && (
              <span
                className={receiptRead ? 'inline-flex items-center text-emerald-300' : 'inline-flex items-center text-white/70'}
                title={receiptRead ? 'Прочитано' : 'Доставлено'}
                aria-label={receiptRead ? 'Прочитано' : 'Доставлено'}
              >
                {receiptRead ? <CheckCheck className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {onReply && message.id > 0 && (
              <button
                type="button"
                onClick={() => onReply(message)}
                title="Ответить"
                aria-label="Ответить"
                className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-white/55 transition hover:bg-white/[0.08] hover:text-white"
              >
                <Reply className="h-3.5 w-3.5" />
              </button>
            )}
            {canCopy && onCopy && (
              <button
                type="button"
                onClick={() => onCopy(message)}
                title="Копировать текст"
                aria-label="Копировать текст"
                className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-white/55 transition hover:bg-white/[0.08] hover:text-white"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            )}
            {canDownload && onDownload && (
              <button
                type="button"
                onClick={() => onDownload(message)}
                title={`Скачать ${downloadFilename(message)}`}
                aria-label="Скачать вложение"
                className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-white/55 transition hover:bg-white/[0.08] hover:text-white"
              >
                <Download className="h-3.5 w-3.5" />
              </button>
            )}
            {showDeliveryReceipt && deliveryState === 'failed' && onRetry && (
              <button
                type="button"
                onClick={() => onRetry(message)}
                title="Повторить отправку"
                aria-label="Повторить отправку"
                className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-rose-200/25 bg-rose-300/10 text-rose-100 transition hover:bg-rose-300/18"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {alignRight && (
        <div className={`mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/75 ${avatarClass}`}>
          <AvatarIcon className="h-4 w-4" />
        </div>
      )}
    </div>
  );
}

export default memo(SupportMessageBubble);
