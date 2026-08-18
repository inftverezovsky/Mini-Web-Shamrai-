import React, { useEffect, useRef, useState } from 'react';
import { SmilePlus } from 'lucide-react';
import { useGlassOverlayGuard } from '../hooks/useGlassOverlayGuard';
import { buildApiUrl } from '../config/api';
import TelegramCustomEmojiText from './TelegramCustomEmojiText';

type TextTarget = HTMLInputElement | HTMLTextAreaElement;

export interface TelegramCustomEmojiEntity {
  offset: number;
  length: number;
  custom_emoji_id: string;
}

export interface TelegramCustomEmojiOption {
  custom_emoji_id: string;
  fallback: string;
  preview_url: string;
  preview_ready: boolean;
}

const DEFAULT_EMOJIS = [
  '🔥', '⚡', '✅', '❌', '↩️', '💰', '📈', '📉',
  '🎯', '🧠', '🏆', '⭐', '🚨', '💎', '⏱️', '📌',
  '⚽', '🏒', '🏀', '🎾', '🥊', '🎮', '🤝', '🔒',
];

type CommonEmojiProps = {
  value: string;
  onValueChange: (value: string) => void;
  className?: string;
  containerClassName?: string;
  emojis?: string[];
  customEmojis?: TelegramCustomEmojiOption[];
  customEmojiEntities?: TelegramCustomEmojiEntity[];
  onCustomEmojiEntitiesChange?: (entities: TelegramCustomEmojiEntity[]) => void;
};

type EmojiInputProps = CommonEmojiProps &
  Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'className'> & {
    multiline?: false;
  };

type EmojiTextareaProps = CommonEmojiProps &
  Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'className'> & {
    multiline: true;
  };

type EmojiTextFieldProps = EmojiInputProps | EmojiTextareaProps;

