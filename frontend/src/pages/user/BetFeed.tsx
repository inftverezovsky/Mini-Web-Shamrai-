import React, { Suspense, lazy, memo, useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { useWindowVirtualizer } from '@tanstack/react-virtual';
import { apiFetch } from '../../utils/api';
import { API_BASE_URL, DEBUG_AUTH_ENABLED } from '../../config/api';
import { BetResponse, PaginatedResponse } from '../../schemas/schemas';
import { useAuthSelector } from '../../context/AuthContext';
import { useLayoutMode } from '../../context/LayoutModeContext';
import { Trophy, Calendar, Check, Plus, AlertCircle, Loader2, Sparkles, Flame, ExternalLink, Star, Image as ImageIcon } from 'lucide-react';
import { hasActivePromo, promoFlags } from '../../config/promoFlags';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import { isStaffRole } from '../../utils/roles';
import { notifyError, notifyPending, notifySuccess } from '../../utils/notify';
import { trackEvent } from '../../utils/analytics';
import { BETS_FEED_QUERY_KEY, TAB_QUERY_STALE_TIME, fetchBetsFeedPage } from '../../utils/tabPrefetch';

const MarathonWidget = lazy(() => import('./MarathonWidget'));
const LiveTracker = lazy(() => import('./LiveTracker'));
const SwipeCard = lazy(() => import('./SwipeCard'));
const QuizWidget = lazy(() => import('./QuizWidget'));
const PvPWidget = lazy(() => import('./PvPWidget'));
const CrowdBetWidget = lazy(() => import('./CrowdBetWidget'));
const TAKEN_BETS_STORAGE_KEY = 'bet_tma_taken_ids';

type BetFeedInfiniteData = InfiniteData<PaginatedResponse<BetResponse>, string | null>;

function persistTakenBetIds(ids: string[]) {
  localStorage.setItem(TAKEN_BETS_STORAGE_KEY, JSON.stringify(ids));
}

function markBetAsTakenInFeed(data: BetFeedInfiniteData | undefined, betId: string): BetFeedInfiniteData | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.map((bet) => (
        bet.id === betId
          ? { ...bet, is_taken: true }
          : bet
      )),
    })),
  };
}

function resolveAssetUrl(path: string | null) {
  if (!path) return null;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE_URL}${path}`;
}

function getBookmakerLinkUrl(bet: BetResponse, bookmakerId: number) {
  const link = bet.bookmaker_links?.find((item) => Number(item.bookmaker_id) === bookmakerId);
  return link?.url?.trim() || null;
}

function formatLiveTimeLeft(endsAt: string, now = Date.now()) {
  const difference = +new Date(endsAt) - now;
  const minutes = Math.max(0, Math.floor((difference / 1000 / 60) % 60));
  const seconds = Math.max(0, Math.floor((difference / 1000) % 60));
  return difference <= 0
    ? '00:00'
    : `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

// Countdown text is intentionally mutated outside React. A 1s state update in a
// blurred feed card makes iOS Safari re-run glass painting; this touches one text node.
const LiveTimer = memo(function LiveTimer({ endsAt, active = true }: { endsAt: string; active?: boolean }) {
  const textRef = useRef<HTMLSpanElement | null>(null);
  const frameRef = useRef<number | undefined>();

  const updateText = useCallback(() => {
    if (frameRef.current !== undefined) window.cancelAnimationFrame(frameRef.current);
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = undefined;
      if (textRef.current) textRef.current.textContent = formatLiveTimeLeft(endsAt);
    });
  }, [endsAt]);

  useEffect(() => {
    if (!active) return undefined;

    updateText();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') updateText();
    }, 1000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') updateText();
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', handleVisibility);
      if (frameRef.current !== undefined) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = undefined;
      }
    };
  }, [active, updateText]);

  return (
    <span className="shimmer-border relative flex shrink-0 select-none items-center gap-1 rounded-full border border-rose-500/25 bg-rose-500/10 px-2.5 py-0.5 text-[8.5px] font-black uppercase tracking-wider text-rose-400 shadow-neon-rose animate-pulse">
      <Flame className="w-3 h-3 fill-rose-500 text-rose-500 animate-bounce" />
      <span>Live</span>
      <span ref={textRef} className="ml-1 font-mono text-[9px]">
        {formatLiveTimeLeft(endsAt)}
      </span>
    </span>
  );
});

