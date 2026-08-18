import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Gamepad2, Gift, Ticket, Star, Percent, Coins, Send, Trophy, Zap, Crown, Heart, Flame, Target, Award, Diamond, Sparkles, Tag } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { WheelOfFortuneResponse, WheelConfigPayload, WheelPrizeConfig } from '../../schemas/schemas';
import { notifySuccess, notifyError } from '../../utils/notify';
import { motion, AnimatePresence, MotionConfig } from 'framer-motion';

interface ActivitiesProps {
  active: boolean;
}

const ICON_MAP: Record<string, React.ElementType> = {
  Star, Percent, Coins, Gift, Trophy, Zap, Crown, Heart, Flame, Target, Award, Diamond, Sparkles, Tag
};

// Fallback if backend config not loaded
const DEFAULT_PRIZES: WheelPrizeConfig[] = [
  { id: 'post_payment_top_error', label: 'Топ Ошибка', sub: 'на послеоплату', color: 'from-amber-300 to-yellow-600', icon: 'Star', probability: 50, reward_type: 'post_payment_match', reward_value: 0 },
  { id: 'discount_70', label: 'Скидка 70%', sub: 'на абонемент', color: 'from-purple-400 to-indigo-600', icon: 'Percent', probability: 20, reward_type: 'discount', reward_value: 70 },
  { id: 'bonus_1000', label: '1000 бонусов', sub: 'на топ ошибку', color: 'from-slate-400 to-slate-600', icon: 'Coins', probability: 15, reward_type: 'bonus_1000', reward_value: 1000 },
  { id: 'discount_50', label: 'Скидка 50%', sub: 'на абонемент', color: 'from-cyan-400 to-blue-600', icon: 'Percent', probability: 15, reward_type: 'discount', reward_value: 50 },
];

const INITIAL_INDEX = 5;
const WIN_INDEX = 80;
const TOTAL_ITEMS = 100;
const LOGO_URL = "/brand/shamrai-channel-emblem.png";

/** Compute item dimensions from container width so 4-5+ prizes are visible. */
const computeDims = (containerW: number) => {
  if (containerW < 360)  return { itemWidth: 72,  itemGap: 6  };
  if (containerW < 480)  return { itemWidth: 84,  itemGap: 8  };
  if (containerW < 640)  return { itemWidth: 105, itemGap: 10 };
  return { itemWidth: 140, itemGap: 12 };
};

/**
 * Builds the visual strip for the roulette animation.
 * Cycles through prizes in a shuffled order so that no two adjacent
 * items ever show the same prize. The actual winning prize is determined
 * by the backend (weighted probability) and injected at WIN_INDEX later.
 */
const generateRandomItems = (prizes: WheelPrizeConfig[]): string[] => {
  if (prizes.length === 0) return [];
  if (prizes.length === 1) return Array.from({ length: TOTAL_ITEMS }, () => prizes[0].id);

  const result: string[] = [];

  // Build a shuffled deck, then deal cards from it round-robin.
  // When the deck is exhausted, re-shuffle — but guarantee the last
  // card of the previous deck !== first card of the new one.
  let deck: string[] = [];
  const shuffle = (arr: string[]) => {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
  };

  const makeDeck = () => {
    const d = prizes.map(p => p.id);
    shuffle(d);
    return d;
  };

  deck = makeDeck();

  for (let i = 0; i < TOTAL_ITEMS; i++) {
    if (deck.length === 0) {
      deck = makeDeck();
      // Avoid repeating the last placed item
      if (result.length > 0 && deck[0] === result[result.length - 1]) {
        // Swap first element with a random later one
        const swapIdx = 1 + Math.floor(Math.random() * (deck.length - 1));
        [deck[0], deck[swapIdx]] = [deck[swapIdx], deck[0]];
      }
    }
    result.push(deck.shift()!);
  }

  return result;
};

