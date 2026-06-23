import { useEffect, useState } from 'react';
import { AlertTriangle, BellRing, Check, Loader2, RefreshCw, ShieldCheck, Smartphone } from 'lucide-react';

import {
  getWebPushReadiness,
  registerPwaServiceWorker,
  registerWebPushSubscription,
  showWebPushTestNotification,
  type WebPushReadinessState,
} from '../utils/webPush';
import { notifyInfo, notifySuccess } from '../utils/notify';
import { playIncomingSignalSound, unlockIncomingSignalSound } from '../utils/signalAudio';
import { hasTelegramLaunchParams, isTelegramMiniApp } from '../utils/telegramSdk';

const defaultState: WebPushReadinessState = {
  status: 'failed',
  message: 'Проверяем канал уведомлений.',
  canRequest: false,
  installed: false,
  permission: 'default',
  subscription: 'missing',
  sound: 'unsupported',
  fallbackRecommended: false,
  platformHint: 'failed',
};

export default function WebPushSettingsCard() {
  const [state, setState] = useState<WebPushReadinessState>(defaultState);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [testing, setTesting] = useState(false);
  const runsInTelegramMiniApp = isTelegramMiniApp() || hasTelegramLaunchParams();

  const refresh = async () => {
    setLoading(true);
    try {
      await registerPwaServiceWorker();
      setState(await getWebPushReadiness());
    } catch {
      setState({
        ...defaultState,
        message: 'Push-уведомления временно недоступны. Приложение продолжит работать без них.',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (runsInTelegramMiniApp) return;
    void refresh();
  }, [runsInTelegramMiniApp]);

  if (runsInTelegramMiniApp) return null;

  const requestPermission = async () => {
    setRequesting(true);
    try {
      await unlockIncomingSignalSound();
      const result = await registerWebPushSubscription();
      setState(await getWebPushReadiness());
      if (result.status !== 'subscribed') {
        setState((current) => ({ ...current, status: result.status, message: result.message }));
      }
    } catch {
      setState({
        ...defaultState,
        message: 'Не удалось подключить push-уведомления. Можно продолжить без них.',
      });
    } finally {
      setRequesting(false);
    }
  };

  const testNotification = async () => {
    setTesting(true);
    try {
      await unlockIncomingSignalSound();
      await playIncomingSignalSound();
      const result = await showWebPushTestNotification();
      setState(await getWebPushReadiness());
      if (result.status === 'subscribed') {
        notifySuccess(result.message, 'Тест уведомлений');
      } else {
        notifyInfo(result.message, 'Тест уведомлений');
      }
    } catch {
      notifyInfo('Не удалось показать тестовое уведомление.', 'Тест уведомлений');
    } finally {
      setTesting(false);
    }
  };

  const primaryDisabled = loading || requesting || !state.canRequest;
  const primaryLabel = state.status === 'permission_required'
    ? 'Разрешить уведомления'
    : state.status === 'subscription_required' || state.status === 'failed'
      ? 'Подключить Web Push'
      : 'Проверить снова';
  const isReady = state.status === 'subscribed';
  const statusLabel = isReady
    ? 'подключено'
    : state.status === 'needs_install'
      ? 'нужна PWA'
      : state.status === 'denied'
        ? 'запрещено'
        : state.status === 'unsupported'
          ? 'не поддерживается'
          : state.status === 'missing_key'
            ? 'нет ключа'
            : 'нужно включить';
  const badgeClass = isReady
    ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-200'
    : state.fallbackRecommended
      ? 'border-amber-300/25 bg-amber-400/10 text-amber-200'
      : 'border-cyan-300/25 bg-cyan-300/10 text-cyan-100';

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
      <div className="flex items-start gap-3">
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border ${
          isReady
            ? 'border-emerald-300/20 bg-emerald-400/10 text-emerald-200'
            : 'border-cyan-300/25 bg-cyan-300/10 text-cyan-100'
        }`}>
          {isReady ? <Check className="h-5 w-5" /> : <BellRing className="h-5 w-5" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[10px] font-black uppercase tracking-wider text-slate-300">
              Web Push уведомления
            </span>
            <span className={`rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${badgeClass}`}>
              {statusLabel}
            </span>
          </div>
          <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-400">
            {loading ? 'Проверяем состояние браузера...' : state.message}
          </p>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-300">
        <div className="rounded-xl border border-white/10 bg-white/[0.045] px-3 py-2">
          Push: <span className={state.subscription === 'saved' ? 'text-emerald-200' : 'text-amber-200'}>
            {state.subscription === 'saved' ? 'saved' : 'missing'}
          </span>
        </div>
        <div className="rounded-xl border border-white/10 bg-white/[0.045] px-3 py-2">
          Sound: <span className={state.sound === 'unlocked' ? 'text-emerald-200' : 'text-amber-200'}>
            {state.sound}
          </span>
        </div>
      </div>

      {state.status === 'needs_install' && (
        <div className="mt-3 flex gap-2 rounded-xl border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-[11px] font-semibold leading-relaxed text-amber-100">
          <Smartphone className="mt-0.5 h-4 w-4 shrink-0" />
          <span>На iPhone/iPad добавьте Shamrai на экран и откройте через иконку.</span>
        </div>
      )}

      {state.status === 'denied' && (
        <div className="mt-3 flex gap-2 rounded-xl border border-rose-300/20 bg-rose-400/10 px-3 py-2 text-[11px] font-semibold leading-relaxed text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Уведомления запрещены системой. Разрешите их в настройках браузера или приложения Shamrai.</span>
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {isReady ? (
          <button
            type="button"
            onClick={testNotification}
            disabled={loading || testing}
            className="min-h-[44px] rounded-xl border border-emerald-300/25 bg-emerald-400/18 px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-emerald-400/26 active:scale-[0.98] disabled:cursor-wait disabled:opacity-55"
          >
            <span className="inline-flex items-center justify-center gap-1.5">
              {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <BellRing className="h-3.5 w-3.5" />}
              {testing ? 'Проверяем...' : 'Проверить'}
            </span>
          </button>
        ) : state.canRequest && (
          <button
            type="button"
            onClick={requestPermission}
            disabled={primaryDisabled}
            className="min-h-[44px] rounded-xl border border-cyan-300/30 bg-cyan-300/16 px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-cyan-300/24 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
          >
            <span className="inline-flex items-center justify-center gap-1.5">
              {requesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <BellRing className="h-3.5 w-3.5" />}
              {requesting ? 'Подключаем...' : primaryLabel}
            </span>
          </button>
        )}

        <button
          type="button"
          onClick={refresh}
          disabled={loading || requesting || testing}
          className="min-h-[44px] rounded-xl border border-white/10 bg-white/5 px-3 text-[10px] font-black uppercase tracking-wider text-slate-100 transition hover:bg-white/10 active:scale-[0.98] disabled:cursor-wait disabled:opacity-50"
        >
          <span className="inline-flex items-center justify-center gap-1.5">
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {loading ? 'Проверяем...' : 'Обновить'}
          </span>
        </button>
      </div>

      {state.fallbackRecommended && (
        <div className="mt-3 flex gap-2 rounded-xl border border-white/10 bg-white/[0.045] px-3 py-2 text-[11px] font-semibold leading-relaxed text-slate-300">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" />
          <span>Для резерва держите включенными Telegram и VK ниже в профиле.</span>
        </div>
      )}
    </div>
  );
}
