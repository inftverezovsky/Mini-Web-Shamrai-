import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { Gift, Trash2, Plus, Loader2, Calendar, Percent, Ticket } from 'lucide-react';
import { notifyError, notifySuccess } from '../../utils/notify';

type PromoRewardType = 'discount' | 'matches';

interface PromoCodeData {
  id: number;
  code: string;
  reward_type?: PromoRewardType;
  discount_percent: number;
  matches_count?: number;
  valid_until: string;
  is_active: boolean;
}

export default function AdminMarketing() {
  const [promos, setPromos] = useState<PromoCodeData[]>([]);
  const [loading, setLoading] = useState(true);

  // Promo Code Form
  const [promoCode, setPromoCode] = useState('');
  const [promoRewardType, setPromoRewardType] = useState<PromoRewardType>('discount');
  const [discountPercent, setDiscountPercent] = useState('');
  const [matchesCount, setMatchesCount] = useState('');
  const [validUntil, setValidUntil] = useState('');
  const [submittingPromo, setSubmittingPromo] = useState(false);

  const loadMarketingData = async () => {
    try {
      setLoading(true);
      const promosList = await apiFetch('/admin/promo/list');
      setPromos(promosList);
    } catch (err) {
      console.error('Failed to load marketing dashboard stats:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMarketingData();
  }, []);

  const handleCreatePromo = async (e: React.FormEvent) => {
    e.preventDefault();
    const valueField = promoRewardType === 'discount' ? discountPercent : matchesCount;
    if (!promoCode || !valueField || !validUntil) {
      notifyError('Заполните все поля промокода');
      return;
    }

    try {
      setSubmittingPromo(true);
      
      const payload = {
        code: promoCode,
        reward_type: promoRewardType,
        discount_percent: promoRewardType === 'discount' ? Number.parseInt(discountPercent, 10) : 0,
        matches_count: promoRewardType === 'matches' ? Number.parseInt(matchesCount, 10) : 0,
        valid_until: new Date(validUntil).toISOString(),
      };

      await apiFetch('/admin/promo', {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      notifySuccess('Промокод успешно создан');
      setPromoCode('');
      setDiscountPercent('');
      setMatchesCount('');
      setValidUntil('');

      // Refresh list
      const freshPromos = await apiFetch('/admin/promo/list');
      setPromos(freshPromos);
    } catch (err: any) {
      notifyError(err.message || 'Ошибка создания промокода');
    } finally {
      setSubmittingPromo(false);
    }
  };

  const handleDeactivatePromo = async (promoId: number) => {
    try {
      await apiFetch(`/admin/promo/${promoId}/deactivate`, { method: 'POST' });
      setPromos(prev =>
        prev.map(p => (p.id === promoId ? { ...p, is_active: false } : p))
      );
      notifySuccess('Промокод деактивирован');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось деактивировать промокод');
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">

      {/* Promo Code manager */}
      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-5 rounded-2xl shadow-xl space-y-4">
        <h3 className="text-sm font-black text-white flex items-center uppercase tracking-wider">
          <Gift className="w-4.5 h-4.5 text-indigo-400 mr-2 shrink-0" />
          Управление Промокодами
        </h3>

        {/* Promo code create form */}
        <form onSubmit={handleCreatePromo} className="space-y-3.5 text-xs text-slate-350">
          <div className="grid grid-cols-2 gap-2 rounded-xl border border-white/10 bg-slate-950/30 p-1">
            <button
              type="button"
              onClick={() => setPromoRewardType('discount')}
              className={`min-h-[34px] rounded-lg px-3 text-[10px] font-black uppercase transition-all flex items-center justify-center gap-1.5 ${
                promoRewardType === 'discount'
                  ? 'bg-indigo-500 text-white shadow-glass'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Percent className="w-3.5 h-3.5" />
              <span>Скидка</span>
            </button>
            <button
              type="button"
              onClick={() => setPromoRewardType('matches')}
              className={`min-h-[34px] rounded-lg px-3 text-[10px] font-black uppercase transition-all flex items-center justify-center gap-1.5 ${
                promoRewardType === 'matches'
                  ? 'bg-indigo-500 text-white shadow-glass'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Ticket className="w-3.5 h-3.5" />
              <span>Матчи</span>
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Код купона</label>
              <input
                type="text"
                value={promoCode}
                onChange={e => setPromoCode(e.target.value)}
                placeholder="PROMO50"
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-black uppercase tracking-wider"
              />
            </div>
            <div>
              <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">
                {promoRewardType === 'discount' ? 'Скидка (%)' : 'Матчей'}
              </label>
              <input
                type="number"
                min="1"
                value={promoRewardType === 'discount' ? discountPercent : matchesCount}
                onChange={e => (
                  promoRewardType === 'discount'
                    ? setDiscountPercent(e.target.value)
                    : setMatchesCount(e.target.value)
                )}
                placeholder={promoRewardType === 'discount' ? '50' : '3'}
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
              />
            </div>
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Действует до</label>
            <input
              type="date"
              value={validUntil}
              onChange={e => setValidUntil(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
            />
          </div>

          <button
            type="submit"
            disabled={submittingPromo}
            className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-2.5 rounded-xl flex items-center justify-center space-x-1.5 transition-all shadow-neon-green"
          >
            {submittingPromo ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <Plus className="w-4 h-4 shrink-0" />
                <span>Создать промокод</span>
              </>
            )}
          </button>
        </form>

        {/* Promo code list */}
        <div className="space-y-2.5 pt-2">
          <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wider block">Список промокодов:</span>
          <div className="space-y-2 max-h-[140px] overflow-y-auto pr-1">
            {promos.length === 0 ? (
              <p className="text-center text-slate-550 text-[10px] py-4">Список промокодов пуст.</p>
            ) : (
              promos.map(p => (
                <div 
                  key={p.id}
                  className="bg-white/[0.02] border border-white/5 p-3 rounded-xl flex justify-between items-center text-xs"
                >
                  <div className="truncate pr-4 space-y-0.5">
                    <span className={`font-black tracking-wider uppercase ${p.is_active ? 'text-white' : 'text-slate-500 line-through'}`}>
                      {p.code}
                    </span>
                    <p className="text-[9px] text-slate-450 font-semibold uppercase">
                      {(p.reward_type || 'discount') === 'matches'
                        ? `Матчи: +${p.matches_count || 0}`
                        : `Скидка: ${p.discount_percent}%`}
                    </p>
                    <p className="text-[8px] text-slate-500 flex items-center uppercase font-bold">
                      <Calendar className="w-2.5 h-2.5 mr-0.5" />
                      до {new Date(p.valid_until).toLocaleDateString('ru-RU')}
                    </p>
                  </div>

                  {p.is_active ? (
                    <button
                      onClick={() => handleDeactivatePromo(p.id)}
                      className="bg-slate-500/10 border border-slate-500/20 text-slate-450 hover:bg-slate-500 hover:text-white p-1.5 rounded-lg active:scale-95 transition-all shrink-0"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  ) : (
                    <span className="text-[9px] text-slate-600 font-black uppercase tracking-wider shrink-0 select-none">Неактивен</span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

      </div>

    </div>
  );
}
