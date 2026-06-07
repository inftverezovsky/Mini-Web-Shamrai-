import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { Trophy, Award, Sparkles, Loader2 } from 'lucide-react';

interface MarathonData {
  id: number;
  title: string;
  target_multiplier: number;
  current_step: number;
  total_steps: number;
  is_active: boolean;
}

export default function MarathonWidget() {
  const [marathon, setMarathon] = useState<MarathonData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadMarathon() {
      try {
        setLoading(true);
        const data = await apiFetch('/marketing/marathon');
        setMarathon(data);
      } catch (err) {
        console.error('Failed to load active marathon:', err);
      } finally {
        setLoading(false);
      }
    }
    loadMarathon();
  }, []);

  if (loading) {
    return (
      <div className="bg-white/[0.03] border border-white/10 backdrop-blur-md p-4 rounded-3xl flex justify-center py-6 shadow-glass">
        <Loader2 className="w-5 h-5 text-emerald-500 animate-spin" />
      </div>
    );
  }

  if (!marathon) return null;

  const percentage = (marathon.current_step / marathon.total_steps) * 100;

  return (
    <div className="bg-white/[0.04] border border-white/15 backdrop-blur-md p-5 rounded-3xl relative overflow-hidden shadow-glass space-y-4">
      {/* Background glowing blob */}
      <div className="absolute top-[-30%] left-[-20%] w-24 h-24 bg-emerald-500/10 rounded-full blur-2xl pointer-events-none"></div>

      {/* Header section with icons */}
      <div className="flex justify-between items-center text-xs">
        <div className="flex items-center space-x-2">
          <Trophy className="w-5 h-5 text-amber-400 fill-amber-400/20 shrink-0" />
          <div>
            <h4 className="font-black text-white text-xs leading-none uppercase tracking-wide">
              {marathon.title}
            </h4>
            <span className="text-[8px] text-slate-400 font-bold block mt-1 uppercase tracking-wider">
              Общая цель: х{marathon.target_multiplier} от банка
            </span>
          </div>
        </div>
        <div className="bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 rounded-xl text-emerald-400 text-[9px] font-black shadow-neon-green flex items-center space-x-1 uppercase tracking-wider shrink-0 select-none animate-pulse">
          <Sparkles className="w-3 h-3" />
          <span>Марафон</span>
        </div>
      </div>

      {/* Status metrics display */}
      <div className="flex justify-between items-baseline text-xs pt-1">
        <span className="text-slate-400 font-bold text-[10px] uppercase">Прогресс шагов:</span>
        <span className="text-white font-extrabold text-xs">
          Шаг <strong className="text-emerald-400 text-sm font-black">{marathon.current_step}</strong> из {marathon.total_steps}
        </span>
      </div>

      {/* Customizable progress bar */}
      <div className="w-full bg-slate-900/60 border border-white/5 h-3 rounded-full relative overflow-hidden p-0.5">
        <div 
          className="bg-gradient-to-r from-emerald-400 to-teal-400 h-full rounded-full transition-all duration-1000 ease-out shadow-neon-green"
          style={{ width: `${percentage}%` }}
        />
      </div>

      {/* Goal rewards footer info */}
      <div className="flex items-start space-x-2 bg-black/25 p-3 rounded-xl border border-white/5 text-[9.5px] text-slate-300 leading-normal">
        <Award className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
        <p>
          Мы ставим последовательные ординары, увеличивая банк на каждом успешном шаге. Присоединяйтесь и повторяйте ставки из Ленты!
        </p>
      </div>

    </div>
  );
}
