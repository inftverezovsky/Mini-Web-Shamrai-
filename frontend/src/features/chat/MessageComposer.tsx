import { ClipboardEvent, DragEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import { FileText, Loader2, Mic, Paperclip, Send, Smile, Square, Trash2, X } from 'lucide-react';
import { getClipboardImageFile } from '../../utils/clipboardImages';

export type ChatAttachmentType = 'image' | 'voice' | 'file';

export interface ChatComposerAttachment {
  messageType: ChatAttachmentType;
  file: File;
  previewUrl: string;
  durationMs?: number;
}

interface MessageComposerProps {
  draft: string;
  onDraftChange: (value: string) => void;
  disabled?: boolean;
  active?: boolean;
  sending?: boolean;
  placeholder: string;
  submitTitle: string;
  onSendText: (text: string) => Promise<boolean>;
  onSendAttachment: (attachment: ChatComposerAttachment, text: string) => Promise<boolean>;
  onError: (message: string) => void;
  onTyping?: () => void;
}

const IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const VOICE_MAX_BYTES = 10 * 1024 * 1024;
const FILE_MAX_BYTES = 25 * 1024 * 1024;
const VOICE_MAX_DURATION_MS = 120_000;
const EMOJI_OPTIONS = ['🙂', '😊', '🔥', '❤️', '👍', '🙏', '💪', '✅', '🚀', '👀', '🤝', '💬', '😎', '🥳', '⚡', '📌', '🎯', '💎'];

function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
  if (value >= 1024) return `${Math.round(value / 1024)} КБ`;
  return `${value} Б`;
}

function formatDuration(durationMs: number) {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function supportedAudioMimeType() {
  if (typeof MediaRecorder === 'undefined') return '';
  const candidates = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'];
  return candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate)) || '';
}

function extensionForMimeType(mimeType: string) {
  if (mimeType.includes('ogg')) return 'ogg';
  if (mimeType.includes('mp4')) return 'm4a';
  return 'webm';
}

