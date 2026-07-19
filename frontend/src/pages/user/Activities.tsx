import React, { useState, useRef, useEffect } from 'react';
import { Gamepad2, Gift, Ticket, Star, Percent, Coins, Send } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { WheelOfFortuneResponse } from '../../schemas/schemas';
import { notifySuccess, notifyError } from '../../utils/notify';
import { motion, AnimatePresence, MotionConfig } from 'framer-motion';

interface ActivitiesProps {
  active: boolean;
}

const PRIZE_TYPES: Record<string, { label: string; sub: string; color: string; icon: React.ElementType }> = {
  'post_payment_top_error': { 
    label: 'Топ Ошибка', sub: 'на послеоплату', color: 'from-amber-300 to-yellow-600', icon: Star 
  },
  'discount_70': { 
    label: 'Скидка 70%', sub: 'на абонемент', color: 'from-purple-400 to-indigo-600', icon: Percent 
  },
  'bonus_1000': { 
    label: '1000 бонусов', sub: 'на топ ошибку', color: 'from-slate-400 to-slate-600', icon: Coins 
  },
  'discount_50': { 
    label: 'Скидка 50%', sub: 'на абонемент', color: 'from-cyan-400 to-blue-600', icon: Percent 
  },
  'discount_30': { 
    label: 'Скидка 30%', sub: 'на абонемент', color: 'from-emerald-400 to-green-600', icon: Percent 
  },
  'match_gift': { 
    label: 'Матч в подарок', sub: 'к абонементу', color: 'from-slate-300 to-slate-500', icon: Gift 
  },
};

const ITEM_WIDTH = 140;
const ITEM_GAP = 12; 
const TOTAL_WIDTH = ITEM_WIDTH + ITEM_GAP;
const INITIAL_INDEX = 5;
const WIN_INDEX = 80; 
const TOTAL_ITEMS = 100;
const LOGO_URL = "/brand/shamrai-channel-emblem.png";

const generateRandomItems = () => {
  const keys = Object.keys(PRIZE_TYPES);
  return Array.from({ length: TOTAL_ITEMS }).map(() => keys[Math.floor(Math.random() * keys.length)]);
};

