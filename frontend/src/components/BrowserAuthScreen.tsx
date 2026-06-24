import React, { useState } from 'react';
import { ExternalLink, Loader2, LogIn, MessageCircle, ShieldCheck } from 'lucide-react';
import { useAuthActions, useAuthSelector } from '../context/AuthContext';
import LogoText from './LogoText';
import TelegramAuthAssist from './TelegramAuthAssist';
import { getVkAuthCooldownStatus, getVkIdConfig } from '../utils/vkId';
import { pickPrimaryAuthProvider, type IdentityProvider } from '../utils/identityAccess';
import { trackEvent, trackPageView } from '../utils/analytics';
import { hasTelegramLaunchParams, isTelegramMiniApp } from '../utils/telegramSdk';
import { isVkMiniAppRuntime } from '../utils/vkDelivery';

export default function BrowserAuthScreen() {
  const error = useAuthSelector((state) => state.error);
  const loading = useAuthSelector((state) => state.loading);
  const { loginWithVk, loginWithTelegramBot } = useAuthActions();
  const [vkBusy, setVkBusy] = useState(false);
  const [vkCooldown, setVkCooldown] = useState(() => getVkAuthCooldownStatus());
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [telegramAuthBotUrl, setTelegramAuthBotUrl] = useState<string | null>(null);
  const vkConfig = getVkIdConfig();
  const botUsername = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || 'Shamra1_bot';
  const cleanBotUsername = botUsername.replace(/^@/, '');
  const telegramBotUrl = `https://t.me/${cleanBotUsername}`;
  const showVkLogin = vkConfig.ready && vkConfig.originCompatible;
  const secureAppUrl = vkConfig.canonicalAppUrl || 'https://shamra1.pro/app/';
  const vkLoginDisabled = vkBusy || loading || vkCooldown.active;
  const primaryProvider = pickPrimaryAuthProvider({
    runsInTelegramMiniApp: isTelegramMiniApp(),
    hasTelegramLaunchParams: hasTelegramLaunchParams(),
    runsInVkApp: isVkMiniAppRuntime(),
    vkReady: showVkLogin,
    vkOriginCompatible: vkConfig.originCompatible,
    telegramAvailable: Boolean(botUsername),
  });
  const secondaryProvider: IdentityProvider = primaryProvider === 'telegram' ? 'vk' : 'telegram';

  React.useEffect(() => {
    trackPageView('/auth', {
      vk_ready: showVkLogin,
      telegram_ready: Boolean(botUsername),
    });
  }, [botUsername, showVkLogin]);

  React.useEffect(() => {
    if (!vkCooldown.active) return undefined;
    const timer = window.setInterval(() => {
      setVkCooldown(getVkAuthCooldownStatus());
    }, 5000);
    return () => window.clearInterval(timer);
  }, [vkCooldown.active]);

  const handleVkLogin = async () => {
    const cooldown = getVkAuthCooldownStatus();
    setVkCooldown(cooldown);
    if (!vkConfig.ready || cooldown.active) return;
    try {
      setVkBusy(true);
      trackEvent('Auth Started', { provider: 'vk' });
      await loginWithVk();
    } catch {
      setVkCooldown(getVkAuthCooldownStatus());
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

  const handlePrimaryLogin = () => (
    primaryProvider === 'vk' ? handleVkLogin() : handleTelegramLogin()
  );

  const renderVkAction = (variant: 'primary' | 'secondary') => {
    const content = (
      <>
        {vkBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
        <span>
          {variant === 'primary'
            ? (vkCooldown.active ? `VK ID через ${Math.ceil(vkCooldown.remainingSeconds / 60)} мин.` : 'Начать')
            : (vkCooldown.active ? `VK ID через ${Math.ceil(vkCooldown.remainingSeconds / 60)} мин.` : 'Войти через VK ID')}
        </span>
      </>
    );

    if (showVkLogin) {
      return (
        <button
          type="button"
          onClick={variant === 'primary' ? handlePrimaryLogin : handleVkLogin}
          disabled={vkLoginDisabled}
          className={`auth-readable-action shamrai-glass-button group relative flex w-full items-center justify-center gap-2 overflow-hidden px-4 text-sm font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55 ${
            variant === 'primary' ? 'min-h-[54px] rounded-2xl py-3.5' : 'min-h-[46px] rounded-xl py-2.5'
          }`}
        >
          {content}
        </button>
      );
    }

    if (vkConfig.configured) {
      return (
        <a
          href={secureAppUrl}
          className={`auth-readable-action shamrai-glass-button group relative flex w-full items-center justify-center gap-2 overflow-hidden px-4 text-sm font-black text-white transition-all active:scale-[0.98] ${
            variant === 'primary' ? 'min-h-[54px] rounded-2xl py-3.5' : 'min-h-[46px] rounded-xl py-2.5'
          }`}
        >
          <ExternalLink className="h-4 w-4" />
          <span>{variant === 'primary' ? 'Начать' : 'Открыть защищенный вход'}</span>
        </a>
      );
    }

    if (variant === 'secondary') return null;

    return (
      <button
        type="button"
        disabled
        className="auth-readable-action shamrai-glass-button group relative flex min-h-[54px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl px-4 py-3.5 text-sm font-black text-white opacity-55"
      >
        <LogIn className="h-4 w-4" />
        <span>Начать</span>
      </button>
    );
  };

  const renderTelegramAction = (variant: 'primary' | 'secondary') => {
    if (!botUsername) return null;

    return (
      <button
        type="button"
        onClick={variant === 'primary' ? handlePrimaryLogin : handleTelegramLogin}
        disabled={telegramBusy || loading}
        className={`auth-readable-action shamrai-glass-button group relative flex w-full items-center justify-center gap-2 overflow-hidden px-4 text-sm font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55 ${
          variant === 'primary' ? 'min-h-[54px] rounded-2xl py-3.5' : 'min-h-[46px] rounded-xl py-2.5'
        }`}
      >
        {telegramBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4" />}
        <span>
          {telegramBusy
            ? 'Ожидаем Start в Telegram'
            : variant === 'primary' ? 'Начать' : 'Войти через Telegram'}
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
            {primaryProvider === 'vk' ? renderVkAction('primary') : renderTelegramAction('primary')}

            {telegramBusy && <TelegramAuthAssist botUrl={telegramAuthBotUrl} />}

            <div className="grid gap-2">
              {secondaryProvider === 'vk' ? renderVkAction('secondary') : renderTelegramAction('secondary')}
              {secondaryProvider === 'vk' && !showVkLogin && !vkConfig.configured && (
                <p className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-[11px] font-semibold text-amber-200">
                  VK ID не настроен для этой сборки.
                </p>
              )}
            </div>

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

          {(error || telegramError) && (
            <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 px-3.5 py-3 text-xs font-semibold leading-relaxed text-rose-100">
              {telegramError || error}
            </div>
          )}

          {vkCooldown.active && vkCooldown.message && !error && (
            <div className="rounded-2xl border border-amber-400/20 bg-amber-400/10 px-3.5 py-3 text-xs font-semibold leading-relaxed text-amber-100">
              {vkCooldown.message}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
