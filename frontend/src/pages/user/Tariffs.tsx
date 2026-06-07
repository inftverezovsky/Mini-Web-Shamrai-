import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { SubscriptionPlanResponse } from '../../schemas/schemas';
import { CreditCard, Sparkles, Check, Loader2 } from 'lucide-react';
import ProfitSimulator from './ProfitSimulator';
import { useAuth } from '../../context/AuthContext';
import { notifyError, notifyPending, notifySuccess } from '../../utils/notify';

interface TariffsProps {
  onSubscriptionActivated?: () => void;
}

export default function Tariffs({ onSubscriptionActivated }: TariffsProps) {
  const { login } = useAuth();
  const debugCheckoutEnabled = import.meta.env.VITE_ENABLE_DEBUG_AUTH === 'true';
  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [referralDiscountPercent, setReferralDiscountPercent] = useState(0);
  const [loading, setLoading] = useState(true);
  
  // Checkout state variables
  const [buyingId, setBuyingId] = useState<number | null>(null);
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

      // GET /api/payments/promo/validate?code=...
      const data = await apiFetch(`/payments/promo/validate?code=${encodeURIComponent(trimmed)}`);
      setAppliedPromo(data);
    } catch (err: any) {
      setAppliedPromo(null);
      setPromoError(err.message || 'Неверный или истекший промокод');
    } finally {
      setValidatingPromo(false);
    }
  };

  const handleClearPromo = () => {
    setPromoCodeInput('');
    setAppliedPromo(null);
    setPromoError(null);
  };

  const refreshAccount = async () => {
    await login();
    if (onSubscriptionActivated) {
      onSubscriptionActivated();
    }
  };

  const handleBuyYooKassa = async (planId: number) => {
    try {
      setBuyingId(planId);
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
        handleClearPromo();
        await refreshAccount();
        setTimeout(() => setSuccessPopup(false), 5000);
        return;
      }

      if (paymentData.confirmation_url) {
        notifyPending('Сейчас откроется защищенная страница ЮKassa.');
        window.location.href = paymentData.confirmation_url;
      } else {
        throw new Error('ЮKassa не вернула ссылку на оплату');
      }
    } catch (err: any) {
      notifyError(err.message || 'Ошибка оплаты через ЮKassa');
    } finally {
      setBuyingId(null);
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
      <div className="bg-white/[0.04] border border-white/10 backdrop-blur-md p-4 rounded-2xl space-y-2">
        <label className="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">
          У вас есть промокод?
        </label>
        <div className="flex space-x-2">
          <input
            type="text"
            placeholder="Введите промокод..."
            value={promoCodeInput}
            onChange={(e) => setPromoCodeInput(e.target.value)}
            disabled={appliedPromo !== null || validatingPromo}
            className="flex-grow bg-black/20 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 transition-all uppercase"
          />
          {appliedPromo ? (
            <button
              onClick={handleClearPromo}
              className="bg-rose-500/20 hover:bg-rose-500/30 text-rose-450 border border-rose-500/30 px-4 py-2.5 rounded-xl text-xs font-bold transition-all shrink-0"
            >
              Сбросить
            </button>
          ) : (
            <button
              onClick={handleApplyPromo}
              disabled={validatingPromo || !promoCodeInput.trim()}
              className="bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 text-white px-4 py-2.5 rounded-xl text-xs font-bold transition-all shrink-0 shadow-glass"
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
              className={`p-6 rounded-2xl border transition-all duration-300 hover:scale-[1.02] hover:-translate-y-0.5 motion-card flex flex-col justify-between ${
                isGold 
                  ? 'bg-white/10 backdrop-blur-md border-indigo-500/40 shadow-glass shadow-indigo-500/5' 
                  : 'bg-white/[0.04] border-white/10 backdrop-blur-sm shadow-glass'
              }`}
            >
              
              {/* Header: Plan Name and duration */}
              <div className="flex justify-between items-start">
                <div>
                  <h4 className="text-sm font-extrabold text-white">{plan.name}</h4>
                  <p className="text-slate-400 text-[10px] mt-0.5 uppercase tracking-wider font-semibold">
                    {plan.match_count} матчей в пакете
                  </p>
                </div>
                
                <div className="bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-xl text-right text-emerald-300 shadow-neon-green">
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
              <div className="my-4 pt-3.5 pb-3 border-t border-b border-white/5 space-y-2 text-[11px] text-slate-300">
                <div className="flex items-center space-x-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Ежедневные ординары и экспрессы</span>
                </div>
                <div className="flex items-center space-x-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Фильтрация под БК в вашем профиле</span>
                </div>
                <div className="flex items-center space-x-2">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Замены до победы при проигрыше</span>
                </div>
              </div>

              {/* Action checkout button */}
              <div>
                <button
                  onClick={() => handleBuyYooKassa(plan.id)}
                  disabled={buyingId !== null}
                  className="w-full bg-indigo-500 hover:bg-indigo-600 active:scale-[0.98] disabled:opacity-50 text-white text-xs font-black py-3 px-3 rounded-xl flex items-center justify-center space-x-1.5 transition-all shadow-neon-indigo"
                >
                  {buyingId === plan.id ? (
                    <Loader2 className="w-4 h-4 animate-spin text-white" />
                  ) : (
                    <>
                      <CreditCard className="w-4 h-4" />
                      <span>Оплатить {discountedRubPrice.toLocaleString('ru-RU')} ₽</span>
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
