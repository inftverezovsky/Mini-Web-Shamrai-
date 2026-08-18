import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Save, Loader2, Users } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { PvPBattleResponse, PvPBattleCreate } from '../../schemas/schemas';

export default function AdminPromoPvP() {
  const queryClient = useQueryClient();
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<PvPBattleCreate>({
    match_name: '',
    option_a: '',
    option_b: '',
  });

  const { data: battles, isLoading } = useQuery({
    queryKey: ['admin-pvp'],
    queryFn: () => apiFetch<PvPBattleResponse[]>('/admin/pvp'),
  });

  const createMutation = useMutation({
    mutationFn: (data: PvPBattleCreate) =>
      apiFetch('/admin/pvp', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-pvp'] });
      setIsCreating(false);
      setFormData({ match_name: '', option_a: '', option_b: '' });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate(formData);
  };

  if (isLoading) {
    return <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-indigo-400" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h4 className="text-white font-medium">Активные и прошедшие PvP батлы</h4>
        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="px-4 py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center"
          >
            <Plus className="w-4 h-4 mr-2" />
            Создать батл
          </button>
        )}
      </div>

      {isCreating && (
        <form onSubmit={handleSubmit} className="bg-white/[0.03] border border-indigo-500/30 rounded-2xl p-6 space-y-4">
          <div className="flex justify-between items-center mb-2">
            <h5 className="text-indigo-400 font-bold flex items-center">
              <Users className="w-4 h-4 mr-2" />
              Новое Голосование (PvP)
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
            <label className="block text-sm text-slate-400 mb-1">Событие (например: Победитель Лиги Чемпионов)</label>
            <input
              type="text"
              required
              value={formData.match_name}
              onChange={e => setFormData({ ...formData, match_name: e.target.value })}
              className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-slate-400 mb-1">Вариант А (например: Реал Мадрид)</label>
              <input
                type="text"
                required
                value={formData.option_a}
                onChange={e => setFormData({ ...formData, option_a: e.target.value })}
                className="w-full bg-black/40 border border-indigo-500/30 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Вариант Б (например: Боруссия Д)</label>
              <input
                type="text"
                required
                value={formData.option_b}
                onChange={e => setFormData({ ...formData, option_b: e.target.value })}
                className="w-full bg-black/40 border border-emerald-500/30 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-emerald-500"
              />
            </div>
          </div>

          <div className="pt-4">
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="w-full py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center justify-center"
            >
              {createMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Save className="w-4 h-4 mr-2" /> Запустить Голосование</>}
            </button>
          </div>
        </form>
      )}

      {/* List */}
      <div className="grid gap-3">
        {battles?.map(battle => (
          <div key={battle.id} className="bg-white/[0.02] border border-white/5 p-4 rounded-xl">
            <div className="flex justify-between items-start mb-2">
              <div className="text-white font-bold">{battle.match_name}</div>
              <span className="px-2 py-1 bg-emerald-500/20 text-emerald-400 rounded-md text-xs font-medium">Активен</span>
            </div>

            <div className="flex items-center gap-2 mt-4 text-sm">
              <div className="flex-1 bg-indigo-500/20 text-indigo-300 p-2 rounded-lg text-center border border-indigo-500/30">
                {battle.option_a} <span className="font-bold ml-2">{battle.percent_a.toFixed(0)}%</span>
                <div className="text-xs text-indigo-400/50 mt-1">{battle.votes_a} голосов</div>
              </div>
              <div className="text-slate-500 font-bold px-2">VS</div>
              <div className="flex-1 bg-emerald-500/20 text-emerald-300 p-2 rounded-lg text-center border border-emerald-500/30">
                {battle.option_b} <span className="font-bold ml-2">{battle.percent_b.toFixed(0)}%</span>
                <div className="text-xs text-emerald-400/50 mt-1">{battle.votes_b} голосов</div>
              </div>
            </div>
          </div>
        ))}
        {battles?.length === 0 && (
          <div className="text-center py-8 text-slate-400 text-sm">
            Нет созданных PvP батлов
          </div>
        )}
      </div>
    </div>
  );
}
