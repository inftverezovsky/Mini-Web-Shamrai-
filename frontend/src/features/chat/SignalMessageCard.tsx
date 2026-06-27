import { memo, useMemo, useState, type MouseEvent } from 'react';
import { motion } from 'framer-motion';
import {
  BellRing,
  Bot,
  Check,
  ChevronRight,
  ExternalLink,
  Image as ImageIcon,
  Loader2,
  MessageCircle,
  ShieldCheck,
  X,
  Zap,
} from 'lucide-react';

import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import { ChatSignalMessageResponse } from '../../schemas/schemas';
import { API_BASE_URL } from '../../utils/api';
import { syncConnectionOnboarding } from '../../utils/connectionOnboarding';
import { notifyError, notifyInfo, notifySuccess } from '../../utils/notify';
import {
  connectionSetupActionPresentation,
  navigateToConnectionSetupAction,
  normalizeConnectionSetupActionUrl,
  shouldRunConnectionSetupInline,
  type ConnectionSetupActionPresentation,
} from '../../utils/profileSetup';
import { unlockIncomingSignalSound } from '../../utils/signalAudio';
import { getWebPushReadiness, registerWebPushSubscription } from '../../utils/webPush';

type ForecastSignalAction = 'take' | 'decline';
type SetupActionFeedbackTone = 'info' | 'success' | 'warning' | 'error';

interface SetupActionFeedback {
  tone: SetupActionFeedbackTone;
  message: string;
}

interface SignalBookmaker {
  id: number | string;
  name: string;
  code: string;
  logo_url?: string;
  url?: string;
}

interface SignalSetupAction {
  id: string;
  label: string;
  url: string;
  presentation: ConnectionSetupActionPresentation;
}

interface SignalMessageCardProps {
  signal: ChatSignalMessageResponse;
  index: number;
  actionBusy: string | null;
  onForecastAction: (signal: ChatSignalMessageResponse, action: ForecastSignalAction) => void;
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
  if (type === 'connection_setup_guide') return 'border-cyan-300/25 bg-cyan-400/10 text-cyan-50';
  if (type === 'connection_setup_complete') return 'border-emerald-300/30 bg-emerald-400/12 text-emerald-50';
  if (type === 'system') return 'border-cyan-300/25 bg-cyan-400/10 text-cyan-100';
  return 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100';
}

function signalKindLabel(signal: ChatSignalMessageResponse) {
  if (signal.data?.request_kind === 'paid_set') return 'Набор';
  return 'Прогноз';
}

function forecastStatusLabel(signal: ChatSignalMessageResponse) {
  const forecastStatus = signal.data?.forecast_status;
  const isPaidSet = signal.data?.request_kind === 'paid_set';
  if (forecastStatus === 'interested' || forecastStatus === 'processing') return 'Заявка отправлена';
  if (forecastStatus === 'sent' || forecastStatus === 'manual_sent') return isPaidSet ? 'Заявка закрыта' : 'Прогноз оформлен';
  if (forecastStatus === 'declined') return 'Отказ учтен';
  if (forecastStatus === 'cancelled') return 'Заявка отменена';
  if (forecastStatus === 'removed') return isPaidSet ? 'Набор остановлен' : 'Анонс остановлен';
  return '';
}

function signalCanShowForecastActions(signal: ChatSignalMessageResponse) {
  return (
    signal.type === 'forecast_teaser'
    && Boolean(signal.data?.forecast_request_id)
    && (signal.data?.forecast_status || 'announced') === 'announced'
  );
}

function resolveAssetUrl(path?: string | null) {
  const cleanPath = (path || '').trim();
  if (!cleanPath) return '';
  if (cleanPath.startsWith('http://') || cleanPath.startsWith('https://')) return cleanPath;
  const normalizedPath = cleanPath.startsWith('/') ? cleanPath : `/${cleanPath}`;
  if (normalizedPath.startsWith('/static')) return `${API_BASE_URL}${normalizedPath}`;
  return normalizedPath;
}

function isForecastSignal(signal: ChatSignalMessageResponse) {
  return signal.type === 'forecast_full' || signal.type === 'forecast_teaser';
}

