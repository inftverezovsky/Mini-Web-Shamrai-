import React, { useState } from 'react';
import { ExternalLink, Loader2, MessageCircle, ShieldCheck } from 'lucide-react';
import { useAuthActions, useAuthSelector } from '../context/AuthContext';
import LogoText from './LogoText';
import TelegramAuthAssist from './TelegramAuthAssist';
import { trackEvent, trackPageView } from '../utils/analytics';

export default function BrowserAuthScreen() {
  const error = useAuthSelector((state) => state.error);
  const loading = useAuthSelector((state) => state.loading);
  const { loginWithTelegramBot } = useAuthActions();
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [telegramAuthBotUrl, setTelegramAuthBotUrl] = useState<string | null>(null);
  const botUsername = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || 'Shamra1_bot';
  const cleanBotUsername = botUsername.replace(/^@/, '');
  const telegramBotUrl = `https://t.me/${cleanBotUsername}`;

  React.useEffect(() => {
    trackPageView('/auth', {
      telegram_first: true,
      telegram_ready: Boolean(botUsername),
      post_login_recommendations: 'vk_web_push',
    });
  }, [botUsername]);

  const handleTelegramLogin = async () => {
    try {
      setTelegramBusy(true);
      setTelegramError(null);
      setTelegramAuthBotUrl(null);
      trackEvent('Auth Started', { provider: 'telegram' });
      await loginWithTelegramBot({
        onSessionStarted: (session) => setTelegramAuthBotUrl(session.botUrl),
      });
    } catch (err: any) {
      setTelegramError(err?.message || 'Не удалось войти через Telegram');
      trackEvent('Auth Failed', { provider: 'telegram' });
    } finally {
      setTelegramBusy(false);
    }
  };

  const renderTelegramAction = () => {
    if (!botUsername) return null;

    return (
      <button
        type="button"
        onClick={handleTelegramLogin}
        disabled={telegramBusy || loading}
        className="auth-readable-action shamrai-glass-button group relative flex min-h-[54px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl px-4 py-3.5 text-sm font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
      >
        {telegramBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4" />}
        <span>
          {telegramBusy
            ? 'Ожидаем Start в Telegram'
            : 'Войти через Telegram'}
        </span>
      </button>
    );
  };

  return (
    <div className="app-shell start-screen compact-ui relative z-10 flex min-h-screen items-center justify-center px-4 py-8 text-slate-50">
      <div className="ambient-field" aria-hidden="true">
        <div className="ambient-field__grid" />
        <div className="ambient-field__rings" />
      </div>

      <section className="shamrai-glass-panel relative z-10 w-full max-w-[430px] overflow-hidden rounded-[2rem] p-5">
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent" />

        <div className="space-y-5">
          <div className="flex items-center gap-3">
            <div className="shamrai-glass-button grid h-12 w-12 place-items-center rounded-2xl text-cyan-200">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div className="min-w-0">
              <LogoText className="h-8 w-32" ariaLabel="Shamrai" width={160} height={48} />
              <h1 className="auth-readable-title mt-1 text-xl font-black leading-tight text-white">
                Вход в кабинет
              </h1>
            </div>
          </div>

          <div className="space-y-4">
            {renderTelegramAction()}

            {telegramBusy && <TelegramAuthAssist botUrl={telegramAuthBotUrl} />}

            <p className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-center text-[11px] font-semibold leading-relaxed text-slate-300">
              VK и Web Push подключаются после входа в кабинете.
            </p>

            {botUsername ? (
              <a
                href={telegramBotUrl}
                target="_blank"
                rel="noreferrer"
                className="mx-auto flex w-fit items-center justify-center gap-1.5 px-2 text-[11px] font-bold text-slate-400 transition hover:text-cyan-100"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                <span>Открыть бота без входа</span>
              </a>
            ) : (
              <p className="text-[11px] font-semibold text-amber-200">
                Не указан `VITE_TELEGRAM_BOT_USERNAME`.
              </p>
            )}
          </div>

          {(telegramError || (!telegramBusy && error)) && (
            <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 px-3.5 py-3 text-xs font-semibold leading-relaxed text-rose-100">
              {telegramError || error}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
