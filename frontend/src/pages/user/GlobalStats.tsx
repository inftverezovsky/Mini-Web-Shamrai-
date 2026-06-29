import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { TrendingUp, Percent, Award, BookOpen, Loader2, RefreshCw, Sparkles } from 'lucide-react';

interface ChartPoint {
  month: string;
  profit: number;
}

interface GlobalStatsData {
  winrate: number;
  roi: number;
  net_profit: number;
  total_bets: number;
  won_bets: number;
  lost_bets: number;
  refund_bets: number;
  average_coefficient: number;
  chart_points: ChartPoint[];
}

export default function GlobalStats() {
  const [stats, setStats] = useState<GlobalStatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadStats = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch('/stats/global');
      setStats(data);
    } catch (err: any) {
      console.error('Failed to load global stats:', err);
      setError(err.message || 'Ошибка при загрузке статистики');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStats();
  }, []);

  // Custom SVG line chart builder
  const renderSvgChart = (points: ChartPoint[]) => {
    if (!points || points.length === 0) return null;

    const width = 360;
    const height = 150;
    const padding = 15;

    const profits = points.map(p => p.profit);
    const maxProfit = Math.max(...profits, 1);
    const minProfit = Math.min(...profits, -1);
    const profitRange = maxProfit - minProfit || 2;

    const chartWidth = width - padding * 2;
    const chartHeight = height - padding * 2;

    const coords = points.map((p, i) => {
      const x = padding + (i / Math.max(points.length - 1, 1)) * chartWidth;
      // Normalize profit to y coordinate (higher profit is smaller y)
      const ratio = (p.profit - minProfit) / profitRange;
      const y = padding + chartHeight - ratio * chartHeight;
      return { x, y, profit: p.profit, label: p.month };
    });

    // Build line path
    let linePath = '';
    if (coords.length > 0) {
      linePath = `M ${coords[0].x} ${coords[0].y}`;
      for (let i = 1; i < coords.length; i++) {
        // Curve construction using control points
        const xc = (coords[i - 1].x + coords[i].x) / 2;
        const yc = (coords[i - 1].y + coords[i].y) / 2;
        linePath += ` Q ${coords[i - 1].x} ${coords[i - 1].y}, ${xc} ${yc}`;
        linePath += ` T ${coords[i].x} ${coords[i].y}`;
      }
    }

    // Build area path for glow gradient underneath
    const areaPath = coords.length > 0
      ? `${linePath} L ${coords[coords.length - 1].x} ${height - padding} L ${coords[0].x} ${height - padding} Z`
      : '';

    return (
      <div className="motion-card shimmer-border relative w-full h-[180px] bg-black/30 border border-white/5 rounded-2xl p-4 flex flex-col justify-between overflow-hidden">
        {/* Glow indicator behind chart */}
        <div className="absolute top-10 left-1/2 transform -translate-x-1/2 w-48 h-12 bg-emerald-500/10 rounded-full blur-2xl pointer-events-none"></div>

        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-[130px] overflow-visible z-10">
          <defs>
            {/* Gradients */}
            <linearGradient id="chartLineGrad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#818CF8" />
              <stop offset="50%" stopColor="#10B981" />
              <stop offset="100%" stopColor="#34D399" />
            </linearGradient>
            <linearGradient id="chartAreaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#10B981" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#10B981" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="rgba(255,255,255,0.03)" strokeDasharray="3,3" />
          <line x1={padding} y1={padding + chartHeight / 2} x2={width - padding} y2={padding + chartHeight / 2} stroke="rgba(255,255,255,0.03)" strokeDasharray="3,3" />
          <line x1={padding} y1={padding + chartHeight} x2={width - padding} y2={padding + chartHeight} stroke="rgba(255,255,255,0.05)" />

          {/* Area fill */}
          {areaPath && <path d={areaPath} fill="url(#chartAreaGrad)" />}

          {/* Line stroke */}
          {linePath && <path d={linePath} fill="none" stroke="url(#chartLineGrad)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />}

          {/* Interactive points */}
          {coords.map((c, idx) => (
            <g key={idx} className="group cursor-pointer">
              <circle 
                cx={c.x} 
                cy={c.y} 
                r="4" 
                fill="#070B19" 
                stroke={c.profit >= 0 ? '#10B981' : '#EF4444'} 
                strokeWidth="2.5" 
                className="transition-all hover:scale-150 duration-300"
              />
              <text 
                x={c.x} 
                y={c.y - 10} 
                textAnchor="middle" 
                fill="#A7F3D0" 
                fontSize="8" 
                fontWeight="black" 
                className="opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none bg-black/85"
              >
                {c.profit > 0 ? '+' : ''}{c.profit}%
              </text>
            </g>
          ))}
        </svg>

        {/* X Axis Labels */}
        <div className="flex justify-between px-3 text-[8px] text-slate-500 font-extrabold uppercase tracking-wider z-10">
          {points.map((p, i) => (
            <span key={i}>{p.month}</span>
          ))}
        </div>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-3">
        <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
        <span className="text-slate-400 text-xs">Загрузка глобальной статистики...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center p-8 bg-white/[0.04] border border-white/15 backdrop-blur-md rounded-3xl max-w-md mx-auto space-y-4">
        <p className="text-rose-400 text-xs font-semibold">Не удалось загрузить глобальную аналитику: {error}</p>
        <button 
          onClick={loadStats} 
          className="bg-white/10 hover:bg-white/15 text-white active:scale-95 text-xs px-5 py-2.5 rounded-xl flex items-center justify-center mx-auto space-x-1.5 transition-all"
        >
          <RefreshCw className="w-4 h-4 text-emerald-400" />
          <span>Повторить</span>
        </button>
      </div>
    );
  }

  if (!stats) return null;

  const profitIsPositive = stats.net_profit >= 0;

  return (
    <div className="space-y-6 animate-slide-up pb-10">
      
      {/* Header Info */}
      <div className="text-center space-y-1">
        <h2 className="text-xl font-black text-white flex items-center justify-center">
          <TrendingUp className="iridescent-icon w-5 h-5 mr-2" />
          Глобальный Дашборд
        </h2>
        <p className="text-slate-400 text-xs leading-relaxed max-w-xs mx-auto">
          Открытая верифицированная статистика прогнозов канала. Обновляется автоматически в реальном времени.
        </p>
      </div>

      {/* Grid containing premium metric blocks */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {/* Metric 1: pass rate */}
        <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-3.5 rounded-2xl flex flex-col justify-between space-y-2 relative overflow-hidden shadow-glass">
          <div className="absolute top-1 right-1 w-6 h-6 bg-indigo-500/5 rounded-full blur-sm"></div>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <Percent className="iridescent-icon w-3.5 h-3.5" />
            <span className="text-[9px] font-bold tracking-wider uppercase">Проход</span>
          </div>
          <div>
            <h3 className="text-base font-black text-white">{stats.winrate}%</h3>
            <p className="text-[7.5px] text-slate-500 font-extrabold uppercase mt-0.5">победных ординаров</p>
          </div>
        </div>

        {/* Metric 2: ROI */}
        <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-3.5 rounded-2xl flex flex-col justify-between space-y-2 relative overflow-hidden shadow-glass">
          <div className="absolute top-1 right-1 w-6 h-6 bg-emerald-500/5 rounded-full blur-sm"></div>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <Award className="iridescent-icon w-3.5 h-3.5" />
            <span className="text-[9px] font-bold tracking-wider uppercase">ROI</span>
          </div>
          <div>
            <h3 className={`text-base font-black ${stats.roi >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {stats.roi > 0 ? '+' : ''}{stats.roi}%
            </h3>
            <p className="text-[7.5px] text-slate-500 font-extrabold uppercase mt-0.5">средняя доходность</p>
          </div>
        </div>

        {/* Metric 3: Net Profit */}
        <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-3.5 rounded-2xl flex flex-col justify-between space-y-2 relative overflow-hidden shadow-glass">
          <div className={`absolute top-1 right-1 w-6 h-6 rounded-full blur-sm ${profitIsPositive ? 'bg-emerald-500/5' : 'bg-rose-500/5'}`}></div>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <TrendingUp className="iridescent-icon w-3.5 h-3.5" />
            <span className="text-[9px] font-bold tracking-wider uppercase">Прибыль</span>
          </div>
          <div>
            <h3 className={`text-base font-black ${profitIsPositive ? 'text-emerald-450 text-glow-green' : 'text-rose-450'}`}>
              {profitIsPositive ? '+' : ''}{stats.net_profit} флэт
            </h3>
            <p className="text-[7.5px] text-slate-500 font-extrabold uppercase mt-0.5">за все время</p>
          </div>
        </div>

        {/* Metric 4: Average coefficient */}
        <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-3.5 rounded-2xl flex flex-col justify-between space-y-2 relative overflow-hidden shadow-glass">
          <div className="absolute top-1 right-1 w-6 h-6 bg-indigo-500/5 rounded-full blur-sm"></div>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <Sparkles className="iridescent-icon w-3.5 h-3.5" />
            <span className="text-[9px] font-bold tracking-wider uppercase">Средний КФ</span>
          </div>
          <div>
            <h3 className="text-base font-black text-indigo-200">{Number(stats.average_coefficient || 0).toFixed(2)}</h3>
            <p className="text-[7.5px] text-slate-500 font-extrabold uppercase mt-0.5">по расчетам</p>
          </div>
        </div>
      </div>

      {/* Monthly chart block */}
      <div className="space-y-2">
        <h4 className="text-[10px] uppercase font-bold text-slate-400 tracking-wider flex items-center px-1">
          <TrendingUp className="iridescent-icon w-3.5 h-3.5 mr-1.5 shrink-0" />
          Динамика роста прибыли
        </h4>
        {renderSvgChart(stats.chart_points)}
      </div>

      {/* Summary counters section */}
      <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-4 rounded-2xl space-y-3 shadow-glass text-xs text-slate-300 relative overflow-hidden">
        <h4 className="font-extrabold text-white flex items-center text-[10px] uppercase tracking-wider">
          <BookOpen className="iridescent-icon w-4 h-4 mr-2 shrink-0" />
          Верифицировано ставок
        </h4>
        
        <div className="grid grid-cols-2 gap-4 text-[11px] pt-1">
          <div className="flex justify-between border-r border-white/5 pr-4">
            <span className="text-slate-450">Всего прогнозов:</span>
            <span className="font-bold text-white">{stats.total_bets}</span>
          </div>
          <div className="flex justify-between pl-2">
            <span className="text-slate-450">Победа:</span>
            <span className="font-bold text-emerald-400">{stats.won_bets}</span>
          </div>
          <div className="flex justify-between border-r border-white/5 pr-4">
            <span className="text-slate-450">Неудача:</span>
            <span className="font-bold text-rose-400">{stats.lost_bets}</span>
          </div>
          <div className="flex justify-between pl-2">
            <span className="text-slate-450">Возвраты:</span>
            <span className="font-bold text-amber-400">{stats.refund_bets}</span>
          </div>
        </div>
      </div>

    </div>
  );
}