function extractCouponImageUrl(text: string) {
  const couponLine = text.match(/^Купон:\s*(\S+)/im);
  return couponLine?.[1]?.trim() || '';
}

function couponImageUrl(signal: ChatSignalMessageResponse) {
  return signal.data?.coupon_image_url || extractCouponImageUrl(String(signal.data?.message_text || signal.text));
}

function signalHasBookmakerItems(signal: ChatSignalMessageResponse) {
  const bookmakers = signal.data?.bookmakers;
  return Array.isArray(bookmakers) && bookmakers.length > 0;
}

function safeSetupActionUrl(url: string) {
  const trimmedUrl = url.trim();
  if (!trimmedUrl) return '';
  if (trimmedUrl.startsWith('/')) return trimmedUrl;
  try {
    const baseUrl = typeof window === 'undefined' ? 'https://shamra1.pro' : window.location.origin;
    const parsed = new URL(trimmedUrl, baseUrl);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? trimmedUrl : '';
  } catch {
    return '';
  }
}

function setupActions(signal: ChatSignalMessageResponse): SignalSetupAction[] {
  const actions = signal.data?.setup_actions;
  if (!Array.isArray(actions)) return [];
  return actions.flatMap((action, index) => {
    const label = typeof action?.label === 'string' ? action.label.trim() : '';
    const id = typeof action?.id === 'string' && action.id.trim() ? action.id.trim() : `setup-${index}`;
    const rawUrl = typeof action?.url === 'string' ? action.url : '';
    const url = safeSetupActionUrl(normalizeConnectionSetupActionUrl(id, rawUrl));
    if (!label || !url) return [];
    return [{
      id,
      label,
      url,
      presentation: connectionSetupActionPresentation(id, label),
    }];
  });
}

function isPlainLeftClick(event: MouseEvent<HTMLAnchorElement>) {
  return (
    !event.defaultPrevented
    && event.button === 0
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
    && !event.shiftKey
  );
}

function setupActionIcon(actionId: string, busy: boolean, feedback?: SetupActionFeedback) {
  if (busy) return <Loader2 className="h-4 w-4 animate-spin" />;
  if (feedback?.tone === 'success') return <Check className="h-4 w-4" />;
  if (actionId === 'enable-web-push') return <BellRing className="h-4 w-4" />;
  if (actionId === 'connect-telegram' || actionId === 'confirm-telegram-chat') return <MessageCircle className="h-4 w-4" />;
  if (actionId === 'connect-vk' || actionId === 'allow-vk-messages') return <ShieldCheck className="h-4 w-4" />;
  return <ExternalLink className="h-4 w-4" />;
}

function setupActionClass(feedback?: SetupActionFeedback) {
  if (feedback?.tone === 'success') return 'border-emerald-300/35 bg-emerald-400/14 hover:border-emerald-200/50';
  if (feedback?.tone === 'warning') return 'border-amber-300/30 bg-amber-400/12 hover:border-amber-200/45';
  if (feedback?.tone === 'error') return 'border-rose-300/30 bg-rose-400/12 hover:border-rose-200/45';
  return 'border-cyan-200/25 bg-cyan-200/12 hover:border-cyan-200/45 hover:bg-cyan-200/18';
}

function setupActionIconClass(feedback?: SetupActionFeedback) {
  if (feedback?.tone === 'success') return 'border-emerald-200/30 bg-emerald-300/16 text-emerald-100';
  if (feedback?.tone === 'warning') return 'border-amber-200/30 bg-amber-300/14 text-amber-100';
  if (feedback?.tone === 'error') return 'border-rose-200/30 bg-rose-300/14 text-rose-100';
  return 'border-cyan-200/25 bg-slate-950/28 text-cyan-100';
}

