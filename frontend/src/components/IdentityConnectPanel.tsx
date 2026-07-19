import { useEffect, useMemo, useState } from 'react';
import { Link2, Loader2, MessageCircle, Send, ShieldCheck, X } from 'lucide-react';
import { useAuthActions, useAuthSelector } from '../context/AuthContext';
import { UserResponse } from '../schemas/schemas';
import { apiFetch } from '../utils/api';
import { getMissingIdentityActions, type IdentityProvider } from '../utils/identityAccess';
import { trackEvent } from '../utils/analytics';
import { isStaffRole } from '../utils/roles';
import { getVkRedirectMode, isVkRedirectStartedError, linkVkProfile } from '../utils/vkId';
import TelegramAuthAssist from './TelegramAuthAssist';

interface IdentityConnectPanelProps {
  className?: string;
  onProfileUpdated?: () => void | Promise<void>;
}

export default function IdentityConnectPanel({
  className = '',
  onProfileUpdated,
}: IdentityConnectPanelProps) {
  const user = useAuthSelector((state) => state.user);
  const { loginWithTelegramBot, setUser } = useAuthActions();
  const [busyProvider, setBusyProvider] = useState<IdentityProvider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [telegramBotUrl, setTelegramBotUrl] = useState<string | null>(null);

  const actions = useMemo(() => getMissingIdentityActions(user), [user]);
  const actionKey = actions.map((action) => action.provider).join('-') || 'complete';
  const storageKey = user ? `shamrai_identity_connect_dismissed:${user.telegram_id}:${actionKey}` : '';
  const [dismissed, setDismissed] = useState(() => (storageKey ? localStorage.getItem(storageKey) === '1' : false));

  useEffect(() => {
    setDismissed(storageKey ? localStorage.getItem(storageKey) === '1' : false);
    setError(null);
    setNotice(null);
    setTelegramBotUrl(null);
  }, [storageKey]);

  if (!user || isStaffRole(user.role) || actions.length === 0 || dismissed) return null;

  const refreshProfile = async () => {
    const freshProfile = await apiFetch<UserResponse>('/users/me');
    setUser(freshProfile);
    await onProfileUpdated?.();
    return freshProfile;
  };

  const waitForVkProfileLink = async () => {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
      const freshProfile = await refreshProfile();
      if (freshProfile.vk_user_id) return true;
    }
    return false;
  };

  const handleDismiss = () => {
    localStorage.setItem(storageKey, '1');
    setDismissed(true);
    trackEvent('Identity Connect Dismissed', {
      missing: actionKey,
    });
  };

  const handleTelegramLink = async () => {
    try {
      setBusyProvider('telegram');
      setError(null);
      setNotice(null);
      setTelegramBotUrl(null);
      trackEvent('Identity Connect Started', { provider: 'telegram' });
      await loginWithTelegramBot({
        onSessionStarted: (session) => setTelegramBotUrl(session.botUrl),
      });
      await refreshProfile();
      trackEvent('Identity Connect Confirmed', { provider: 'telegram' });
    } catch (err: any) {
      setError(err?.message || 'Не удалось привязать Telegram. Попробуйте еще раз.');
      trackEvent('Identity Connect Failed', { provider: 'telegram' });
    } finally {
      setBusyProvider(null);
    }
  };

  const handleVkLink = async () => {
    try {
      setBusyProvider('vk');
      setError(null);
      setNotice(null);
      trackEvent('Identity Connect Started', { provider: 'vk' });
      await linkVkProfile();
      await refreshProfile();
      trackEvent('Identity Connect Confirmed', { provider: 'vk' });
    } catch (err: any) {
      if (isVkRedirectStartedError(err)) {
        if (getVkRedirectMode(err) === 'external') {
          setNotice('VK ID открыт во внешнем браузере. Подтвердите вход и вернитесь сюда - профиль обновится автоматически.');
          try {
            const linked = await waitForVkProfileLink();
            if (linked) {
              setNotice('VK ID подключен.');
              trackEvent('Identity Connect Confirmed', { provider: 'vk' });
            } else {
              setError('Если VK ID уже подтвердили, обновите страницу кабинета. Иначе нажмите "Подключить VK" еще раз.');
            }
          } catch {
            setError('VK ID открыт во внешнем браузере. После подтверждения обновите страницу кабинета.');
          }
        }
        return;
      }
      setError(err?.message || 'Не удалось привязать VK. Попробуйте еще раз.');
      trackEvent('Identity Connect Failed', { provider: 'vk' });
    } finally {
      setBusyProvider(null);
    }
  };

  const handleAction = (provider: IdentityProvider) => (
    provider === 'telegram' ? handleTelegramLink() : handleVkLink()
  );

  return (
    <section className={`shamrai-glass-card relative z-10 mb-3 rounded-2xl p-3.5 ${className}`}>
      <button
        type="button"
        onClick={handleDismiss}
        className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-xl border border-white/10 bg-white/5 text-slate-300 transition hover:bg-white/10"
        aria-label="Скрыть предложение подключить канал"
      >
        <X className="h-4 w-4" />
      </button>

      <div className="flex items-start gap-3 pr-8">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl border border-emerald-300/25 bg-emerald-400/12 text-emerald-100">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-black leading-tight text-white">
            Кабинет уже открыт
          </h2>
          <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-300">
            Можно пользоваться сейчас. Второй канал подключается отдельно для доставки и восстановления доступа.
          </p>
        </div>
      </div>

      <div className="mt-3 grid gap-2">
        {actions.map((action) => {
          const busy = busyProvider === action.provider;
          const icon = action.provider === 'telegram'
            ? <Send className="h-4 w-4" />
            : <MessageCircle className="h-4 w-4" />;

          return (
            <div
              key={action.provider}
              className="rounded-xl border border-white/10 bg-white/[0.04] p-2.5"
            >
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-cyan-200/20 bg-cyan-400/10 text-cyan-100">
                  {action.provider === 'telegram' ? <Send className="h-4 w-4" /> : <Link2 className="h-4 w-4" />}
                </div>
                <div className="min-w-0">
                  <h3 className="text-xs font-black text-white">{action.title}</h3>
                  <p className="mt-0.5 text-[11px] font-semibold leading-relaxed text-slate-300">
                    {action.description}
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={() => void handleAction(action.provider)}
                disabled={busyProvider !== null}
                className="shamrai-glass-button mt-2 flex min-h-[40px] w-full items-center justify-center gap-2 rounded-xl px-3 py-2 text-xs font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
                <span>{busy ? (action.provider === 'telegram' ? 'Ожидаем Start в Telegram' : 'Открываем VK...') : action.buttonLabel}</span>
              </button>

              {busy && action.provider === 'telegram' && (
                <TelegramAuthAssist botUrl={telegramBotUrl} className="mt-2" />
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <p className="mt-3 rounded-xl border border-slate-300/15 bg-slate-500/10 px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-slate-100">
          {error}
        </p>
      )}

      {notice && !error && (
        <p className="mt-3 rounded-xl border border-cyan-300/15 bg-cyan-500/10 px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-cyan-100">
          {notice}
        </p>
      )}
    </section>
  );
}
