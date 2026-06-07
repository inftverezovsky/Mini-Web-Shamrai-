import React, { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/api';
import { Activity, AlertCircle, Loader2, TrendingUp, Trophy, Users } from 'lucide-react';

interface StatsData {
  total_revenue: number;
  active_subscribers: number;
  channel_roi: number;
  winrate: number;
  total_bets_issued: number;
  average_coefficient: number;
  author_total_bets_issued?: number;
  author_winrate?: number;
  author_average_coefficient?: number;
  author_channel_roi?: number;
  total_users: number;
}

export default function AdminStats() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch('/admin/dashboard/stats');
      setStats(data);
    } catch (err: any) {
      console.error('Failed to fetch admin stats:', err);
      setError(err.message || 'Ошибка загрузки статистики админа');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-3">
        <Loader2 className="w-8 h-8 text-indigo-500 animate-spin" />
        <span className="text-slate-400 text-sm font-bold uppercase tracking-wider">Загрузка статистики...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center p-8 bg-rose-500/10 border border-rose-500/25 rounded-2xl max-w-md mx-auto space-y-3">
        <AlertCircle className="w-8 h-8 text-rose-500 mx-auto" />
        <h4 className="text-white text-sm font-bold">Ошибка соединения</h4>
        <p className="text-xs text-slate-400">{error}</p>
        <button
          type="button"
          onClick={fetchStats}
          className="bg-white/10 hover:bg-white/15 px-4 py-2 rounded-xl text-xs font-bold text-white transition-all"
        >
          Попробовать снова
        </button>
      </div>
    );
  }

  const authorTotalBets = stats?.author_total_bets_issued ?? stats?.total_bets_issued ?? 0;
  const authorWinrate = stats?.author_winrate ?? stats?.winrate ?? 0;
  const authorAverageCoefficient = stats?.author_average_coefficient ?? stats?.average_coefficient ?? 0;

  return (
    <div className="space-y-6 animate-slide-up pb-10">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-white flex items-center uppercase tracking-wider">
            <TrendingUp className="w-5 h-5 text-emerald-400 mr-2" />
            Статистика
          </h2>
          <p className="text-slate-400 text-[10px] uppercase font-bold tracking-widest mt-0.5">
            Отдельный центр метрик
          </p>
        </div>
      </div>

      <div className="space-y-3.5">
        <div className="grid grid-cols-2 gap-3.5">
          <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-xl flex flex-col justify-between relative overflow-hidden group hover:border-cyan-500/35 transition-colors duration-300">
            <div className="absolute top-0 right-0 w-16 h-16 bg-cyan-500/5 rounded-full blur-xl group-hover:bg-cyan-500/10 transition-colors"></div>
            <span className="text-slate-400 text-[9px] font-black uppercase tracking-wider flex items-center">
              <Activity className="w-3.5 h-3.5 mr-1.5 text-cyan-400" /> Прогнозов
            </span>
            <div className="mt-2.5">
              <div className="text-xl font-black text-white flex items-baseline">
                {authorTotalBets} <span className="text-[10px] text-slate-500 font-bold ml-1">шт.</span>
              </div>
              <div className="text-[8px] text-slate-500 font-bold uppercase tracking-wider mt-0.5">
                опубликовано автором
              </div>
            </div>
          </div>

          <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-xl flex flex-col justify-between relative overflow-hidden group hover:border-emerald-500/35 transition-colors duration-300">
            <div className="absolute top-0 right-0 w-16 h-16 bg-emerald-500/5 rounded-full blur-xl group-hover:bg-emerald-500/10 transition-colors"></div>
            <span className="text-slate-400 text-[9px] font-black uppercase tracking-wider flex items-center">
              <Trophy className="w-3.5 h-3.5 mr-1.5 text-emerald-400" /> Процент побед
            </span>
            <div className="mt-2.5">
              <div className="text-xl font-black text-emerald-400 text-glow-green">
                {authorWinrate.toFixed(authorWinrate % 1 === 0 ? 0 : 1)}%
              </div>
              <div className="text-[8px] text-slate-500 font-bold uppercase tracking-wider mt-0.5">
                win/loss автора
              </div>
            </div>
          </div>

          <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-xl flex flex-col justify-between relative overflow-hidden group hover:border-teal-500/35 transition-colors duration-300">
            <div className="absolute top-0 right-0 w-16 h-16 bg-teal-500/5 rounded-full blur-xl group-hover:bg-teal-500/10 transition-colors"></div>
            <span className="text-slate-400 text-[9px] font-black uppercase tracking-wider flex items-center">
              <TrendingUp className="w-3.5 h-3.5 mr-1.5 text-teal-400" /> Средний КФ
            </span>
            <div className="mt-2.5">
              <div className="text-xl font-black text-indigo-300">
                {authorAverageCoefficient.toFixed(2)}
              </div>
              <div className="text-[8px] text-slate-500 font-bold uppercase tracking-wider mt-0.5">
                по прогнозам автора
              </div>
            </div>
          </div>

          <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-4 rounded-2xl shadow-xl flex flex-col justify-between relative overflow-hidden group hover:border-indigo-500/35 transition-colors duration-300">
            <div className="absolute top-0 right-0 w-16 h-16 bg-indigo-500/5 rounded-full blur-xl group-hover:bg-indigo-500/10 transition-colors"></div>
            <span className="text-slate-400 text-[9px] font-black uppercase tracking-wider flex items-center">
              <Users className="w-3.5 h-3.5 mr-1.5 text-indigo-400" /> Активные VIP
            </span>
            <div className="mt-2.5">
              <div className="text-xl font-black text-white flex items-baseline">
                {stats?.active_subscribers} <span className="text-[10px] text-slate-500 font-bold ml-1">чел.</span>
              </div>
              <div className="text-[8px] text-slate-500 font-bold uppercase tracking-wider mt-0.5">
                всего в базе: {stats?.total_users}
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3">
          <div className="bg-white/[0.04] border border-white/10 rounded-2xl p-4">
            <div className="text-[10px] font-black uppercase tracking-wider text-slate-500">Выручка</div>
            <div className="mt-1 text-2xl font-black text-white">{stats?.total_revenue ?? 0}</div>
            <div className="mt-1 text-[10px] text-slate-400">
              Сумма processed payments по всем провайдерам.
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-white/[0.04] border border-white/10 rounded-2xl p-4">
              <div className="text-[10px] font-black uppercase tracking-wider text-slate-500">ROI канала</div>
              <div className="mt-1 text-xl font-black text-emerald-400">{stats?.channel_roi ?? 0}%</div>
            </div>
            <div className="bg-white/[0.04] border border-white/10 rounded-2xl p-4">
              <div className="text-[10px] font-black uppercase tracking-wider text-slate-500">Winrate</div>
              <div className="mt-1 text-xl font-black text-cyan-300">{stats?.winrate ?? 0}%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
