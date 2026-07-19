import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Loader2, Swords } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { PvPBattleResponse } from '../../schemas/schemas';

export default function PvPWidget() {
  const [battle, setBattle] = useState<PvPBattleResponse | null>(null);
  const [selected, setSelected] = useState<'a' | 'b' | null>(null);
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState<'a' | 'b' | null>(null);

  useEffect(() => {
    async function loadBattle() {
      try {
        setLoading(true);
        const data = await apiFetch('/marketing/pvp-active');
        setBattle(data);
      } catch (err) {
        console.error('PvP battle loading failed:', err);
      } finally {
        setLoading(false);
      }
    }

    loadBattle();
  }, []);

  const vote = async (option: 'a' | 'b') => {
    if (!battle) return;

    try {
      setVoting(option);
      const data = await apiFetch('/marketing/pvp-vote', {
        method: 'POST',
        body: JSON.stringify({ battle_id: battle.id, option }),
      });
      setBattle(data);
      setSelected(option);
    } catch (err) {
      console.error('PvP vote failed:', err);
    } finally {
      setVoting(null);
    }
  };

  if (loading) {
    return (
      <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-4 text-center text-xs font-bold text-slate-300 backdrop-blur-xl">
        <Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin text-[#00d2ff]" />
        Баттл загружается
      </div>
    );
  }

  if (!battle) return null;

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.045] p-4 shadow-glass backdrop-blur-xl">
      <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(255,0,127,0.10),transparent_42%,rgba(0,210,255,0.12))]" />
      <div className="relative z-10">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <p className="text-[9px] font-black uppercase tracking-[0.22em] text-[#8eeaff]">Баттл Разумов</p>
            <h3 className="mt-1 text-sm font-black text-white">{battle.match_name}</h3>
          </div>
          <Swords className="h-7 w-7 text-slate-500 drop-shadow-[0_0_12px_rgba(255,0,127,0.62)]" />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => vote('a')}
            disabled={!!voting}
            className="rounded-2xl border border-[#00d2ff]/25 bg-[#00d2ff]/10 p-3 text-left active:scale-95"
          >
            <span className="text-[10px] font-black uppercase text-[#8eeaff]">{battle.option_a}</span>
            <span className="mt-1 block text-lg font-black text-white">{battle.percent_a}%</span>
          </button>
          <button
            onClick={() => vote('b')}
            disabled={!!voting}
            className="rounded-2xl border border-slate-500/25 bg-slate-500/10 p-3 text-left active:scale-95"
          >
            <span className="text-[10px] font-black uppercase text-slate-400">{battle.option_b}</span>
            <span className="mt-1 block text-lg font-black text-white">{battle.percent_b}%</span>
          </button>
        </div>

        <div className="mt-3 overflow-hidden rounded-full bg-white/[0.08]">
          <motion.div
            initial={false}
            animate={{ width: `${battle.percent_a}%` }}
            transition={{ type: 'spring', stiffness: 90, damping: 16 }}
            className="h-2 rounded-full bg-gradient-to-r from-[#00d2ff] to-slate-500 shadow-[0_0_16px_rgba(0,210,255,0.55)]"
          />
        </div>

        {selected && (
          <motion.p
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-3 text-center text-[11px] font-bold text-slate-300"
          >
            Узнай, что думает нейросеть Shamrai
          </motion.p>
        )}
      </div>
    </div>
  );
}