export default function Activities({ active }: ActivitiesProps) {
  const [items, setItems] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [isSpinning, setIsSpinning] = useState(false);
  const [result, setResult] = useState<WheelOfFortuneResponse | null>(null);
  
  // Status state
  const [wheelStatus, setWheelStatus] = useState<{ can_spin: boolean; next_spin_at: string | null } | null>(null);
  const [timeLeftStr, setTimeLeftStr] = useState<string>('');

  const containerRef = useRef<HTMLDivElement>(null);

  const resetPosition = () => {
    if (containerRef.current) {
      const containerW = containerRef.current.offsetWidth;
      const initialOffset = (INITIAL_INDEX * TOTAL_WIDTH) - (containerW / 2) + (ITEM_WIDTH / 2);
      setOffset(initialOffset);
    }
  };

  useEffect(() => {
    if (active) {
      apiFetch<any>('/marketing/wheel-of-fortune/status')
        .then(res => setWheelStatus(res))
        .catch(err => {
          console.error(err);
          // If backend fails (e.g. not reloaded yet), default to allowing spin
          setWheelStatus({ can_spin: true, next_spin_at: null });
        });
    }
    if (active && items.length === 0) {
      setItems(generateRandomItems());
      setTimeout(resetPosition, 50);
      window.addEventListener('resize', resetPosition);
      return () => window.removeEventListener('resize', resetPosition);
    }
  }, [active, items.length]);

  // Update countdown
  useEffect(() => {
    if (!wheelStatus || wheelStatus.can_spin || !wheelStatus.next_spin_at) return;
    const interval = setInterval(() => {
      const diff = new Date(wheelStatus.next_spin_at!).getTime() - Date.now();
      if (diff <= 0) {
        setWheelStatus({ can_spin: true, next_spin_at: null });
        return;
      }
      const days = Math.floor(diff / (1000 * 60 * 60 * 24));
      const hours = Math.floor((diff / (1000 * 60 * 60)) % 24);
      const mins = Math.floor((diff / 1000 / 60) % 60);
      const secs = Math.floor((diff / 1000) % 60);
      setTimeLeftStr(`${days}д ${hours}ч ${mins}м ${secs}с`);
    }, 1000);
    return () => clearInterval(interval);
  }, [wheelStatus]);

  const spinWheel = async () => {
    if (isSpinning) return;
    
    // 1. Reset state
    setResult(null);
    setIsSpinning(false); 
    resetPosition();
    setItems(generateRandomItems()); 
    
    await new Promise(r => setTimeout(r, 50));

    // 2. Start animation INSTANTLY
    setIsSpinning(true);
    if (!containerRef.current) return;
    const containerW = containerRef.current.offsetWidth;
    const centerPoint = containerW / 2;
    const itemCenter = ITEM_WIDTH / 2;
    
    const stopOffset = (Math.random() - 0.5) * (ITEM_WIDTH - 20); 
    const targetOffset = (WIN_INDEX * TOTAL_WIDTH) - centerPoint + itemCenter + stopOffset;
    
    setOffset(targetOffset);
    const spinStartTime = Date.now();

    // 3. Fetch API in background
    try {
      const res = await apiFetch<WheelOfFortuneResponse>('/marketing/wheel-of-fortune', {
        method: 'POST',
      });
      
      // 4. Inject winner at index 80 (it is still off-screen)
      setItems(prev => {
        const newItems = [...prev];
        newItems[WIN_INDEX] = res.reward_type || 'post_payment_top_error';
        return newItems;
      });
      
      // 5. Wait for the remaining animation time (8 seconds total)
      const timeElapsed = Date.now() - spinStartTime;
      const timeLeft = Math.max(0, 8200 - timeElapsed);
      await new Promise(resolve => setTimeout(resolve, timeLeft));
      
      setIsSpinning(false); // Stop spinning state so button unlocks (or disappears)
      setResult(res);
      // Immediately set cool-down
      const nextWeek = new Date();
      nextWeek.setDate(nextWeek.getDate() + 7);
      setWheelStatus({ can_spin: false, next_spin_at: nextWeek.toISOString() });
      notifySuccess('Приз получен!');
    } catch (err: any) {
      setIsSpinning(false);
      resetPosition();
      notifyError(err.message || 'Произошла ошибка при получении приза');
    }
  };

  if (!active) return null;

  return (
    <MotionConfig reducedMotion="never">
      <div className="flex flex-col gap-6 pb-24 overflow-x-hidden relative">
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-72 h-72 bg-indigo-600/20 rounded-full blur-[80px] pointer-events-none" />
        <div className="absolute top-2/3 left-1/2 -translate-x-1/2 w-72 h-72 bg-cyan-600/20 rounded-full blur-[80px] pointer-events-none" />

        <header className="shamrai-glass-card rounded-2xl p-4 mt-2 relative z-10">
          <div className="flex items-center gap-3">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-indigo-500/20 to-cyan-500/20 border border-white/10 text-cyan-400 shadow-[0_0_15px_rgba(34,211,238,0.3)]">
              <Gamepad2 className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-xl font-black text-white bg-clip-text text-transparent bg-gradient-to-r from-cyan-200 to-indigo-300">
                Активности
              </h1>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Еженедельный бонус
              </p>
            </div>
          </div>
        </header>

        <section className="relative z-10 w-full max-w-3xl mx-auto flex flex-col items-center">
          <div className="px-4 text-center w-full flex flex-col items-center">
            <h2 className="text-2xl sm:text-3xl font-black text-white drop-shadow-md">Получение бонуса</h2>
            <p className="mt-1 mb-6 text-sm sm:text-base text-slate-400 max-w-lg">
              Участвуйте раз в неделю, чтобы выбить эксклюзивный промокод!
            </p>
          </div>

          <div className="relative w-full h-52 sm:h-64 mb-8 bg-slate-900 border-y border-white/10 shadow-[0_0_50px_rgba(0,0,0,0.6)] overflow-hidden rounded-xl" ref={containerRef}>
            
            <div className="absolute inset-0 flex items-center justify-center opacity-[0.03] pointer-events-none grayscale">
              <img src={LOGO_URL} alt="" className="w-64 h-64 object-cover" />
            </div>

            <div className="absolute inset-y-0 left-0 w-16 bg-gradient-to-r from-slate-900 via-slate-900/80 to-transparent z-10 pointer-events-none" />
            <div className="absolute inset-y-0 right-0 w-16 bg-gradient-to-l from-slate-900 via-slate-900/80 to-transparent z-10 pointer-events-none" />
            
            <div className="absolute top-0 left-1/2 w-0 h-0 border-l-[10px] border-r-[10px] border-t-[14px] border-l-transparent border-r-transparent border-t-amber-400 z-20 -translate-x-1/2 drop-shadow-[0_2px_5px_rgba(0,0,0,0.5)]" />
            <div className="absolute bottom-0 left-1/2 w-0 h-0 border-l-[10px] border-r-[10px] border-b-[14px] border-l-transparent border-r-transparent border-b-amber-400 z-20 -translate-x-1/2 drop-shadow-[0_-2px_5px_rgba(0,0,0,0.5)]" />

            <motion.div 
              className="flex items-center gap-3 h-full"
              animate={{ x: -offset }}
              transition={
                isSpinning 
                  ? { duration: 8, ease: [0.05, 0.95, 0.1, 1] } 
                  : { duration: 0 }
              }
            >
              {items.map((key, i) => {
                const prize = PRIZE_TYPES[key] || PRIZE_TYPES['match_gift'];
                const Icon = prize.icon;
                return (
                  <div 
                    key={i} 
                    style={{ width: ITEM_WIDTH, minWidth: ITEM_WIDTH }} 
                    className="relative h-40 rounded bg-slate-800/80 border border-slate-700 shrink-0 overflow-hidden shadow-inner flex flex-col items-center justify-center p-3"
                  >
                    <div className={`absolute top-0 inset-x-0 h-full bg-gradient-to-b ${prize.color} opacity-[0.15] pointer-events-none mix-blend-screen`} />
                    
                    <div className="relative w-14 h-14 mb-2 z-10 flex items-center justify-center">
                      <div className={`absolute inset-0 bg-gradient-to-b ${prize.color} blur-xl opacity-40 rounded-full`} />
                      <Icon className="w-10 h-10 text-white opacity-95 drop-shadow-lg z-10" />
                    </div>
                    
                    <span className="text-[12px] font-black text-center text-white leading-tight z-10 drop-shadow-md">
                      {prize.label}
                    </span>
                    <span className="text-[10px] font-semibold text-center text-slate-300 z-10 mt-1 drop-shadow-sm">
                      {prize.sub}
                    </span>
                    
                    <div className={`absolute bottom-0 left-0 right-0 h-[6px] bg-gradient-to-r ${prize.color} shadow-[0_-2px_10px_rgba(0,0,0,0.3)]`} />
                  </div>
                );
              })}
            </motion.div>
          </div>

          {/* Button or Cooldown UI */}
          <div className="flex flex-col gap-4">
            {wheelStatus === null ? (
              <div className="h-14 flex items-center justify-center opacity-50">Загрузка...</div>
            ) : (
              <>
                {/* Timer block, shown if the user cannot spin natively */}
                {(!wheelStatus.can_spin && !isSpinning) ? (
                  <div className="w-full max-w-[280px] mx-auto p-[2px] rounded-2xl bg-slate-800/50 border border-white/5">
                    <div className="flex flex-col items-center justify-center bg-slate-900/80 px-6 py-3 rounded-[14px]">
                      <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-1">
                        Следующий бонус через
                      </span>
                      <span className="text-white font-mono font-black text-lg tracking-widest text-cyan-400">
                        {timeLeftStr}
                      </span>
                    </div>
                  </div>
                ) : null}

                {/* Button is always shown if can_spin is true OR if we are in DEV mode for local testing */}
                {(wheelStatus.can_spin || import.meta.env.DEV) && (
                  <button
                    onClick={spinWheel}
                    disabled={isSpinning || result !== null}
                    className="relative group w-full max-w-[280px] mx-auto rounded-2xl p-[2px] overflow-hidden transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100 disabled:cursor-not-allowed"
                  >
                    <div className="absolute inset-0 bg-gradient-to-r from-cyan-500 via-indigo-500 to-purple-500 rounded-2xl opacity-80 group-hover:opacity-100 transition-opacity" />
                    <div className="relative flex items-center justify-center gap-2 bg-slate-950 px-6 py-4 rounded-[14px]">
                      <span className="font-black text-white text-lg tracking-wide uppercase">
                        {isSpinning ? 'Получение...' : 'Получить бонус'}
                      </span>
                    </div>
                  </button>
                )}
              </>
            )}
          </div>

          <AnimatePresence>
            {result && (
              <motion.div 
                initial={{ opacity: 0, y: 20, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                className="mt-8 rounded-2xl border border-indigo-500/30 bg-indigo-500/10 p-5 text-left shadow-[0_0_30px_rgba(99,102,241,0.15)] relative overflow-hidden"
              >
                <div className="absolute top-0 right-0 p-4 opacity-10">
                  <Gift className="w-24 h-24 text-indigo-300" />
                </div>
                
                <div className="relative z-10">
                  <div className="flex items-center gap-2 mb-2">
                    <Ticket className="w-5 h-5 text-indigo-400" />
                    <h3 className="font-black text-indigo-300 uppercase tracking-wider text-xs">Предмет получен!</h3>
                  </div>
                  <p className="text-lg font-black text-white mb-4 leading-tight">{result.message}</p>
                  
                  {result.promo_code && (
                    <div className="flex items-center justify-between rounded-xl bg-slate-950/80 border border-indigo-500/20 p-3 shadow-inner">
                      <span className="font-mono text-lg font-black tracking-widest text-cyan-400">{result.promo_code}</span>
                      <a
                        href={`https://t.me/Shamrai_Osnova?text=${encodeURIComponent(`Мой промокод: ${result.promo_code}`)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider text-white transition-colors px-3 py-2 bg-cyan-500/20 border border-cyan-500/30 rounded-lg hover:bg-cyan-500 hover:text-slate-950"
                      >
                        <Send className="w-3.5 h-3.5" />
                        Отправить
                      </a>
                    </div>
                  )}
                  <p className="mt-3 text-[10px] text-indigo-200/60 font-semibold">
                    * Нажмите «Отправить», чтобы перейти в чат с менеджером и активировать промокод.
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </section>
      </div>
    </MotionConfig>
  );
}
