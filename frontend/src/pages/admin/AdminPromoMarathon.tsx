import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Save, Loader2, Trophy } from 'lucide-react';
import { apiFetch } from '../../utils/api';

export default function AdminPromoMarathon() {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    title: 'Путь к х10 от банка',
    target_multiplier: 10,
    current_multiplier: 1,
    current_step: 1,
    total_steps: 30,
  });

  const { data: marathon, isLoading } = useQuery({
    queryKey: ['admin-marathon'],
    queryFn: () => apiFetch('/admin/marathon'),
  });

  const createMutation = useMutation({
    mutationFn: (data: typeof formData) =>
      apiFetch('/admin/marathon', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-marathon'] });
      alert('Марафон обновлен!');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate(formData);
  };

  // Sync form data with fetched marathon data once loaded
  React.useEffect(() => {
    if (marathon) {
      setFormData({
        title: marathon.title || 'Путь к х10 от банка',
        target_multiplier: marathon.target_multiplier || 10,
        current_multiplier: marathon.current_multiplier || 1,
        current_step: marathon.current_step || 1,
        total_steps: marathon.total_steps || 30,
      });
    }
  }, [marathon]);

  if (isLoading) {
    return <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-indigo-400" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h4 className="text-white font-medium">Текущий Марафон Ставок</h4>
      </div>

      <form onSubmit={handleSubmit} className="bg-white/[0.03] border border-indigo-500/30 rounded-2xl p-6 space-y-4">
        <div className="flex justify-between items-center mb-2">
          <h5 className="text-indigo-400 font-bold flex items-center">
            <Trophy className="w-4 h-4 mr-2" />
            Настройки Марафона
          </h5>
        </div>

        <div>
          <label className="block text-sm text-slate-400 mb-1">Название марафона</label>
          <input
            type="text"
            required
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
            className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-slate-400 mb-1">Цель (множитель)</label>
            <input
              type="number"
              required
              step="0.1"
              value={formData.target_multiplier}
              onChange={e => setFormData({ ...formData, target_multiplier: Number(e.target.value) })}
              className="w-full bg-black/40 border border-indigo-500/30 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1">Текущий множитель</label>
            <input
              type="number"
              required
              step="0.1"
              value={formData.current_multiplier}
              onChange={e => setFormData({ ...formData, current_multiplier: Number(e.target.value) })}
              className="w-full bg-black/40 border border-emerald-500/30 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-emerald-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-slate-400 mb-1">Всего шагов (дней)</label>
            <input
              type="number"
              required
              value={formData.total_steps}
              onChange={e => setFormData({ ...formData, total_steps: Number(e.target.value) })}
              className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1">Текущий шаг</label>
            <input
              type="number"
              required
              value={formData.current_step}
              onChange={e => setFormData({ ...formData, current_step: Number(e.target.value) })}
              className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        <div className="pt-4">
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="w-full py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center justify-center"
          >
            {createMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Save className="w-4 h-4 mr-2" /> Сохранить Марафон</>}
          </button>
        </div>
      </form>
    </div>
  );
}
