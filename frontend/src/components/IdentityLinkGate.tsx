import { useMemo, useState } from 'react';
import { Loader2, MessageCircle, Send, ShieldCheck } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { UserResponse } from '../schemas/schemas';
import { apiFetch } from '../utils/api';
import { isVkRedirectStartedError, linkVkProfile } from '../utils/vkId';

export default function IdentityLinkGate() {
  const { user, loginWithTelegramBot, setUser } = useAuth();
  const [busyProvider, setBusyProvider] = useState<'telegram' | 'vk' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const missingProviders = useMemo(() => new Set(user?.missing_identity_providers || []), [user]);
  const needsTelegram = Boolean(user && (missingProviders.has('telegram') || user.telegram_id < 0));
  const needsVk = Boolean(user && missingProviders.has('vk'));

  const refreshProfile = async () => {
    const freshProfile = await apiFetch<UserResponse>('/users/me');
    setUser(freshProfile);
  };

  const handleTelegramLink = async () => {
    try {
      setBusyProvider('telegram');
      setError(null);
      await loginWithTelegramBot();
      await refreshProfile();
    } catch (err: any) {
      setError(err?.message || 'Не удалось привязать Telegram. Попробуйте еще раз.');
    } finally {
      setBusyProvider(null);
    }
  };

  const handleVkLink = async () => {
    try {
      setBusyProvider('vk');
      setError(null);
      await linkVkProfile();
      await refreshProfile();
    } catch (err: any) {
      if (isVkRedirectStartedError(err)) return;
      setError(err?.message || 'Не удалось привязать VK. Попробуйте еще раз.');
    } finally {
      setBusyProvider(null);
    }
  };

  return (
    <main className="relative z-10 flex min-h-[calc(100dvh-2rem)] items-center justify-center px-3 py-6 text-slate-50">
      <section className="shamrai-glass-panel w-full max-w-md rounded-2xl p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border border-emerald-300/25 bg-emerald-400/12 text-emerald-100">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <div className="min-w-0">
            <h1 className="text-base font-black leading-tight text-white">Завершите вход</h1>
            <p className="mt-1 text-xs font-semibold leading-relaxed text-slate-300">
              Кабинет откроется после привязки Telegram и VK к одному профилю.
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-2.5">
          {needsTelegram && (
            <button
              type="button"
              onClick={handleTelegramLink}
              disabled={busyProvider !== null}
              className="shamrai-glass-button flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {busyProvider === 'telegram' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              <span>{busyProvider === 'telegram' ? 'Ждем Telegram...' : 'Привязать Telegram'}</span>
            </button>
          )}

          {needsVk && (
            <button
              type="button"
              onClick={handleVkLink}
              disabled={busyProvider !== null}
              className="shamrai-glass-button flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {busyProvider === 'vk' ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4" />}
              <span>{busyProvider === 'vk' ? 'Открываем VK...' : 'Привязать VK'}</span>
            </button>
          )}
        </div>

        {error && (
          <p className="mt-3 rounded-xl border border-rose-300/15 bg-rose-500/10 px-3 py-2 text-center text-xs font-bold leading-relaxed text-rose-100">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