export default function Activities({ active }: ActivitiesProps) {
  const [items, setItems] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [isSpinning, setIsSpinning] = useState(false);
  const [result, setResult] = useState<WheelOfFortuneResponse | null>(null);
  const [wheelConfig, setWheelConfig] = useState<WheelPrizeConfig[]>(DEFAULT_PRIZES);
  
  // Status state
  const [wheelStatus, setWheelStatus] = useState<{ can_spin: boolean; is_enabled?: boolean; next_spin_at: string | null; disabled_reason?: string } | null>(null);
  const [timeLeftStr, setTimeLeftStr] = useState<string>('');
  const [dims, setDims] = useState({ itemWidth: 84, itemGap: 8 });

  const containerRef = useRef<HTMLDivElement>(null);

  // Recompute item dimensions when container resizes
  const refreshDims = useCallback(() => {
    if (!containerRef.current) return;
    const w = containerRef.current.offsetWidth;
    setDims(computeDims(w));
  }, []);

  const resetPosition = useCallback(() => {
    if (!containerRef.current) return;
    const containerW = containerRef.current.offsetWidth;
    const d = computeDims(containerW);
    setDims(d);
    const totalW = d.itemWidth + d.itemGap;
    const initialOffset = (INITIAL_INDEX * totalW) - (containerW / 2) + (d.itemWidth / 2);
    setOffset(initialOffset);
  }, []);

  useEffect(() => {
    if (active) {
      apiFetch<any>('/marketing/wheel-of-fortune/status')
        .then(res => setWheelStatus(res))
        .catch(err => {
          console.error(err);
          // If backend fails, default to allowing spin
          setWheelStatus({ can_spin: true, is_enabled: true, next_spin_at: null });
        });

      apiFetch<WheelConfigPayload>('/marketing/wheel-config')
        .then(res => {
          if (res && res.prizes && res.prizes.length > 0) {
            setWheelConfig(res.prizes);
          }
        })
        .catch(err => console.error("Failed to load wheel config", err));
    }
  }, [active]);

  useEffect(() => {
    if (active && items.length === 0 && wheelConfig.length > 0) {
      setItems(generateRandomItems(wheelConfig));
      setTimeout(resetPosition, 50);
      const onResize = () => { refreshDims(); resetPosition(); };
      window.addEventListener('resize', onResize);
      return () => window.removeEventListener('resize', onResize);
    }
  }, [active, items.length, wheelConfig, resetPosition, refreshDims]);

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
    setItems(generateRandomItems(wheelConfig));
    
    await new Promise(r => setTimeout(r, 50));

    // 2. Start animation INSTANTLY
    setIsSpinning(true);
    if (!containerRef.current) return;
    const containerW = containerRef.current.offsetWidth;
    const d = computeDims(containerW);
    const totalW = d.itemWidth + d.itemGap;
    const centerPoint = containerW / 2;
    const itemCenter = d.itemWidth / 2;
    
    const stopOffset = (Math.random() - 0.5) * (d.itemWidth - 16);
    const targetOffset = (WIN_INDEX * totalW) - centerPoint + itemCenter + stopOffset;
    
    setOffset(targetOffset);
    const spinStartTime = Date.now();

    // 3. Fetch API in background
    try {
      const res = await apiFetch<WheelOfFortuneResponse>('/marketing/wheel-of-fortune', {
        method: 'POST',
      });
      
      // 4. Inject winner at WIN_INDEX and ensure neighbours differ
      setItems(prev => {
        const newItems = [...prev];
        const winnerId = res.reward_type || 'post_payment_top_error';
        newItems[WIN_INDEX] = winnerId;

        // Make sure adjacent items are different from the winner
        const otherPrizes = wheelConfig
          .map(p => p.id)
          .filter(id => id !== winnerId);
        if (otherPrizes.length > 0) {
          // Fix left neighbour
          if (WIN_INDEX > 0 && newItems[WIN_INDEX - 1] === winnerId) {
            newItems[WIN_INDEX - 1] = otherPrizes[Math.floor(Math.random() * otherPrizes.length)];
          }
          // Fix right neighbour
          if (WIN_INDEX < newItems.length - 1 && newItems[WIN_INDEX + 1] === winnerId) {
            newItems[WIN_INDEX + 1] = otherPrizes[Math.floor(Math.random() * otherPrizes.length)];
          }
        }

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

          <div className="relative w-full h-36 sm:h-52 md:h-64 mb-8 bg-slate-900 border-y border-white/10 shadow-[0_0_50px_rgba(0,0,0,0.6)] overflow-hidden rounded-xl" ref={containerRef}>
            
            <div className="absolute inset-0 flex items-center justify-center opacity-[0.03] pointer-events-none grayscale">
              <img src={LOGO_URL} alt="" className="w-64 h-64 object-cover" />
            </div>

            <div className="absolute inset-y-0 left-0 w-12 sm:w-16 bg-gradient-to-r from-slate-900 via-slate-900/80 to-transparent z-10 pointer-events-none" />
            <div className="absolute inset-y-0 right-0 w-12 sm:w-16 bg-gradient-to-l from-slate-900 via-slate-900/80 to-transparent z-10 pointer-events-none" />
            
            <div className="absolute top-0 left-1/2 w-0 h-0 border-l-[8px] sm:border-l-[10px] border-r-[8px] sm:border-r-[10px] border-t-[11px] sm:border-t-[14px] border-l-transparent border-r-transparent border-t-amber-400 z-20 -translate-x-1/2 drop-shadow-[0_2px_5px_rgba(0,0,0,0.5)]" />
            <div className="absolute bottom-0 left-1/2 w-0 h-0 border-l-[8px] sm:border-l-[10px] border-r-[8px] sm:border-r-[10px] border-b-[11px] sm:border-b-[14px] border-l-transparent border-r-transparent border-b-amber-400 z-20 -translate-x-1/2 drop-shadow-[0_-2px_5px_rgba(0,0,0,0.5)]" />

            <motion.div 
              className="flex items-center h-full"
              style={{ gap: dims.itemGap }}
              animate={{ x: -offset }}
              transition={
                isSpinning 
                  ? { duration: 8, ease: [0.05, 0.95, 0.1, 1] } 
                  : { duration: 0 }
              }
            >
              {items.map((item, index) => {
                const prizeConfig = wheelConfig.find(p => p.id === item) || DEFAULT_PRIZES[0];
                const IconComponent = ICON_MAP[prizeConfig.icon] || Star;
                const isCompact = dims.itemWidth < 100;

                return (
                  <div 
                    key={`${index}-${item}`}
                    style={{ width: dims.itemWidth, minWidth: dims.itemWidth }}
                    className={`relative rounded bg-slate-800/80 border border-slate-700 shrink-0 overflow-hidden shadow-inner flex flex-col items-center justify-center ${
                      isCompact ? 'h-24 p-1.5' : 'h-40 p-3'
                    }`}
                  >
                    <div className={`absolute top-0 inset-x-0 h-full bg-gradient-to-b ${prizeConfig.color} opacity-[0.15] pointer-events-none mix-blend-screen`} />
                    
                    <div className={`relative z-10 flex items-center justify-center ${
                      isCompact ? 'w-8 h-8 mb-1' : 'w-14 h-14 mb-2'
                    }`}>
                      <div className={`absolute inset-0 bg-gradient-to-b ${prizeConfig.color} blur-xl opacity-40 rounded-full`} />
                      <IconComponent className={`text-white opacity-95 drop-shadow-lg z-10 ${
                        isCompact ? 'w-6 h-6' : 'w-10 h-10'
                      }`} />
                    </div>
                    
                    <span className={`font-black text-center text-white leading-tight z-10 drop-shadow-md ${
                      isCompact ? 'text-[9px]' : 'text-[12px]'
                    }`}>
                      {prizeConfig.label}
                    </span>
                    {!isCompact && (
                      <span className="text-[10px] font-semibold text-center text-slate-300 z-10 mt-1 drop-shadow-sm">
                        {prizeConfig.sub}
                      </span>
                    )}
                    
                    <div className={`absolute bottom-0 left-0 right-0 bg-gradient-to-r ${prizeConfig.color} shadow-[0_-2px_10px_rgba(0,0,0,0.3)] ${
                      isCompact ? 'h-[4px]' : 'h-[6px]'
                    }`} />
                  </div>
                );
              })}
            </motion.div>
          </div>

          {/* Button or Cooldown UI */}
          <div className="flex flex-col gap-4">
            {wheelStatus === null ? (
              <div className="h-14 flex items-center justify-center opacity-50">Загрузка...</div>
            ) : wheelStatus.is_enabled === false ? (
              <div className="w-full max-w-sm mx-auto p-5 text-center rounded-2xl bg-slate-900/90 border border-slate-700/50 shadow-lg">
                <div className="mx-auto w-12 h-12 rounded-2xl bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-400 mb-3">
                  <Gamepad2 className="w-6 h-6 opacity-60" />
                </div>
                <h3 className="text-base font-black text-white mb-1">Активность выключена</h3>
                <p className="text-xs text-slate-400 font-medium leading-relaxed">
                  {wheelStatus.disabled_reason || 'Колесо Фортуны сейчас недоступно.'}
                </p>
              </div>
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