function feedbackMessageForWebPush(statusMessage: string) {
  const message = statusMessage.trim();
  return message || 'Открыл профиль с точной инструкцией для этого устройства.';
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

function signalText(signal: ChatSignalMessageResponse) {
  const text = String(signal.data?.message_text || '').trim() || signal.text;
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

export function signalActionNotice(signal: ChatSignalMessageResponse, action: ForecastSignalAction, fallbackMessage: string) {
  return {
    title: signalKindLabel(signal),
    message: action === 'take' ? fallbackMessage || 'Заявка отправлена Shamrai' : fallbackMessage || 'Отказ учтен',
  };
}

function SignalMessageCard({
  signal,
  index,
  actionBusy,
  onForecastAction,
}: SignalMessageCardProps) {
  const couponUrl = useMemo(() => couponImageUrl(signal), [signal]);
  const bookmakers = useMemo(
    () => (Array.isArray(signal.data?.bookmakers) ? signal.data.bookmakers as SignalBookmaker[] : []),
    [signal],
  );
  const sportType = typeof signal.data?.sport_type === 'string' ? signal.data.sport_type.trim() : '';
  const messageText = useMemo(() => signalText(signal), [signal]);
  const renderedMessage = useMemo(() => renderTextWithLinks(messageText), [messageText]);
  const connectionSetupActions = useMemo(() => setupActions(signal), [signal]);
  const [setupActionBusy, setSetupActionBusy] = useState<string | null>(null);
  const [setupActionFeedback, setSetupActionFeedback] = useState<Record<string, SetupActionFeedback>>({});

  const setActionFeedback = (
    actionId: string,
    tone: SetupActionFeedbackTone,
    message: string,
  ) => {
    setSetupActionFeedback((current) => ({
      ...current,
      [actionId]: { tone, message },
    }));
  };

  const runWebPushSetup = async (action: SignalSetupAction) => {
    setSetupActionBusy(action.id);
    setActionFeedback(action.id, 'info', 'Сейчас браузер может показать запрос разрешения.');

    try {
      void unlockIncomingSignalSound();
      const result = await registerWebPushSubscription();

      if (result.status === 'subscribed') {
        setActionFeedback(action.id, 'success', 'Готово. Сигналы будут приходить в уведомления этого устройства.');
        notifySuccess('Web Push подключен для этого устройства.', 'Уведомления готовы');
        void syncConnectionOnboarding().catch(() => undefined);
        return;
      }

      const readiness = await getWebPushReadiness().catch(() => null);
      const message = feedbackMessageForWebPush(readiness?.message || result.message);
      const isHardFailure = result.status === 'failed' || result.status === 'unsupported';
      setActionFeedback(action.id, isHardFailure ? 'error' : 'warning', message);

      if (isHardFailure) {
        notifyError(message, 'Web Push не включился');
      } else {
        notifyInfo(message, action.presentation.fallbackLabel);
      }
      navigateToConnectionSetupAction(action.id, action.url);
    } catch {
      const message = 'Не удалось включить уведомления автоматически. Открыл профиль с ручным шагом.';
      setActionFeedback(action.id, 'error', message);
      notifyError(message, 'Web Push');
      navigateToConnectionSetupAction(action.id, action.url);
    } finally {
      setSetupActionBusy(null);
    }
  };

  const handleSetupActionClick = (event: MouseEvent<HTMLAnchorElement>, action: SignalSetupAction) => {
    if (!isPlainLeftClick(event)) return;

    event.preventDefault();
    if (setupActionBusy) return;

    if (shouldRunConnectionSetupInline(action.id)) {
      void runWebPushSetup(action);
      return;
    }

    setSetupActionBusy(action.id);
    setActionFeedback(action.id, 'info', action.presentation.busyLabel);
    notifyInfo(action.presentation.caption, action.presentation.fallbackLabel);
    if (navigateToConnectionSetupAction(action.id, action.url)) {
      window.setTimeout(() => setSetupActionBusy(null), 700);
      return;
    }
    window.location.href = action.url;
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.22, delay: Math.min(index, 8) * 0.015 }}
      className="flex min-w-0 items-start gap-2 sm:gap-3"
    >
      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-slate-900/70 text-emerald-200">
        {signal.type === 'live_signal' ? <Zap className="h-4 w-4 text-rose-200" /> : <Bot className="h-4 w-4" />}
      </div>

      <div className={`min-w-0 max-w-[calc(100%_-_2.5rem)] rounded-2xl rounded-bl-md border px-3 py-3 sm:max-w-[86%] sm:px-3.5 ${signalAccent(signal.type)}`}>
        {couponUrl && (
          <div className="mb-3 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/50">
            <div className="flex items-center gap-1.5 border-b border-white/10 px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-white/60">
              <ImageIcon className="h-3.5 w-3.5 text-emerald-200" />
              <span>Скрин купона</span>
            </div>
            <img
              src={resolveAssetUrl(couponUrl)}
              alt="Скрин купона"
              className="web-bot-chat__coupon-image w-full object-contain"
              loading="lazy"
              decoding="async"
            />
          </div>
        )}

        {(sportType || bookmakers.length > 0) && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {sportType && (
              <span className="inline-flex min-h-[30px] min-w-0 items-center gap-1.5 rounded-xl border border-cyan-200/20 bg-cyan-200/10 px-2 py-1 text-[11px] font-black text-cyan-50">
                <SportIconFrame label={sportType} size="tiny" className="shrink-0 rounded-full" />
                <span className="min-w-0 truncate">{sportType}</span>
              </span>
            )}
            {bookmakers.map((bookmaker) => (
              <span
                key={`chip:${bookmaker.id}`}
                className="inline-flex min-h-[30px] min-w-0 items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.06] px-2 py-1 text-[11px] font-black text-white/85"
              >
                <BookmakerLogoFrame bookmaker={bookmaker} size="tiny" className="shrink-0 rounded-full" />
                <span className="min-w-0 truncate">{bookmaker.name}</span>
              </span>
            ))}
          </div>
        )}

        <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-relaxed">
          {renderedMessage}
        </p>

        {connectionSetupActions.length > 0 && (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {connectionSetupActions.map((action) => {
              const feedback = setupActionFeedback[action.id];
              const busy = setupActionBusy === action.id;
              const title = busy
                ? action.presentation.busyLabel
                : feedback?.tone === 'success'
                  ? action.presentation.successLabel
                  : action.presentation.title;
              const caption = feedback?.message || action.presentation.caption;
              return (
                <a
                  key={action.id}
                  href={action.url}
                  onClick={(event) => handleSetupActionClick(event, action)}
                  aria-busy={busy}
                  className={`group inline-flex min-h-[72px] min-w-0 items-center gap-2 rounded-xl border px-2.5 py-2.5 text-left text-xs text-white transition-all active:scale-[0.98] ${setupActionClass(feedback)} ${setupActionBusy && !busy ? 'pointer-events-none opacity-55' : ''}`}
                >
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${setupActionIconClass(feedback)}`}>
                    {setupActionIcon(action.id, busy, feedback)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-black leading-tight">{title}</span>
                    <span className="mt-1 block text-[10px] font-bold leading-snug text-slate-200/80">
                      {caption}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.07] px-1.5 py-1 text-[9px] font-black uppercase tracking-[0.08em] text-cyan-50/85">
                    {shouldRunConnectionSetupInline(action.id) ? '1 клик' : 'авто'}
                    <ChevronRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                  </span>
                </a>
              );
            })}
          </div>
        )}

        {bookmakers.length > 0 && (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {bookmakers.map((bookmaker) => {
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
              onClick={() => onForecastAction(signal, 'take')}
              disabled={Boolean(actionBusy)}
              className="inline-flex min-h-[38px] min-w-0 items-center gap-2 rounded-xl border border-emerald-300/35 bg-emerald-400/15 px-3 py-2 text-xs font-black text-emerald-50 transition-all hover:bg-emerald-400/25 active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
            >
              {actionBusy === `${signal.data?.forecast_request_id}:take` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              <span>Взять</span>
            </button>
            <button
              type="button"
              onClick={() => onForecastAction(signal, 'decline')}
              disabled={Boolean(actionBusy)}
              className="inline-flex min-h-[38px] min-w-0 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-slate-100 transition-all hover:bg-white/[0.1] active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
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
  );
}

export default memo(SignalMessageCard);
