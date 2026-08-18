import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../../utils/api';
import { DEBUG_AUTH_ENABLED } from '../../config/api';
import { AlertCircle, BadgeRussianRuble, CreditCard, Sparkles, Check, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import ProfitSimulator from './ProfitSimulator';
import { useAuthActions, useAuthSelector } from '../../context/AuthContext';
import { notifyError, notifyPending, notifySuccess } from '../../utils/notify';
import { trackEvent } from '../../utils/analytics';
import {
  TAB_QUERY_STALE_TIME,
  TARIFFS_QUERY_KEY,
  fetchTariffsDashboard,
  type TariffsDashboardData,
} from '../../utils/tabPrefetch';
import type { FlatSubscriptionResponse, PaymentAttemptStatusResponse } from '../../schemas/schemas';
import { parseStakeInput } from '../../components/FlatStakeModal';
import { flatSubscriptionUiState } from '../../utils/flatSubscriptionUi';
import {
  clearStoredCheckout,
  createCheckoutIntent,
  isValidCheckoutUuid,
  normalizePaymentAttempt,
  readStoredCheckout,
  resolveCheckoutAttemptId,
  writeStoredCheckout,
  type CheckoutProvider,
} from '../../utils/paymentCheckout';

interface TariffsProps {
  onSubscriptionActivated?: () => void;
}

type PromoRewardType = 'discount' | 'matches' | 'flats';

interface PromoValidationResponse {
  code: string;
  reward_type?: PromoRewardType;
  discount_percent?: number;
  matches_count?: number;
  target_flats?: string | number | null;
}

interface PromoRedeemResponse {
  code: string;
  reward_type: 'matches' | 'flats';
  matches_added: number;
  target_flats_added?: string | number | null;
  balance_before: number;
  balance_after: number;
}

export default function Tariffs({ onSubscriptionActivated }: TariffsProps) {
  const { login } = useAuthActions();
  const userProfile = useAuthSelector((state) => state.user);
  const debugCheckoutEnabled = DEBUG_AUTH_ENABLED;
  const [buying, setBuying] = useState<{ planId: number; provider: 'tegro' | 'yookassa' } | null>(null);
  const [successPopup, setSuccessPopup] = useState(false);
  const [flatAmountInput, setFlatAmountInput] = useState('');
  const [configuringFlat, setConfiguringFlat] = useState(false);
  const [checkoutAttemptId, setCheckoutAttemptId] = useState<string | null>(() => (
    resolveCheckoutAttemptId(window.location.search, readStoredCheckout())
  ));
  const handledPaymentAttemptRef = useRef<string | null>(null);

  const [promoCodeInput, setPromoCodeInput] = useState('');
  const [appliedPromo, setAppliedPromo] = useState<{ code: string; discount_percent: number } | null>(null);
  const [promoError, setPromoError] = useState<string | null>(null);
  const [validatingPromo, setValidatingPromo] = useState(false);

  const tariffsQuery = useQuery<TariffsDashboardData>({
    queryKey: TARIFFS_QUERY_KEY,
    queryFn: fetchTariffsDashboard,
    staleTime: TAB_QUERY_STALE_TIME,
  });
  const plans = tariffsQuery.data?.plans ?? [];
  const flatSubscriptionQuery = useQuery<FlatSubscriptionResponse | null>({
    queryKey: ['flat-subscription-current'],
    queryFn: () => apiFetch<FlatSubscriptionResponse | null>('/subscriptions/flats/current'),
    staleTime: 15_000,
    retry: false,
  });
  const paymentAttemptQuery = useQuery<PaymentAttemptStatusResponse>({
    queryKey: ['payment-attempt', checkoutAttemptId],
    queryFn: () => apiFetch<PaymentAttemptStatusResponse>(`/payments/attempts/${checkoutAttemptId}`),
    enabled: Boolean(checkoutAttemptId),
    retry: 1,
    refetchInterval: (query) => {
      if (!query.state.data) return 2_000;
      return normalizePaymentAttempt(query.state.data).phase === 'checking' ? 2_000 : false;
    },
    refetchIntervalInBackground: false,
  });
  const normalizedPaymentAttempt = useMemo(() => (
    paymentAttemptQuery.data ? normalizePaymentAttempt(paymentAttemptQuery.data) : null
  ), [paymentAttemptQuery.data]);
  const hasLegacyAccess = Boolean((userProfile?.matches_remaining || 0) > 0 || userProfile?.guarantee_active);
  const subscriptionUi = flatSubscriptionUiState(flatSubscriptionQuery.data, hasLegacyAccess);
  const parsedFlatAmount = useMemo(() => parseStakeInput(flatAmountInput), [flatAmountInput]);
  const referralDiscountPercent = tariffsQuery.data?.referralDiscountPercent ?? 0;
  const loading = tariffsQuery.isLoading || (tariffsQuery.isFetching && !tariffsQuery.data);
  const loadError = tariffsQuery.error
    ? 'Не удалось загрузить тарифы. Проверьте подключение и попробуйте еще раз.'
    : null;

  const handleApplyPromo = async () => {
    const trimmed = promoCodeInput.trim();
    if (!trimmed) return;

    try {
      setValidatingPromo(true);
      setPromoError(null);
      trackEvent('Promo Validate Started');

      const data = await apiFetch<PromoValidationResponse>(`/payments/promo/validate?code=${encodeURIComponent(trimmed)}`);
      const rewardType = data.reward_type || 'discount';

      if (rewardType === 'matches' || rewardType === 'flats') {
        const redeemed = await apiFetch<PromoRedeemResponse>('/payments/promo/redeem', {
          method: 'POST',
          body: JSON.stringify({ code: trimmed }),
        });

        setAppliedPromo(null);
        setPromoCodeInput('');
        setSuccessPopup(true);
        notifySuccess(rewardType === 'flats'
          ? `Промокод ${redeemed.code} применён: +${Number(redeemed.target_flats_added || 0).toFixed(2)} флета к цели.`
          : `Старый промокод ${redeemed.code} применён: +${redeemed.matches_added} матчей.`);
        trackEvent('Promo Redeem Success', {
          reward_type: rewardType,
          matches_added: redeemed.matches_added,
          target_flats_added: redeemed.target_flats_added,
          balance_after: redeemed.balance_after,
        });
        await refreshAccount();
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      const discountPercent = data.discount_percent ?? 0;
      setAppliedPromo({ code: data.code, discount_percent: discountPercent });
      trackEvent('Promo Validate Success', {
        reward_type: rewardType,
        discount_percent: discountPercent,
      });
    } catch (err: any) {
      setAppliedPromo(null);
      setPromoError(err.message || 'Неверный или истекший промокод');
      trackEvent('Promo Validate Failed');
    } finally {
      setValidatingPromo(false);
    }
  };

  const handleClearPromo = () => {
    setPromoCodeInput('');
    setAppliedPromo(null);
    setPromoError(null);
    trackEvent('Promo Cleared');
  };

  const refreshAccount = useCallback(async () => {
    await login();
    await flatSubscriptionQuery.refetch();
    if (onSubscriptionActivated) {
      onSubscriptionActivated();
    }
  }, [flatSubscriptionQuery, login, onSubscriptionActivated]);

  useEffect(() => {
    const phase = normalizedPaymentAttempt?.phase;
    if (!checkoutAttemptId || (phase !== 'succeeded' && phase !== 'configure_flat')) return;
    if (handledPaymentAttemptRef.current === checkoutAttemptId) return;
    handledPaymentAttemptRef.current = checkoutAttemptId;
    void refreshAccount();
  }, [checkoutAttemptId, normalizedPaymentAttempt?.phase, refreshAccount]);

  const dismissPaymentStatus = () => {
    clearStoredCheckout();
    setCheckoutAttemptId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete('attempt_id');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  };

  const prepareCheckout = (provider: CheckoutProvider, planId: number) => {
    const checkout = createCheckoutIntent(readStoredCheckout(), {
      provider,
      planId,
      promoCode: appliedPromo?.code,
    });
    writeStoredCheckout(checkout);
    return checkout;
  };

  const persistCheckoutAttempt = (
    checkout: ReturnType<typeof createCheckoutIntent>,
    attemptId: string | undefined,
  ) => {
    if (!isValidCheckoutUuid(attemptId)) {
      throw new Error('Платёж создан без безопасного идентификатора. Повторите попытку.');
    }
    const nextCheckout = { ...checkout, attempt_id: attemptId };
    writeStoredCheckout(nextCheckout);
    setCheckoutAttemptId(attemptId);
    return nextCheckout;
  };

  const configureFlatAmount = async () => {
    const amount = parsedFlatAmount;
    if (!amount) {
      notifyError('Укажите размер флета от 1 ₽ до 100 000 000 ₽');
      return;
    }
    try {
      setConfiguringFlat(true);
      const updated = await apiFetch<FlatSubscriptionResponse>('/subscriptions/flats/current/configure', {
        method: 'POST',
        body: JSON.stringify({ flat_amount_rub: amount }),
      });
      setFlatAmountInput('');
      notifySuccess(updated.status === 'active'
        ? 'Размер флета сохранён. Абонемент активирован.'
        : 'Размер флета сохранён. Абонемент включится после завершения старого доступа.');
      await refreshAccount();
      if (checkoutAttemptId) await paymentAttemptQuery.refetch();
    } catch (error: any) {
      notifyError(error?.message || 'Не удалось сохранить размер флета');
    } finally {
      setConfiguringFlat(false);
    }
  };

  const handleBuyTegro = async (planId: number) => {
    const plan = plans.find((item) => item.id === planId);
    const activeDiscountPercent = appliedPromo?.discount_percent ?? referralDiscountPercent;

    try {
      setBuying({ planId, provider: 'tegro' });
      const checkout = prepareCheckout('tegro', planId);
      trackEvent('Checkout Started', {
        provider: 'tegro',
        plan_id: planId,
        target_flats: plan?.target_flats,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });

      const paymentData = await apiFetch<{ mock?: boolean; attempt_id?: string; confirmation_url?: string }>('/payments/tegro/create', {
        method: 'POST',
        headers: { 'Idempotency-Key': checkout.intent_id },
        body: JSON.stringify({
          plan_id: planId,
          promo_code: appliedPromo ? appliedPromo.code : undefined,
        }),
      });
      persistCheckoutAttempt(checkout, paymentData.attempt_id);

      if (paymentData.mock) {
        if (!debugCheckoutEnabled) {
          throw new Error('Debug checkout выключен на фронте');
        }
        await apiFetch('/payments/tegro/debug-complete', {
          method: 'POST',
          body: JSON.stringify({
            plan_id: planId,
            promo_code: appliedPromo ? appliedPromo.code : undefined,
            attempt_id: paymentData.attempt_id,
          }),
        });
        setSuccessPopup(true);
        notifySuccess('Debug-оплата Tegro проведена, абонемент начислен.');
        trackEvent('Checkout Completed', {
          provider: 'tegro_debug',
          plan_id: planId,
          target_flats: plan?.target_flats,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        handleClearPromo();
        await refreshAccount();
        clearStoredCheckout();
        setCheckoutAttemptId(null);
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      if (paymentData.confirmation_url) {
        notifyPending('Сейчас откроется защищенная страница Tegro.');
        trackEvent('Checkout Redirected', {
          provider: 'tegro',
          plan_id: planId,
          target_flats: plan?.target_flats,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        window.location.href = paymentData.confirmation_url;
      } else {
        throw new Error('Tegro не вернул ссылку на оплату');
      }
    } catch (err: any) {
      notifyError(err.message || 'Ошибка оплаты через Tegro');
      trackEvent('Checkout Failed', {
        provider: 'tegro',
        plan_id: planId,
        target_flats: plan?.target_flats,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });
    } finally {
      setBuying(null);
    }
  };

  const handleBuyYooKassa = async (planId: number) => {
    const plan = plans.find((item) => item.id === planId);
    const activeDiscountPercent = appliedPromo?.discount_percent ?? referralDiscountPercent;

    try {
      setBuying({ planId, provider: 'yookassa' });
      const checkout = prepareCheckout('yookassa', planId);
      trackEvent('Checkout Started', {
        provider: 'yookassa',
        plan_id: planId,
        target_flats: plan?.target_flats,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });

      const paymentData = await apiFetch<{ mock?: boolean; attempt_id?: string; confirmation_url?: string }>('/payments/yookassa/create', {
        method: 'POST',
        headers: { 'Idempotency-Key': checkout.intent_id },
        body: JSON.stringify({
          plan_id: planId,
          promo_code: appliedPromo ? appliedPromo.code : undefined,
        }),
      });
      persistCheckoutAttempt(checkout, paymentData.attempt_id);

      if (paymentData.mock) {
        if (!debugCheckoutEnabled) {
          throw new Error('Debug checkout выключен на фронте');
        }
        await apiFetch('/payments/yookassa/debug-complete', {
          method: 'POST',
          body: JSON.stringify({
            plan_id: planId,
            promo_code: appliedPromo ? appliedPromo.code : undefined,
            attempt_id: paymentData.attempt_id,
          }),
        });
        setSuccessPopup(true);
        notifySuccess('Debug-оплата проведена, абонемент начислен.');
        trackEvent('Checkout Completed', {
          provider: 'yookassa_debug',
          plan_id: planId,
          target_flats: plan?.target_flats,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        handleClearPromo();
        await refreshAccount();
        clearStoredCheckout();
        setCheckoutAttemptId(null);
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      if (paymentData.confirmation_url) {
        notifyPending('Сейчас откроется защищенная страница ЮKassa.');
        trackEvent('Checkout Redirected', {
          provider: 'yookassa',
          plan_id: planId,
          target_flats: plan?.target_flats,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        window.location.href = paymentData.confirmation_url;
      } else {
        throw new Error('ЮKassa не вернула ссылку на оплату');
      }
    } catch (err: any) {
      notifyError(err.message || 'Ошибка оплаты через ЮKassa');
      trackEvent('Checkout Failed', {
        provider: 'yookassa',
        plan_id: planId,
        target_flats: plan?.target_flats,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });
    } finally {
      setBuying(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-3">
        <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
        <span className="text-slate-400 text-xs">Загрузка тарифной сетки...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-slide-up pb-10">
      <div className="text-center space-y-1">
        <h2 className="text-xl font-black text-white flex items-center justify-center">
          <Sparkles className="w-5 h-5 text-indigo-400 mr-2" />
          Абонементы до прибыли
        </h2>
        <p className="text-slate-400 text-xs leading-relaxed max-w-xs mx-auto">
          Выберите цель в флетах. Прогнозы доступны, пока чистая прибыль не достигнет этой цели.
        </p>
      </div>

      {successPopup && (
        <div className="bg-emerald-500/20 border border-emerald-500/35 text-emerald-400 text-xs font-semibold p-4 rounded-xl text-center shadow-neon-green animate-pulse">
          Абонемент пополнен! Цель в флетах добавлена.
        </div>
      )}

      {checkoutAttemptId && (
        <section
          className={`rounded-2xl border p-4 shadow-glass ${
            normalizedPaymentAttempt?.phase === 'failed' || paymentAttemptQuery.isError
              ? 'border-rose-300/25 bg-rose-300/10'
              : normalizedPaymentAttempt?.phase === 'succeeded' || normalizedPaymentAttempt?.phase === 'configure_flat'
                ? 'border-emerald-300/25 bg-emerald-300/10'
                : 'border-cyan-300/25 bg-cyan-300/10'
          }`}
          aria-live="polite"
          data-testid="payment-return-status"
        >
          <div className="flex items-start gap-3">
            {normalizedPaymentAttempt?.phase === 'failed' || paymentAttemptQuery.isError
              ? <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-200" />
              : normalizedPaymentAttempt?.phase === 'succeeded' || normalizedPaymentAttempt?.phase === 'configure_flat'
                ? <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-200" />
                : <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-cyan-200" />}
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-black text-white">
                {normalizedPaymentAttempt?.phase === 'failed' || paymentAttemptQuery.isError
                  ? 'Оплата не подтверждена'
                  : normalizedPaymentAttempt?.phase === 'succeeded' || normalizedPaymentAttempt?.phase === 'configure_flat'
                    ? 'Оплата прошла'
                    : 'Проверяем оплату'}
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-slate-300">
                {paymentAttemptQuery.isError
                  ? 'Не удалось получить статус. Деньги не будут списаны повторно — просто обновите проверку.'
                  : normalizedPaymentAttempt?.phase === 'failed'
                    ? 'Платёж завершился ошибкой или был отменён. Можно проверить статус ещё раз или начать новую оплату.'
                    : normalizedPaymentAttempt?.phase === 'configure_flat'
                      ? 'Абонемент начислен. Осталось указать размер одного флета.'
                      : normalizedPaymentAttempt?.phase === 'succeeded'
                        ? 'Абонемент начислен, данные профиля обновлены.'
                        : 'Ожидаем подтверждение платёжной системы. Обычно это занимает несколько секунд.'}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {(paymentAttemptQuery.isError || normalizedPaymentAttempt?.phase === 'failed' || normalizedPaymentAttempt?.phase === 'checking') && (
                  <button
                    type="button"
                    onClick={() => void paymentAttemptQuery.refetch()}
                    disabled={paymentAttemptQuery.isFetching}
                    className="inline-flex min-h-[38px] items-center gap-2 rounded-xl border border-white/10 bg-white/[0.07] px-3 py-2 text-[10px] font-black uppercase text-white disabled:opacity-50"
                  >
                    {paymentAttemptQuery.isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    Проверить ещё раз
                  </button>
                )}
                {normalizedPaymentAttempt?.phase === 'configure_flat' && (
                  <button
                    type="button"
                    onClick={() => document.getElementById('flat-configuration-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
                    className="min-h-[38px] rounded-xl bg-emerald-300 px-3 py-2 text-[10px] font-black uppercase text-slate-950"
                  >
                    Настроить флет
                  </button>
                )}
                {(normalizedPaymentAttempt?.phase === 'failed' || normalizedPaymentAttempt?.phase === 'succeeded') && (
                  <button
                    type="button"
                    onClick={dismissPaymentStatus}
                    className="min-h-[38px] rounded-xl border border-white/10 bg-black/15 px-3 py-2 text-[10px] font-black uppercase text-slate-200"
                  >
                    {normalizedPaymentAttempt.phase === 'failed' ? 'Начать заново' : 'Закрыть'}
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>
      )}

      {flatSubscriptionQuery.data?.status === 'pending_setup' && flatSubscriptionQuery.data.flat_amount_rub == null && (
        <div id="flat-configuration-card" className="rounded-2xl border border-amber-300/30 bg-amber-300/10 p-4 shadow-glass">
          <h3 className="text-sm font-black text-white">Укажите размер одного флета</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-300">
            Это ваша обычная сумма ставки. Например, если флет равен 10 000 ₽, ставка 5 000 ₽ будет учтена как 0,5 флета.
          </p>
          <form
            className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]"
            onSubmit={(event) => {
              event.preventDefault();
              void configureFlatAmount();
            }}
          >
            <input
              inputMode="decimal"
              value={flatAmountInput}
              onChange={(event) => setFlatAmountInput(event.target.value)}
              placeholder="Например: 10 000"
              className="min-w-0 rounded-xl border border-white/10 bg-slate-950/60 px-3.5 py-2.5 text-sm font-black text-white outline-none focus:border-amber-300/50"
            />
            <button
              type="submit"
              disabled={configuringFlat || !parsedFlatAmount}
              className="flex min-h-[42px] items-center justify-center gap-2 rounded-xl bg-amber-300 px-4 py-2 text-xs font-black text-slate-950 disabled:opacity-50"
            >
              {configuringFlat && <Loader2 className="h-4 w-4 animate-spin" />}
              Активировать
            </button>
          </form>
        </div>
      )}

      {flatSubscriptionQuery.data && subscriptionUi.key !== 'pending_setup' && (
        <div className={`rounded-2xl border p-4 shadow-glass ${
          subscriptionUi.tone === 'emerald'
            ? 'border-emerald-300/25 bg-emerald-300/10'
            : subscriptionUi.tone === 'amber'
              ? 'border-amber-300/25 bg-amber-300/10'
              : subscriptionUi.tone === 'cyan'
                ? 'border-cyan-300/25 bg-cyan-300/10'
                : 'border-white/10 bg-white/[0.04]'
        }`}>
          <h3 className="text-sm font-black text-white">{subscriptionUi.title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-300">{subscriptionUi.description}</p>
          {flatSubscriptionQuery.data.flat_amount_rub != null && (
            <p className="mt-2 text-[10px] font-black uppercase tracking-wider text-slate-400">
              Один флет: {Number(flatSubscriptionQuery.data.flat_amount_rub).toLocaleString('ru-RU')} ₽
            </p>
          )}
        </div>
      )}

      <div className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.04] p-4 backdrop-blur-md">
        <label className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">
          У вас есть промокод?
        </label>
        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
          <input
            type="text"
            placeholder="Введите промокод..."
            value={promoCodeInput}
            onChange={(e) => setPromoCodeInput(e.target.value)}
            disabled={appliedPromo !== null || validatingPromo}
            className="min-w-0 rounded-xl border border-white/10 bg-black/20 px-3.5 py-2.5 text-xs uppercase text-white transition-all placeholder-slate-500 focus:border-indigo-500/50 focus:outline-none"
          />
          {appliedPromo ? (
            <button
              onClick={handleClearPromo}
              className="min-h-[40px] rounded-xl border border-slate-500/30 bg-slate-500/20 px-4 py-2.5 text-xs font-bold text-slate-450 transition-all hover:bg-slate-500/30"
            >
              Сбросить
            </button>
          ) : (
            <button
              onClick={handleApplyPromo}
              disabled={validatingPromo || !promoCodeInput.trim()}
              className="min-h-[40px] rounded-xl bg-indigo-500 px-4 py-2.5 text-xs font-bold text-white shadow-glass transition-all hover:bg-indigo-600 disabled:opacity-50"
            >
              {validatingPromo ? <Loader2 className="w-4 h-4 animate-spin text-white" /> : 'Применить'}
            </button>
          )}
        </div>
        {appliedPromo && (
          <p className="text-[10px] text-emerald-400 font-bold flex items-center space-x-1 animate-pulse">
            <span>✓ Промокод {appliedPromo.code} активирован! Скидка {appliedPromo.discount_percent}%</span>
          </p>
        )}
        {!appliedPromo && referralDiscountPercent > 0 && (
          <p className="text-[10px] text-emerald-400 font-bold">
            Реферальная скидка {referralDiscountPercent}% применится автоматически к абонементу.
          </p>
        )}
        {promoError && (
          <p className="text-[10px] text-slate-400 font-semibold">
            ✗ {promoError}
          </p>
        )}
      </div>

      <div className="space-y-4">
        {(loadError || plans.length === 0) && (
          <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-center shadow-glass">
            <p className="text-sm font-black text-white">
              {loadError || 'Тарифы временно недоступны'}
            </p>
            <button
              type="button"
              onClick={() => void tariffsQuery.refetch()}
              disabled={loading}
              className="mx-auto mt-3 inline-flex min-h-[40px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-4 py-2.5 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98] disabled:cursor-wait disabled:opacity-55"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span>Повторить</span>
            </button>
          </div>
        )}

        {plans.map((plan) => {
          const isGold = Number(plan.target_flats || 0) >= 5;
          const priceRub = Number(plan.price || 0);
          const activeDiscountPercent = appliedPromo?.discount_percent ?? referralDiscountPercent;
          const hasDiscount = activeDiscountPercent > 0;
          const discountedRubPrice = hasDiscount
            ? Math.max(1, Math.round(priceRub * (100 - activeDiscountPercent) / 100))
            : priceRub;

          return (
            <div
              key={plan.id}
              className={`motion-card flex min-w-0 flex-col justify-between rounded-2xl border p-6 transition-all duration-300 hover:-translate-y-0.5 hover:scale-[1.02] ${
                isGold
                  ? 'bg-white/10 backdrop-blur-md border-indigo-500/40 shadow-glass shadow-indigo-500/5'
                  : 'bg-white/[0.04] border-white/10 backdrop-blur-sm shadow-glass'
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h4 className="text-sm font-extrabold text-white">{plan.name}</h4>
                  <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                    Цель: +{Number(plan.target_flats || 0).toLocaleString('ru-RU')} флета
                  </p>
                </div>

                <div className="shrink-0 rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-right text-emerald-300 shadow-neon-green">
                  {hasDiscount ? (
                    <div className="flex flex-col items-end leading-tight">
                      <span className="text-[9px] text-slate-400 line-through">{priceRub.toLocaleString('ru-RU')} ₽</span>
                      <span className="text-sm font-black">{discountedRubPrice.toLocaleString('ru-RU')} ₽</span>
                    </div>
                  ) : (
                    <span className="text-sm font-black">{priceRub.toLocaleString('ru-RU')} ₽</span>
                  )}
                </div>
              </div>

              <div className="my-4 space-y-2 border-y border-white/5 py-3 text-[11px] text-slate-300">
                <div className="flex items-start gap-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Ежедневные ординары и экспрессы</span>
                </div>
                <div className="flex items-start gap-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Фильтрация под БК в вашем профиле</span>
                </div>
                <div className="flex items-start gap-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Без срока и лимита прогнозов — до достижения чистой прибыли</span>
                </div>
              </div>

              <div>
                <button
                  onClick={() => handleBuyTegro(plan.id)}
                  disabled={buying !== null}
                  className="flex min-h-[44px] w-full min-w-0 items-center justify-center gap-1.5 rounded-xl bg-emerald-500 px-3 py-3 text-xs font-black text-slate-950 shadow-neon-green transition-all hover:bg-emerald-400 active:scale-[0.98] disabled:opacity-50"
                >
                  {buying?.planId === plan.id && buying.provider === 'tegro' ? (
                    <Loader2 className="w-4 h-4 animate-spin text-slate-950" />
                  ) : (
                    <>
                      <BadgeRussianRuble className="w-4 h-4" />
                      <span className="min-w-0 truncate">СБП / карта {discountedRubPrice.toLocaleString('ru-RU')} ₽</span>
                    </>
                  )}
                </button>
                <button
                  onClick={() => handleBuyYooKassa(plan.id)}
                  disabled={buying !== null}
                  className="mt-2 flex min-h-[40px] w-full min-w-0 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[10px] font-black text-slate-300 transition-all hover:bg-white/[0.08] active:scale-[0.98] disabled:opacity-50"
                >
                  {buying?.planId === plan.id && buying.provider === 'yookassa' ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-300" />
                  ) : (
                    <>
                      <CreditCard className="w-3.5 h-3.5" />
                      <span className="min-w-0 truncate">Запасной вариант: ЮKassa</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <ProfitSimulator />
    </div>
  );
}