export default function MessageComposer({
  draft,
  onDraftChange,
  disabled = false,
  active = true,
  sending = false,
  placeholder,
  submitTitle,
  onSendText,
  onSendAttachment,
  onError,
  onTyping,
}: MessageComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const recordingStartedAtRef = useRef(0);
  const focusTimerRef = useRef<number | undefined>();
  const recordingDurationRef = useRef<HTMLSpanElement | null>(null);
  const recordingFrameRef = useRef<number | undefined>();
  const [attachment, setAttachment] = useState<ChatComposerAttachment | null>(null);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  useEffect(() => () => {
    if (focusTimerRef.current !== undefined) window.clearTimeout(focusTimerRef.current);
    if (recordingFrameRef.current !== undefined) window.cancelAnimationFrame(recordingFrameRef.current);
    if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, [attachment]);

  useEffect(() => {
    if (!recording) return undefined;
    const updateDuration = () => {
      if (recordingFrameRef.current !== undefined) window.cancelAnimationFrame(recordingFrameRef.current);
      recordingFrameRef.current = window.requestAnimationFrame(() => {
        recordingFrameRef.current = undefined;
        if (recordingDurationRef.current) {
          recordingDurationRef.current.textContent = formatDuration(Date.now() - recordingStartedAtRef.current);
        }
      });
    };

    updateDuration();
    const timer = window.setInterval(() => {
      const elapsed = Date.now() - recordingStartedAtRef.current;
      updateDuration();
      if (elapsed >= VOICE_MAX_DURATION_MS) {
        recorderRef.current?.stop();
      }
    }, 250);
    return () => {
      window.clearInterval(timer);
      if (recordingFrameRef.current !== undefined) {
        window.cancelAnimationFrame(recordingFrameRef.current);
        recordingFrameRef.current = undefined;
      }
    };
  }, [recording]);

  useEffect(() => {
    if (active || !recording) return;
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  }, [active, recording]);

  const clearAttachment = () => {
    if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
    setAttachment(null);
  };

  const replaceAttachment = (nextAttachment: ChatComposerAttachment) => {
    if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
    setAttachment(nextAttachment);
  };

  const handleDraftChange = (value: string) => {
    onDraftChange(value);
    onTyping?.();
  };

  const insertEmoji = (emoji: string) => {
    const textarea = textareaRef.current;
    if (!textarea) {
      handleDraftChange(`${draft}${emoji}`);
      return;
    }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const nextDraft = `${draft.slice(0, start)}${emoji}${draft.slice(end)}`;
    handleDraftChange(nextDraft);
    if (focusTimerRef.current !== undefined) window.clearTimeout(focusTimerRef.current);
    focusTimerRef.current = window.setTimeout(() => {
      focusTimerRef.current = undefined;
      textarea.focus();
      const cursor = start + emoji.length;
      textarea.setSelectionRange(cursor, cursor);
    }, 0);
  };

  const handleFileSelected = (file?: File | null) => {
    if (!file) return;
    const isImage = ['image/png', 'image/jpeg', 'image/webp'].includes(file.type);
    if (isImage) {
      if (file.size > IMAGE_MAX_BYTES) {
        onError('Скрин слишком большой. Максимум 5 МБ');
        return;
      }
      replaceAttachment({
        messageType: 'image',
        file,
        previewUrl: URL.createObjectURL(file),
      });
      return;
    }
    if (file.size > FILE_MAX_BYTES) {
      onError('Файл слишком большой. Максимум 25 МБ');
      return;
    }
    replaceAttachment({
      messageType: 'file',
      file,
      previewUrl: URL.createObjectURL(file),
    });
  };

  const handlePaste = (event: ClipboardEvent<HTMLFormElement>) => {
    if (!active || disabled || sending || recording) return;
    const pastedImage = getClipboardImageFile(event.clipboardData);
    if (!pastedImage) return;
    event.preventDefault();
    event.stopPropagation();
    handleFileSelected(pastedImage);
  };

  const handleDragOver = (event: DragEvent<HTMLFormElement>) => {
    if (!active || disabled || sending || recording) return;
    if (!Array.from(event.dataTransfer.types).includes('Files')) return;
    event.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLFormElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragActive(false);
  };

  const handleDrop = (event: DragEvent<HTMLFormElement>) => {
    if (!active || disabled || sending || recording) return;
    event.preventDefault();
    setDragActive(false);
    handleFileSelected(event.dataTransfer.files?.[0]);
  };

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const startRecording = async () => {
    if (!active || disabled || sending || recording) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      onError('Браузер не поддерживает запись голосовых сообщений');
      return;
    }
    const mimeType = supportedAudioMimeType();
    if (!mimeType) {
      onError('Браузер не поддерживает подходящий аудиоформат');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType });
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      recordingStartedAtRef.current = Date.now();

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const durationMs = Date.now() - recordingStartedAtRef.current;
        setRecording(false);
        stopStream();
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType });
        chunksRef.current = [];
        if (blob.size <= 0) {
          onError('Не удалось записать голосовое сообщение');
          return;
        }
        if (blob.size > VOICE_MAX_BYTES || durationMs > VOICE_MAX_DURATION_MS + 1000) {
          onError('Голосовое слишком длинное или тяжелое. Максимум 2 минуты и 10 МБ');
          return;
        }
        const extension = extensionForMimeType(blob.type);
        const file = new File([blob], `voice-${Date.now()}.${extension}`, { type: blob.type || 'audio/webm' });
        replaceAttachment({
          messageType: 'voice',
          file,
          previewUrl: URL.createObjectURL(file),
          durationMs: Math.min(durationMs, VOICE_MAX_DURATION_MS),
        });
      };
      recorder.start();
      setRecording(true);
    } catch {
      stopStream();
      setRecording(false);
      onError('Нет доступа к микрофону. Разрешите запись и попробуйте снова');
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  };

  const handleSubmit = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!active || disabled || sending || recording) return;
    const cleanText = draft.trim();
    let sent = false;
    if (attachment) {
      sent = await onSendAttachment(attachment, cleanText);
    } else if (cleanText) {
      sent = await onSendText(cleanText);
    }
    if (!sent) return;
    clearAttachment();
    handleDraftChange('');
    setEmojiOpen(false);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    void handleSubmit();
  };

  const canSend = active && !disabled && !sending && !recording && Boolean(draft.trim() || attachment);

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      onPaste={handlePaste}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`min-w-0 border-t border-white/10 bg-slate-950/55 px-3 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] transition ${
        dragActive ? 'ring-2 ring-cyan-300/40' : ''
      }`}
    >
      {attachment && (
        <div className="mb-2 flex min-w-0 items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.045] p-2">
          {attachment.messageType === 'image' ? (
            <img src={attachment.previewUrl} alt="preview" className="h-14 w-14 shrink-0 rounded-xl object-cover" />
          ) : attachment.messageType === 'voice' ? (
            <audio controls preload="metadata" src={attachment.previewUrl} className="h-10 min-w-0 flex-1" />
          ) : (
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-cyan-300/10 text-cyan-100">
              <FileText className="h-5 w-5" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-black text-white">
              {attachment.messageType === 'voice' ? 'Голосовое сообщение' : attachment.file.name}
            </p>
            <p className="mt-1 text-[10px] font-bold text-slate-500">
              {formatBytes(attachment.file.size)}
              {attachment.durationMs ? ` · ${formatDuration(attachment.durationMs)}` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={clearAttachment}
            title="Убрать вложение"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-slate-300 transition hover:bg-white/[0.08]"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      )}

      {recording && (
        <div className="mb-2 flex items-center justify-between gap-3 rounded-2xl border border-slate-300/25 bg-slate-400/10 px-3 py-2 text-slate-100">
          <div className="flex items-center gap-2 text-xs font-black">
            <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
            <span ref={recordingDurationRef}>0:01</span>
          </div>
          <button
            type="button"
            onClick={stopRecording}
            className="inline-flex min-h-[34px] items-center gap-2 rounded-xl border border-slate-200/20 bg-slate-200/10 px-3 py-1.5 text-xs font-black transition hover:bg-slate-200/16"
          >
            <Square className="h-3.5 w-3.5" />
            <span>Стоп</span>
          </button>
        </div>
      )}

      {emojiOpen && (
        <div className="mb-2 grid grid-cols-6 gap-1 rounded-2xl border border-white/10 bg-slate-950/85 p-2 sm:grid-cols-9">
          {EMOJI_OPTIONS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              onClick={() => insertEmoji(emoji)}
              className="grid h-9 w-full place-items-center rounded-xl text-lg transition hover:bg-white/[0.08]"
            >
              {emoji}
            </button>
          ))}
        </div>
      )}

      <div className="grid min-w-0 grid-cols-[auto_auto_auto_minmax(0,1fr)_auto] items-end gap-2">
        <input
          ref={imageInputRef}
          type="file"
          className="hidden"
          onChange={(event) => {
            handleFileSelected(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => imageInputRef.current?.click()}
          disabled={disabled || sending || recording}
          title="Прикрепить файл или скрин"
          className="flex h-[46px] w-[46px] items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] text-slate-200 transition hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-45"
        >
          <Paperclip className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => (recording ? stopRecording() : void startRecording())}
          disabled={disabled || sending}
          title={recording ? 'Остановить запись' : 'Записать голосовое'}
          className={`flex h-[46px] w-[46px] items-center justify-center rounded-2xl border transition disabled:cursor-not-allowed disabled:opacity-45 ${
            recording
              ? 'border-slate-300/30 bg-slate-400/15 text-slate-100'
              : 'border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/[0.08]'
          }`}
        >
          {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </button>
        <button
          type="button"
          onClick={() => setEmojiOpen((current) => !current)}
          disabled={disabled || sending || recording}
          title={emojiOpen ? 'Закрыть emoji' : 'Emoji'}
          className="flex h-[46px] w-[46px] items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] text-slate-200 transition hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-45"
        >
          {emojiOpen ? <X className="h-4 w-4" /> : <Smile className="h-4 w-4" />}
        </button>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(event) => handleDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || sending || recording}
          maxLength={4000}
          rows={2}
          placeholder={attachment ? 'Подпись к вложению...' : placeholder}
          className="min-h-[46px] max-h-28 min-w-0 w-full resize-none rounded-2xl border border-white/10 bg-slate-950/70 px-3 py-2.5 text-sm font-semibold text-white placeholder:text-slate-500 focus:border-cyan-300/45 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!canSend}
          title={submitTitle}
          className="flex h-[46px] w-[46px] items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/15 text-cyan-50 transition-all hover:bg-cyan-300/25 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45"
        >
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </button>
      </div>
    </form>
  );
}
