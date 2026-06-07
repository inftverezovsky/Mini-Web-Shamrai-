import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { Activity, Clock } from 'lucide-react';

interface LiveTrackerProps {
  apiMatchId: string;
}

interface MatchScoreData {
  api_match_id: string;
  team_a: string;
  team_b: string;
  score_a: number;
  score_b: number;
  minute: number;
  status: string;
}

export default function LiveTracker({ apiMatchId }: LiveTrackerProps) {
  const [matchData, setMatchData] = useState<MatchScoreData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchLiveScore() {
      try {
        const data = await apiFetch(`/bets/match/${apiMatchId}`);
        setMatchData(data);
      } catch (err) {
        console.error('Failed to fetch live match score for tracker:', err);
      } finally {
        setLoading(false);
      }
    }

    // Initial fetch
    fetchLiveScore();

    // Set polling interval every 5 seconds
    const intervalId = setInterval(fetchLiveScore, 5000);

    return () => {
      clearInterval(intervalId);
    };
  }, [apiMatchId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-3 bg-white/[0.02] border border-white/5 rounded-xl">
        <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wider animate-pulse flex items-center">
          <Activity className="w-3.5 h-3.5 text-rose-500 mr-1.5 animate-spin" />
          Поиск матча...
        </span>
      </div>
    );
  }

  if (!matchData) return null;

  const isLive = matchData.status === 'Live';

  return (
    <div className="bg-slate-950/70 border border-white/10 p-4 rounded-xl flex flex-col justify-between items-center shadow-lg relative overflow-hidden group">
      
      {/* Visual neon lines */}
      <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-rose-500 to-transparent shadow-[0_0_8px_#ff007f]"></div>

      {/* Top Header: Live Pulse indicator and minute */}
      <div className="flex justify-between items-center w-full mb-2">
        <div className="flex items-center space-x-1.5">
          <span className={`w-2 h-2 rounded-full ${isLive ? 'bg-rose-500 animate-pulse shadow-[0_0_8px_#ff007f]' : 'bg-slate-600'}`}></span>
          <span className={`text-[9px] font-black uppercase tracking-widest ${isLive ? 'text-rose-500' : 'text-slate-500'}`}>
            {isLive ? 'Live репортаж' : 'Завершен'}
          </span>
        </div>

        <div className="flex items-center text-slate-400 font-extrabold text-[9px] uppercase tracking-wider">
          <Clock className="w-3 h-3 text-indigo-400 mr-1 shrink-0" />
          <span>{matchData.minute}' мин</span>
        </div>
      </div>

      {/* Teams and score */}
      <div className="flex justify-between items-center w-full py-1">
        {/* Team A */}
        <div className="flex-1 text-right pr-3">
          <span className="text-white font-extrabold text-[11px] truncate block uppercase tracking-wider">
            {matchData.team_a}
          </span>
        </div>

        {/* Score Board */}
        <div className="bg-slate-900 border border-white/10 px-3 py-1 rounded-lg flex items-center justify-center space-x-1.5 shadow-[0_0_10px_rgba(0,210,255,0.15)] font-black text-xs text-white">
          <span className="text-[#00d2ff] text-glow-blue">{matchData.score_a}</span>
          <span className="text-slate-600 font-bold">:</span>
          <span className="text-[#ff007f] text-glow-rose">{matchData.score_b}</span>
        </div>

        {/* Team B */}
        <div className="flex-1 text-left pl-3">
          <span className="text-white font-extrabold text-[11px] truncate block uppercase tracking-wider">
            {matchData.team_b}
          </span>
        </div>
      </div>
    </div>
  );
}
