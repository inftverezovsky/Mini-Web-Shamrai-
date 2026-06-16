import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Bot, Check, ExternalLink, Image as ImageIcon, Loader2, Radio, ShieldCheck, WifiOff, X, Zap } from 'lucide-react';

import { BookmakerLogoFrame } from './LogoFrame';
import { WEB_SIGNAL_EVENT, WEB_SIGNAL_STATUS_EVENT } from './WebSignalListener';
import { API_BASE_URL, apiFetch } from '../utils/api';
import { getStoredAuthToken } from '../utils/authStorage';
import { notifyError, notifyInfo, notifySuccess } from '../utils/notify';
import { unlockIncomingSignalSound } from '../utils/signalAudio';
import { isTelegramMiniApp } from '../utils/telegramSdk';

type ForecastSignalAction = 'take' | 'decline';

interface SignalBookmaker {
  id: number | string;
  name: string;
  code: string;
  logo_url?: string;
  url?: string;
}

interface PersonalSignal {
  id: number;
  user_id: number;
  text: string;
  type: string;
  data?: {
    forecast_request_id?: string;
    forecast_status?: string;
    actions?: ForecastSignalAction[];
    message_text?: string;
    coupon_image_url?: string | null;
    bookmakers?: SignalBookmaker[];
    event_name?: string;
    outcome?: string | null;
    coefficient?: string | number | null;
    sport_type?: string | null;
    request_kind?: string;
    price_text?: string | null;
  };
  created_at: string;
}

interface ForecastSignalActionResponse {
  status: string;
  message: string;
  forecast_request_id: string;
}

