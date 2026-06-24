import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from 'lucide-react';
import {
  ConfirmPayload,
  NoticePayload,
  subscribeConfirm,
  subscribeNotice,
} from '../utils/notify';
import { useGlassOverlayGuard } from '../hooks/useGlassOverlayGuard';

type NoticeItem = Required<Pick<NoticePayload, 'id' | 'duration'>> & NoticePayload;

const toneMap = {
  success: {
    Icon: CheckCircle2,
    className: 'border-emerald-400/30 bg-emerald-500/12 text-emerald-200',
  },
  error: {
    Icon: AlertTriangle,
    className: 'border-rose-400/35 bg-rose-500/12 text-rose-100',
  },
  warning: {
    Icon: AlertTriangle,
    className: 'border-amber-400/35 bg-amber-500/12 text-amber-100',
  },
  info: {
    Icon: Info,
    className: 'border-cyan-400/30 bg-cyan-500/12 text-cyan-100',
  },
  pending: {
    Icon: Loader2,
    className: 'border-indigo-400/30 bg-indigo-500/12 text-indigo-100',
  },
};

export default function NotificationCenter() {
  const [notices, setNotices] = useState<NoticeItem[]>([]);
  const [confirm, setConfirm] = useState<ConfirmPayload | null>(null);
  const timeoutIdsRef = React.useRef<Set<number>>(new Set());

  useGlassOverlayGuard(Boolean(confirm));

  useEffect(() => {
    const timeoutIds = timeoutIdsRef.current;
    const scheduleDismiss = (id: string, duration: number) => {
      const timeoutId = window.setTimeout(() => {
        timeoutIds.delete(timeoutId);
        setNotices((items) => items.filter((item) => item.id !== id));
      }, duration);
      timeoutIds.add(timeoutId);
    };

    const unsubscribeNotice = subscribeNotice((payload) => {
      const id = payload.id || crypto.randomUUID();
      const duration = payload.duration ?? 4200;
      const notice = { ...payload, id, duration };
      setNotices((items) => [notice, ...items].slice(0, 4));
      scheduleDismiss(id, duration);
    });

    const unsubscribeConfirm = subscribeConfirm((payload) => setConfirm(payload));

    const originalAlert = window.alert;
    window.alert = (message?: unknown) => {
      const alertNotice: NoticeItem = {
        id: crypto.randomUUID(),
        type: 'info',
        title: 'Сообщение',
        message: String(message || ''),
        duration: 4200,
      };

      setNotices((items) => [
        alertNotice,
        ...items,
      ].slice(0, 4));
      scheduleDismiss(alertNotice.id, alertNotice.duration);
    };

    return () => {
      unsubscribeNotice();
      unsubscribeConfirm();
      timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
      timeoutIds.clear();
      window.alert = originalAlert;
    };
  }, []);

  const confirmTone = useMemo(() => {
    if (!confirm) return 'border-white/10 bg-slate-950/95';
    return confirm.tone === 'warning'
      ? 'border-amber-400/30 bg-slate-950/95'
      : 'border-rose-400/30 bg-slate-950/95';
  }, [confirm]);

  const resolveConfirm = (value: boolean) => {
    if (!confirm) return;
    confirm.resolve(value);
    setConfirm(null);
  };

  return (
    <>
      <div className="pointer-events-none fixed left-3 right-3 top-3 z-[1200] mx-auto flex max-w-md flex-col gap-2">
        {notices.map((notice) => {
          const tone = toneMap[notice.type];
          const Icon = tone.Icon;
          const isPending = notice.type === 'pending';
          return (
            <div
              key={notice.id}
              className={`pointer-events-auto flex items-start gap-3 rounded-2xl border px-4 py-3 shadow-2xl backdrop-blur-xl animate-slide-up ${tone.className}`}
            >
              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${isPending ? 'animate-spin' : ''}`} />
              <div className="min-w-0 flex-1">
                {notice.title && (
                  <div className="truncate text-[10px] font-black uppercase tracking-wider text-white/80">
                    {notice.title}
                  </div>
                )}
                <div className="mt-0.5 text-xs font-semibold leading-snug text-white">
                  {notice.message}
                </div>
              </div>
              <button
                onClick={() => setNotices((items) => items.filter((item) => item.id !== notice.id))}
                className="rounded-lg p-1 text-white/45 transition-colors hover:bg-white/10 hover:text-white"
                aria-label="Закрыть уведомление"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>

      {confirm && (
        <div className="glass-modal-layer fixed inset-0 z-[1250] flex items-center justify-center bg-slate-950/72 p-4 backdrop-blur-md">
          <div className={`w-full max-w-sm rounded-3xl border p-5 shadow-2xl ${confirmTone}`}>
            <div className="flex items-start gap-3">
              <div className="rounded-2xl border border-white/10 bg-white/5 p-2 text-rose-300">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-black text-white">{confirm.title}</h3>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-300">{confirm.message}</p>
              </div>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-2">
              <button
                onClick={() => resolveConfirm(false)}
                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-xs font-black text-slate-200 transition-colors hover:bg-white/10"
              >
                {confirm.cancelLabel || 'Отмена'}
              </button>
              <button
                onClick={() => resolveConfirm(true)}
                className="rounded-xl bg-rose-500 px-3 py-2.5 text-xs font-black text-white shadow-[0_0_22px_rgba(244,63,94,0.24)] transition-colors hover:bg-rose-400"
              >
                {confirm.confirmLabel || 'Подтвердить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
