import React, { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/api';
import { SubscriptionPlanResponse } from '../../schemas/schemas';
import { Loader2, Save, Ticket, ToggleLeft, ToggleRight, Trash2 } from 'lucide-react';
import EmojiTextField from '../../components/EmojiTextField';
import { notifyError, notifySuccess } from '../../utils/notify';

export default function AdminPlans() {
  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [matchCount, setMatchCount] = useState('5');
  const [price, setPrice] = useState('990');
  const [successMsg, setSuccessMsg] = useState('');
  const [deletingPlanId, setDeletingPlanId] = useState<number | null>(null);

  const loadPlans = async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/subscriptions/plans?include_inactive=true');
      setPlans(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPlans();
  }, []);

  const createPlan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      notifyError('Укажите название абонемента');
      return;
    }

    try {
      setSaving(true);
      await apiFetch('/subscriptions/plans', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          duration_days: 0,
          match_count: parseInt(matchCount),
          price: parseFloat(price),
          price_stars: 0,
          currency: 'RUB',
          is_active: true,
        }),
      });
      setName('');
      setMatchCount('5');
      setPrice('990');
      setSuccessMsg('Абонемент создан');
      await loadPlans();
      setTimeout(() => setSuccessMsg(''), 3000);
      notifySuccess('Абонемент создан');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось создать абонемент');
    } finally {
      setSaving(false);
    }
  };

  const togglePlan = async (plan: SubscriptionPlanResponse) => {
    try {
      await apiFetch(`/subscriptions/plans/${plan.id}`, {
        method: 'PUT',
        body: JSON.stringify({ is_active: !plan.is_active }),
      });
      await loadPlans();
      notifySuccess(plan.is_active ? 'Абонемент скрыт' : 'Абонемент активирован');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось обновить абонемент');
    }
  };

  const deletePlan = async (plan: SubscriptionPlanResponse) => {
    const confirmed = window.confirm(
      `Удалить абонемент «${plan.name}»? История покупок сохранится, но сам абонемент исчезнет из списка.`,
    );
    if (!confirmed) return;

    try {
      setDeletingPlanId(plan.id);
      await apiFetch(`/subscriptions/plans/${plan.id}`, { method: 'DELETE' });
      await loadPlans();
      notifySuccess('Абонемент удалён');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось удалить абонемент');
    } finally {
      setDeletingPlanId(null);
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
    <div className="space-y-5">
      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-5 rounded-2xl space-y-4">
        <h3 className="text-sm font-black text-white flex items-center uppercase tracking-wider">
          <Ticket className="w-4 h-4 text-indigo-400 mr-2" />
          Новый абонемент
        </h3>
        <form onSubmit={createPlan} className="space-y-3 text-xs">
          <EmojiTextField
            value={name}
            onValueChange={setName}
            placeholder="Название абонемента"
            className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50"
          />
          <div className="grid grid-cols-2 gap-2">
            <input
              type="number"
              min="1"
              value={matchCount}
              onChange={e => setMatchCount(e.target.value)}
              placeholder="Матчи"
              className="bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50"
            />
            <input
              type="number"
              min="1"
              value={price}
              onChange={e => setPrice(e.target.value)}
              placeholder="Рубли"
              className="bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50"
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="w-full bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-slate-950 font-black py-3 rounded-xl flex items-center justify-center space-x-1.5"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            <span>Сохранить абонемент</span>
          </button>
          {successMsg && <p className="text-emerald-400 text-[10px] font-black text-center">{successMsg}</p>}
        </form>
      </div>

      <div className="space-y-3">
        {plans.map(plan => (
          <div key={plan.id} className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 text-xs sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-white font-black">{plan.name}</div>
              <div className="text-slate-400 mt-1">
                {plan.match_count} матчей • {Number(plan.price).toLocaleString('ru-RU')} ₽
              </div>
            </div>
            <div className="grid w-full shrink-0 grid-cols-2 gap-2 sm:flex sm:w-auto sm:items-center">
              <button
                type="button"
                onClick={() => togglePlan(plan)}
                className={`px-3 py-2 rounded-xl font-black flex items-center space-x-1 ${
                  plan.is_active ? 'bg-emerald-500/10 text-emerald-400' : 'bg-slate-800 text-slate-500'
                }`}
              >
                {plan.is_active ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
                <span>{plan.is_active ? 'Активен' : 'Скрыт'}</span>
              </button>
              <button
                type="button"
                onClick={() => deletePlan(plan)}
                disabled={deletingPlanId === plan.id}
                className="flex items-center space-x-1 rounded-xl bg-slate-500/10 px-3 py-2 font-black text-slate-300 transition hover:bg-slate-500/20 disabled:opacity-50"
              >
                {deletingPlanId === plan.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                <span>Удалить</span>
              </button>
            </div>
          </div>
        ))}
        {plans.length === 0 && (
          <div className="bg-white/5 border border-white/10 rounded-2xl p-6 text-center text-slate-500 text-xs">
            Абонементы еще не созданы.
          </div>
        )}
      </div>
    </div>
  );
}
