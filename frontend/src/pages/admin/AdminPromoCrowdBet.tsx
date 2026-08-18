import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Save, Loader2, Target } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { CrowdBetResponse, CrowdBetCreate, BetResponse } from '../../schemas/schemas';
import { fetchBetsFeedPage } from '../../utils/tabPrefetch';

export default function AdminPromoCrowdBet() {
  const queryClient = useQueryClient();
  const [isCreating, setIsCreating] = useState(false);
  const [targetAmount, setTargetAmount] = useState(1000);
  const [selectedBetId, setSelectedBetId] = useState<string>('');

  const { data: crowdBets, isLoading } = useQuery({
    queryKey: ['admin-crowd-bets'],
    queryFn: () => apiFetch<CrowdBetResponse[]>('/admin/crowd-bets'),
  });

  const { data: betsData } = useQuery({
    queryKey: ['admin-bets-for-crowd'],
    queryFn: () => fetchBetsFeedPage(null),
    enabled: isCreating,
  });

  const createMutation = useMutation({
    mutationFn: (data: CrowdBetCreate) =>
      apiFetch('/admin/crowd-bets', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-crowd-bets'] });
      setIsCreating(false);
      setTargetAmount(1000);
      setSelectedBetId('');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedBetId) return alert('Выберите ставку');
    createMutation.mutate({ bet_id: selectedBetId, target_amount: targetAmount });
  };

  if (isLoading) {
    return <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-indigo-400" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h4 className="text-white font-medium">Совместные прогнозы</h4>
        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="px-4 py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center"
          >
            <Plus className="w-4 h-4 mr-2" />
            Создать сбор
          </button>
        )}
      </div>

      {isCreating && (
        <form onSubmit={handleSubmit} className="bg-white/[0.03] border border-indigo-500/30 rounded-2xl p-6 space-y-4">
          <div className="flex justify-between items-center mb-2">
            <h5 className="text-indigo-400 font-bold flex items-center">
              <Target className="w-4 h-4 mr-2" />
              Новый Совместный Прогноз (Crowd Bet)
            </h5>
            <button
              type="button"
              onClick={() => setIsCreating(false)}
              className="text-slate-400 hover:text-white"
            >
              Отмена
            </button>
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-1">Цель сбора (XTR - Telegram Stars)</label>
            <input
              type="number"
              required
              value={targetAmount}
              onChange={e => setTargetAmount(Number(e.target.value))}
              min="10"
              className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">Привязанная ставка (которую мы откроем после сбора)</label>
            <div className="max-h-60 overflow-y-auto space-y-2 pr-2">
              {betsData?.items.map((bet: BetResponse) => (
                <div
                  key={bet.id}
                  onClick={() => setSelectedBetId(bet.id)}
                  className={`p-3 rounded-xl cursor-pointer border transition-colors ${
                    selectedBetId === bet.id
                      ? 'bg-indigo-500/20 border-indigo-500'
                      : 'bg-black/40 border-white/5 hover:bg-white/5'
                  }`}
                >
                  <div className="text-white font-medium">{bet.event_name}</div>
                  <div className="text-sm text-slate-400 flex justify-between mt-1">
                    <span>{bet.outcome || 'Исход'}</span>
                    <span className="text-indigo-400 font-bold">{bet.coefficient}</span>
                  </div>
                </div>
              ))}
              {!betsData?.items?.length && (
                <div className="text-center py-4 text-slate-500 text-sm">
                  Нет активных ставок в ленте. Сначала создайте ставку в разделе "Лента".
                </div>
              )}
            </div>
          </div>

          <div className="pt-4">
            <button
              type="submit"
              disabled={createMutation.isPending || !selectedBetId}
              className="w-full py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {createMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Save className="w-4 h-4 mr-2" /> Запустить Сбор</>}
            </button>
          </div>
        </form>
      )}

      {/* List */}
      <div className="grid gap-3">
        {crowdBets?.map(cb => {
          const progress = Math.min(100, Math.round((cb.current_amount / cb.target_amount) * 100));
          return (
            <div key={cb.id} className="bg-white/[0.02] border border-white/5 p-4 rounded-xl">
              <div className="flex justify-between items-start mb-4">
                <div>
                  <div className="text-white font-bold">Сбор на прогноз #{cb.bet_id.split('-')[0]}</div>
                </div>
                <span className={`px-2 py-1 rounded-md text-xs font-medium ${cb.status === 'opened' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                  {cb.status === 'opened' ? 'Сбор закрыт' : 'В процессе'}
                </span>
              </div>

              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-300 font-medium">{cb.current_amount} XTR собрано</span>
                  <span className="text-slate-500">из {cb.target_amount} XTR</span>
                </div>
                <div className="h-2 bg-black/60 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-amber-500 to-amber-300 transition-all duration-500"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
        {crowdBets?.length === 0 && (
          <div className="text-center py-8 text-slate-400 text-sm">
            Нет активных сборов
          </div>
        )}
      </div>
    </div>
  );
}
