import React, { useState, useEffect } from 'react';
import { TrendingUp } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import {
  CLIENT_CHECKOUT_ROI_PERCENT,
  resolveProfitSimulatorRoi,
  shouldFetchGlobalRoi,
} from '../../utils/profitSimulator';

export default function ProfitSimulator() {
  const [bankroll, setBankroll] = useState<number>(20000);
  const [roi, setRoi] = useState<number>(CLIENT_CHECKOUT_ROI_PERCENT);

  useEffect(() => {
    if (!shouldFetchGlobalRoi()) return;

    async function fetchRoi() {
      try {
        const stats = await apiFetch<{ roi?: unknown }>('/stats/global');
        setRoi(resolveProfitSimulatorRoi(stats?.roi));
      } catch (err) {
        console.error('Failed to load global ROI for simulator:', err);
      }
    }
    fetchRoi();
  }, []);

  // Simulator parameters
  const betPercentage = 5; // 5% of bankroll per bet (standard recommendation)
  const totalBets = 100;

  // Calculate coordinates for the projected profit trend
  // Y = Bankroll + (i * stake * ROI)
  // Let's add some slight positive/negative fluctuations to make the line look like a real trading/betting chart!
  const generateSimulatedPoints = () => {
    const points: Array<{ step: number; bankroll: number }> = [];
    let currentBankroll = bankroll;
    
    // Seeded randomness for consistent, premium looking path shape
    const fluctuations = [
      0.2, -0.4, 0.8, 1.2, -0.2, 0.5, -0.1, 0.9, -0.3, 1.1,
      0.4, -0.6, 0.7, 1.5, -0.1, 0.3, -0.2, 1.0, -0.4, 1.3,
      0.1, -0.3, 0.9, 1.1, -0.5, 0.6, -0.2, 0.8, -0.1, 1.2,
      0.3, -0.5, 0.8, 1.4, -0.2, 0.4, -0.3, 0.9, -0.5, 1.1
    ];

    points.push({ step: 0, bankroll: currentBankroll });

    const stepInterval = totalBets / 10; // 10 steps in chart
    for (let i = 1; i <= 10; i++) {
      const flucIndex = (i - 1) % fluctuations.length;
      const fluc = fluctuations[flucIndex];
      
      // Expected profit for this interval
      const expectedProfit = stepInterval * (bankroll * (betPercentage / 100) * (roi / 100));
      
      // Add fluctuation to make the chart look authentic
      const noise = expectedProfit * fluc * 0.4;
      
      currentBankroll = Math.max(0, currentBankroll + expectedProfit + noise);
      points.push({ step: i * stepInterval, bankroll: Math.round(currentBankroll) });
    }
    return points;
  };

  const points = generateSimulatedPoints();
  const projectedProfit = points[points.length - 1].bankroll - bankroll;

  const renderProjectionChart = () => {
    const width = 360;
    const height = 140;
    const padding = 15;

    const bankrolls = points.map(p => p.bankroll);
    const maxVal = Math.max(...bankrolls);
    const minVal = Math.min(...bankrolls);
    const valRange = maxVal - minVal || 1;

    const chartWidth = width - padding * 2;
    const chartHeight = height - padding * 2;

    const coords = points.map((p, i) => {
      const x = padding + (i / (points.length - 1)) * chartWidth;
      const ratio = (p.bankroll - minVal) / valRange;
      const y = padding + chartHeight - ratio * chartHeight;
      return { x, y, val: p.bankroll, label: p.step };
    });

    let linePath = '';
    if (coords.length > 0) {
      linePath = `M ${coords[0].x} ${coords[0].y}`;
      for (let i = 1; i < coords.length; i++) {
        const xc = (coords[i - 1].x + coords[i].x) / 2;
        const yc = (coords[i - 1].y + coords[i].y) / 2;
        linePath += ` Q ${coords[i - 1].x} ${coords[i - 1].y}, ${xc} ${yc}`;
        linePath += ` T ${coords[i].x} ${coords[i].y}`;
      }
    }

    const areaPath = coords.length > 0
      ? `${linePath} L ${coords[coords.length - 1].x} ${height - padding} L ${coords[0].x} ${height - padding} Z`
      : '';

    return (
      <div className="relative w-full h-[160px] bg-[#0E132A]/50 border border-white/5 rounded-2xl p-4 flex flex-col justify-between overflow-hidden shadow-inner">
        <div className="absolute top-10 left-1/2 transform -translate-x-1/2 w-48 h-12 bg-[#00d2ff]/10 rounded-full blur-2xl pointer-events-none"></div>

        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-[120px] overflow-visible z-10">
          <defs>
            <linearGradient id="simLineGrad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#00d2ff" />
              <stop offset="100%" stopColor="#ff007f" />
            </linearGradient>
            <linearGradient id="simAreaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ff007f" stopOpacity="0.2" />
              <stop offset="100%" stopColor="#00d2ff" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="rgba(255,255,255,0.03)" strokeDasharray="3,3" />
          <line x1={padding} y1={padding + chartHeight / 2} x2={width - padding} y2={padding + chartHeight / 2} stroke="rgba(255,255,255,0.03)" strokeDasharray="3,3" />
          <line x1={padding} y1={padding + chartHeight} x2={width - padding} y2={padding + chartHeight} stroke="rgba(255,255,255,0.05)" />

          {areaPath && <path d={areaPath} fill="url(#simAreaGrad)" />}
          {linePath && <path d={linePath} fill="none" stroke="url(#simLineGrad)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="filter drop-shadow-[0_0_6px_rgba(255,0,127,0.4)]" />}

          {coords.map((c, idx) => {
            // Only draw dots at start, middle, and end for clean layout
            if (idx !== 0 && idx !== 5 && idx !== 10) return null;
            return (
              <g key={idx} className="group cursor-pointer">
                <circle 
                  cx={c.x} 
                  cy={c.y} 
                  r="3.5" 
                  fill="#070B19" 
                  stroke="#ff007f" 
                  strokeWidth="2" 
                />
                <text 
                  x={c.x} 
                  y={c.y - 8} 
                  textAnchor="middle" 
                  fill="#ffffff" 
                  fontSize="7.5" 
                  fontWeight="bold"
                >
                  {c.val.toLocaleString('ru-RU')} ₽
                </text>
              </g>
            );
          })}
        </svg>

        <div className="flex justify-between px-3 text-[7.5px] text-slate-500 font-extrabold uppercase tracking-wider z-10">
          <span>0 ставок</span>
          <span>50 ставок</span>
          <span>100 ставок</span>
        </div>
      </div>
    );
  };

  return (
    <div className="bg-slate-900/40 border border-white/10 backdrop-blur-xl p-5 rounded-2xl shadow-xl space-y-4 relative overflow-hidden group">
      {/* Glow highlight */}
      <div className="absolute -top-12 -right-12 w-24 h-24 bg-[#ff007f]/5 rounded-full blur-xl group-hover:bg-[#ff007f]/10 transition-colors"></div>

      <div className="flex justify-between items-start">
        <div>
          <h4 className="text-xs font-black text-white uppercase tracking-wider flex items-center">
            <TrendingUp className="w-4 h-4 text-[#ff007f] mr-1.5 shrink-0 animate-pulse" />
            Калькулятор потенциальной прибыли
          </h4>
          <p className="text-slate-400 text-[9px] uppercase font-bold tracking-wider mt-0.5">
            Симуляция на основе верифицированного ROI: {roi}%
          </p>
        </div>
      </div>

      <div className="space-y-4">
        {/* Bankroll slider */}
        <div className="space-y-2">
          <div className="flex justify-between items-baseline text-[10px] uppercase font-bold tracking-wider text-slate-400">
            <span>Ваш начальный банк:</span>
            <span className="text-[#00d2ff] font-extrabold text-xs">{bankroll.toLocaleString('ru-RU')} ₽</span>
          </div>

          <input 
            type="range"
            min="5000"
            max="150000"
            step="5000"
            value={bankroll}
            onChange={e => setBankroll(parseInt(e.target.value))}
            className="w-full h-1.5 bg-slate-950 rounded-lg appearance-none cursor-pointer accent-[#ff007f]"
          />
        </div>

        {/* Projection chart */}
        {renderProjectionChart()}

        {/* Projected metrics */}
        <div className="grid grid-cols-2 gap-3.5 pt-1.5">
          <div className="bg-slate-900/60 border border-white/5 p-3 rounded-xl flex flex-col justify-between">
            <span className="text-[8px] text-slate-500 font-extrabold uppercase tracking-wider block">Рекомендуемая ставка</span>
            <div className="text-sm font-black text-white mt-1.5">
              {Math.round(bankroll * (betPercentage / 100))} ₽
              <span className="text-[8px] text-slate-550 font-bold ml-1 uppercase">({betPercentage}%)</span>
            </div>
          </div>
          <div className="bg-slate-900/60 border border-white/5 p-3 rounded-xl flex flex-col justify-between">
            <span className="text-[8px] text-[#ff007f] font-extrabold uppercase tracking-wider block">Ожидаемый профит (100 ст.)</span>
            <div className="text-sm font-black text-[#10B981] mt-1.5">
              +{projectedProfit.toLocaleString('ru-RU')} ₽
              <span className="text-[8px] text-slate-550 font-bold ml-1 uppercase">({Math.round((projectedProfit / bankroll) * 100)}%)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
