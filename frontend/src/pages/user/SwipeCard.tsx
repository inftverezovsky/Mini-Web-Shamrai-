import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence, PanInfo } from 'framer-motion';
import { Brain, Loader2, Sparkles, X } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { SwipeCandidateResponse, SwipeResponse } from '../../schemas/schemas';

const SWIPE_THRESHOLD = 82;

export default function SwipeCard() {
  const [candidate, setCandidate] = useState<SwipeCandidateResponse | null>(null);
  const [result, setResult] = useState<SwipeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadCandidate() {
      try {
        setLoading(true);
        setError(null);
        const data = await apiFetch('/marketing/swipe-candidate');
        setCandidate(data);
      } catch (err: any) {
        setError(err.message || 'Не удалось загрузить Shamrai Swipe');
      } finally {
        setLoading(false);
      }
    }

    loadCandidate();
  }, []);

  const submitGuess = async (guess: string) => {
    if (!candidate || submitting) return;

    try {
      setSubmitting(true);
      const data = await apiFetch('/marketing/swipe', {
        method: 'POST',
        body: JSON.stringify({ bet_id: candidate.bet_id, guess }),
      });
      setResult(data);
    } catch (err: any) {
      setError(err.message || 'Swipe не принят');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDragEnd = (_event: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
    if (info.offset.x > SWIPE_THRESHOLD) {
      submitGuess('П1');
    } else if (info.offset.x < -SWIPE_THRESHOLD) {
      submitGuess('П2');
    }
  };

  if (loading) {
    return (
      <div className="shimmer-border rounded-3xl border border-white/10 bg-white/[0.04] p-4 backdrop-blur-xl">
        <div className="flex items-center justify-center gap-2 text-xs font-bold text-cyan-200">
          <Loader2 className="h-4 w-4 animate-spin" />
          Shamrai Swipe загружается
        </div>
      </div>
    );
  }

  if (error || !candidate) {
    return (
      <div className="rounded-3xl border border-rose-500/20 bg-rose-500/10 p-4 text-[11px] font-bold text-rose-200">
        {error || 'Swipe-кандидат не найден'}
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.045] p-4 shadow-glass backdrop-blur-xl">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(0,210,255,0.18),transparent_34%),radial-gradient(circle_at_85%_30%,rgba(255,0,127,0.18),transparent_36%)]" />
      <div className="relative z-10 mb-3 flex items-center justify-between">
        <div>
          <p className="text-[9px] font-black uppercase tracking-[0.22em] text-cyan-200/80">Shamrai Swipe</p>
          <h3 className="mt-1 text-sm font-black text-white">Угадай сторону прогноза</h3>
        </div>
        <Brain className="h-7 w-7 text-[#00d2ff] drop-shadow-[0_0_12px_rgba(0,210,255,0.75)]" />
      </div>

      <motion.div
        drag="x"
        dragConstraints={{ left: 0, right: 0 }}
        onDragEnd={handleDragEnd}
        whileTap={{ scale: 0.97 }}
        className="relative z-10 rounded-2xl border border-white/10 bg-slate-950/55 p-4"
      >
        <div className="mb-3 flex items-center justify-between text-[10px] font-bold text-slate-400">
          <span>{candidate.bookmaker_name || 'Shamrai line'}</span>
          <span className="text-emerald-300">кф. {Number(candidate.coefficient).toFixed(2)}</span>
        </div>
        <p className="text-base font-black leading-snug text-white">{candidate.match_name}</p>
        <div className="mt-4 grid grid-cols-2 gap-2 text-center text-[10px] font-black uppercase tracking-wider">
          <button
            onClick={() => submitGuess('П2')}
            disabled={submitting}
            className="rounded-xl border border-[#ff007f]/30 bg-[#ff007f]/10 px-3 py-2 text-[#ff7fbd] shadow-[0_0_16px_rgba(255,0,127,0.18)] active:scale-95"
          >
            Влево: П2
          </button>
          <button
            onClick={() => submitGuess('П1')}
            disabled={submitting}
            className="rounded-xl border border-[#00d2ff]/30 bg-[#00d2ff]/10 px-3 py-2 text-[#8eeaff] shadow-[0_0_16px_rgba(0,210,255,0.2)] active:scale-95"
          >
            Вправо: П1
          </button>
        </div>
      </motion.div>

      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, scale: 0.92, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.94 }}
            className="absolute inset-3 z-20 flex flex-col items-center justify-center rounded-3xl border border-white/15 bg-slate-950/85 p-5 text-center shadow-glass backdrop-blur-xl"
          >
            <button
              onClick={() => setResult(null)}
              className="absolute right-3 top-3 rounded-full border border-white/10 bg-white/10 p-1 text-slate-300"
            >
              <X className="h-3.5 w-3.5" />
            </button>
            <Sparkles className={`mb-2 h-9 w-9 ${result.match ? 'text-[#00d2ff]' : 'text-amber-300'}`} />
            <h4 className="text-base font-black text-white">
              {result.match ? 'Наши мысли сходятся!' : 'Инстинкт принят'}
            </h4>
            <p className="mt-1 text-xs font-semibold text-slate-300">{result.message}</p>
            {result.promo_code && (
              <div className="mt-3 rounded-xl border border-[#00d2ff]/25 bg-[#00d2ff]/10 px-3 py-2 font-mono text-xs font-black text-[#8eeaff]">
                {result.promo_code}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
