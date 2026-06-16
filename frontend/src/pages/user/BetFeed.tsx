import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { apiFetch } from '../../utils/api';
import { API_BASE_URL, DEBUG_AUTH_ENABLED } from '../../config/api';
import { BetResponse, PaginatedResponse } from '../../schemas/schemas';
import { useAuth } from '../../context/AuthContext';
import { useLayoutMode } from '../../context/LayoutModeContext';
import { Trophy, Calendar, Check, Plus, AlertCircle, Loader2, Sparkles, Flame, ExternalLink, Star, Image as ImageIcon } from 'lucide-react';
import MarathonWidget from './MarathonWidget';
import LiveTracker from './LiveTracker';
import SwipeCard from './SwipeCard';
import QuizWidget from './QuizWidget';
import PvPWidget from './PvPWidget';
import CrowdBetWidget from './CrowdBetWidget';
import { hasActivePromo, promoFlags } from '../../config/promoFlags';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import { isStaffRole } from '../../utils/roles';
import { notifyError, notifyPending, notifySuccess } from '../../utils/notify';
import { trackEvent } from '../../utils/analytics';

function resolveAssetUrl(path: string | null) {
  if (!path) return null;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE_URL}${path}`;
}

function getBookmakerLinkUrl(bet: BetResponse, bookmakerId: number) {
  const link = bet.bookmaker_links?.find((item) => Number(item.bookmaker_id) === bookmakerId);
  return link?.url?.trim() || null;
}

function useVisibleNowTick(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        setNow(Date.now());
      }
    }, intervalMs);
    const syncOnVisible = () => {
      if (document.visibilityState === 'visible') setNow(Date.now());
    };
    document.addEventListener('visibilitychange', syncOnVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', syncOnVisible);
    };
  }, [intervalMs]);

  return now;
}

// Shared ticking countdown timer for Live forecasts.
function LiveTimer({ endsAt, now }: { endsAt: string; now: number }) {
  const difference = +new Date(endsAt) - now;
  const minutes = Math.max(0, Math.floor((difference / 1000 / 60) % 60));
  const seconds = Math.max(0, Math.floor((difference / 1000) % 60));
  const timeLeft = difference <= 0
    ? '00:00'
    : `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

  return (
    <span className="shimmer-border bg-rose-500/10 border border-rose-500/25 px-2.5 py-0.5 rounded-full font-black text-rose-400 shadow-neon-rose animate-pulse text-[8.5px] uppercase tracking-wider flex items-center space-x-1 shrink-0 select-none relative">
      <Flame className="w-3 h-3 fill-rose-500 text-rose-500 animate-bounce" />
      <span>Live</span>
      <span className="ml-1 font-mono text-[9px]">{timeLeft}</span>
    </span>
  );
}

interface BetFeedProps {
  onNavigateToBilling?: () => void;
}