function signalTime(value: string) {
  try {
    return new Intl.DateTimeFormat('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value));
  } catch {
    return '';
  }
}

function signalAccent(type: string) {
  if (type === 'live_signal') return 'border-rose-400/30 bg-rose-500/10 text-rose-100';
  if (type === 'forecast_full') return 'border-emerald-300/30 bg-emerald-400/10 text-emerald-50';
  if (type === 'forecast_teaser' || type === 'announcement') return 'border-amber-300/25 bg-amber-400/10 text-amber-100';
  if (type === 'system') return 'border-cyan-300/25 bg-cyan-400/10 text-cyan-100';
  return 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100';
}

function signalKindLabel(signal: PersonalSignal) {
  if (signal.data?.request_kind === 'paid_set') return 'Набор';
  return 'Прогноз';
}

function forecastStatusLabel(signal: PersonalSignal) {
  const status = signal.data?.forecast_status;
  const isPaidSet = signal.data?.request_kind === 'paid_set';
  if (status === 'interested' || status === 'processing') return 'Заявка отправлена';
  if (status === 'sent' || status === 'manual_sent') return isPaidSet ? 'Заявка закрыта' : 'Прогноз оформлен';
  if (status === 'declined') return 'Отказ учтен';
  if (status === 'cancelled') return 'Заявка отменена';
  if (status === 'removed') return isPaidSet ? 'Набор остановлен' : 'Анонс остановлен';
  return '';
}

function signalActionLabel(signal: PersonalSignal) {
  return signal.data?.request_kind === 'paid_set' ? 'Взять' : 'Взять';
}

function signalCanShowForecastActions(signal: PersonalSignal) {
  return (
    signal.type === 'forecast_teaser'
    && Boolean(signal.data?.forecast_request_id)
    && (signal.data?.forecast_status || 'announced') === 'announced'
  );
}

function mergeSignals(history: PersonalSignal[], current: PersonalSignal[]) {
  const byId = new Map<number, PersonalSignal>();
  history.forEach((signal) => byId.set(signal.id, signal));
  current.forEach((signal) => byId.set(signal.id, signal));
  return Array.from(byId.values())
    .sort((left, right) => {
      const timeDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      return timeDelta || left.id - right.id;
    })
    .slice(-120);
}

function resolveAssetUrl(path?: string | null) {
  const cleanPath = (path || '').trim();
  if (!cleanPath) return '';
  if (cleanPath.startsWith('http://') || cleanPath.startsWith('https://')) return cleanPath;
  const normalizedPath = cleanPath.startsWith('/') ? cleanPath : `/${cleanPath}`;
  if (normalizedPath.startsWith('/static')) return `${API_BASE_URL}${normalizedPath}`;
  return normalizedPath;
}

function isForecastSignal(signal: PersonalSignal) {
  return signal.type === 'forecast_full' || signal.type === 'forecast_teaser';
}

function extractCouponImageUrl(text: string) {
  const couponLine = text.match(/^Купон:\s*(\S+)/im);
  return couponLine?.[1]?.trim() || '';
}

function couponImageUrl(signal: PersonalSignal) {
  return signal.data?.coupon_image_url || extractCouponImageUrl(signal.data?.message_text || signal.text);
}

function signalHasBookmakerItems(signal: PersonalSignal) {
  return Boolean(signal.data?.bookmakers?.length);
}

function cleanForecastText(
  text: string,
  {
    hideBookmakerLines,
    hideCouponLines,
    hideMatchLinkLines,
    hideSportLines,
  }: {
    hideBookmakerLines: boolean;
    hideCouponLines: boolean;
    hideMatchLinkLines: boolean;
    hideSportLines: boolean;
  },
) {
  const lines = text.split(/\r?\n/);
  const cleanedLines: string[] = [];
  let skippingMatchLinks = false;

  lines.forEach((line) => {
    const trimmed = line.trim();

    if (hideMatchLinkLines && trimmed === 'Ссылки на матч') {
      skippingMatchLinks = true;
      return;
    }

    if (skippingMatchLinks) {
      if (!trimmed) {
        skippingMatchLinks = false;
      }
      return;
    }

    if (hideCouponLines && /^Купон:\s*(?:https?:\/\/|\/?static\/)/i.test(trimmed)) return;
    if (hideBookmakerLines && /^БК:\s*/i.test(trimmed)) return;
    if (hideSportLines && /^(?:🏟\s*)?Спорт:\s*/i.test(trimmed)) return;
    cleanedLines.push(line);
  });

  return cleanedLines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function signalText(signal: PersonalSignal) {
  const text = signal.data?.message_text?.trim() || signal.text;
  const hasBookmakers = signalHasBookmakerItems(signal);
  const hasCoupon = Boolean(couponImageUrl(signal));
  const hideSportLines = signal.type === 'forecast_teaser' || signal.type === 'announcement';
  return isForecastSignal(signal) || hideSportLines
    ? cleanForecastText(text, {
      hideBookmakerLines: isForecastSignal(signal) && hasBookmakers,
      hideCouponLines: isForecastSignal(signal) && hasCoupon,
      hideMatchLinkLines: isForecastSignal(signal) && hasBookmakers,
      hideSportLines,
    })
    : text;
}

function signalCanShowCoupon(signal: PersonalSignal) {
  return Boolean(couponImageUrl(signal));
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

export default function WebBotChat() {
  const [signals, setSignals] = useState<PersonalSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [streamState, setStreamState] = useState<'connecting' | 'online' | 'offline'>('connecting');
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const seenSignalIdsRef = useRef<Set<number>>(new Set());

  const isTma = isTelegramMiniApp();

  const appendSignal = useCallback((signal: PersonalSignal) => {
    setSignals((current) => {
      if (current.some((item) => item.id === signal.id)) return current;
      return [...current, signal].slice(-120);
    });
  }, []);

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
  }, []);

  const answerForecastRequest = async (signal: PersonalSignal, action: ForecastSignalAction) => {
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
      const kindLabel = signalKindLabel(signal);
      if (action === 'take') {
        notifySuccess(response.message || 'Заявка отправлена продажнику', kindLabel);
      } else {
        notifyInfo(response.message || 'Отказ учтен', kindLabel);
      }
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось обработать заявку');
    } finally {
      setActionBusy((current) => (current === busyKey ? null : current));
    }
  };

  useEffect(() => {
    if (isTma) return;
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
  }, [isTma]);

  useEffect(() => {
    if (isTma) return;
    let cancelled = false;

    async function loadHistory() {
      try {
        const history = await apiFetch<PersonalSignal[]>('/signals/history');
        if (!cancelled) {
          const nextSeenIds = new Set(seenSignalIdsRef.current);
          history.forEach((signal) => nextSeenIds.add(signal.id));
          seenSignalIdsRef.current = nextSeenIds;
          setSignals((current) => mergeSignals(history, current));
        }
      } catch {
        if (!cancelled) setSignals([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [isTma]);

  useEffect(() => {
    if (isTma) return;
    const token = getStoredAuthToken();
    if (!token || token === 'mock_debug_access_token') {
      setStreamState(token ? 'online' : 'offline');
      return;
    }

    const handleStatus = (event: Event) => {
      const state = (event as CustomEvent<{ state?: 'connecting' | 'online' | 'offline' }>).detail?.state;
      if (state) setStreamState(state);
    };
    const handleSignal = (event: Event) => {
      const signal = (event as CustomEvent<PersonalSignal>).detail;
      if (!signal || seenSignalIdsRef.current.has(signal.id)) return;
      seenSignalIdsRef.current.add(signal.id);
      appendSignal(signal);
    };

    window.addEventListener(WEB_SIGNAL_STATUS_EVENT, handleStatus);
    window.addEventListener(WEB_SIGNAL_EVENT, handleSignal);
    return () => {
      window.removeEventListener(WEB_SIGNAL_STATUS_EVENT, handleStatus);
      window.removeEventListener(WEB_SIGNAL_EVENT, handleSignal);
    };
  }, [appendSignal, isTma]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
  }, [signals.length]);

  if (isTma) return null;

  const emptyState = !loading && signals.length === 0;
  const statusLabel = streamState === 'online' ? 'online' : streamState === 'connecting' ? 'sync' : 'offline';

  return (
    <section
      id="web-bot-chat"
      className="mb-5 overflow-hidden rounded-3xl border border-white/10 bg-white/[0.045] shadow-glass backdrop-blur-xl"
    >
      <div className="border-b border-white/10 bg-slate-950/35 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
              <Bot className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-black text-white">Личный бот Shamrai</p>
              <div className="mt-1 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
                <Radio className={`h-3 w-3 ${streamState === 'online' ? 'text-emerald-300' : 'text-slate-500'}`} />
                <span>{statusLabel}</span>
              </div>
            </div>
          </div>

          <div className="flex min-h-[34px] shrink-0 items-center gap-2 rounded-2xl border border-emerald-300/15 bg-emerald-300/10 px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.12em] text-emerald-100">
            <ShieldCheck className="h-3.5 w-3.5" />
            <span>Push on</span>
          </div>
        </div>
      </div>

      <div ref={listRef} className="max-h-[360px] space-y-3 overflow-y-auto px-4 py-4">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Синхронизация...</span>
          </div>
        )}

        {emptyState && (
          <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-4 text-center">
            <ShieldCheck className="mx-auto h-6 w-6 text-emerald-300" />
            <p className="mt-2 text-sm font-black text-white">Канал готов</p>
          </div>
        )}

        {signals.map((signal, index) => (
          <motion.div
            key={signal.id}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.22, delay: Math.min(index, 8) * 0.015 }}
            className="flex items-start gap-3"
          >
            <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/70 text-emerald-200">
              {signal.type === 'live_signal' ? <Zap className="h-4 w-4 text-rose-200" /> : <Bot className="h-4 w-4" />}
            </div>
            <div className={`min-w-0 rounded-2xl border px-3.5 py-3 ${signalAccent(signal.type)}`}>
              {signalCanShowCoupon(signal) && (
                <div className="mb-3 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/50">
                  <div className="flex items-center gap-1.5 border-b border-white/10 px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/60">
                    <ImageIcon className="h-3.5 w-3.5 text-emerald-200" />
                    <span>Скрин купона</span>
                  </div>
                  <img
                    src={resolveAssetUrl(couponImageUrl(signal))}
                    alt="Скрин купона"
                    className="max-h-[420px] w-full object-contain"
                    loading="lazy"
                  />
                </div>
              )}

              <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
                {renderTextWithLinks(signalText(signal))}
              </p>

              {Boolean(signal.data?.bookmakers?.length) && (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {signal.data?.bookmakers?.map((bookmaker) => {
                    const url = bookmaker.url?.trim();
                    const content = (
                      <>
                        <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="rounded-full" />
                        <span className="min-w-0 flex-1 truncate text-left">{bookmaker.name}</span>
                        {url && <ExternalLink className="h-3.5 w-3.5 shrink-0 text-cyan-100/80" />}
                      </>
                    );
                    return url ? (
                      <a
                        key={bookmaker.id}
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex min-h-[42px] min-w-0 items-center gap-2 rounded-2xl border border-cyan-200/20 bg-cyan-200/10 px-2.5 py-1.5 text-xs font-black text-white transition-all hover:border-cyan-200/45 hover:bg-cyan-200/15 active:scale-[0.98]"
                      >
                        {content}
                      </a>
                    ) : (
                      <span
                        key={bookmaker.id}
                        className="inline-flex min-h-[42px] min-w-0 items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.055] px-2.5 py-1.5 text-xs font-black text-white/75"
                      >
                        {content}
                      </span>
                    );
                  })}
                </div>
              )}

              {signalCanShowForecastActions(signal) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void answerForecastRequest(signal, 'take')}
                    disabled={Boolean(actionBusy)}
                    className="inline-flex min-h-[38px] items-center gap-2 rounded-xl border border-emerald-300/35 bg-emerald-400/15 px-3 py-2 text-xs font-black text-emerald-50 transition-all hover:bg-emerald-400/25 active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
                  >
                    {actionBusy === `${signal.data?.forecast_request_id}:take` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    <span>{signalActionLabel(signal)}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void answerForecastRequest(signal, 'decline')}
                    disabled={Boolean(actionBusy)}
                    className="inline-flex min-h-[38px] items-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-slate-100 transition-all hover:bg-white/[0.1] active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
                  >
                    {actionBusy === `${signal.data?.forecast_request_id}:decline` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
                    <span>Не взять</span>
                  </button>
                </div>
              )}
              {!signalCanShowForecastActions(signal) && signal.data?.forecast_request_id && forecastStatusLabel(signal) && (
                <p className="mt-3 inline-flex rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.12em] text-white/65">
                  {forecastStatusLabel(signal)}
                </p>
              )}
              <p className="mt-2 text-[10px] font-black uppercase tracking-[0.14em] text-white/45">
                {signalTime(signal.created_at)}
              </p>
            </div>
          </motion.div>
        ))}
      </div>

      {streamState === 'offline' && (
        <div className="flex items-center gap-2 border-t border-white/10 bg-slate-950/45 px-4 py-2 text-[11px] font-bold text-slate-400">
          <WifiOff className="h-3.5 w-3.5 text-rose-300" />
          <span>Переподключение...</span>
        </div>
      )}
    </section>
  );
}
