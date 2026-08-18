import React, { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/api';
import { SubscriptionPlanResponse } from '../../schemas/schemas';
import { Eye, EyeOff, Loader2, Save, Ticket, ToggleLeft, ToggleRight, Trash2, Users } from 'lucide-react';
import EmojiTextField from '../../components/EmojiTextField';
import { notifyError, notifySuccess } from '../../utils/notify';

export default function AdminPlans() {
  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [targetFlats, setTargetFlats] = useState('3');
  const [price, setPrice] = useState('990');
  const [isHidden, setIsHidden] = useState(true);
  const [allowlist, setAllowlist] = useState('');
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

    const allowedUserIds = Array.from(new Set(
      allowlist
        .split(/[\s,;]+/)
        .filter(Boolean)
        .map(value => Number(value)),
    ));
    if (allowedUserIds.some(value => !Number.isSafeInteger(value) || value <= 0)) {
      notifyError('ID клиентов должны быть положительными целыми числами');
      return;
    }
    if (isHidden && allowedUserIds.length === 0) {
      notifyError('Для скрытого тестового тарифа добавьте хотя бы одного клиента');
      return;
    }

    try {
      setSaving(true);
      await apiFetch('/subscriptions/plans', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          duration_days: 0,
          match_count: 1,
          entitlement_type: 'flat',
          target_flats: targetFlats.replace(',', '.'),
          price: parseFloat(price),
          price_stars: 0,
          currency: 'RUB',
          is_active: true,
          is_hidden: isHidden,
          allowed_user_ids: allowedUserIds,
        }),
      });
      setName('');
      setTargetFlats('3');
      setPrice('990');
      setIsHidden(true);
      setAllowlist('');
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

  const toggleVisibility = async (plan: SubscriptionPlanResponse) => {
    const nextHidden = !plan.is_hidden;
    if (!nextHidden && !window.confirm('Сделать тариф публичным? Он станет доступен после включения общего флага продаж.')) return;
    try {
      await apiFetch(`/subscriptions/plans/${plan.id}`, {
        method: 'PUT',
        body: JSON.stringify({ is_hidden: nextHidden }),
      });
      await loadPlans();
      notifySuccess(nextHidden ? 'Тариф оставлен только для тестовой группы' : 'Тариф подготовлен к публичному запуску');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось изменить видимость тарифа');
    }
  };

  const editAllowlist = async (plan: SubscriptionPlanResponse) => {
    try {
      const current = await apiFetch(`/subscriptions/plans/${plan.id}/allowlist`);
      const entered = window.prompt(
        'Telegram ID клиентов через запятую',
        (current.allowed_user_ids || []).join(', '),
      );
      if (entered === null) return;
      const allowedUserIds = Array.from(new Set(
        entered.split(/[\s,;]+/).filter(Boolean).map(value => Number(value)),
      ));
      if (allowedUserIds.some(value => !Number.isSafeInteger(value) || value <= 0)) {
        notifyError('ID клиентов должны быть положительными целыми числами');
        return;
      }
      await apiFetch(`/subscriptions/plans/${plan.id}/allowlist`, {
        method: 'PUT',
        body: JSON.stringify({ allowed_user_ids: allowedUserIds }),
      });
      notifySuccess('Тестовая группа обновлена');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось обновить тестовую группу');
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
      `Архивировать абонемент «${plan.name}»? История покупок сохранится.`,
    );
    if (!confirmed) return;

    try {
      setDeletingPlanId(plan.id);
      await apiFetch(`/subscriptions/plans/${plan.id}`, { method: 'DELETE' });
      await loadPlans();
      notifySuccess('Абонемент архивирован');
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
              min="0.01"
              step="0.01"
              value={targetFlats}
              onChange={e => setTargetFlats(e.target.value)}
              placeholder="Цель, флетов"
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
          <label className="flex items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-amber-100">
            <input
              type="checkbox"
              checked={isHidden}
              onChange={event => setIsHidden(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="block font-black">Скрытый тестовый тариф</span>
              <span className="mt-0.5 block text-[10px] text-amber-200/70">Виден и доступен только клиентам из тестовой группы.</span>
            </span>
          </label>
          {isHidden && (
            <input
              value={allowlist}
              onChange={event => setAllowlist(event.target.value)}
              placeholder="Telegram ID клиентов: 123, 456"
              className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-white focus:border-indigo-500/50 focus:outline-none"
            />
          )}
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
              <div className="flex items-center gap-2 text-white font-black">
                <span>{plan.name}</span>
                {plan.is_hidden && <span className="rounded-full bg-amber-400/10 px-2 py-0.5 text-[9px] text-amber-300">Тестовый</span>}
              </div>
              <div className="text-slate-400 mt-1">
                {plan.entitlement_type === 'flat'
                  ? `Цель +${Number(plan.target_flats || 0).toLocaleString('ru-RU')} флета`
                  : `${plan.match_count} матчей (старый тариф)`}
                {' • '}{Number(plan.price).toLocaleString('ru-RU')} ₽
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
                <span>{plan.is_active ? 'Активен' : 'Архив'}</span>
              </button>
              <button
                type="button"
                onClick={() => toggleVisibility(plan)}
                className="flex items-center space-x-1 rounded-xl bg-amber-400/10 px-3 py-2 font-black text-amber-300"
              >
                {plan.is_hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                <span>{plan.is_hidden ? 'Тестовый' : 'Публичный'}</span>
              </button>
              {plan.is_hidden && (
                <button
                  type="button"
                  onClick={() => editAllowlist(plan)}
                  className="flex items-center space-x-1 rounded-xl bg-indigo-500/10 px-3 py-2 font-black text-indigo-300"
                >
                  <Users className="h-4 w-4" />
                  <span>Клиенты</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => deletePlan(plan)}
                disabled={deletingPlanId === plan.id}
                className="flex items-center space-x-1 rounded-xl bg-slate-500/10 px-3 py-2 font-black text-slate-300 transition hover:bg-slate-500/20 disabled:opacity-50"
              >
                {deletingPlanId === plan.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                <span>В архив</span>
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
