import React, { useEffect, useState } from 'react';
import { AlertTriangle, BellRing, Loader2, RefreshCw, ShieldCheck, Smartphone } from 'lucide-react';

import {
  getWebPushReadiness,
  looksLikeMobileDevice,
  registerPwaServiceWorker,
  registerWebPushSubscription,
  type WebPushReadinessState,
} from '../utils/webPush';

interface PwaPushGateProps {
  enabled: boolean;
  children: React.ReactNode;
}

const defaultState: WebPushReadinessState = {
  status: 'failed',
  message: 'Проверяем канал уведомлений.',
  canRequest: false,
  installed: false,
  permission: 'default',
};

export default function PwaPushGate({ enabled, children }: PwaPushGateProps) {
  const [state, setState] = useState<WebPushReadinessState>(defaultState);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const requiresMobilePushSetup = looksLikeMobileDevice();

  const refresh = async () => {
    setLoading(true);
    try {
      await registerPwaServiceWorker();
      setState(await getWebPushReadiness());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!enabled) return;
    if (!requiresMobilePushSetup) {
      void registerPwaServiceWorker();
      return;
    }
    void refresh();
  }, [enabled, requiresMobilePushSetup]);

  const requestPermission = async () => {
    setRequesting(true);
    try {
      const result = await registerWebPushSubscription();
      setState(await getWebPushReadiness());
      if (result.status !== 'subscribed') {
        setState((current) => ({ ...current, status: result.status, message: result.message }));
      }
    } finally {
      setRequesting(false);
    }
  };

  if (!enabled || !requiresMobilePushSetup || state.status === 'subscribed') {
    return <>{children}</>;
  }

  const primaryDisabled = loading || requesting || !state.canRequest;
  const primaryLabel = state.status === 'permission_required'
    ? 'Разрешить уведомления'
    : state.status === 'subscription_required' || state.status === 'failed'
      ? 'Подключить push'
      : 'Проверить снова';

  return (
    <div className="relative z-10 flex min-h-[calc(100vh-2rem)] items-center justify-center px-2 py-8">
      <section className="w-full max-w-lg overflow-hidden rounded-3xl border border-cyan-300/20 bg-slate-950/80 shadow-glass backdrop-blur-2xl">
        <div className="border-b border-white/10 bg-cyan-300/10 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-cyan-200/25 bg-cyan-200/10">
              <BellRing className="h-5 w-5 text-cyan-100" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-black text-white">Канал уведомлений Shamrai</p>
              <p className="mt-1 text-[11px] font-semibold text-cyan-100/75">Нужен для закрытых сигналов на телефоне</p>
            </div>
          </div>
        </div>

        <div className="space-y-4 px-5 py-5">
          <div className="grid gap-3 text-sm text-slate-200">
            <div className="flex gap-3 rounded-2xl border border-white/10 bg-white/[0.045] p-3">
              <Smartphone className="mt-0.5 h-4 w-4 shrink-0 text-emerald-200" />
              <span>Откройте Shamrai через иконку на экране телефона.</span>
            </div>
            <div className="flex gap-3 rounded-2xl border border-white/10 bg-white/[0.045] p-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-200" />
              <span>Разрешите системные уведомления для этого устройства.</span>
            </div>
          </div>

          <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-3 text-xs font-semibold leading-relaxed text-amber-50/90">
            {loading ? 'Проверяем состояние уведомлений...' : state.message}
          </div>

          {state.status === 'denied' && (
            <div className="flex gap-2 rounded-2xl border border-rose-300/20 bg-rose-400/10 p-3 text-xs font-semibold leading-relaxed text-rose-50/90">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Уведомления запрещены системой. Разрешите их в настройках браузера или установленного приложения Shamrai.</span>
            </div>
          )}

          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={requestPermission}
              disabled={primaryDisabled}
              className="inline-flex min-h-[46px] flex-1 items-center justify-center gap-2 rounded-2xl bg-emerald-400 px-4 py-3 text-sm font-black text-slate-950 transition-all hover:bg-emerald-300 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {requesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <BellRing className="h-4 w-4" />}
              <span>{requesting ? 'Подключаем...' : primaryLabel}</span>
            </button>
            <button
              type="button"
              onClick={refresh}
              disabled={loading || requesting}
              className="inline-flex min-h-[46px] items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.06] px-4 py-3 text-sm font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98] disabled:cursor-wait disabled:opacity-55"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span>Проверить</span>
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
