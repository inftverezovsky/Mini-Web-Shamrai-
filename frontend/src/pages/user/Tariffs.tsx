import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { DEBUG_AUTH_ENABLED } from '../../config/api';
import { SubscriptionPlanResponse } from '../../schemas/schemas';
import { BadgeRussianRuble, CreditCard, Sparkles, Check, Loader2 } from 'lucide-react';
import ProfitSimulator from './ProfitSimulator';
import { useAuth } from '../../context/AuthContext';
import { notifyError, notifyPending, notifySuccess } from '../../utils/notify';
import { trackEvent } from '../../utils/analytics';

interface TariffsProps {
  onSubscriptionActivated?: () => void;
}

export default function Tariffs({ onSubscriptionActivated }: TariffsProps) {
  const { login } = useAuth();
  const debugCheckoutEnabled = DEBUG_AUTH_ENABLED;
  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [referralDiscountPercent, setReferralDiscountPercent] = useState(0);
  const [loading, setLoading] = useState(true);
  
  // Checkout state variables
  const [buying, setBuying] = useState<{ planId: number; provider: 'tegro' | 'yookassa' } | null>(null);
  const [successPopup, setSuccessPopup] = useState(false);

  // Promo code states
  const [promoCodeInput, setPromoCodeInput] = useState('');
  const [appliedPromo, setAppliedPromo] = useState<{ code: string; discount_percent: number } | null>(null);
  const [promoError, setPromoError] = useState<string | null>(null);
  const [validatingPromo, setValidatingPromo] = useState(false);

  useEffect(() => {
    async function loadTariffs() {
      try {
        setLoading(true);
        // GET /api/subscriptions/plans
        const [tariffs, referral] = await Promise.all([
          apiFetch('/subscriptions/plans'),
          apiFetch('/users/me/referral'),
        ]);
        setPlans(tariffs);
        setReferralDiscountPercent(referral.referral_discount_percent ?? 0);
      } catch (err) {
        console.error('Failed to load tariffs list:', err);
      } finally {
        setLoading(false);
      }
    }
    loadTariffs();
  }, []);

  const handleApplyPromo = async () => {
    const trimmed = promoCodeInput.trim();
    if (!trimmed) return;

    try {
      setValidatingPromo(true);
      setPromoError(null);
      trackEvent('Promo Validate Started');

      // GET /api/payments/promo/validate?code=...
      const data = await apiFetch(`/payments/promo/validate?code=${encodeURIComponent(trimmed)}`);
      setAppliedPromo(data);
      trackEvent('Promo Validate Success', {
        discount_percent: data.discount_percent,
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

  const refreshAccount = async () => {
    await login();
    if (onSubscriptionActivated) {
      onSubscriptionActivated();
    }
  };

  const handleBuyTegro = async (planId: number) => {
    const plan = plans.find((item) => item.id === planId);
    const activeDiscountPercent = appliedPromo?.discount_percent ?? referralDiscountPercent;

    try {
      setBuying({ planId, provider: 'tegro' });
      trackEvent('Checkout Started', {
        provider: 'tegro',
        plan_id: planId,
        matches: plan?.match_count,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });

      const paymentData = await apiFetch('/payments/tegro/create', {
        method: 'POST',
        body: JSON.stringify({
          plan_id: planId,
          promo_code: appliedPromo ? appliedPromo.code : undefined
        })
      });

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
          })
        });
        setSuccessPopup(true);
        notifySuccess('Debug-оплата Tegro проведена, пакет матчей начислен.');
        trackEvent('Checkout Completed', {
          provider: 'tegro_debug',
          plan_id: planId,
          matches: plan?.match_count,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        handleClearPromo();
        await refreshAccount();
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      if (paymentData.confirmation_url) {
        notifyPending('Сейчас откроется защищенная страница Tegro.');
        trackEvent('Checkout Redirected', {
          provider: 'tegro',
          plan_id: planId,
          matches: plan?.match_count,
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
        matches: plan?.match_count,
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
      trackEvent('Checkout Started', {
        provider: 'yookassa',
        plan_id: planId,
        matches: plan?.match_count,
        has_promo: Boolean(appliedPromo),
        discount_percent: activeDiscountPercent,
      });

      const paymentData = await apiFetch('/payments/yookassa/create', {
        method: 'POST',
        body: JSON.stringify({
          plan_id: planId,
          promo_code: appliedPromo ? appliedPromo.code : undefined
        })
      });

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
          })
        });
        setSuccessPopup(true);
        notifySuccess('Debug-оплата проведена, пакет матчей начислен.');
        trackEvent('Checkout Completed', {
          provider: 'yookassa_debug',
          plan_id: planId,
          matches: plan?.match_count,
          has_promo: Boolean(appliedPromo),
          discount_percent: activeDiscountPercent,
        });
        handleClearPromo();
        await refreshAccount();
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      if (paymentData.confirmation_url) {
        notifyPending('Сейчас откроется защищенная страница ЮKassa.');
        trackEvent('Checkout Redirected', {
          provider: 'yookassa',
          plan_id: planId,
          matches: plan?.match_count,
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
        matches: plan?.match_count,
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
      
      {/* Tariffs Page Title */}
      <div className="text-center space-y-1">
        <h2 className="text-xl font-black text-white flex items-center justify-center">
          <Sparkles className="w-5 h-5 text-indigo-400 mr-2" />
          Абонементы на матчи
        </h2>
        <p className="text-slate-400 text-xs leading-relaxed max-w-xs mx-auto">
          Покупайте пакет матчей. Если прогноз проиграет, замены идут бесплатно до победы.
        </p>
      </div>

      {successPopup && (
        <div className="bg-emerald-500/20 border border-emerald-500/35 text-emerald-400 text-xs font-semibold p-4 rounded-xl text-center shadow-neon-green animate-pulse">
          Абонемент пополнен! Матчи добавлены к вашему балансу.
        </div>
      )}

      {/* Promo Code Input Block */}
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
              className="min-h-[40px] rounded-xl border border-rose-500/30 bg-rose-500/20 px-4 py-2.5 text-xs font-bold text-rose-450 transition-all hover:bg-rose-500/30"
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
          <p className="text-[10px] text-rose-400 font-semibold">
            ✗ {promoError}
          </p>
        )}
      </div>

      {/* Grid List of Tariff options */}
      <div className="space-y-4">
        {plans.map((plan) => {
          const isGold = plan.match_count >= 20;
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
              
              {/* Header: Plan Name and duration */}
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h4 className="text-sm font-extrabold text-white">{plan.name}</h4>
                  <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                    {plan.match_count} матчей в пакете
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

              {/* Features List */}
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
                  <span>Замены до победы при проигрыше</span>
                </div>
              </div>

              {/* Action checkout button */}
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

      {/* Interactive Guest Profit Simulator */}
      <ProfitSimulator />
      
    </div>
  );
}
