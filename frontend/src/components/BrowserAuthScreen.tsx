import React, { useState } from 'react';
import { ExternalLink, Loader2, LogIn, MessageCircle, ShieldCheck } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { getVkIdConfig } from '../utils/vkId';
import { trackEvent, trackPageView } from '../utils/analytics';

export default function BrowserAuthScreen() {
  const { error, loading, loginWithVk, loginWithTelegramBot } = useAuth();
  const [vkBusy, setVkBusy] = useState(false);
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const vkConfig = getVkIdConfig();
  const botUsername = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || 'Shamra1_bot';
  const cleanBotUsername = botUsername.replace(/^@/, '');
  const telegramBotUrl = `https://t.me/${cleanBotUsername}`;
  const showVkLogin = vkConfig.ready && vkConfig.originCompatible;
  const secureAppUrl = vkConfig.canonicalAppUrl || 'https://shamra1.pro/app/';

  React.useEffect(() => {
    trackPageView('/auth', {
      vk_ready: showVkLogin,
      telegram_ready: Boolean(botUsername),
    });
  }, [botUsername, showVkLogin]);

  const handleVkLogin = async () => {
    if (!vkConfig.ready) return;
    try {
      setVkBusy(true);
      trackEvent('Auth Started', { provider: 'vk' });
      await loginWithVk();
    } catch {
      trackEvent('Auth Failed', { provider: 'vk' });
      // AuthContext exposes the message in-place; keep the screen available.
    } finally {
      setVkBusy(false);
    }
  };

  const handleTelegramLogin = async () => {
    try {
      setTelegramBusy(true);
      setTelegramError(null);
      trackEvent('Auth Started', { provider: 'telegram' });
      await loginWithTelegramBot();
    } catch (err: any) {
      setTelegramError(err?.message || 'Не удалось войти через Telegram');
      trackEvent('Auth Failed', { provider: 'telegram' });
    } finally {
      setTelegramBusy(false);
    }
  };

  return (
    <div className="relative z-10 flex min-h-screen items-center justify-center px-4 py-8 text-slate-50">
      <div className="ambient-field" aria-hidden="true">
        <div className="ambient-field__grid" />
        <div className="ambient-field__rings" />
      </div>

      <section className="relative w-full max-w-[430px] overflow-hidden rounded-[2rem] border border-white/10 bg-slate-950/55 p-5 shadow-glass backdrop-blur-2xl">
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent" />

        <div className="space-y-5">
          <div className="flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div className="min-w-0">
              <p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-200/80">
                Shamrai
              </p>
              <h1 className="mt-1 text-xl font-black leading-tight text-white">
                Вход в кабинет
              </h1>
            </div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-3.5 text-xs leading-relaxed text-slate-300">
            Можно начать с VK ID или Telegram. Второй канал подключается позже в профиле,
            а баланс, тарифы и настройки останутся в одном кабинете.
          </div>

          <div className="space-y-4">
            {showVkLogin ? (
              <button
                type="button"
                onClick={handleVkLogin}
                disabled={vkBusy || loading}
                className="group relative flex min-h-[50px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl border border-[#4c8dff]/35 bg-[#2787f5]/18 px-4 py-3 text-sm font-black text-white shadow-glass transition-all hover:bg-[#2787f5]/26 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
              >
                {vkBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
                <span>Войти или создать через VK ID</span>
              </button>
            ) : vkConfig.configured ? (
              <a
                href={secureAppUrl}
                className="group relative flex min-h-[50px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl border border-[#4c8dff]/35 bg-[#2787f5]/18 px-4 py-3 text-sm font-black text-white shadow-glass transition-all hover:bg-[#2787f5]/26 active:scale-[0.98]"
              >
                <ExternalLink className="h-4 w-4" />
                <span>Открыть защищенный вход</span>
              </a>
            ) : (
              <p className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-[11px] font-semibold text-amber-200">
                VK ID не настроен для этой сборки.
              </p>
            )}

            {botUsername ? (
              <>
                <button
                  type="button"
                  onClick={handleTelegramLogin}
                  disabled={telegramBusy || loading}
                  className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl border border-cyan-300/35 bg-cyan-300/14 px-4 py-3.5 text-sm font-black text-white shadow-glass transition-all hover:bg-cyan-300/22 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
                >
                  {telegramBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4" />}
                  <span>Войти или создать через Telegram</span>
                </button>

                <a
                  href={telegramBotUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mx-auto flex w-fit items-center justify-center gap-1.5 px-2 text-[11px] font-bold text-slate-400 transition hover:text-cyan-100"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span>Открыть бота без входа</span>
                </a>
              </>
            ) : (
              <p className="text-[11px] font-semibold text-amber-200">
                Не указан `VITE_TELEGRAM_BOT_USERNAME`.
              </p>
            )}
          </div>

          {(error || telegramError) && (
            <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 px-3.5 py-3 text-xs font-semibold leading-relaxed text-rose-100">
              {telegramError || error}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
