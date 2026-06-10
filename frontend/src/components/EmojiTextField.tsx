import React, { useEffect, useRef, useState } from 'react';
import { SmilePlus } from 'lucide-react';

type TextTarget = HTMLInputElement | HTMLTextAreaElement;

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
    multiline,
    ...fieldProps
  } = props;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const fieldRef = useRef<TextTarget>(null);

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

  const handleEmojiSelect = (emoji: string) => {
    const field = fieldRef.current;
    const start = field?.selectionStart ?? value.length;
    const end = field?.selectionEnd ?? value.length;
    const nextValue = `${value.slice(0, start)}${emoji}${value.slice(end)}`;
    const cursorPosition = start + emoji.length;

    onValueChange(nextValue);
    setOpen(false);

    window.requestAnimationFrame(() => {
      field?.focus();
      field?.setSelectionRange(cursorPosition, cursorPosition);
    });
  };

  const fieldClassName = `${className} pr-12`;

  return (
    <div ref={rootRef} className={`relative w-full ${containerClassName}`}>
      {multiline ? (
        <textarea
          {...(fieldProps as Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'className'>)}
          ref={fieldRef as React.RefObject<HTMLTextAreaElement>}
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          className={fieldClassName}
        />
      ) : (
        <input
          {...(fieldProps as Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'className'>)}
          ref={fieldRef as React.RefObject<HTMLInputElement>}
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          className={fieldClassName}
        />
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
        <div className="absolute right-0 top-full z-50 mt-2 grid w-[216px] grid-cols-8 gap-1 rounded-xl border border-white/10 bg-slate-950/95 p-2 shadow-2xl backdrop-blur-xl">
          {emojis.map((emoji) => (
            <button
              key={emoji}
              type="button"
              onClick={() => handleEmojiSelect(emoji)}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-base transition-all hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-cyan-300/25"
              aria-label={`Добавить ${emoji}`}
            >
              {emoji}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