export default function EmojiTextField(props: EmojiTextFieldProps) {
  const {
    value,
    onValueChange,
    className = '',
    containerClassName = '',
    emojis = DEFAULT_EMOJIS,
    customEmojis = [],
    customEmojiEntities = [],
    onCustomEmojiEntitiesChange,
    multiline,
    ...fieldProps
  } = props;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const fieldRef = useRef<TextTarget>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const focusFrameRef = useRef<number | undefined>();
  const richPreviewActive = customEmojiEntities.length > 0;

  useGlassOverlayGuard(open);

  useEffect(() => () => {
    if (focusFrameRef.current !== undefined) window.cancelAnimationFrame(focusFrameRef.current);
  }, []);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  const updateEntitiesForEdit = (nextValue: string) => {
    if (!onCustomEmojiEntitiesChange) return;
    let prefixLength = 0;
    while (
      prefixLength < value.length
      && prefixLength < nextValue.length
      && value[prefixLength] === nextValue[prefixLength]
    ) {
      prefixLength += 1;
    }
    let suffixLength = 0;
    while (
      suffixLength < value.length - prefixLength
      && suffixLength < nextValue.length - prefixLength
      && value[value.length - suffixLength - 1] === nextValue[nextValue.length - suffixLength - 1]
    ) {
      suffixLength += 1;
    }
    const oldChangedEnd = value.length - suffixLength;
    const delta = nextValue.length - value.length;
    onCustomEmojiEntitiesChange(
      customEmojiEntities
        .filter((entity) => (
          entity.offset + entity.length <= prefixLength
          || entity.offset >= oldChangedEnd
        ))
        .map((entity) => (
          entity.offset >= oldChangedEnd
            ? { ...entity, offset: entity.offset + delta }
            : entity
        )),
    );
  };

  const handleValueChange = (nextValue: string) => {
    updateEntitiesForEdit(nextValue);
    onValueChange(nextValue);
  };

  const syncPreviewScroll = (field: TextTarget | null) => {
    if (!field || !previewRef.current) return;
    previewRef.current.scrollTop = field.scrollTop;
    previewRef.current.scrollLeft = field.scrollLeft;
  };

  const insertText = (insertedText: string, customEmojiId?: string) => {
    const field = fieldRef.current;
    const start = field?.selectionStart ?? value.length;
    const end = field?.selectionEnd ?? value.length;
    const nextValue = `${value.slice(0, start)}${insertedText}${value.slice(end)}`;
    const cursorPosition = start + insertedText.length;

    if (customEmojiId && onCustomEmojiEntitiesChange) {
      const replacedLength = end - start;
      const delta = insertedText.length - replacedLength;
      const shiftedEntities = customEmojiEntities
        .filter((entity) => entity.offset + entity.length <= start || entity.offset >= end)
        .map((entity) => (
          entity.offset >= end
            ? { ...entity, offset: entity.offset + delta }
            : entity
        ));
      onCustomEmojiEntitiesChange([
        ...shiftedEntities,
        {
          offset: start,
          length: insertedText.length,
          custom_emoji_id: customEmojiId,
        },
      ].sort((left, right) => left.offset - right.offset));
    } else {
      updateEntitiesForEdit(nextValue);
    }
    onValueChange(nextValue);
    setOpen(false);

    if (focusFrameRef.current !== undefined) window.cancelAnimationFrame(focusFrameRef.current);
    focusFrameRef.current = window.requestAnimationFrame(() => {
      focusFrameRef.current = undefined;
      field?.focus();
      field?.setSelectionRange(cursorPosition, cursorPosition);
      syncPreviewScroll(field);
    });
  };

  const fieldClassName = `${className} pr-12`;
  const fieldStyle: React.CSSProperties = {
    ...(fieldProps.style || {}),
    ...(richPreviewActive
      ? {
          color: 'transparent',
          caretColor: '#fff',
        }
      : {}),
  };
  const previewClassName = [
    fieldClassName,
    'pointer-events-none absolute inset-0 z-[1] overflow-hidden',
    multiline ? 'whitespace-pre-wrap break-words' : 'whitespace-pre',
  ].join(' ');

  return (
    <div ref={rootRef} className={`relative w-full ${containerClassName}`}>
      {multiline ? (
        <textarea
          {...(fieldProps as Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'className'>)}
          ref={fieldRef as React.RefObject<HTMLTextAreaElement>}
          value={value}
          onChange={(event) => handleValueChange(event.target.value)}
          onScroll={(event) => {
            syncPreviewScroll(event.currentTarget);
            (
              fieldProps as Omit<
                React.TextareaHTMLAttributes<HTMLTextAreaElement>,
                'value' | 'onChange' | 'className'
              >
            ).onScroll?.(event);
          }}
          className={fieldClassName}
          style={fieldStyle}
        />
      ) : (
        <input
          {...(fieldProps as Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'className'>)}
          ref={fieldRef as React.RefObject<HTMLInputElement>}
          value={value}
          onChange={(event) => handleValueChange(event.target.value)}
          onScroll={(event) => {
            syncPreviewScroll(event.currentTarget);
            (
              fieldProps as Omit<
                React.InputHTMLAttributes<HTMLInputElement>,
                'value' | 'onChange' | 'className'
              >
            ).onScroll?.(event);
          }}
          className={fieldClassName}
          style={fieldStyle}
        />
      )}

      {richPreviewActive && (
        <div
          ref={previewRef}
          data-custom-emoji-preview="true"
          aria-hidden="true"
          className={previewClassName}
          style={{
            ...fieldProps.style,
            background: 'transparent',
            borderColor: 'transparent',
            boxShadow: 'none',
            color: '#fff',
          }}
        >
          <TelegramCustomEmojiText
            text={value}
            entities={customEmojiEntities}
          />
          {multiline && value.endsWith('\n') ? '\u00a0' : null}
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-slate-950/70 text-slate-400 shadow-lg backdrop-blur transition-all hover:border-cyan-300/40 hover:text-cyan-200 focus:outline-none focus:ring-2 focus:ring-cyan-300/25"
        title="Выбрать смайл"
        aria-label="Выбрать смайл"
      >
        <SmilePlus className="h-4 w-4" />
      </button>

      {open && (
        <div className="glass-modal-layer absolute right-0 top-full z-50 mt-2 w-[264px] rounded-xl border border-white/10 bg-slate-950/95 p-2 shadow-2xl backdrop-blur-xl">
          {customEmojis.length > 0 && (
            <>
              <div className="mb-1 px-1 text-[8px] font-black uppercase tracking-wider text-cyan-300/70">
                Загруженные
              </div>
              <div className="mb-2 grid max-h-44 grid-cols-6 gap-1.5 overflow-y-auto pr-0.5">
                {customEmojis.map((emoji) => (
                  <button
                    key={emoji.custom_emoji_id}
                    type="button"
                    onClick={() => insertText(emoji.fallback, emoji.custom_emoji_id)}
                    disabled={!emoji.preview_ready}
                    className="relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-lg border border-white/5 bg-white/[0.025] text-base transition-all hover:border-cyan-300/25 hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-cyan-300/25 disabled:cursor-wait disabled:opacity-45"
                    aria-label={emoji.preview_ready ? 'Добавить загруженный эмодзи' : 'Эмодзи подготавливается'}
                    title={emoji.preview_ready ? 'Добавить эмодзи' : 'Подготавливается…'}
                  >
                    <span
                      aria-hidden="true"
                      className={`absolute inset-1 rounded-md bg-slate-800/70 ${
                        emoji.preview_ready ? '' : 'animate-pulse'
                      }`}
                    />
                    {emoji.preview_ready && (
                      <img
                        src={buildApiUrl(emoji.preview_url)}
                        alt=""
                        className="relative z-10 h-full w-full object-contain p-0.5"
                        loading="eager"
                        onError={(event) => {
                          event.currentTarget.style.display = 'none';
                        }}
                      />
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="grid grid-cols-8 gap-1">
            {emojis.map((emoji) => (
              <button
                key={emoji}
                type="button"
                onClick={() => insertText(emoji)}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-base transition-all hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-cyan-300/25"
                aria-label={`Добавить ${emoji}`}
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