function PromoBoundary({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>;
}

function formatBetDate(dateStr: string) {
  const d = new Date(dateStr);
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface BetFeedCardProps {
  bet: BetResponse;
  index: number;
  isTaken: boolean;
  isActionLoading: boolean;
  onTakeBet: (betId: string) => void;
  onBuyBet: (betId: string) => void;
  active?: boolean;
}

const BetFeedCard = memo(function BetFeedCard({
  bet,
  index,
  isTaken,
  isActionLoading,
  onTakeBet,
  onBuyBet,
  active = true,
}: BetFeedCardProps) {
  const unlocked = bet.is_unlocked;
  const isLive = bet.category === 'live';
  const isFreePublication = bet.price_stars == null || Number(bet.price_stars) <= 0;
  const betBookmakers = bet.bookmakers?.length
    ? bet.bookmakers
    : bet.bookmaker
      ? [bet.bookmaker]
      : [];

  return (
    <div
      className="bet-feed-card motion-card shimmer-border relative min-w-0 animate-fade-in space-y-2.5 overflow-hidden rounded-2xl border border-white/15 bg-white/[0.04] p-3.5 shadow-glass backdrop-blur-md transition-all duration-300 hover:border-cyan-400/30 hover:scale-[1.01] hover:-translate-y-0.5"
      style={{ animationDelay: `${Math.min(index * 70, 420)}ms` }}
    >
      <div className="relative z-10 flex flex-wrap items-center justify-between gap-2 text-[9px] text-slate-400">
        <span className="flex min-w-0 items-center font-medium">
          <Calendar className="w-3 h-3 mr-1 text-cyan-300/70" />
          {formatBetDate(bet.created_at)}
        </span>

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1.5">
          {bet.sport_type && (
            <span className="flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-white/10 bg-slate-900/60 py-0.5 pl-1 pr-2.5 text-[8.5px] font-bold uppercase tracking-wider text-slate-200 shadow-sm transition-all duration-300 hover:border-pink-500/30">
              <SportIconFrame label={bet.sport_type} size="compact" className="rounded-full overflow-hidden" />
              <span className="min-w-0 truncate">{bet.sport_type}</span>
            </span>
          )}
          {isLive && bet.live_ends_at ? (
            <LiveTimer endsAt={bet.live_ends_at} active={active} />
          ) : (
            <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full font-bold">
              Ожидает
            </span>
          )}
        </div>
      </div>

      <div className={!unlocked ? 'select-none opacity-45 pointer-events-none' : ''}>
        <div>
          <h4 className="text-xs font-extrabold text-white leading-snug">{bet.event_name}</h4>
        </div>

        {unlocked && bet.coupon_image_url && (
          <div className="mt-2 bg-black/20 border border-white/10 rounded-xl p-1.5">
            <div className="flex items-center space-x-1.5 text-[8px] text-slate-500 font-extrabold uppercase tracking-wider mb-1.5 px-0.5">
              <ImageIcon className="w-3.5 h-3.5 text-emerald-400" />
              <span>Скрин купона</span>
            </div>
            <img
              src={resolveAssetUrl(bet.coupon_image_url) || ''}
              alt="Скрин купона"
              className="w-full max-h-56 object-contain rounded-lg border border-white/5 bg-slate-950/60"
              loading="lazy"
              decoding="async"
            />
          </div>
        )}

        {betBookmakers.length > 0 && (
          <div className="mt-2 grid grid-cols-1 gap-1.5 min-[360px]:grid-cols-2">
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
          <div className="mt-2 bg-emerald-500/10 border border-emerald-500/20 p-2.5 rounded-xl">
            <span className="text-[8px] text-emerald-300/70 font-extrabold uppercase tracking-wider block">
              Ставка
            </span>
            <span className="text-xs font-black text-emerald-300">{bet.outcome}</span>
          </div>
        )}

        {bet.description && (
          <p className="text-slate-300 text-[11px] leading-normal bg-black/20 p-2.5 rounded-xl border border-white/5 mt-2">
            {bet.description}
          </p>
        )}

        {unlocked && isLive && bet.api_match_id && (
          <div className="mt-2">
            <PromoBoundary>
              <LiveTracker apiMatchId={bet.api_match_id} active={active} />
            </PromoBoundary>
          </div>
        )}

        {unlocked && bet.match_link && (
          <div className="mt-2 space-y-2">
            <a
              href={bet.match_link}
              target="_blank"
              rel="noreferrer"
              onClick={() => trackEvent('Match Link Clicked', {
                sport: bet.sport_type,
                category: bet.category,
              })}
              className="bg-cyan-500/10 border border-cyan-500/20 text-cyan-200 text-[9px] font-extrabold py-2 px-3 rounded-xl flex items-center justify-center space-x-1.5 active:scale-[0.98] transition-all"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              <span>Открыть матч</span>
            </a>
          </div>
        )}
      </div>

      <div className="relative z-10 mt-1 flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-2">
        <div className="min-w-0">
          <span className="text-[9px] uppercase font-bold text-slate-500 block">Коэффициент</span>
          <span className="text-base font-black text-emerald-400 text-glow-green">{parseFloat(bet.coefficient as any).toFixed(2)}</span>
        </div>

        {unlocked ? (
          isTaken ? (
            <div className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-[9px] font-extrabold text-emerald-400 shadow-neon-green animate-pulse">
              <Check className="w-3.5 h-3.5" />
              <span>Принято</span>
            </div>
          ) : isFreePublication ? (
            null
          ) : (
            <button
              onClick={() => onTakeBet(bet.id)}
              disabled={isActionLoading}
              className="flex min-h-[36px] min-w-0 items-center justify-center gap-1.5 rounded-xl bg-emerald-500 px-3 py-2 text-[11px] font-black text-slate-950 shadow-neon-green transition-all duration-300 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50"
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
            onClick={() => onBuyBet(bet.id)}
            disabled={isActionLoading}
            className="flex min-h-[36px] min-w-0 items-center justify-center gap-1.5 rounded-xl bg-amber-500 px-3 py-2 text-[11px] font-black text-slate-950 shadow-neon-amber transition-all duration-300 hover:bg-amber-600 active:scale-[0.98] disabled:opacity-50"
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
});

interface BetFeedListProps {
  bets: BetResponse[];
  takenBetIds: string[];
  actionLoadingId: string | null;
  onTakeBet: (betId: string) => void;
  onBuyBet: (betId: string) => void;
  active?: boolean;
}

const BetFeedGridList = memo(function BetFeedGridList({
  bets,
  takenBetIds,
  actionLoadingId,
  onTakeBet,
  onBuyBet,
  active = true,
}: BetFeedListProps) {
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      {bets.map((bet, idx) => {
        const isTaken = bet.is_taken || takenBetIds.includes(bet.id);
        return (
          <BetFeedCard
            key={bet.id}
            bet={bet}
            index={idx}
            isTaken={isTaken}
            isActionLoading={actionLoadingId === bet.id}
            onTakeBet={onTakeBet}
            onBuyBet={onBuyBet}
            active={active}
          />
        );
      })}
    </div>
  );
});

const VirtualBetFeedList = memo(function VirtualBetFeedList({
  bets,
  takenBetIds,
  actionLoadingId,
  onTakeBet,
  onBuyBet,
  active = true,
}: BetFeedListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const [scrollMargin, setScrollMargin] = useState(0);

  useLayoutEffect(() => {
    const updateScrollMargin = () => {
      setScrollMargin(listRef.current?.offsetTop ?? 0);
    };

    updateScrollMargin();
    window.addEventListener('resize', updateScrollMargin);
    return () => window.removeEventListener('resize', updateScrollMargin);
  }, [bets.length]);

  const virtualizer = useWindowVirtualizer<HTMLDivElement>({
    count: bets.length,
    estimateSize: () => 360,
    getItemKey: (index) => bets[index]?.id ?? index,
    overscan: 2,
    scrollMargin,
  });

  return (
    <div ref={listRef} className="relative w-full min-w-0">
      <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((virtualItem) => {
          const bet = bets[virtualItem.index];
          if (!bet) return null;
          const isTaken = bet.is_taken || takenBetIds.includes(bet.id);

          return (
            <div
              key={bet.id}
              ref={virtualizer.measureElement}
              data-index={virtualItem.index}
              className="virtual-bet-feed-row absolute left-0 top-0 w-full pb-3"
              style={{
                transform: `translateY(${virtualItem.start - scrollMargin}px)`,
              }}
            >
              <BetFeedCard
                bet={bet}
                index={virtualItem.index}
                isTaken={isTaken}
                isActionLoading={actionLoadingId === bet.id}
                onTakeBet={onTakeBet}
                onBuyBet={onBuyBet}
                active={active}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
});

interface BetFeedProps {
  onNavigateToBilling?: () => void;
  active?: boolean;
}

export default function BetFeed({ onNavigateToBilling, active: feedActive = true }: BetFeedProps) {
  const queryClient = useQueryClient();
  const userProfile = useAuthSelector((state) => state.user);
  const { isCompact } = useLayoutMode();
  const debugCheckoutEnabled = DEBUG_AUTH_ENABLED;
  
  const [takenBetIds, setTakenBetIds] = useState<string[]>([]);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  const feedQuery = useInfiniteQuery<PaginatedResponse<BetResponse>, Error>({
    queryKey: BETS_FEED_QUERY_KEY,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => fetchBetsFeedPage((pageParam as string | null) ?? null, signal),
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_cursor : undefined),
    staleTime: TAB_QUERY_STALE_TIME,
  });
  const {
    data: feedData,
    error: feedError,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    refetch: refetchFeed,
  } = feedQuery;

  const bets = useMemo(
    () => feedData?.pages.flatMap((page) => page.items) ?? [],
    [feedData],
  );

  const loadFeed = useCallback(async () => {
    await refetchFeed();
  }, [refetchFeed]);

  useEffect(() => {
    const backendTakenIds = bets.filter((bet) => bet.is_taken).map((bet) => bet.id);
    setTakenBetIds(backendTakenIds);
    persistTakenBetIds(backendTakenIds);
  }, [bets]);

  const loading = isLoading;
  const error = feedError?.message || null;

  const takeBetMutation = useMutation({
    mutationFn: async ({ betId }: { betId: string; bet?: BetResponse }) => {
      return apiFetch(`/bets/${betId}/take`, { method: 'POST' });
    },
    onMutate: async ({ betId }) => {
      setActionLoadingId(betId);
      await queryClient.cancelQueries({ queryKey: BETS_FEED_QUERY_KEY });

      const previousFeed = queryClient.getQueryData<BetFeedInfiniteData>(BETS_FEED_QUERY_KEY);
      const previousTakenIds = takenBetIds;

      queryClient.setQueryData<BetFeedInfiniteData>(
        BETS_FEED_QUERY_KEY,
        (current) => markBetAsTakenInFeed(current, betId),
      );
      setTakenBetIds((current) => {
        const nextTaken = current.includes(betId) ? current : [...current, betId];
        persistTakenBetIds(nextTaken);
        return nextTaken;
      });

      return { previousFeed, previousTakenIds };
    },
    onSuccess: (_data, { bet }) => {
      notifySuccess('Прогноз добавлен в “Мои ставки”.');
      trackEvent('Bet Take Success', {
        category: bet?.category,
        sport: bet?.sport_type,
        unlocked: bet?.is_unlocked,
      });
    },
    onError: (err: any, { bet }, context) => {
      queryClient.setQueryData(BETS_FEED_QUERY_KEY, context?.previousFeed);
      if (context?.previousTakenIds) {
        setTakenBetIds(context.previousTakenIds);
        persistTakenBetIds(context.previousTakenIds);
      }
      notifyError(err.message || 'Не удалось принять ставку');
      trackEvent('Bet Take Failed', {
        category: bet?.category,
        sport: bet?.sport_type,
        unlocked: bet?.is_unlocked,
      });
    },
    onSettled: () => {
      setActionLoadingId(null);
      queryClient.invalidateQueries({ queryKey: BETS_FEED_QUERY_KEY });
    },
  });

  const handleTakeBet = useCallback((betId: string) => {
    const bet = bets.find((item) => item.id === betId);
    trackEvent('Bet Take Started', {
      category: bet?.category,
      sport: bet?.sport_type,
      unlocked: bet?.is_unlocked,
    });
    takeBetMutation.mutate({ betId, bet });
  }, [bets, takeBetMutation]);

  const handleBuyBet = useCallback(async (betId: string) => {
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
  }, [bets, debugCheckoutEnabled, loadFeed]);

  const handleNavigateToBilling = () => {
    trackEvent('Billing CTA Clicked', {
      location: 'feed_access_banner',
      active_access: active,
    });
    onNavigateToBilling?.();
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
    <div className={`${isCompact ? 'w-full min-w-0 space-y-3' : 'space-y-4'} pb-8 animate-slide-up`}>
      
      {promoFlags.marathon && (
        <PromoBoundary>
          <MarathonWidget />
        </PromoBoundary>
      )}
      {promoFlags.swipe && (
        <PromoBoundary>
          <SwipeCard />
        </PromoBoundary>
      )}

      {hasActivePromo && (
        <div className="grid grid-cols-1 gap-3">
          {promoFlags.pvp && (
            <PromoBoundary>
              <PvPWidget />
            </PromoBoundary>
          )}
          {promoFlags.quiz && (
            <PromoBoundary>
              <QuizWidget />
            </PromoBoundary>
          )}
          {promoFlags.crowdBet && (
            <PromoBoundary>
              <CrowdBetWidget onFunded={loadFeed} />
            </PromoBoundary>
          )}
        </div>
      )}

      {/* 2. Account Access Banner */}
      {!active && (
        <div className="promo-status-panel motion-card shimmer-border spark-field relative flex flex-col gap-3 overflow-hidden rounded-xl border border-indigo-500/20 bg-gradient-to-r from-cyan-500/10 via-indigo-500/10 to-fuchsia-500/10 p-3 text-[11px] text-slate-200 shadow-glass backdrop-blur-md sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 space-y-0.5">
            <p className="font-extrabold text-white flex items-center flex-wrap gap-1.5">
              <Sparkles className="iridescent-icon w-3.5 h-3.5 mr-1.5 shrink-0" />
              Нужен абонемент
              <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[8px] font-black uppercase tracking-wider text-cyan-300">
                В разработке
              </span>
            </p>
          <p className="text-[9px] text-slate-400">
              Купите абонемент на матчи, чтобы открыть премиум-ленту прогнозов.
            </p>
          </div>
          {onNavigateToBilling && (
            <button 
              onClick={handleNavigateToBilling}
              className="inline-flex min-h-[36px] w-full items-center justify-center rounded-lg bg-indigo-500 px-3 py-1.5 text-[10px] font-extrabold text-white shadow-glass transition-all active:scale-95 sm:w-auto"
            >
              Купить матчи
            </button>
          )}
        </div>
      )}

      {/* 3. Bets Feed Grid list */}
      {bets.length === 0 ? (
        <div className="motion-card shimmer-border relative overflow-hidden rounded-2xl border border-white/10 bg-white/[0.04] p-5 text-center shadow-glass backdrop-blur-md">
          <Trophy className="iridescent-icon w-8 h-8 mx-auto mb-2" />
          <h4 className="text-xs font-bold text-white uppercase tracking-wider">Лента пуста</h4>
          <p className="text-slate-400 text-[10px] mt-1 leading-relaxed">
            {active ? 'Сейчас нет активных прогнозов. Ожидайте уведомлений.' : 'Премиум-лента откроется после покупки абонемента.'}
          </p>
        </div>
      ) : (
        isCompact ? (
          <VirtualBetFeedList
            bets={bets}
            takenBetIds={takenBetIds}
            actionLoadingId={actionLoadingId}
            onTakeBet={handleTakeBet}
            onBuyBet={handleBuyBet}
            active={feedActive}
          />
        ) : (
          <BetFeedGridList
            bets={bets}
            takenBetIds={takenBetIds}
            actionLoadingId={actionLoadingId}
            onTakeBet={handleTakeBet}
            onBuyBet={handleBuyBet}
            active={feedActive}
          />
        )
      )}

      {hasNextPage && (
        <button
          type="button"
          onClick={() => {
            trackEvent('Feed Load More Clicked', {
              loaded_count: bets.length,
            });
            void fetchNextPage();
          }}
          disabled={isFetchingNextPage}
          className="mx-auto flex min-h-[36px] items-center justify-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/10 px-3 text-[11px] font-black text-cyan-100 transition-all hover:bg-cyan-300/15 disabled:opacity-50"
        >
          {isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          <span>{isFetchingNextPage ? 'Загружаем...' : 'Показать еще'}</span>
        </button>
      )}
    </div>
  );
}