export default function BetFeed({ onNavigateToBilling }: BetFeedProps) {
  const { user: userProfile } = useAuth();
  const { isCompact } = useLayoutMode();
  const debugCheckoutEnabled = DEBUG_AUTH_ENABLED;
  
  const [takenBetIds, setTakenBetIds] = useState<string[]>([]);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);
  const nowTick = useVisibleNowTick();

  const feedQuery = useInfiniteQuery<PaginatedResponse<BetResponse>, Error>({
    queryKey: ['bets-feed-page'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: '20' });
      if (pageParam) params.set('cursor', String(pageParam));
      return apiFetch<PaginatedResponse<BetResponse>>(`/bets/feed-page?${params.toString()}`);
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_cursor : undefined),
    staleTime: 20_000,
  });

  const bets = useMemo(
    () => feedQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [feedQuery.data],
  );

  const loadFeed = useCallback(async () => {
    await feedQuery.refetch();
  }, [feedQuery]);

  useEffect(() => {
    const backendTakenIds = bets.filter((bet) => bet.is_taken).map((bet) => bet.id);
    setTakenBetIds(backendTakenIds);
    localStorage.setItem('bet_tma_taken_ids', JSON.stringify(backendTakenIds));
  }, [bets]);

  const loading = feedQuery.isLoading;
  const error = feedQuery.error?.message || null;

  const handleTakeBet = async (betId: string) => {
    const bet = bets.find((item) => item.id === betId);

    try {
      setActionLoadingId(betId);
      trackEvent('Bet Take Started', {
        category: bet?.category,
        sport: bet?.sport_type,
        unlocked: bet?.is_unlocked,
      });

      // POST /api/bets/{bet_id}/take
      await apiFetch(`/bets/${betId}/take`, { method: 'POST' });
      
      const nextTaken = [...takenBetIds, betId];
      setTakenBetIds(nextTaken);
      localStorage.setItem('bet_tma_taken_ids', JSON.stringify(nextTaken));
      notifySuccess('Прогноз добавлен в “Мои ставки”.');
      trackEvent('Bet Take Success', {
        category: bet?.category,
        sport: bet?.sport_type,
        unlocked: bet?.is_unlocked,
      });
      await loadFeed();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось принять ставку');
      trackEvent('Bet Take Failed', {
        category: bet?.category,
        sport: bet?.sport_type,
        unlocked: bet?.is_unlocked,
      });
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleBuyBet = async (betId: string) => {
    const bet = bets.find((item) => item.id === betId);

    try {
      setActionLoadingId(betId);
      trackEvent('Stars Checkout Started', {
        category: bet?.category,
        sport: bet?.sport_type,
        price_stars: bet?.price_stars ?? 50,
      });
      
      // POST /api/payments/invoice
      const invoiceData = await apiFetch('/payments/invoice', {
        method: 'POST',
        body: JSON.stringify({ 
          bet_id: betId
        })
      });

      const invoiceUrl = invoiceData.invoice_url;
      const tg = window.Telegram?.WebApp;

      if (tg && typeof tg.openInvoice === 'function') {
        trackEvent('Stars Checkout Opened', {
          category: bet?.category,
          sport: bet?.sport_type,
          price_stars: bet?.price_stars ?? 50,
        });
        tg.openInvoice(invoiceUrl, async (status: string) => {
          if (status === 'paid') {
            notifyPending('Оплата прошла в Telegram. Ждем webhook и обновляем ленту.');
            trackEvent('Stars Checkout Paid', {
              category: bet?.category,
              sport: bet?.sport_type,
              price_stars: bet?.price_stars ?? 50,
            });
            await loadFeed();
          } else {
            trackEvent('Stars Checkout Closed', {
              status,
              category: bet?.category,
              sport: bet?.sport_type,
            });
            if (import.meta.env.DEV) {
              console.warn('Payment sheet closed or failed. Status:', status);
            }
          }
        });
      } else {
        if (!debugCheckoutEnabled) {
          notifyError('Покупка за Stars доступна внутри Telegram. В браузере включите debug auth только для smoke-тестов.');
          return;
        }

        await apiFetch(`/payments/debug/complete-bet/${betId}`, { method: 'POST' });
        notifySuccess('Debug-покупка проведена, прогноз открыт.');
        trackEvent('Stars Checkout Paid', {
          provider: 'debug',
          category: bet?.category,
          sport: bet?.sport_type,
          price_stars: bet?.price_stars ?? 50,
        });
        await loadFeed();
      }
    } catch (err: any) {
      notifyError(err.message || 'Ошибка обработки транзакции');
      trackEvent('Stars Checkout Failed', {
        category: bet?.category,
        sport: bet?.sport_type,
        price_stars: bet?.price_stars ?? 50,
      });
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleNavigateToBilling = () => {
    trackEvent('Billing CTA Clicked', {
      location: 'feed_access_banner',
      active_access: active,
    });
    onNavigateToBilling?.();
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const hasMatchAccess = () => {
    if (isStaffRole(userProfile?.role)) return true;
    return (userProfile?.matches_remaining || 0) > 0 || Boolean(userProfile?.guarantee_active);
  };

  const active = hasMatchAccess();

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] space-y-3">
        <Loader2 className="w-7 h-7 text-emerald-500 animate-spin" />
        <span className="text-slate-400 text-xs">Загрузка прогнозов...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center p-6 text-rose-400 text-xs flex flex-col items-center space-y-2">
        <AlertCircle className="w-8 h-8" />
        <span>Ошибка: {error}</span>
        <button onClick={loadFeed} className="underline text-indigo-400">Повторить загрузку</button>
      </div>
    );
  }

  return (
    <div className={`${isCompact ? 'mx-auto max-w-md space-y-4' : 'space-y-5'} animate-slide-up pb-10`}>
      
      {promoFlags.marathon && <MarathonWidget />}
      {promoFlags.swipe && <SwipeCard />}

      {hasActivePromo && (
        <div className="grid grid-cols-1 gap-4">
          {promoFlags.pvp && <PvPWidget />}
          {promoFlags.quiz && <QuizWidget />}
          {promoFlags.crowdBet && <CrowdBetWidget onFunded={loadFeed} />}
        </div>
      )}

      {/* 2. Account Access Banner */}
      {!active && (
        <div className="promo-status-panel motion-card shimmer-border spark-field bg-gradient-to-r from-cyan-500/10 via-indigo-500/10 to-fuchsia-500/10 border border-indigo-500/20 backdrop-blur-md p-4 rounded-2xl flex items-center justify-between text-xs text-slate-200 shadow-glass relative overflow-hidden">
          <div className="space-y-0.5">
            <p className="font-extrabold text-white flex items-center flex-wrap gap-1.5">
              <Sparkles className="iridescent-icon w-3.5 h-3.5 mr-1.5 shrink-0" />
              Нужен абонемент
              <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[8px] font-black uppercase tracking-wider text-cyan-300">
                В разработке
              </span>
            </p>
            <p className="text-[10px] text-slate-400">
              Купите пакет матчей, чтобы открыть премиум-ленту прогнозов.
            </p>
          </div>
          {onNavigateToBilling && (
            <button 
              onClick={handleNavigateToBilling}
              className="bg-indigo-500 text-white font-extrabold px-3 py-1.5 rounded-lg text-[10px] active:scale-95 transition-all shadow-glass"
            >
              Купить матчи
            </button>
          )}
        </div>
      )}

      {/* 3. Bets Feed Grid list */}
      {bets.length === 0 ? (
        <div className="motion-card shimmer-border bg-white/[0.04] border border-white/10 backdrop-blur-md p-8 text-center rounded-3xl relative overflow-hidden">
          <Trophy className="iridescent-icon w-10 h-10 mx-auto mb-2.5" />
          <h4 className="text-xs font-bold text-white uppercase tracking-wider">Лента пуста</h4>
          <p className="text-slate-400 text-[10px] mt-1 leading-relaxed">
            {active ? 'Сейчас нет активных прогнозов. Ожидайте уведомлений.' : 'Премиум-лента откроется после покупки абонемента.'}
          </p>
        </div>
      ) : (
        <div className={isCompact ? 'space-y-4' : 'grid grid-cols-1 gap-4 xl:grid-cols-2'}>
          {bets.map((bet, idx) => {
          const isTaken = bet.is_taken || takenBetIds.includes(bet.id);
          const unlocked = bet.is_unlocked;
          const isActionLoading = actionLoadingId === bet.id;
          const isLive = bet.category === 'live';
          const isFreePublication = bet.price_stars == null || Number(bet.price_stars) <= 0;
          const betBookmakers = bet.bookmakers?.length
            ? bet.bookmakers
            : bet.bookmaker
              ? [bet.bookmaker]
              : [];

          return (
            <div 
              key={bet.id} 
              className="bet-feed-card motion-card shimmer-border bg-white/[0.04] border border-white/15 backdrop-blur-md p-5 rounded-3xl space-y-3.5 relative overflow-hidden shadow-glass transition-all duration-300 hover:border-cyan-400/30 hover:scale-[1.02] hover:-translate-y-0.5 animate-fade-in"
              style={{ animationDelay: `${Math.min(idx * 70, 420)}ms` }}
            >
              {/* Header: Date & Status (and Countdown Timer if Live) */}
              <div className="flex justify-between items-center text-[10px] text-slate-400 z-10 relative">
                <span className="flex items-center font-medium">
                  <Calendar className="w-3 h-3 mr-1 text-cyan-300/70" />
                  {formatDate(bet.created_at)}
                </span>
                
                <div className="flex items-center space-x-1.5">
                  {bet.sport_type && (
                    <span className="bg-slate-900/60 border border-white/10 pl-1 pr-2.5 py-0.5 rounded-full font-bold text-[8.5px] uppercase tracking-wider flex items-center gap-1.5 text-slate-200 hover:border-pink-500/30 transition-all duration-300 shadow-sm">
                      <SportIconFrame label={bet.sport_type} size="compact" className="rounded-full overflow-hidden" />
                      {bet.sport_type}
                    </span>
                  )}
                  {isLive && bet.live_ends_at ? (
                    <LiveTimer endsAt={bet.live_ends_at} now={nowTick} />
                  ) : (
                    <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full font-bold">
                      Ожидает
                    </span>
                  )}
                </div>
              </div>

              {/* Blurred Container for Guest Mode */}
              <div className={!unlocked ? "select-none opacity-45 pointer-events-none" : ""}>
                {/* Event Info */}
                <div>
                  <h4 className="text-sm font-extrabold text-white leading-snug">{bet.event_name}</h4>
                </div>

                {unlocked && bet.coupon_image_url && (
                  <div className="mt-3 bg-black/20 border border-white/10 rounded-xl p-2">
                    <div className="flex items-center space-x-1.5 text-[9px] text-slate-500 font-extrabold uppercase tracking-wider mb-2 px-0.5">
                      <ImageIcon className="w-3.5 h-3.5 text-emerald-400" />
                      <span>Скрин купона</span>
                    </div>
                    <img
                      src={resolveAssetUrl(bet.coupon_image_url) || ''}
                      alt="Скрин купона"
                      className="w-full max-h-64 object-contain rounded-lg border border-white/5 bg-slate-950/60"
                      loading="lazy"
                    />
                  </div>
                )}

                {betBookmakers.length > 0 && (
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {betBookmakers.map((bookmaker) => {
                      const bookmakerUrl = unlocked ? getBookmakerLinkUrl(bet, bookmaker.id) : null;
                      const bookmakerContent = (
                        <>
                          <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="rounded-full overflow-hidden" />
                          <span className="min-w-0 flex-1 truncate text-left">{bookmaker.name}</span>
                          {bookmakerUrl && <ExternalLink className="w-3 h-3 shrink-0 text-cyan-300/80" />}
                        </>
                      );

                      return bookmakerUrl ? (
                        <a
                          key={bookmaker.id}
                          href={bookmakerUrl}
                          target="_blank"
                          rel="noreferrer"
                          onClick={() => trackEvent('Bookmaker Link Clicked', {
                            bookmaker: bookmaker.code || bookmaker.name,
                            sport: bet.sport_type,
                            category: bet.category,
                          })}
                          className="min-w-0 bg-slate-900/60 border border-cyan-400/20 pl-1 pr-2.5 py-1 rounded-full text-[9px] font-bold text-slate-100 inline-flex items-center gap-1.5 hover:border-cyan-400/50 hover:bg-cyan-400/10 active:scale-[0.98] transition-all duration-300 shadow-sm"
                        >
                          {bookmakerContent}
                        </a>
                      ) : (
                        <span
                          key={bookmaker.id}
                          className="min-w-0 bg-slate-900/60 border border-white/10 pl-1 pr-2.5 py-1 rounded-full text-[9px] font-bold text-slate-300 inline-flex items-center gap-1.5 transition-all duration-300 shadow-sm"
                        >
                          {bookmakerContent}
                        </span>
                      );
                    })}
                  </div>
                )}

                {unlocked && bet.outcome && (
                  <div className="mt-3 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-xl">
                    <span className="text-[8px] text-emerald-300/70 font-extrabold uppercase tracking-wider block">
                      Ставка
                    </span>
                    <span className="text-sm font-black text-emerald-300">{bet.outcome}</span>
                  </div>
                )}

                {/* Description */}
                {bet.description && (
                  <p className="text-slate-300 text-xs leading-normal bg-black/20 p-3 rounded-xl border border-white/5 mt-3">
                    {bet.description}
                  </p>
                )}

                {/* Live Match Scoreboard Tracker */}
                {unlocked && isLive && bet.api_match_id && (
                  <div className="mt-3">
                    <LiveTracker apiMatchId={bet.api_match_id} />
                  </div>
                )}

                {unlocked && bet.match_link && (
                  <div className="mt-3 space-y-2.5">
                    <a
                      href={bet.match_link}
                      target="_blank"
                      rel="noreferrer"
                      onClick={() => trackEvent('Match Link Clicked', {
                        sport: bet.sport_type,
                        category: bet.category,
                      })}
                      className="bg-cyan-500/10 border border-cyan-500/20 text-cyan-200 text-[10px] font-extrabold py-2.5 px-3 rounded-xl flex items-center justify-center space-x-1.5 active:scale-[0.98] transition-all"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                      <span>Открыть матч</span>
                    </a>
                  </div>
                )}
              </div>

              {/* Odds & Action button */}
              <div className="flex items-center justify-between pt-2 border-t border-white/5 mt-2 z-10 relative">
                <div>
                  <span className="text-[9px] uppercase font-bold text-slate-500 block">Коэффициент</span>
                  <span className="text-lg font-black text-emerald-400 text-glow-green">{parseFloat(bet.coefficient as any).toFixed(2)}</span>
                </div>

                {unlocked ? (
                  isTaken ? (
                    <div className="bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-[10px] font-extrabold py-2.5 px-4 rounded-xl flex items-center space-x-1.5 shadow-neon-green animate-pulse">
                      <Check className="w-3.5 h-3.5" />
                      <span>Принято</span>
                    </div>
                  ) : isFreePublication ? (
                    null
                  ) : (
                    <button
                      onClick={() => handleTakeBet(bet.id)}
                      disabled={isActionLoading}
                      className="bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 text-xs font-black py-2.5 px-4 rounded-xl flex items-center space-x-1.5 shadow-neon-green transition-all duration-300"
                    >
                      {isActionLoading ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <>
                          <Plus className="w-3.5 h-3.5" />
                          <span>Взять ставку</span>
                        </>
                      )}
                    </button>
                  )
                ) : (
                  <button
                    onClick={() => handleBuyBet(bet.id)}
                    disabled={isActionLoading}
                    className="bg-amber-500 hover:bg-amber-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 text-xs font-black py-2.5 px-4 rounded-xl flex items-center space-x-1.5 shadow-neon-amber transition-all duration-300"
                  >
                    {isActionLoading ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <>
                        <Star className="w-3.5 h-3.5 fill-slate-950 text-slate-950" />
                        <span>Купить за {bet.price_stars ?? 50} Stars</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
          );
        })}
        </div>
      )}

      {feedQuery.hasNextPage && (
        <button
          type="button"
          onClick={() => {
            trackEvent('Feed Load More Clicked', {
              loaded_count: bets.length,
            });
            void feedQuery.fetchNextPage();
          }}
          disabled={feedQuery.isFetchingNextPage}
          className="mx-auto flex min-h-[44px] items-center justify-center gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-300/10 px-4 text-xs font-black text-cyan-100 transition-all hover:bg-cyan-300/15 disabled:opacity-50"
        >
          {feedQuery.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          <span>{feedQuery.isFetchingNextPage ? 'Загружаем...' : 'Показать еще'}</span>
        </button>
      )}
    </div>
  );
}
